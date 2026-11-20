"""Read-only baseline of legacy API behavior. Does not invoke real OrcaSlicer.

These tests intentionally pin the *legacy* output shape, including used_cm3 as
volume. V2 must define a different contract for mass in grams.
"""

import json
import zipfile

import pytest

pytest.importorskip("flask")
pytest.importorskip("requests")

from app import app as flask_app
from versions.v0_1_3.routes import get_data_from_orcaslicer_output, resolve_profile


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_root_redirect_remains_legacy_v013(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["Location"].endswith("/v0.1.3/")


def test_missing_file_v013_is_structured_error(client):
    response = client.post("/v0.1.3/", data={})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert data["error"]["code"] == "missing_file"


def test_missing_file_v011_stays_legacy_text_contract(client):
    response = client.post("/v0.1.1/", data={})
    assert response.status_code == 400
    assert b"stlFile" in response.data


def test_unknown_v013_quality_rejected_before_slicing(client):
    response = client.post("/v0.1.3/", data={"quality": "not_a_real_profile"})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert data["error"]["code"] == "unknown_quality"


def test_v013_process_profile_baseline():
    process, filament, error = resolve_profile(quality="standard_020")
    assert error is None
    assert process == "0.20mm Standard @BBL X1C.json"
    assert filament == "Bambu PETG Basic @BBL X1C.json"


def test_v013_metadata_parser_baseline(tmp_path):
    archive = tmp_path / "output.3mf"
    gcode = (
        "; filament used [mm] = 1234.50\n"
        "; filament used [cm3] = 3.21\n"
        "; model printing time: 0h 10m 30s;\n"
        "; total estimated time: 0h 12m 0s\n"
        "; estimated first layer printing time (normal mode) = 1m 0s\n"
        "; filament_type = PLA\n"
        "; default_print_profile = 0.20mm Standard @BBL X1C\n"
        "max_z_height: 12.34\n"
    )
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
    parsed = get_data_from_orcaslicer_output(str(archive))
    assert parsed["used_mm"] == 1234.5
    assert parsed["used_cm3"] == 3.21
    assert parsed["max_z"] == 12.34
    assert parsed["model_time"] == 10.5
    assert parsed["total_time"] == 12.0
    assert parsed["first_layer_time"] == 1.0
    # Deliberate documentation of current API: this is VOLUME, not grams.
    assert "used_g" not in parsed
