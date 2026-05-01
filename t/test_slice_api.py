import io
import json
import os
import zipfile
import subprocess
import sys
import re
import base64
import shutil

# Ensure project root is on sys.path so tests can import top-level modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from app import app as flask_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Ensure clean temp
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    # Ensure OrcaSlicer binary points to a known command (python) for tests
    os.environ['ORCASLICER_BIN'] = 'python'
    # Monkeypatch subprocess.run to simulate OrcaSlicer behavior
    def fake_run(cmd, stdout, stderr, check, text, timeout):
        # create result.json
        result = {"return_code": 0, "error_string": "Success."}
        with open('result.json', 'w') as f:
            json.dump(result, f)
        # create a dummy output.3mf with Metadata/plate_1.gcode
        os.makedirs('temp', exist_ok=True)
        with zipfile.ZipFile('temp/output.3mf', 'w') as z:
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
        return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")

    monkeypatch.setattr(subprocess, 'run', fake_run)

    flask_app.config['TESTING'] = True
    client_obj = flask_app.test_client()
    yield client_obj

    # Teardown: remove temporary files produced by tests to keep workspace clean
    try:
        import shutil as _shutil
        _shutil.rmtree('temp')
    except Exception:
        pass
    try:
        os.remove('result.json')
    except Exception:
        pass


def test_slice_success_returns_json(client):
    data = {
        'process': '0.08mm Extra Fine @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'supports': True, 'layer_height': '0.20mm'})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')

    resp = client.post('/v0.1.2/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body['success'] is True
    assert 'metadata' in body
    md = body['metadata']
    assert md['used_mm'] == 1234.5
    assert md['used_cm3'] == 3.21
    assert md['max_z'] == 12.34
    # confirm overrides were applied and process override file was created
    assert body.get('applied_overrides') == {'supports': True, 'layer_height': '0.20mm'}
    process_used = body.get('process_used')
    assert process_used and process_used.startswith('temp/process_override_')
    # verify file contains support_enable as integer 1
    with open(process_used, 'r') as f:
        proc = json.load(f)
    assert proc.get('support_enable') == 1
    # nested support.enable should also be integer 1
    assert proc.get('support', {}).get('enable') == 1


def test_invalid_settings_key_returns_400(client):
    data = {
        'process': '0.08mm Extra Fine @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'unsupported': True})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')

    resp = client.post('/v0.1.2/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body['success'] is False
    assert body['error']['code'] == 'bad_settings'


def test_auto_enable_supports_on_warning(client, monkeypatch, tmp_path):
    # Simulate Orca first run issuing a support suggestion, second run uses overrides and clears warning
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(exist_ok=True)
    os.environ['ORCASLICER_BIN'] = 'python'

    def fake_run(cmd, stdout, stderr, check, text, timeout):
        # inspect load-settings arg to find the process file path
        cmd_str = ' '.join(cmd)
        m = re.search(r"--load-settings\s+([^\s]+)", cmd_str)
        process_path = None
        if m:
            settings_arg = m.group(1)
            # settings_arg format: 'printer.json;process.json' or similar
            if ';' in settings_arg:
                parts = settings_arg.split(';')
                process_path = parts[1]
            else:
                process_path = settings_arg
        # Default: no override used -> produce initial warning
        produces_warning = True
        if process_path and os.path.exists(process_path):
            try:
                with open(process_path, 'r') as pf:
                    proc = json.load(pf)
                # If process file contains support_enable as string '1' then succeed
                v = proc.get('support_enable')
                if v == '1' or v == 'true' or v is True:
                    produces_warning = False
                else:
                    produces_warning = True
            except Exception:
                produces_warning = True

        if produces_warning:
            result = {"return_code": 0, "error_string": "Success.", "sliced_plates": [{"id": 1, "sliced_time": 1, "warning_message": "It seems object file.stl has floating cantilever. Please re-orient the object or enable support generation."}]}
            # Also simulate Orca complaining about invalid json type (to trigger alternate-encoding logic)
            stdout = "[error] load_from_json: parse {} error, invalid json type for support_enable\n".format(process_path if process_path else 'unknown')
            stderr = stdout
        else:
            result = {"return_code": 0, "error_string": "Success.", "sliced_plates": [{"id": 1, "sliced_time": 1, "warning_message": ""}]}
            stdout = "Success run"
            stderr = ""

        with open('result.json', 'w') as f:
            json.dump(result, f)
        # create a dummy output.3mf
        os.makedirs('temp', exist_ok=True)
        with zipfile.ZipFile('temp/output.3mf', 'w') as z:
            gcode_content = (
                "; filament used [mm] = 10.00\n"
                "; filament used [cm3] = 0.01\n"
                "; model printing time: 0h 1m 0s;\n"
                "; total estimated time: 0h 1m 30s\n"
                "; estimated first layer printing time (normal mode) = 0m 10s\n"
                "; filament_type = PLA\n"
                "; default_print_profile = 0.20mm Standard @BBL X1C\n"
                "max_z_height: 1.00\n"
            )
            z.writestr('Metadata/plate_1.gcode', gcode_content)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    # Post without supports to test automatic retry on warning
    data = {
        'process': '0.08mm Extra Fine @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')

    resp = client.post('/v0.1.2/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body['success'] is True
    assert body['auto_enabled_supports'] is True
    assert body['auto_retry_info']['attempted'] is True
    assert body['auto_retry_info']['retry_success'] is True
    assert body['auto_retry_info']['retry_applied_overrides'] == {'supports': True}
    assert body['auto_retry_info']['retry_process_used'].startswith('temp/process_override_')


def test_debug_mode_includes_3mf(client):
    # In debug mode the response should include a base64-encoded output.3mf
    from io import BytesIO

    # enable debug on the app
    flask_app.debug = True

    data = {
        'process': '0.08mm Extra Fine @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'supports': True, 'layer_height': '0.20mm'})
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')

    resp = client.post('/v0.1.2/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert 'output_3mf_base64' in body

    decoded = base64.b64decode(body['output_3mf_base64'])
    z = zipfile.ZipFile(BytesIO(decoded))
    assert 'Metadata/plate_1.gcode' in z.namelist()

    # Restore debug flag
    flask_app.debug = False


def test_include_3mf_returns_file(client):
    # When include_3mf=true is set, endpoint should return the actual .3mf file as an attachment
    from io import BytesIO

    data = {
        'process': '0.08mm Extra Fine @BBL X1C.json',
        'filament': 'Bambu PETG Basic @BBL X1C.json',
        'settings': json.dumps({'supports': True, 'layer_height': '0.20mm'}),
        'include_3mf': 'true'
    }
    data['stlFile'] = (io.BytesIO(b"solid test\nendsolid test"), 'test.stl')

    resp = client.post('/v0.1.2/', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    # Should be an attachment with filename output.3mf
    cd = resp.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert 'output.3mf' in cd

    # Validate the returned body is a zip and contains Metadata/plate_1.gcode
    z = zipfile.ZipFile(BytesIO(resp.data))
    assert 'Metadata/plate_1.gcode' in z.namelist()