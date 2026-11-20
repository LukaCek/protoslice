"""Allowlisted, deterministic profile selection with compatibility checks.

The OrcaSlicer application resolves upstream inherited profile settings. We
must not pretend that a thin derived JSON is itself an effective profile.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path

from .models import SlicingError


PRINTERS = {"bambu_p1s_04": "Bambu Lab P1S 0.4 nozzle.json"}
PROCESSES = {
    "standard_020": "0.20mm Standard @BBL X1C.json",
    "fine_012": "0.12mm Fine @BBL X1C.json",
    "high_quality_012": "0.12mm High Quality @BBL X1C.json",
    "extra_fine_008": "0.08mm Extra Fine @BBL X1C.json",
    "strength_020": "0.20mm Strength @BBL X1C.json",
    "extra_draft_028": "0.28mm Extra Draft @BBL X1C.json",
}
# Only the filament actually committed in files/filament is selectable.
FILAMENTS = {"bambu_petg_basic": "Bambu PETG Basic @BBL X1C.json"}


@dataclass(frozen=True)
class ResolvedProfiles:
    printer: Path
    process: Path
    filament: Path
    printer_name: str
    density_g_cm3: float | None


class ProfileCatalog:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _load(self, category: str, identifier: str, mapping: dict):
        filename = mapping.get(identifier)
        if filename is None:
            raise SlicingError("unknown_profile", f"Unknown {category} profile ID: {identifier}")
        folder = {"printer": "printers", "process": "process", "filament": "filament"}[category]
        path = (self.root / "files" / folder / filename).resolve()
        if not path.is_relative_to((self.root / "files" / folder).resolve()):
            raise SlicingError("invalid_profile", "Profile path escapes its directory")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SlicingError("invalid_profile", f"{category} profile cannot be read") from exc
        if not isinstance(data, dict) or data.get("type") != ("machine" if category == "printer" else category):
            raise SlicingError("invalid_profile", f"Invalid {category} profile type")
        return path, data

    def resolve(self, printer_id: str, process_id: str, filament_id: str):
        printer_path, printer = self._load("printer", printer_id, PRINTERS)
        process_path, process = self._load("process", process_id, PROCESSES)
        filament_path, filament = self._load("filament", filament_id, FILAMENTS)
        printer_name = printer.get("name")
        if not isinstance(printer_name, str):
            raise SlicingError("invalid_profile", "Printer name missing")
        for category, profile in (("process", process), ("filament", filament)):
            compatible = profile.get("compatible_printers")
            if compatible is not None and printer_name not in compatible:
                raise SlicingError("incompatible_profile", f"{category} profile is incompatible with printer")
        density = filament.get("filament_density")
        if isinstance(density, list):
            density = density[0] if len(density) == 1 else None
        if density is not None:
            try:
                density = float(density)
                if not math.isfinite(density) or density <= 0:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise SlicingError("invalid_profile", "Invalid explicit filament density") from exc
        # None means inherited/unknown; the parser then REQUIRES Orca's grams.
        return ResolvedProfiles(printer_path, process_path, filament_path, printer_name, density)
