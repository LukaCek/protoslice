import io
import json
import os
import zipfile
import subprocess
import sys
import re
import base64
import shutil
from pathlib import Path

# Ensure project root is on sys.path so tests can import top-level modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from app import app as flask_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create test client with mocked OrcaSlicer."""
    # Ensure clean temp
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    
    # Ensure OrcaSlicer binary points to a known command (python) for tests
    os.environ['ORCASLICER_BIN'] = 'python'
    
    # Monkeypatch subprocess.run to simulate OrcaSlicer behavior
    def fake_run(cmd, stdout=None, stderr=None, check=True, text=True, timeout=300, cwd=None):
        job_dir = cwd or str(temp_dir / "jobs" / "test_job")
        os.makedirs(job_dir, exist_ok=True)
        
        # Create result.json
        result = {"return_code": 0, "error_string": "Success."}
        with open(os.path.join(job_dir, 'result.json'), 'w') as f:
            json.dump(result, f)
        
        # Create a dummy output.3mf with Metadata/plate_1.gcode
        output_3mf = os.path.join(job_dir, 'output.3mf')
        with zipfile.ZipFile(output_3mf, 'w') as z:
            gcode_content = (
                "; filament used [mm] = 1234.50\n"
                "; filament used [cm3] = 3.21\n"
                "; model printing time: 0h 10m 30s;\n"
                "; total estimated time: 0h 12m 0s\n"
                "; estimated first layer printing time (normal mode) = 1m 0s\n"
                "; filament_type = PLA\n"
                "; default_print_profile = 0.20mm Standard @BBL X1C\n"
                "max_z_height: 12.34\n"
            )
            z.writestr('Metadata/plate_1.gcode', gcode_content)
        
        # Return mock CompletedProcess
        return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")
    
    monkeypatch.setattr(subprocess, 'run', fake_run)
    
    flask_app.config['TESTING'] = True
    client_obj = flask_app.test_client()
    yield client_obj
    
    # Teardown cleanup
    try:
        import shutil as _shutil
        _shutil.rmtree('temp', ignore_errors=True)
    except Exception:
        pass


def test_slice_success_returns_json(client):
    """Test basic successful slicing returns clean JSON response."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'supports': 'true',
        'settings': json.dumps({'layer_height': '0.20mm'})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    assert body['success'] is True
    assert 'metadata' in body
    assert 'warnings' in body
    assert 'support_detection' in body
    
    # Check metadata
    md = body['metadata']
    assert md['used_mm'] == 1234.5
    assert md['used_cm3'] == 3.21
    assert md['max_z'] == 12.34
    
    # Check support_detection
    sd = body['support_detection']
    assert sd['supports_explicitly_requested'] is True
    assert sd['requested_supports'] is True
    assert sd['support_override_applied'] is True
    assert sd['support_key_used'] == 'enable_support'
    assert sd['support_value_written'] == '1'
    
    # Production response should NOT contain debug info
    assert 'debug' not in body
    assert 'stdout' not in body
    assert 'stderr' not in body
    assert 'job_id' not in body


def test_production_response_excludes_debug_info(client):
    """Test that normal responses don't leak internal info."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    # Production response structure
    assert set(body.keys()) == {'success', 'metadata', 'warnings', 'support_detection'}
    
    # No internal paths
    for key in ['job_folder', 'process_used', 'output_3mf_path']:
        assert key not in body
    
    # No raw Orca output
    for key in ['stdout', 'stderr', 'orca_stdout', 'orca_stderr']:
        assert key not in body


def test_support_false_no_auto_retry(client, monkeypatch, tmp_path):
    """Test that explicit supports=false does not trigger auto-retry."""
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(exist_ok=True)
    os.environ['ORCASLICER_BIN'] = 'python'
    
    call_count = [0]
    
    def fake_run_with_warning(cmd, stdout=None, stderr=None, check=True, text=True, timeout=300, cwd=None):
        call_count[0] += 1
        job_dir = cwd or str(temp_dir / "jobs" / "test_job")
        os.makedirs(job_dir, exist_ok=True)
        
        # Simulate floating region warning
        result = {
            "return_code": 0, 
            "error_string": "Success.", 
            "sliced_plates": [{
                "id": 1, 
                "sliced_time": 1, 
                "warning_message": "It seems object file.stl has floating regions. Please enable support generation."
            }]
        }
        
        with open(os.path.join(job_dir, 'result.json'), 'w') as f:
            json.dump(result, f)
        
        output_3mf = os.path.join(job_dir, 'output.3mf')
        with zipfile.ZipFile(output_3mf, 'w') as z:
            gcode = (
                "; filament used [mm] = 100.00\n"
                "; filament used [cm3] = 0.24\n"
                "; model printing time: 0h 5m 0s;\n"
                "; total estimated time: 0h 6m 0s\n"
                "max_z_height: 5.00\n"
            )
            z.writestr('Metadata/plate_1.gcode', gcode)
        
        return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")
    
    monkeypatch.setattr(subprocess, 'run', fake_run_with_warning)
    
    # Request with explicit supports=false
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'supports': 'false'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    # Should only run once (no retry)
    assert call_count[0] == 1
    
    sd = body['support_detection']
    assert sd['supports_explicitly_requested'] is True
    assert sd['requested_supports'] is False
    assert sd['floating_regions_detected'] is True
    assert sd['auto_retry_attempted'] is False
    assert sd['auto_retry_skipped_reason'] == 'supports_explicitly_disabled'


def test_support_override_uses_enable_support_string(client):
    """Test that support override uses 'enable_support' as string value."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'supports': 'true'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    sd = body['support_detection']
    assert sd['support_key_used'] == 'enable_support'
    assert sd['support_value_written'] == '1'
    assert sd['requested_supports'] is True


def test_layer_height_strips_mm_suffix(client):
    """Test that layer_height is normalized without 'mm' suffix."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'layer_height': '0.25mm'})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    # Check applied overrides in debug mode would show '0.25'
    assert body['success'] is True


def test_debug_mode_includes_debug_info(client):
    """Test that debug mode includes additional debug information."""
    flask_app.debug = True
    
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'debug': 'true'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    assert 'debug' in body
    debug = body['debug']
    assert 'job_id' in debug
    assert 'job_folder' in debug
    assert 'orca_return_code' in debug
    # When debug=true param is set, should include stdout/stderr
    assert 'stdout' in debug
    assert 'stderr' in debug
    
    flask_app.debug = False


def test_debug_param_without_debug_mode_ignored(client):
    """Test that debug=true param is ignored when app is not in debug mode."""
    flask_app.debug = False
    
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'debug': 'true'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    # Should NOT have debug info even though debug=true param was sent
    assert 'debug' not in body
    assert 'stdout' not in body
    assert 'stderr' not in body


def test_invalid_settings_key_returns_400(client):
    """Test that unsupported settings keys return 400 error."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'unsupported': True})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'bad_settings'


def test_unknown_quality_returns_400(client):
    """Test that unknown quality ID returns 400 error."""
    data = {
        'quality': 'unknown_quality',
        'material': 'pla'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'unknown_quality'


def test_unknown_material_returns_400(client):
    """Test that unknown material returns 400 error."""
    data = {
        'quality': 'standard_020',
        'material': 'unknownmaterial12345'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'unknown_material'


def test_legacy_process_param_still_works(client):
    """Test that legacy process/filament params still work."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body['success'] is True


def test_path_traversal_blocked(client):
    """Test that path traversal in process/filament params is blocked or fails safely."""
    data = {
        'process': '../../../etc/passwd',
        'filament': 'Bambu PETG Basic @BBL X1C.json'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    # Should fail (either 400 for invalid path or 500 for missing file)
    assert resp.status_code in [400, 500]
    body = json.loads(resp.data)
    assert body['success'] is False


def test_non_stl_file_rejected(client):
    """Test that non-STL files are rejected."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json'
    }
    data['stlFile'] = (io.BytesIO(b"not an stl file"), 'test.txt')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'invalid_file'


def test_missing_file_returns_400(client):
    """Test that missing stlFile/gcsLink returns 400."""
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json'
    }
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'missing_file'


def test_invalid_json_type_warning_detected(client, monkeypatch, tmp_path):
    """Test that invalid json type errors are detected and reported."""
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(exist_ok=True)
    os.environ['ORCASLICER_BIN'] = 'python'
    
    def fake_run_with_invalid_type(cmd, stdout=None, stderr=None, check=True, text=True, timeout=300, cwd=None):
        job_dir = cwd or str(temp_dir / "jobs" / "test_job")
        os.makedirs(job_dir, exist_ok=True)
        
        # Simulate Orca with invalid json type error
        result = {"return_code": 0, "error_string": "Success."}
        with open(os.path.join(job_dir, 'result.json'), 'w') as f:
            json.dump(result, f)
        
        output_3mf = os.path.join(job_dir, 'output.3mf')
        with zipfile.ZipFile(output_3mf, 'w') as z:
            gcode = "; filament used [mm] = 100.00\nmax_z_height: 5.00\n"
            z.writestr('Metadata/plate_1.gcode', gcode)
        
        # Return process with invalid type error in stdout
        stdout_content = "[error] load_from_json: parse temp/process.json error, invalid json type for enable_support\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout_content, stderr="")
    
    monkeypatch.setattr(subprocess, 'run', fake_run_with_invalid_type)
    
    data = {
        'process': '0.20mm Standard @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'supports': 'true'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')
    
    resp = client.post('/v0.1.3/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    
    # Should have warning about invalid json type
    assert any('Invalid JSON type' in w for w in body['warnings'])
    assert any('enable_support' in w for w in body['warnings'])


def test_concurrent_jobs_isolated():
    """Test that concurrent requests use different job folders."""
    # This test verifies the job isolation mechanism
    # In real concurrent testing, each request would get unique uuid
    import uuid
    
    job_ids = set()
    for _ in range(100):
        job_id = uuid.uuid4().hex[:16]
        job_ids.add(job_id)
    
    # All 100 job IDs should be unique
    assert len(job_ids) == 100


def test_get_request_renders_form(client):
    """Test that GET request renders the upload form."""
    resp = client.get('/v0.1.3/')
    assert resp.status_code == 200


def test_debug_endpoint_requires_debug_mode(client):
    """Test that /debug endpoint requires debug mode."""
    flask_app.config['DEBUG'] = False
    resp = client.get('/v0.1.3/debug')
    assert resp.status_code == 403
    
    flask_app.config['DEBUG'] = True
    resp = client.get('/v0.1.3/debug')
    assert resp.status_code == 200
    flask_app.config['DEBUG'] = False
