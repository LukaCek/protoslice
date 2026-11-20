"""Real OrcaSlicer regression matrix: profile changes and generated support.

This module intentionally uses the real pinned Orca binary. It is *not* run by
the fast CI job; ORCA_LIVE=1 is required and the Docker integration workflow
must run it without mocks.
"""
import json
import os
from pathlib import Path
import pytest

from engine import SlicingEngine, SliceRequest, SliceSettings, SlicingError

if os.environ.get("ORCA_LIVE") != "1":
    pytest.skip("Requires real OrcaSlicer (ORCA_LIVE=1)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
CUBE = ROOT / "tests" / "fixtures" / "models" / "cube_20mm.stl"
OVERHANG = ROOT / "tests" / "fixtures" / "models" / "overhang_50mm.stl"


def run_real(engine, model, process_id, supports=False, orient=False):
    request = SliceRequest(
        source=model,
        process_id=process_id,
        settings=SliceSettings(
            supports=supports,
            auto_arrange=True,
            auto_orient=orient,
        ),
    )
    try:
        result = engine.slice(request)
    except SlicingError as exc:
        pytest.fail(f"Real Orca slice failed ({exc.code}): {exc.diagnostics or str(exc)}")
    print(
        f"REAL_ORCA model={model.name} process={process_id} supports={supports} "
        f"grams={result.metrics.used_g:.4f} "
        f"time_min={result.metrics.total_time_min:.4f} "
        f"source={result.metrics.mass_source}"
    )
    assert result.output_3mf.is_file()
    assert result.metrics.used_g > 0
    assert result.metrics.total_time_min > 0
    return result


def test_two_quality_profiles_change_real_cube_toolpaths(tmp_path):
    engine = SlicingEngine(project_root=ROOT, work_root=tmp_path, timeout_seconds=240)
    results = []
    try:
        fine = run_real(engine, CUBE, "fine_012", orient=False)
        results.append(fine)
        draft = run_real(engine, CUBE, "extra_draft_028", orient=False)
        results.append(draft)
        fine_process = json.loads((fine.output_3mf.parent / "process.json").read_text())
        draft_process = json.loads((draft.output_3mf.parent / "process.json").read_text())
        assert float(fine_process["layer_height"]) == pytest.approx(0.12)
        assert float(draft_process["layer_height"]) == pytest.approx(0.28)
        # The finer profile should require more layers/time on the same cube.
        assert fine.metrics.total_time_min > draft.metrics.total_time_min
    finally:
        for result in results:
            engine.delete_job(result)


def test_supports_increase_real_overhang_material_usage(tmp_path):
    engine = SlicingEngine(project_root=ROOT, work_root=tmp_path, timeout_seconds=240)
    results = []
    try:
        unsupported = run_real(engine, OVERHANG, "standard_020", supports=False)
        results.append(unsupported)
        supported = run_real(engine, OVERHANG, "standard_020", supports=True)
        results.append(supported)
        unsupported_process = json.loads(
            (unsupported.output_3mf.parent / "process.json").read_text()
        )
        supported_process = json.loads(
            (supported.output_3mf.parent / "process.json").read_text()
        )
        assert unsupported_process["enable_support"] == "0"
        assert supported_process["enable_support"] == "1"
        # No auto-orientation: fixed 25mm-high T-shaped model with wide overhang.
        # If this fails, actual support generation is not proven and must be fixed.
        assert supported.metrics.used_g > unsupported.metrics.used_g + 0.01
    finally:
        for result in results:
            engine.delete_job(result)
