"""Isolated OrcaSlicer subprocess execution. No Flask, HTTP or cloud SDK imports."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from .models import SliceRequest, SliceResult, SliceSettings, SlicingError
from .profiles import ProfileCatalog
from .results import parse_sliced_3mf

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_3MF_UNPACKED_BYTES = 512 * 1024 * 1024


class SlicingEngine:
    def __init__(
        self,
        project_root: Path | None = None,
        orca_executable: str | None = None,
        work_root: Path | None = None,
        timeout_seconds: float = 300,
    ):
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.catalog = ProfileCatalog(self.project_root)
        self.orca_executable = orca_executable or os.environ.get("ORCASLICER_BIN", "orcaslicer")
        self.work_root = Path(work_root or tempfile.gettempdir()).resolve()
        if not (1 <= timeout_seconds <= 3600):
            raise SlicingError("invalid_timeout", "Timeout must be 1–3600 seconds")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _validate_input(source: Path) -> Path:
        try:
            source = Path(source).resolve(strict=True)
            size = source.stat().st_size
        except (OSError, ValueError) as exc:
            raise SlicingError("invalid_input", "Input model does not exist") from exc
        if not source.is_file() or source.suffix.lower() not in (".stl", ".3mf"):
            raise SlicingError("invalid_input", "Expected a regular STL or 3MF file")
        if not (0 < size <= MAX_INPUT_BYTES):
            raise SlicingError("invalid_input", "Input model is empty or too large")
        if source.suffix.lower() == ".3mf":
            import zipfile
            try:
                with zipfile.ZipFile(source) as archive:
                    files = archive.infolist()
                    if not any(f.filename.lower().endswith(".model") for f in files):
                        raise SlicingError("invalid_input", "3MF has no model")
                    if sum(f.file_size for f in files) > MAX_3MF_UNPACKED_BYTES:
                        raise SlicingError("invalid_input", "3MF expands beyond the size limit")
            except (OSError, zipfile.BadZipFile) as exc:
                raise SlicingError("invalid_input", "Invalid 3MF archive") from exc
        return source

    @staticmethod
    def _override_process(source: Path, target: Path, settings: SliceSettings):
        # This is the exact process profile sent to Orca, never a global edit.
        data = json.loads(source.read_text(encoding="utf-8"))
        data["enable_support"] = "1" if settings.supports else "0"
        if settings.layer_height_mm is not None:
            data["layer_height"] = str(settings.layer_height_mm)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _terminate_tree(process: subprocess.Popen):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
        process.communicate()

    def slice(self, request: SliceRequest) -> SliceResult:
        if not isinstance(request.settings, SliceSettings):
            raise SlicingError("invalid_settings", "Expected validated SliceSettings")
        source = self._validate_input(request.source)
        profile = self.catalog.resolve(request.printer_id, request.process_id, request.filament_id)

        executable = shutil.which(self.orca_executable)
        if not executable:
            raise SlicingError("orca_missing", "OrcaSlicer executable is unavailable")
        executable = str(Path(executable).resolve())

        self.work_root.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="protoslice-v2-", dir=self.work_root)).resolve()
        try:
            model = workspace / ("input" + source.suffix.lower())
            shutil.copyfile(source, model)
            process_override = workspace / "process.json"
            self._override_process(profile.process, process_override, request.settings)
            output = workspace / "output.3mf"
            # Orca 2.3 CLI requires the slicing-data parent to exist;
            # otherwise it exits with -53 (process exit code 203).
            slicedata_dir = workspace / "slicedata"
            slicedata_dir.mkdir()
            command = [
                executable,
                "--arrange", "1" if request.settings.auto_arrange else "0",
                "--orient", "1" if request.settings.auto_orient else "0",
                "--export-slicedata", str(slicedata_dir),
                "--load-settings", str(profile.printer) + ";" + str(process_override),
                "--load-filaments", str(profile.filament),
                "--slice", "0",
                "--debug", "2",
                "--export-3mf", str(output),
                "--info",
                str(model),
            ]
            try:
                process = subprocess.Popen(
                    command, cwd=str(workspace),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, start_new_session=(os.name != "nt"),
                )
            except OSError as exc:
                raise SlicingError("orca_start_failed", "Could not start OrcaSlicer") from exc
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                self._terminate_tree(process)
                raise SlicingError("orca_timeout", "OrcaSlicer exceeded the configured timeout") from exc

            if process.returncode != 0:
                raise SlicingError(
                    "orca_failed", f"OrcaSlicer returned exit code {process.returncode}",
                    diagnostics=((stdout or "") + "\n" + (stderr or ""))[-6000:]
                    if os.environ.get("ORCA_LIVE") == "1" else None,
                )

            report = workspace / "result.json"
            if report.exists():
                try:
                    report_data = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise SlicingError("invalid_output", "Orca result.json is invalid") from exc
                if report_data.get("return_code", 0) != 0:
                    raise SlicingError("orca_failed", "OrcaSlicer reported failure")
            if not output.is_file() or output.stat().st_size == 0:
                raise SlicingError("invalid_output", "Orca did not produce a sliced 3MF")
            metrics = parse_sliced_3mf(output, density_g_cm3=profile.density_g_cm3)
            return SliceResult(
                job_id=workspace.name, output_3mf=output, metrics=metrics,
                printer_id=request.printer_id, process_id=request.process_id,
                filament_id=request.filament_id, orca_executable=executable,
                settings=request.settings,
            )
        except BaseException:
            # Failed attempts never leave a supposedly valid slicing artifact.
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    @staticmethod
    def delete_job(result: SliceResult):
        """Explicitly delete a completed job after the caller stores its artifact."""
        if not result.job_id.startswith("protoslice-v2-"):
            raise SlicingError("invalid_job", "Refusing to delete a non-ProtoSlice workspace")
        path = Path(result.output_3mf).parent
        if path.name != result.job_id:
            raise SlicingError("invalid_job", "Job folder does not match its ID")
        shutil.rmtree(path)
