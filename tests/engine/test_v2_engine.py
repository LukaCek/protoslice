"""Unit and subprocess-boundary tests, independent of an installed OrcaSlicer."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import stat
import zipfile

import pytest

from engine import (
    ProfileCatalog, SliceRequest, SliceSettings,
    SlicingEngine, SlicingError,
)
from engine.results import parse_gcode, parse_sliced_3mf


ROOT = Path(__file__).resolve().parents[2]
CUBE = ROOT / "tests" / "fixtures" / "models" / "cube_20mm.stl"
HEADER = (
    "; filament used [mm] = 1234.50\n"
    "; filament used [cm3] = 3.21\n"
    "; filament used [g] = 4.07\n"
    "; model printing time: 0h 10m 30s;\n"
    "; total estimated time: 0h 12m 0s\n"
    "; estimated first layer printing time (normal mode) = 1m 0s\n"
    "max_z_height: 12.34\n"
)


def test_parser_prefers_real_grams_and_preserves_units():
    md = parse_gcode(HEADER)
    assert md.used_g == 4.07
    assert md.used_cm3 == 3.21
    assert md.used_mm == 1234.5
    assert md.total_time_min == 12.0
    assert md.model_time_min == 10.5
    assert md.first_layer_time_min == 1.0
    assert md.max_z_mm == 12.34
    assert md.mass_source == "orca_gcode"


def test_parser_density_only_when_explicit():
    text = HEADER.replace("; filament used [g] = 4.07\n", "")
    with pytest.raises(SlicingError, match="density") as err:
        parse_gcode(text)
    assert err.value.code == "missing_mass"
    result = parse_gcode(text, density_g_cm3=1.27)
    assert result.used_g == pytest.approx(4.0767, abs=0.0001)
    assert result.mass_source == "explicit_density"


@pytest.mark.parametrize("gcode,code", [
    ("; filament used [cm3] = 4.3\n; total estimated time: 0h 10m 0s\n", "missing_mass"),
    ("; filament used [g] = 3.8\n", "missing_statistics"),
    ("; filament used [g] = 3.8\n; total estimated time: 0h 0m 0s\n", "missing_statistics"),
    ("; filament used [g] = 3.8\n; total estimated time: NOT A TIME\n", "invalid_statistics"),
])
def test_bad_stats_fail_closed(gcode, code):
    with pytest.raises(SlicingError) as err:
        parse_gcode(gcode)
    assert err.value.code == code


def test_corrupt_and_empty_3mf_rejected(tmp_path):
    path = tmp_path / "output.3mf"
    path.write_bytes(b"not zip")
    with pytest.raises(SlicingError) as err:
        parse_sliced_3mf(path)
    assert err.value.code == "invalid_output"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "hello")
    with pytest.raises(SlicingError) as err:
        parse_sliced_3mf(path)
    assert err.value.code == "invalid_output"


def test_strict_settings_validation():
    assert SliceSettings.from_mapping({"supports": True, "layer_height_mm": 0.2}).supports
    for data in ({"supports": "false"}, {"layer_height_mm": "NaN"},
                 {"layer_height_mm": 2}, {"unknown_key": True},
                 {"auto_orient": 1}):
        with pytest.raises(SlicingError):
            SliceSettings.from_mapping(data)


def test_profile_catalog_uses_known_ids():
    catalog = ProfileCatalog(ROOT)
    p = catalog.resolve("bambu_p1s_04", "standard_020", "bambu_petg_basic")
    assert p.printer.is_absolute()
    assert p.process.is_absolute()
    assert p.filament.is_absolute()
    assert p.printer_name == "Bambu Lab P1S 0.4 nozzle"
    # Derived PETG profile inherits density, so we must not guess it.
    assert p.density_g_cm3 is None
    with pytest.raises(SlicingError) as err:
        catalog.resolve("bambu_p1s_04", "../evil", "bambu_petg_basic")
    assert err.value.code == "unknown_profile"


@pytest.fixture
def stub_orca(tmp_path):
    executable = tmp_path / "fake_orca"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, json, time, zipfile, pathlib\n"
        "args=sys.argv[1:]\n"
        "mode=os.environ.get('FAKE_ORCA_MODE','ok')\n"
        "if mode == 'hang': time.sleep(10)\n"
        "if mode == 'fail': sys.exit(13)\n"
        "destination=pathlib.Path(args[args.index('--export-3mf')+1])\n"
        "assert pathlib.Path(args[-1]).is_file()\n"
        "assert all(pathlib.Path(p).is_absolute() for p in args[args.index('--load-settings')+1].split(';'))\n"
        "assert pathlib.Path(args[args.index('--load-filaments')+1]).is_absolute()\n"
        "assert pathlib.Path(args[args.index('--export-slicedata')+1]).is_dir()\n"
        "payload='; filament used [cm3] = 3.21\\n; filament used [g] = 4.07\\n; total estimated time: 0h 12m 0s\\n'\n"
        "if mode == 'invalid_stats': payload='; filament used [cm3] = 3.21\\n'\n"
        "with zipfile.ZipFile(destination,'w') as z: z.writestr('Metadata/plate_1.gcode',payload)\n"
        "pathlib.Path('result.json').write_text(json.dumps({'return_code':0}))\n"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def make_engine(tmp_path, stub_orca, timeout=20):
    return SlicingEngine(
        project_root=ROOT, orca_executable=str(stub_orca),
        work_root=tmp_path / "jobs", timeout_seconds=timeout,
    )


def test_subprocess_receives_absolute_paths_and_persists_result(tmp_path, stub_orca):
    engine = make_engine(tmp_path, stub_orca)
    result = engine.slice(SliceRequest(CUBE, settings=SliceSettings(supports=True, layer_height_mm=0.2)))
    assert result.metrics.used_g == 4.07
    assert result.output_3mf.is_file()
    overridden = json.loads((result.output_3mf.parent / "process.json").read_text())
    assert overridden["enable_support"] == "1"
    assert overridden["layer_height"] == "0.2"
    engine.delete_job(result)
    assert not result.output_3mf.exists()


def test_subprocess_error_and_invalid_stats_clean_workspaces(tmp_path, stub_orca, monkeypatch):
    engine = make_engine(tmp_path, stub_orca)
    for mode, code in (("fail", "orca_failed"), ("invalid_stats", "missing_mass")):
        monkeypatch.setenv("FAKE_ORCA_MODE", mode)
        with pytest.raises(SlicingError) as err:
            engine.slice(SliceRequest(CUBE))
        assert err.value.code == code
        assert list((tmp_path / "jobs").iterdir()) == []


def test_subprocess_timeout_is_enforced(tmp_path, stub_orca, monkeypatch):
    monkeypatch.setenv("FAKE_ORCA_MODE", "hang")
    engine = make_engine(tmp_path, stub_orca, timeout=1)
    with pytest.raises(SlicingError) as err:
        engine.slice(SliceRequest(CUBE))
    assert err.value.code == "orca_timeout"
    assert list((tmp_path / "jobs").iterdir()) == []


def test_two_concurrent_jobs_are_isolated(tmp_path, stub_orca, monkeypatch):
    monkeypatch.setenv("FAKE_ORCA_MODE", "ok")
    engine = make_engine(tmp_path, stub_orca)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(engine.slice, [SliceRequest(CUBE), SliceRequest(CUBE)]))
    assert outputs[0].job_id != outputs[1].job_id
    assert all(out.output_3mf.is_file() for out in outputs)
    for out in outputs:
        engine.delete_job(out)
