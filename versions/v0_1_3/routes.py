from flask import Blueprint, request, render_template, send_file, redirect, current_app
import base64
import xml.etree.ElementTree as ET
import zipfile
import subprocess
import logging
import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timedelta

# Get version from directory name
CURRENT_VERSION = os.path.basename(os.path.dirname(os.path.abspath(__file__)))

app = Blueprint(CURRENT_VERSION, __name__)

# Module-level logger
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

# Track last cleanup time
_last_cleanup_time = None

# Profile mappings for safer selection
QUALITY_PROFILES = {
    'standard_020': '0.20mm Standard @BBL X1C.json',
    'fine_012': '0.12mm Fine @BBL X1C.json',
    'extra_fine_008': '0.08mm Extra Fine @BBL X1C.json',
    'high_quality_012': '0.12mm High Quality @BBL X1C.json',
    'strength_020': '0.20mm Strength @BBL X1C.json',
    'extra_draft_028': '0.28mm Extra Draft @BBL X1C.json',
}

def resolve_profile(quality=None, material=None, process=None, filament=None):
    """Resolve quality/material IDs or legacy process/filament filenames to actual paths.
    Returns (process_filename, filament_filename, error_dict or None)
    """
    # Prefer new API params
    if quality:
        if quality not in QUALITY_PROFILES:
            return None, None, {"code": "unknown_quality", "message": f"Unknown quality ID: {quality}. Valid: {list(QUALITY_PROFILES.keys())}"}
        process_file = QUALITY_PROFILES[quality]
    elif process:
        # Legacy: validate filename to prevent path traversal
        try:
            base_dir = Path('files/process').resolve()
            requested = Path(process).name
            process_path = (base_dir / requested).resolve()
            if not str(process_path).startswith(str(base_dir)):
                return None, None, {"code": "invalid_process", "message": "Process filename not allowed"}
            process_file = requested
        except Exception as e:
            return None, None, {"code": "invalid_process", "message": f"Invalid process filename: {str(e)}"}
    else:
        process_file = '0.20mm Standard @BBL X1C.json'

    if material:
        # Map material to filament file
        material_lower = material.lower()
        filament_dir = Path('files/filament')
        available = list(filament_dir.glob('*.json')) if filament_dir.exists() else []
        
        # Try to find matching filament
        matched = None
        for f in available:
            if material_lower in f.name.lower():
                matched = f.name
                break
        
        if not matched:
            return None, None, {"code": "unknown_material", "message": f"Unknown material: {material}. Available: {[f.name for f in available]}"}
        filament_file = matched
    elif filament:
        # Legacy: validate filename
        try:
            base_dir = Path('files/filament').resolve()
            requested = Path(filament).name
            filament_path = (base_dir / requested).resolve()
            if not str(filament_path).startswith(str(base_dir)):
                return None, None, {"code": "invalid_filament", "message": "Filament filename not allowed"}
            filament_file = requested
        except Exception as e:
            return None, None, {"code": "invalid_filament", "message": f"Invalid filament filename: {str(e)}"}
    else:
        filament_file = 'Bambu PETG Basic @BBL X1C.json'

    return process_file, filament_file, None


def cleanup_old_jobs(max_age_hours=24):
    """Clean up job folders older than max_age_hours."""
    global _last_cleanup_time
    
    now = datetime.now()
    if _last_cleanup_time and (now - _last_cleanup_time) < timedelta(hours=1):
        return  # Skip if cleaned within the last hour
    
    jobs_dir = Path('temp/jobs')
    if not jobs_dir.exists():
        return
    
    cutoff = now - timedelta(hours=max_age_hours)
    cleaned = 0
    
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(job_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(job_dir)
                cleaned += 1
        except Exception as e:
            logger.warning(f"Failed to cleanup job dir {job_dir}: {e}")
    
    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} old job folders")
    
    _last_cleanup_time = now


def create_job_folder() -> tuple:
    """Create isolated job folder and return (job_id, job_path)."""
    job_id = uuid.uuid4().hex[:16]
    job_path = Path('temp/jobs') / job_id
    job_path.mkdir(parents=True, exist_ok=True)
    return job_id, job_path


def create_temp_process(base_process_path: str, overrides: dict, job_path: Path) -> tuple:
    """Create temporary process JSON with overrides.
    Returns (temp_path, applied_overrides, error_info or None)
    """
    if not os.path.exists(base_process_path):
        raise FileNotFoundError(f"Base process not found: {base_process_path}")

    with open(base_process_path, 'r') as f:
        data = json.load(f)

    applied = {}
    error_info = None

    # Apply support override - use only enable_support as string
    if 'supports' in overrides:
        supports_requested = bool(overrides.get('supports'))
        # Orca Bambu profiles use "enable_support" with string values "0" or "1"
        data['enable_support'] = "1" if supports_requested else "0"
        applied['supports'] = supports_requested
        applied['enable_support'] = data['enable_support']

    # Apply layer_height override - strip mm suffix, output as string
    if 'layer_height' in overrides:
        lh = overrides.get('layer_height')
        if isinstance(lh, str):
            lh = lh.replace('mm', '').strip()
        # Ensure it's a valid numeric string
        data['layer_height'] = str(float(lh))
        applied['layer_height'] = data['layer_height']

    temp_path = job_path / 'process_override.json'
    with open(temp_path, 'w') as f:
        json.dump(data, f, indent=2)

    logger.info(f"Created temporary process file: {temp_path} with overrides: {applied}")
    return str(temp_path), applied, error_info


def parse_orca_warnings(stdout: str, stderr: str) -> dict:
    """Parse Orca output and categorize warnings.
    Returns dict with warning categories.
    """
    output = (stdout or '') + '\n' + (stderr or '')
    warnings = {
        'acceleration_capped': False,
        'floating_regions': False,
        'thumbnail_opengl': False,
        'no_filament_colors': False,
        'invalid_json_type': False,
        'invalid_json_type_keys': [],
        'messages': []
    }
    
    for line in output.splitlines():
        line_lower = line.lower()
        
        if 'acceleration capped' in line_lower:
            warnings['acceleration_capped'] = True
            warnings['messages'].append('Acceleration capped warning detected')
        
        if 'floating' in line_lower and ('region' in line_lower or 'cantilever' in line_lower):
            warnings['floating_regions'] = True
            warnings['messages'].append('Floating regions detected - consider enabling supports')
        
        if any(x in line_lower for x in ['wayland', 'glew', 'opengl', 'xdg_runtime_dir']):
            warnings['thumbnail_opengl'] = True
            # Don't add to messages - these are non-fatal noise
        
        if 'no filament colors' in line_lower:
            warnings['no_filament_colors'] = True
            warnings['messages'].append('No filament colors specified')
        
        if 'invalid json type' in line_lower:
            warnings['invalid_json_type'] = True
            m = re.search(r'invalid json type for ([a-zA-Z0-9_]+)', line, re.IGNORECASE)
            if m:
                key = m.group(1)
                if key not in warnings['invalid_json_type_keys']:
                    warnings['invalid_json_type_keys'].append(key)
            warnings['messages'].append(f'Invalid JSON type error: {line.strip()}')
    
    return warnings


def run_orcaslicer(input_file_path: str, process: str, filament: str, 
                   settings: dict, job_path: Path, job_id: str) -> dict:
    """Run OrcaSlicer with job isolation.
    Returns dict with success, metadata, warnings, etc.
    """
    settings = settings or {}
    
    ORCA_BIN = os.getenv('ORCASLICER_BIN', 'orcaslicer')
    orca_path = shutil.which(ORCA_BIN)
    if not orca_path:
        logger.error(f"OrcaSlicer binary not found: {ORCA_BIN}")
        return {"success": False, "error": {"code": "orca_missing", "message": f"OrcaSlicer binary not found: {ORCA_BIN}"}}

    printer_config = 'files/printers/Bambu Lab P1S 0.4 nozzle.json'
    process_config = f'files/process/{process}'
    filament_config = f'files/filament/{filament}'

    logger.info(f"Resolved process_config: {process_config}")
    logger.info(f"Resolved filament_config: {filament_config}")

    if not os.path.exists(process_config):
        logger.error(f"Process config does not exist: {process_config}")
        return {"success": False, "error": {"code": "missing_process", "message": f"Process config does not exist: {process_config}"}}
    if not os.path.exists(filament_config):
        logger.error(f"Filament config does not exist: {filament_config}")
        return {"success": False, "error": {"code": "missing_filament", "message": f"Filament config does not exist: {filament_config}"}}

    # Create temp process with overrides
    process_to_use = process_config
    temp_process_path = None
    applied_overrides = {}
    
    if settings:
        try:
            temp_process_path, applied_overrides, _ = create_temp_process(process_config, settings, job_path)
            process_to_use = temp_process_path
        except Exception as e:
            logger.exception("Failed to create temp process file")
            return {"success": False, "error": {"code": "process_override_failed", "message": str(e)}}

    load_settings_arg = f"{printer_config};{process_to_use}"
    load_filaments_arg = filament_config

    arrange = "1"
    orient = "1"
    timeout = int(os.getenv('ORCA_TIMEOUT', '300'))
    
    output_3mf_path = job_path / 'output.3mf'
    result_json_path = job_path / 'result.json'

    # Run Orca with job directory as cwd for result.json isolation
    cmd = [
        orca_path,
        "--arrange", arrange,
        "--orient", orient,
        "--export-slicedata", str(job_path),
        "--load-settings", load_settings_arg,
        "--load-filaments", load_filaments_arg,
        "--slice", "0",
        "--debug", "2",
        "--export-3mf", str(output_3mf_path),
        "--info",
        input_file_path
    ]
    logger.info("Running command: %s", ' '.join(cmd))

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                               check=True, text=True, cwd=str(job_path))
        stdout = result.stdout
        stderr = result.stderr
        return_code = 0
    except subprocess.CalledProcessError as e:
        stdout = e.stdout
        stderr = e.stderr
        return_code = e.returncode
    except subprocess.TimeoutExpired:
        logger.error("OrcaSlicer timed out")
        return {"success": False, "error": {"code": "timeout", "message": "OrcaSlicer timed out"}}

    logger.info("OrcaSlicer STDOUT:\n%s", stdout)
    logger.info("OrcaSlicer STDERR:\n%s", stderr)

    # Parse warnings from output
    warnings = parse_orca_warnings(stdout, stderr)

    # Load result.json if exists
    orca_result = None
    if result_json_path.exists():
        try:
            with open(result_json_path, 'r') as f:
                orca_result = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse result.json: {e}")

    # Check if Orca reported success
    if orca_result and orca_result.get('return_code', 0) != 0:
        return {
            "success": False, 
            "error": {"code": "orca_error", "message": orca_result.get('error_string', 'Unknown error')},
            "orca_result": orca_result,
            "stdout": stdout,
            "stderr": stderr,
            "warnings": warnings
        }

    # Check if output.3mf exists
    if not output_3mf_path.exists():
        return {
            "success": False,
            "error": {"code": "missing_output", "message": "Output 3MF file not created"},
            "stdout": stdout,
            "stderr": stderr,
            "warnings": warnings
        }

    return {
        "success": True,
        "stdout": stdout,
        "stderr": stderr,
        "warnings": warnings,
        "orca_result": orca_result,
        "process_used": process_to_use,
        "applied_overrides": applied_overrides,
        "output_3mf_path": str(output_3mf_path),
        "return_code": return_code
    }


def get_data_from_orcaslicer_output(output_3mf_path: str) -> dict:
    """Extract metadata from Orca output 3MF."""
    if not os.path.exists(output_3mf_path):
        raise FileNotFoundError(f"Output 3MF not found: {output_3mf_path}")

    with zipfile.ZipFile(output_3mf_path, 'r') as z:
        try:
            with z.open('Metadata/plate_1.gcode', 'r') as f:
                gcode = f.read().decode('utf-8')
        except KeyError:
            raise FileNotFoundError("Metadata/plate_1.gcode not found inside output.3mf")

    def search_float(pattern, default=None):
        m = re.search(pattern, gcode)
        return float(m.group(1)) if m else default

    def search_str(pattern, default=None):
        m = re.search(pattern, gcode)
        return m.group(1) if m else default

    used_mm = search_float(r"filament used \[mm\] = ([0-9.]+)", 0.0)
    used_cm3 = search_float(r"filament used \[cm3\] = ([0-9.]+)", 0.0)
    max_z = search_float(r"max_z_height:\s+([0-9.]+)", 0.0)
    filament_type = search_str(r"filament_type = (\w+)")
    default_print_profile = search_str(r"default_print_profile = (.+)")

    model_time_raw = search_str(r"model printing time:\s+([0-9hms\s]+);")
    total_time_raw = search_str(r"total estimated time:\s+([0-9hms\s]+)")
    first_layer_raw = search_str(r"first layer printing time.*?=\s+([0-9hms\s]+)")

    model_time = time_to_minutes(model_time_raw) if model_time_raw else None
    total_time = time_to_minutes(total_time_raw) if total_time_raw else None
    first_layer_time = time_to_minutes(first_layer_raw) if first_layer_raw else None

    return {
        "used_mm": used_mm,
        "used_cm3": used_cm3,
        "max_z": max_z,
        "default_print_profile": default_print_profile,
        "filament_type": filament_type,
        "model_time": model_time,
        "total_time": total_time,
        "first_layer_time": first_layer_time
    }


def time_to_minutes(time_str):
    if not time_str:
        return None
    hours = re.search(r"([0-9]+)h", time_str)
    minutes = re.search(r"([0-9]+)m", time_str)
    seconds = re.search(r"([0-9]+)s", time_str)
    total_minutes = 0
    if hours:
        total_minutes += int(hours.group(1)) * 60
    if minutes:
        total_minutes += int(minutes.group(1))
    if seconds:
        total_minutes += int(seconds.group(1)) / 60
    return round(total_minutes, 2)


def check_floating_region_warning(orca_result: dict) -> bool:
    """Check if Orca result contains floating region warning."""
    if not orca_result:
        return False
    
    plates = orca_result.get('sliced_plates', [])
    for plate in plates:
        warning = plate.get('warning_message', '') or ''
        warning_lower = warning.lower()
        if 'floating' in warning_lower or 'enable support' in warning_lower:
            return True
    
    # Also check error_string
    error_str = orca_result.get('error_string', '') or ''
    if 'floating' in error_str.lower() or 'enable support' in error_str.lower():
        return True
    
    return False


@app.route('/', methods=['GET', 'POST'])
def home():
    # Run cleanup of old jobs at most once per hour
    cleanup_old_jobs(max_age_hours=24)
    
    # Check if debug mode is truly enabled
    is_debug_mode = current_app.debug or os.getenv('DEBUG', '').lower() in ['true', '1', 'yes']
    
    if request.method == 'POST':
        logger.info("POST request received")
        
        # Get new API params (preferred)
        quality = request.form.get('quality') or request.args.get('quality')
        material = request.form.get('material') or request.args.get('material')
        
        # Legacy params (for backward compatibility)
        stlFile = request.files.get('stlFile')
        gcp_file_link = request.form.get('gcsLink')
        filament = request.form.get('filament')
        process = request.form.get('process')
        settings = request.form.get('settings')
        settings = json.loads(settings) if settings else {}

        # Check for explicit supports request
        supports_explicitly_requested = False
        requested_supports = None
        
        supports_field = request.form.get('supports')
        if supports_field is not None:
            supports_explicitly_requested = True
            if isinstance(supports_field, str):
                requested_supports = supports_field.lower() in ['true', '1', 'yes']
            else:
                requested_supports = bool(supports_field)
            settings['supports'] = requested_supports

        logger.info(f"received ==> stlFile: {stlFile.filename if stlFile else 'No File'}")
        logger.info(f"received ==> gcsLink: {gcp_file_link}")
        logger.info(f"received ==> quality: {quality}, material: {material}")
        logger.info(f"received ==> process: {process}, filament: {filament}")
        logger.info(f"received ==> settings: {settings}")
        logger.info(f"received ==> supports_explicitly_requested: {supports_explicitly_requested}, requested_supports: {requested_supports}")

        # Resolve profiles
        process_file, filament_file, error = resolve_profile(quality, material, process, filament)
        if error:
            return json.dumps({"success": False, "error": error}, indent=2), 400, {'Content-Type': 'application/json'}

        # Create isolated job folder
        job_id, job_path = create_job_folder()
        logger.info(f"Created job folder: {job_path}")

        # Save input file
        input_file_path = job_path / 'input.stl'
        
        if not stlFile:
            if gcp_file_link:
                # Download from GCS
                try:
                    response = requests.get(gcp_file_link)
                    response.raise_for_status()
                    with open(input_file_path, 'wb') as f:
                        f.write(response.content)
                    logger.info(f"Successfully downloaded file from GCS: {gcp_file_link}")
                except HTTPError as e:
                    status_code = e.response.status_code
                    error_text = e.response.text if e.response.text else "No error details"
                    logger.error(f"GCS download failed: HTTP {status_code}: {error_text}")
                    return json.dumps({"success": False, "error": {"code": "gcs_download_failed", "message": f"HTTP {status_code}: {error_text}"}}, indent=2), 500, {'Content-Type': 'application/json'}
                except Exception as e:
                    logger.error(f"GCS download failed: {e}")
                    return json.dumps({"success": False, "error": {"code": "gcs_download_failed", "message": str(e)}}, indent=2), 500, {'Content-Type': 'application/json'}
            else:
                logger.error("'stlFile' or 'gcsLink' is required")
                return json.dumps({"success": False, "error": {"code": "missing_file", "message": "stlFile or gcsLink is required"}}, indent=2), 400, {'Content-Type': 'application/json'}
        else:
            # Validate and save uploaded file
            if not stlFile.filename.lower().endswith('.stl'):
                logger.error("Uploaded file is not an STL file")
                return json.dumps({"success": False, "error": {"code": "invalid_file", "message": "Uploaded file must be an STL file"}}, indent=2), 400, {'Content-Type': 'application/json'}
            
            stlFile.save(input_file_path)

        # Validate settings
        allowed_keys = {"supports", "layer_height"}
        if not isinstance(settings, dict):
            return json.dumps({"success": False, "error": {"code": "bad_request", "message": "'settings' must be a JSON object"}}, indent=2), 400, {'Content-Type': 'application/json'}
        
        unexpected = set(settings.keys()) - allowed_keys
        if unexpected:
            return json.dumps({"success": False, "error": {"code": "bad_settings", "message": f"Unsupported settings keys: {', '.join(unexpected)}"}}, indent=2), 400, {'Content-Type': 'application/json'}

        # Normalize layer_height
        if 'layer_height' in settings:
            lh = settings.get('layer_height')
            if isinstance(lh, str):
                lh = lh.replace('mm', '').strip()
            try:
                settings['layer_height'] = str(float(lh))
            except (ValueError, TypeError):
                return json.dumps({"success": False, "error": {"code": "invalid_layer_height", "message": "layer_height must be a valid number"}}, indent=2), 400, {'Content-Type': 'application/json'}

        # Run OrcaSlicer
        logger.info(f"Running OrcaSlicer with file: {input_file_path}, process: {process_file}, filament: {filament_file}")
        
        result = run_orcaslicer(str(input_file_path), process_file, filament_file, settings, job_path, job_id)
        
        if not result.get('success'):
            error_response = {"success": False, "error": result.get('error')}
            if is_debug_mode:
                error_response['debug'] = {
                    "job_id": job_id,
                    "stdout": result.get('stdout'),
                    "stderr": result.get('stderr'),
                    "warnings": result.get('warnings')
                }
            return json.dumps(error_response, indent=2), 500, {'Content-Type': 'application/json'}

        # Parse metadata
        try:
            metadata = get_data_from_orcaslicer_output(result['output_3mf_path'])
        except FileNotFoundError as e:
            error_response = {"success": False, "error": {"code": "missing_output", "message": str(e)}}
            if is_debug_mode:
                error_response['debug'] = {"job_id": job_id}
            return json.dumps(error_response, indent=2), 500, {'Content-Type': 'application/json'}

        # Check for floating regions and auto-retry logic
        floating_detected = check_floating_region_warning(result.get('orca_result'))
        auto_retry_attempted = False
        auto_retry_success = False
        warning_still_present = False
        retry_result = None
        
        # Only auto-retry if:
        # 1. Floating regions detected, AND
        # 2. Supports were NOT explicitly disabled by user
        if floating_detected and not (supports_explicitly_requested and not requested_supports):
            # Check if we should auto-retry
            if not supports_explicitly_requested or requested_supports:
                logger.info("Floating regions detected; attempting auto-retry with supports enabled")
                auto_retry_attempted = True
                
                retry_settings = {"supports": True}
                if 'layer_height' in settings:
                    retry_settings['layer_height'] = settings['layer_height']
                
                retry_result = run_orcaslicer(str(input_file_path), process_file, filament_file, 
                                               retry_settings, job_path, job_id)
                
                auto_retry_success = retry_result.get('success')
                
                if auto_retry_success:
                    # Use retry result
                    result = retry_result
                    # Re-parse metadata from retry output
                    try:
                        metadata = get_data_from_orcaslicer_output(result['output_3mf_path'])
                    except FileNotFoundError:
                        pass
                    
                    # Check if warning still present after retry
                    warning_still_present = check_floating_region_warning(result.get('orca_result'))
                
        # Build support_detection response
        support_detection = {
            "supports_explicitly_requested": supports_explicitly_requested,
            "requested_supports": requested_supports,
            "floating_regions_detected": floating_detected,
            "auto_retry_attempted": auto_retry_attempted,
            "auto_retry_success": auto_retry_success,
            "support_override_applied": bool(result.get('applied_overrides', {}).get('supports')),
            "support_key_used": "enable_support",
            "support_value_written": result.get('applied_overrides', {}).get('enable_support'),
            "warning_still_present_after_retry": warning_still_present
        }
        
        if supports_explicitly_requested and not requested_supports and floating_detected:
            support_detection["auto_retry_skipped_reason"] = "supports_explicitly_disabled"

        # Build warnings list
        warnings_list = result.get('warnings', {}).get('messages', [])
        
        # Add config warning if invalid_json_type errors present
        if result.get('warnings', {}).get('invalid_json_type'):
            config_warning = "Configuration warning: Invalid JSON type errors detected"
            invalid_keys = result['warnings'].get('invalid_json_type_keys', [])
            if invalid_keys:
                config_warning += f" for keys: {', '.join(invalid_keys)}"
            warnings_list.append(config_warning)

        # Build response
        response = {
            "success": True,
            "metadata": metadata,
            "warnings": warnings_list,
            "support_detection": support_detection
        }

        # Add debug info only if truly in debug mode
        if is_debug_mode:
            debug_info = {
                "job_id": job_id,
                "job_folder": str(job_path),
                "process_used": result.get('process_used'),
                "applied_overrides": result.get('applied_overrides'),
                "orca_return_code": result.get('return_code'),
            }
            
            # Check if we should include stdout/stderr
            debug_param = request.form.get('debug') or request.args.get('debug')
            if debug_param and debug_param.lower() in ['true', '1', 'yes']:
                debug_info['stdout'] = result.get('stdout')
                debug_info['stderr'] = result.get('stderr')
                if retry_result:
                    debug_info['retry_stdout'] = retry_result.get('stdout')
                    debug_info['retry_stderr'] = retry_result.get('stderr')
            
            # Only include base64 3mf if under size limit (5MB)
            try:
                output_path = result.get('output_3mf_path')
                if output_path and os.path.exists(output_path):
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    if size_mb < 5:
                        with open(output_path, 'rb') as f:
                            debug_info['output_3mf_base64'] = base64.b64encode(f.read()).decode('ascii')
                    else:
                        debug_info['output_3mf_note'] = f"File exists ({size_mb:.1f} MB) but exceeds 5MB limit"
            except Exception as e:
                debug_info['output_3mf_error'] = str(e)
            
            response['debug'] = debug_info

        # Handle include_3mf - only in debug mode
        include_3mf_flag = False
        include_3mf_field = request.form.get('include_3mf') or request.args.get('include_3mf')
        if include_3mf_field is not None:
            if isinstance(include_3mf_field, str):
                include_3mf_flag = include_3mf_field.lower() in ['true', '1', 'yes']
            else:
                include_3mf_flag = bool(include_3mf_field)

        if include_3mf_flag and is_debug_mode:
            output_path = result.get('output_3mf_path')
            if output_path and os.path.exists(output_path):
                return send_file(output_path, as_attachment=True, download_name='output.3mf', mimetype='application/octet-stream')
            else:
                response['error_3mf'] = "Output 3MF not found"

        return json.dumps(response, indent=2), 200, {'Content-Type': 'application/json'}

    # GET request - render template
    logger.info("GET request received")

    if not os.path.exists('files/filament'):
        return json.dumps({"error": "files/filament directory does not exist"}), 500, {'Content-Type': 'application/json'}
    filament_list = os.listdir('files/filament')

    if not os.path.exists('files/process'):
        return json.dumps({"error": "files/process directory does not exist"}), 500, {'Content-Type': 'application/json'}
    process_list = os.listdir('files/process')

    return render_template('index.html', filements=filament_list, processes=process_list, 
                          current_version=CURRENT_VERSION, quality_profiles=QUALITY_PROFILES)


@app.route('/3mf', methods=['GET'])
def get_3mf():
    """Extract Metadata directory from job's 3mf file and send as zip."""
    job_id = request.args.get('job_id')
    if not job_id:
        return json.dumps({"success": False, "error": {"code": "missing_job_id", "message": "job_id parameter required"}}, indent=2), 400, {'Content-Type': 'application/json'}
    
    # Validate job_id to prevent path traversal
    if not re.match(r'^[a-f0-9]{16}$', job_id):
        return json.dumps({"success": False, "error": {"code": "invalid_job_id", "message": "Invalid job_id format"}}, indent=2), 400, {'Content-Type': 'application/json'}
    
    job_path = Path('temp/jobs') / job_id
    output_3mf = job_path / 'output.3mf'
    
    if not output_3mf.exists():
        return json.dumps({"success": False, "error": {"code": "not_found", "message": "Job or output 3MF not found"}}, indent=2), 404, {'Content-Type': 'application/json'}

    import io
    metadata_zip_bytes = io.BytesIO()
    with zipfile.ZipFile(output_3mf, 'r') as z_in:
        with zipfile.ZipFile(metadata_zip_bytes, 'w') as z_out:
            for file_info in z_in.infolist():
                if file_info.filename.startswith('Metadata/'):
                    z_out.writestr(file_info, z_in.read(file_info.filename))
    metadata_zip_bytes.seek(0)
    return send_file(metadata_zip_bytes, as_attachment=True, download_name='metadata.zip', mimetype='application/zip')


@app.route('/debug', methods=['GET'])
def debug():
    """Debug endpoint - requires debug mode."""
    if not current_app.debug:
        return json.dumps({"error": "Debug mode required"}), 403, {'Content-Type': 'application/json'}
    
    # List job folders
    jobs_dir = Path('temp/jobs')
    jobs = []
    if jobs_dir.exists():
        for job_dir in jobs_dir.iterdir():
            if job_dir.is_dir():
                try:
                    stat = job_dir.stat()
                    jobs.append({
                        "job_id": job_dir.name,
                        "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "size_mb": sum(f.stat().st_size for f in job_dir.rglob('*') if f.is_file()) / (1024 * 1024)
                    })
                except Exception:
                    pass
    
    return json.dumps({
        "version": CURRENT_VERSION,
        "debug_mode": current_app.debug,
        "jobs": jobs,
        "quality_profiles": QUALITY_PROFILES
    }, indent=2), 200, {'Content-Type': 'application/json'}
