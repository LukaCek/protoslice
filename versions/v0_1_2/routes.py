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

# Get version from directory name
CURRENT_VERSION = os.path.basename(os.path.dirname(os.path.abspath(__file__)))

app = Blueprint(CURRENT_VERSION, __name__)

# Module-level logger
logger = logging.getLogger(__name__)
# If the application hasn't configured logging, provide a sensible default
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

def create_temp_process(base_process_path: str, overrides: dict) -> str:
    """Create a temporary full process JSON by merging overrides into the base process JSON.
    Returns the path to the temporary process JSON file.
    """
    if not os.path.exists(base_process_path):
        raise FileNotFoundError(f"Base process not found: {base_process_path}")

    with open(base_process_path, 'r') as f:
        data = json.load(f)

    # Apply known overrides (POC: supports and layer_height)
    applied = {}
    if 'supports' in overrides:
        supports_bool = bool(overrides.get('supports'))
        # Use integers 1/0 because some Orca profile fields expect numeric types
        supports_int = 1 if supports_bool else 0
        # Common keys used by different profile formats
        data['support_enable'] = supports_int
        # also set a nested object in case profile expects it
        data.setdefault('support', {})
        data['support']['enable'] = supports_int
        # some variants may look for generate_support or support_generate
        data['generate_support'] = supports_int
        data['support_generate'] = supports_int
        applied['supports'] = supports_bool

    if 'layer_height' in overrides:
        data['layer_height'] = overrides.get('layer_height')
        applied['layer_height'] = overrides.get('layer_height')

    os.makedirs('temp', exist_ok=True)
    temp_name = f"process_override_{uuid.uuid4().hex}.json"
    temp_path = os.path.join('temp', temp_name)
    with open(temp_path, 'w') as f:
        json.dump(data, f, indent=2)

    logger.info(f"Created temporary process file: {temp_path} with overrides: {applied}")
    # Return both path and applied overrides for visibility
    return temp_path, applied


def run_orcaslicer(input_file_path, process, filament, settings=None):
    """Run OrcaSlicer with optional process overrides in `settings`.
    Returns a dict with keys: success (bool), stdout, stderr, error (dict)
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

    # If settings provided, create a temporary process JSON file with overrides
    process_to_use = process_config
    temp_process_path = None
    applied_overrides = {}
    if settings:
        try:
            temp_process_path, applied_overrides = create_temp_process(process_config, settings)
            process_to_use = temp_process_path
        except Exception as e:
            logger.exception("Failed to create temp process file")
            return {"success": False, "error": {"code": "process_override_failed", "message": str(e)}}

    load_settings_arg = f"{printer_config};{process_to_use}"
    load_filaments_arg = filament_config

    arrange = "1"
    orient = "1"
    retries = 0
    max_retries = 3
    timeout = int(os.getenv('ORCA_TIMEOUT', '300'))

    def parse_invalid_type_keys(output_text: str):
        """Parse Orca output for lines like 'invalid json type for <key>' and return a set of keys."""
        keys = set()
        for line in (output_text or '').splitlines():
            m = re.search(r"invalid json type for ([a-zA-Z0-9_]+)", line)
            if m:
                keys.add(m.group(1))
        return keys

    def try_alternate_types(failing_keys, desired_bool):
        """Try alternate JSON types for failing keys until Orca accepts the file or variants exhausted.
        Returns (success_result_dict or None, applied_overrides_updated, process_used_path)
        """
        # Candidate value representations in JSON: bool True, int 1, string '1', string 'true'
        candidates = [True, 1, "1", "true"]
        for candidate in candidates:
            logger.info("Attempting candidate type %r for keys: %s", candidate, failing_keys)
            # create new temp process with candidate type for failing keys
            overrides_for_try = {k: desired_bool for k in settings.keys()}
            # force write specific types by passing a special param
            try:
                # create a temp process variant using low-level write
                with open(process_config, 'r') as f:
                    data_base = json.load(f)
                for k in failing_keys:
                    # map common key aliases to candidate value
                    if k in ['generate_support', 'support_generate', 'support_enable', 'support']:
                        if isinstance(candidate, bool):
                            data_base['support_enable'] = candidate
                            data_base.setdefault('support', {})
                            data_base['support']['enable'] = candidate
                            data_base['generate_support'] = candidate
                            data_base['support_generate'] = candidate
                        else:
                            data_base['support_enable'] = candidate
                            data_base.setdefault('support', {})
                            data_base['support']['enable'] = candidate
                            data_base['generate_support'] = candidate
                            data_base['support_generate'] = candidate
                os.makedirs('temp', exist_ok=True)
                temp_name = f"process_override_{uuid.uuid4().hex}.json"
                temp_path = os.path.join('temp', temp_name)
                with open(temp_path, 'w') as tf:
                    json.dump(data_base, tf, indent=2)

                # run Orca with this temp file
                cmd_try = [
                    orca_path,
                    "--arrange", arrange,
                    "--orient", orient,
                    "--export-slicedata", "./temp",
                    "--load-settings", f"{printer_config};{temp_path}",
                    "--load-filaments", load_filaments_arg,
                    "--slice", "0",
                    "--debug", "2",
                    "--export-3mf", "./temp/output.3mf",
                    "--info",
                    input_file_path
                ]
                logger.info("Running candidate command: %s", ' '.join(cmd_try))
                try:
                    res = subprocess.run(cmd_try, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True, timeout=timeout)
                    logger.info("Candidate run succeeded with stdout: %s", res.stdout)
                    return ({"success": True, "stdout": res.stdout, "stderr": res.stderr}, {'supports': bool(desired_bool)}, temp_path)
                except subprocess.CalledProcessError as e:
                    # if Orca complains about invalid json types, continue to next candidate
                    logger.warning("Candidate run failed: %s", e.stderr)
                    continue
                except subprocess.TimeoutExpired:
                    logger.error("Candidate run timed out")
                    continue
            except Exception as e:
                logger.exception("Error while trying candidate types: %s", e)
                continue
        return (None, {}, None)

    last_stdout = None
    last_stderr = None
    while retries < max_retries:
        cmd = [
            orca_path,
            "--arrange", arrange,
            "--orient", orient,
            "--export-slicedata", "./temp",
            "--load-settings", load_settings_arg,
            "--load-filaments", load_filaments_arg,
            "--slice", "0",
            "--debug", "2",
            "--export-3mf", "./temp/output.3mf",
            "--info",
            input_file_path
        ]
        logger.info("Running command: %s", ' '.join(cmd))

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True, timeout=timeout)
            logger.info("OrcaSlicer STDOUT:\n%s", result.stdout)
            logger.info("OrcaSlicer STDERR:\n%s", result.stderr)
            return {"success": True, "stdout": result.stdout, "stderr": result.stderr, "process_used": process_to_use, "applied_overrides": applied_overrides}
        except subprocess.CalledProcessError as e:
            logger.error("OrcaSlicer failed with return code %s", e.returncode)
            logger.error("STDOUT:\n%s", e.stdout)
            logger.error("STDERR:\n%s", e.stderr)
            last_stdout = e.stdout
            last_stderr = e.stderr
            # Inspect stdout for invalid json type errors and try alternate encodings
            invalid_keys = parse_invalid_type_keys(e.stdout + '\n' + e.stderr)
            if invalid_keys and settings.get('supports'):
                # try alternate encodings for failing keys
                logger.info("Detected invalid json type keys: %s. Trying alternate encodings.", invalid_keys)
                alt_res, alt_applied, alt_path = try_alternate_types(invalid_keys, settings.get('supports'))
                if alt_res:
                    # succeeded with an alternate encoding
                    return {"success": True, "stdout": alt_res.get('stdout'), "stderr": alt_res.get('stderr'), "process_used": alt_path, "applied_overrides": alt_applied}

            retries += 1
            # Try different arrange/orient combinations
            if arrange == "1" and orient == "1":
                arrange = "0"
                orient = "1"
            elif arrange == "0" and orient == "1":
                arrange = "1"
                orient = "0"
            elif arrange == "1" and orient == "0":
                arrange = "0"
                orient = "0"
        except subprocess.TimeoutExpired as e:
            logger.error("OrcaSlicer timed out: %s", str(e))
            return {"success": False, "error": {"code": "timeout", "message": "OrcaSlicer timed out"}}

    # Return the last captured stdout/stderr to help debug
    return {"success": False, "error": {"code": "failed_after_retries", "message": "OrcaSlicer failed after retries", "stdout": last_stdout, "stderr": last_stderr}}
def get_data_from_orcaslicer_output() -> dict:

    # Check if output.3mf exists
    if not os.path.exists('temp/output.3mf'):
        raise FileNotFoundError("'temp/output.3mf' does not exist")

    with zipfile.ZipFile('temp/output.3mf', 'r') as z:
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

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        logger.info("POST request received")
        stlFile = request.files.get('stlFile')
        gcp_file_link = request.form.get('gcsLink')
        filament = request.form.get('filament')
        process = request.form.get('process')
        settings = request.form.get('settings')
        settings = json.loads(settings) if settings else {}

        # Convenience: accept top-level form field 'supports' (e.g., supports=true)
        supports_field = request.form.get('supports')
        if supports_field is not None:
            if isinstance(supports_field, str):
                settings['supports'] = supports_field.lower() in ['true', '1', 'yes']
            else:
                settings['supports'] = bool(supports_field)

        logger.info(f"recived ==> stlFile: {stlFile.filename if stlFile else 'No File'}")
        logger.info(f"recived ==> gcsLink: {gcp_file_link}")
        logger.info(f"recived ==> filament: {filament}")
        logger.info(f"recived ==> process: {process}")
        logger.info(f"recived ==> settings: {settings}")
        
        # set to default if not provided
        if not filament:
            filament = 'Bambu PETG Basic @BBL X1C.json'
        if not process:
            process = '0.08mm Extra Fine @BBL X1C.json'

        input_file_path = 'temp/file.stl'
        # Ensure temp directory exists
        os.makedirs('temp', exist_ok=True)

        # Validate file presence
        if not stlFile:
            if gcp_file_link:
                # Download file from GCS. Using google_cloud_link to input_file_path
                try:
                    response = requests.get(gcp_file_link)
                    response.raise_for_status()  # Raise an exception for HTTP errors
                    with open(input_file_path, 'wb') as f:
                        f.write(response.content)
                    logger.info(f"Successfully downloaded file from GCS: {gcp_file_link}")
                except HTTPError as e:
                    status_code = e.response.status_code
                    error_text = e.response.text if e.response.text else "No error details provided"
                    logger.error(f"GCS download failed with HTTP {status_code}: {error_text}")
                    return f"Error downloading file from GCS: HTTP {status_code} - {error_text}", 500
                except ConnectionError as e:
                    logger.error(f"GCS download failed due to connection error: {e}")
                    return f"Error downloading file from GCS: Connection failed - {str(e)}", 500
                except Timeout as e:
                    logger.error(f"GCS download timed out: {e}")
                    return f"Error downloading file from GCS: Request timed out", 500
                except RequestException as e:
                    logger.error(f"GCS download failed with request error: {e}")
                    return f"Error downloading file from GCS: Request error - {str(e)}", 500
                except Exception as e:
                    logger.error(f"Unexpected error downloading from GCS: {e}")
                    return f"Error downloading file from GCS: Unexpected error - {str(e)}", 500
                    
            else:
                logger.error("'stlFile' or 'gcsLink' is required")
                return "'stlFile' or 'gcsLink' is required", 400
        else:
            # Validate file extension
            if not stlFile.filename.lower().endswith('.stl'):
                logger.error("Uploaded file is not an STL file")
                return "Uploaded file must be an STL file", 400
        
            # Save the uploaded file to a temporary location
            stlFile.save(input_file_path)

        logger.info(f"Running OrcaSlicer with file: {input_file_path}, filament: {filament}, process: {process}")

        # Validate settings: only accept supports and layer_height for now
        allowed_keys = {"supports", "layer_height"}
        if not isinstance(settings, dict):
            return json.dumps({"success": False, "error": {"code": "bad_request", "message": "'settings' must be a JSON object"}}, indent=2), 400, {'Content-Type': 'application/json'}
        unexpected = set(settings.keys()) - allowed_keys
        if unexpected:
            return json.dumps({"success": False, "error": {"code": "bad_settings", "message": f"Unsupported settings keys: {', '.join(unexpected)}"}}, indent=2), 400, {'Content-Type': 'application/json'}

        # Normalize supports to boolean if present
        if 'supports' in settings:
            val = settings.get('supports')
            if isinstance(val, str):
                settings['supports'] = val.lower() in ['true', '1', 'yes']
            else:
                settings['supports'] = bool(val)

        # Basic validation for layer_height format like '0.20mm' or '0.2'
        if 'layer_height' in settings:
            lh = settings.get('layer_height')
            if isinstance(lh, (int, float)):
                settings['layer_height'] = f"{lh}mm"
            elif isinstance(lh, str) and not lh.endswith('mm'):
                # accept '0.2' -> '0.2mm'
                if re.match(r'^[0-9]*\.?[0-9]+$', lh):
                    settings['layer_height'] = lh + 'mm'
        
        result = run_orcaslicer(input_file_path, process, filament, settings)
        if not result.get('success'):
            return json.dumps({"success": False, "error": result.get('error')} , indent=2), 500, {'Content-Type': 'application/json'}

        # If Orca reported success, check result.json produced by Orca if present
        if os.path.exists('result.json'):
            with open('result.json', 'r') as f:
                try:
                    orca_result = json.load(f)
                except Exception:
                    orca_result = None
        else:
            orca_result = None

        # If Orca wrote a result.json and it contains a non-success, return error
        if orca_result and orca_result.get('error_string') != 'Success.':
            return json.dumps({"success": False, "error": orca_result}, indent=2), 500, {'Content-Type': 'application/json'}

        # If Orca contains a non-critical warning suggesting enabling supports, auto-retry once with supports enabled
        auto_enabled = False
        retry_info = None
        if orca_result:
            plates = orca_result.get('sliced_plates', [])
            for p in plates:
                warning = p.get('warning_message', '') or ''
                if warning and ('enable support' in warning.lower() or 'enable support generation' in warning.lower() or 'enable support generation' in (warning.lower())):
                    logger.info("Detected support suggestion in Orca output; attempting automatic retry with supports enabled")
                    retry_result = run_orcaslicer(input_file_path, process, filament, settings={"supports": True})
                    retry_info = {
                        'attempted': True,
                        'retry_success': bool(retry_result.get('success')),
                        'retry_stdout': retry_result.get('stdout'),
                        'retry_stderr': retry_result.get('stderr'),
                        'retry_applied_overrides': retry_result.get('applied_overrides'),
                        'retry_process_used': retry_result.get('process_used')
                    }
                    if retry_result.get('success'):
                        auto_enabled = True
                        result = retry_result
                        # reload orca_result from file if present
                        if os.path.exists('result.json'):
                            try:
                                orca_result = json.load(open('result.json'))
                            except Exception:
                                orca_result = orca_result
                    break
        else:
            retry_info = {'attempted': False}

        # Parse metadata out of produced 3mf
        try:
            metadata = get_data_from_orcaslicer_output()
        except FileNotFoundError as e:
            return json.dumps({"success": False, "error": {"code": "missing_output", "message": str(e)}}, indent=2), 500, {'Content-Type': 'application/json'}

        response = {
            "success": True,
            "metadata": metadata,
            "orca": {"stdout": result.get('stdout'), "stderr": result.get('stderr')},
            "result_json": orca_result,
            "process_used": result.get('process_used'),
            "applied_overrides": result.get('applied_overrides'),
            "auto_enabled_supports": auto_enabled,
            "auto_retry_info": retry_info
        }

        # Optionally include the .3mf file itself (as an attachment) when requested by client
        include_3mf_flag = False
        include_3mf_field = request.form.get('include_3mf') or request.args.get('include_3mf')
        if include_3mf_field is not None:
            if isinstance(include_3mf_field, str):
                include_3mf_flag = include_3mf_field.lower() in ['true', '1', 'yes']
            else:
                include_3mf_flag = bool(include_3mf_field)

        # Backwards compatible: if app is in debug mode, prefer returning inline JSON + base64; but if client explicitly
        # requested `include_3mf`, return the file directly as an attachment (without base64 JSON).
        if include_3mf_flag:
            # Return the produced 3mf file directly
            if not os.path.exists('temp/output.3mf'):
                return json.dumps({"success": False, "error": {"code": "missing_output", "message": "'temp/output.3mf' does not exist"}}, indent=2), 500, {'Content-Type': 'application/json'}
            # Use send_file to return the actual file
            return send_file('temp/output.3mf', as_attachment=True, download_name='output.3mf', mimetype='application/octet-stream')

        try:
            if current_app and getattr(current_app, 'debug', False):
                try:
                    with open('temp/output.3mf', 'rb') as _f:
                        response['output_3mf_base64'] = base64.b64encode(_f.read()).decode('ascii')
                except Exception as e:
                    logger.error("Unable to include output.3mf in response: %s", e)
        except RuntimeError:
            # No application context available; skip including the 3mf
            pass

        return json.dumps(response, indent=2), 200, {'Content-Type': 'application/json'}
    
    logger.info("GET request received (non-POST)")

    # get list of files in files/filament
    if not os.path.exists('files/filament'):
        return "'files/filament' directory does not exist", 500
    filament = os.listdir('files/filament')

    if not os.path.exists('files/process'):
        return "'files/process' directory does not exist", 500
    process = os.listdir('files/process')

    return render_template('index.html', filements=filament, processes=process, current_version=CURRENT_VERSION)

@app.route('/3mf', methods=['GET'])
def get_3mf():
    # Extract only the Metadata directory from the 3mf file and send as a zip
    import io
    metadata_zip_bytes = io.BytesIO()
    with zipfile.ZipFile('temp/output.3mf', 'r') as z_in:
        with zipfile.ZipFile(metadata_zip_bytes, 'w') as z_out:
            for file_info in z_in.infolist():
                if file_info.filename.startswith('Metadata/'):
                    z_out.writestr(file_info, z_in.read(file_info.filename))
    metadata_zip_bytes.seek(0)
    return send_file(metadata_zip_bytes, as_attachment=True, download_name='metadata.zip', mimetype='application/zip')


@app.route('/debug', methods=['GET'])
def debug():
    return get_data_from_orcaslicer_output()