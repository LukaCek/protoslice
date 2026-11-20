"""Real, opt-in OrcaSlicer smoke test inside a Docker container.

These tests MUST NOT be replaced with mocked subprocesses or marked passed
when the Orca executable is unavailable.
"""
import os
from pathlib import Path
import zipfile
import pytest

from engine import SlicingEngine, SliceRequest, SliceSettings, SlicingError

if os.environ.get("ORCA_LIVE") != "1":
    pytest.skip("Real OrcaSlicer test is opt-in (ORCA_LIVE=1)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
CUBE = ROOT / "tests" / "fixtures" / "models" / "cube_20mm.stl"


def test_real_orca_slices_cube_and_reports_actual_grams(tmp_path):
    engine = SlicingEngine(
        project_root=ROOT,
        work_root=tmp_path,
        timeout_seconds=240,
    )
    result = engine.slice(SliceRequest(
        source=CUBE, settings=SliceSettings(
            supports=False, auto_arrange=True, auto_orient=True,
            layer_height_mm=0.2,
        ),
    ))
    try:
        assert result.metrics.used_g > 0
        assert result.metrics.total_time_min > 0
        # The checked-in filament profile inherits density, so real Orca's
        # explicit gram statistic is mandatory, not a guessed conversion.
        assert result.metrics.mass_source == "orca_gcode"
        with zipfile.ZipFile(result.output_3mf) as z:
            assert "Metadata/plate_1.gcode" in z.namelist()
            gcode = z.read("Metadata/plate_1.gcode")
            assert len(gcode) > 1000, "Output must contain actual toolpaths"
    finally:
        engine.delete_job(result)
