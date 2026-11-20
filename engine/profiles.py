"""Deterministic, version-pinned OrcaSlicer profile inheritance resolver."""
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
FILAMENTS = {"bambu_petg_basic": "Bambu PETG Basic @BBL X1C.json"}
FOLDERS = {"printer": "printers", "process": "process", "filament": "filament"}
VENDOR_FOLDERS = {"printer": "machine", "process": "process", "filament": "filament"}


@dataclass(frozen=True)
class ResolvedProfiles:
    printer: Path
    process: Path
    filament: Path
    printer_name: str
    density_g_cm3: float
    printer_effective: dict
    process_effective: dict
    filament_effective: dict


class ProfileCatalog:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.vendor = self.root / "engine" / "presets" / "orca_2_3_0" / "BBL"

    def _load(self, category: str, identifier: str, mapping: dict):
        filename = mapping.get(identifier)
        if filename is None:
            raise SlicingError("unknown_profile", f"Unknown {category} profile ID: {identifier}")
        folder = (self.root / "files" / FOLDERS[category]).resolve()
        path = (folder / filename).resolve()
        if not path.is_relative_to(folder):
            raise SlicingError("invalid_profile", "Profile path escapes its directory")
        data = self._read(category, path)
        return path, data

    @staticmethod
    def _read(category: str, path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SlicingError("invalid_profile", f"{category} profile cannot be read") from exc
        type_name = "machine" if category == "printer" else category
        if not isinstance(data, dict) or data.get("type") != type_name:
            raise SlicingError("invalid_profile", f"Invalid {category} profile type")
        return data

    def _flatten(self, category: str, child: dict, stack: frozenset[str] = frozenset()):
        parent_name = child.get("inherits")
        inherited = {}
        if parent_name is not None:
            if (not isinstance(parent_name, str) or not parent_name or
                    Path(parent_name).name != parent_name or
                    parent_name in stack or parent_name in (".", "..")):
                raise SlicingError("invalid_profile", "Invalid or cyclic profile inheritance")
            folder = (self.vendor / VENDOR_FOLDERS[category]).resolve()
            parent = (folder / (parent_name + ".json")).resolve()
            if not parent.is_relative_to(folder):
                raise SlicingError("invalid_profile", "Unsafe inherited profile")
            inherited = self._flatten(
                category, self._read(category, parent), stack | {parent_name}
            )
        merged = dict(inherited)
        merged.update(child)
        # The actual settings sent to the CLI must have no unresolved inheritance.
        merged.pop("inherits", None)
        return merged

    def resolve(self, printer_id: str, process_id: str, filament_id: str):
        printer_path, printer = self._load("printer", printer_id, PRINTERS)
        process_path, process = self._load("process", process_id, PROCESSES)
        filament_path, filament = self._load("filament", filament_id, FILAMENTS)

        printer_effective = self._flatten("printer", printer)
        process_effective = self._flatten("process", process)
        filament_effective = self._flatten("filament", filament)
        printer_name = printer_effective.get("name")
        if not isinstance(printer_name, str):
            raise SlicingError("invalid_profile", "Printer name missing")
        for category, effective in (
            ("process", process_effective), ("filament", filament_effective)
        ):
            compatible = effective.get("compatible_printers")
            if compatible is not None and printer_name not in compatible:
                raise SlicingError(
                    "incompatible_profile", f"{category} profile incompatible with printer"
                )
        density = filament_effective.get("filament_density")
        if not isinstance(density, list) or len(density) != 1:
            raise SlicingError("invalid_profile", "Single-filament density missing")
        try:
            density_value = float(density[0])
        except (ValueError, TypeError) as exc:
            raise SlicingError("invalid_profile", "Invalid filament density") from exc
        if not math.isfinite(density_value) or density_value <= 0:
            raise SlicingError("invalid_profile", "Nonpositive filament density")
        filament_type = filament_effective.get("filament_type")
        if not isinstance(filament_type, list) or filament_type != ["PETG"]:
            raise SlicingError("invalid_profile", "PETG filament type not resolved")
        return ResolvedProfiles(
            printer_path, process_path, filament_path, printer_name, density_value,
            printer_effective, process_effective, filament_effective,
        )
