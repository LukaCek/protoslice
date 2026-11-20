"""Strict parsing of actual OrcaSlicer sliced 3MF metadata."""
import math
import re
import zipfile
from pathlib import Path

from .models import SliceMetrics, SlicingError

MAX_GCODE_BYTES = 256 * 1024 * 1024
NUMBER = r"([0-9]+(?:\.[0-9]+)?)"


def _numeric(gcode: str, label: str):
    match = re.search(r"(?im)^\s*;\s*" + re.escape(label) + r"\s*=\s*" + NUMBER + r"(?:\s*[,;].*)?\s*$", gcode)
    return float(match.group(1)) if match else None


def _minutes(gcode: str, prefix: str):
    # Orca 2.3 combines model and total times on one comment line.
    # Find a named field first, then validate its entire time value so
    # malformed values are errors rather than silently missing metrics.
    match = re.search(
        r"(?im)(?:^\s*;?\s*|;\s*)" + re.escape(prefix)
        + r"\s*[:=]\s*([^;\r\n]+)",
        gcode,
    )
    if not match:
        return None
    value = match.group(1).strip()
    if not re.fullmatch(r"\s*(?:\d+\s*[hms]\s*)+", value, re.IGNORECASE):
        raise SlicingError("invalid_statistics", "Malformed print time in Orca output")
    segments = re.findall(r"(\d+)\s*([hms])\b", value, re.IGNORECASE)
    units = {"h": 60.0, "m": 1.0, "s": 1.0 / 60.0}
    return round(sum(int(n) * units[unit.lower()] for n, unit in segments), 4)

def parse_gcode(gcode: str, density_g_cm3: float | None = None) -> SliceMetrics:
    used_g = _numeric(gcode, "filament used [g]")
    used_cm3 = _numeric(gcode, "filament used [cm3]")
    used_mm = _numeric(gcode, "filament used [mm]")
    if used_g is not None:
        source = "orca_gcode"
        if used_cm3 is not None and density_g_cm3 is not None:
            estimated = used_cm3 * density_g_cm3
            if estimated > 0 and abs(used_g - estimated) / estimated > 0.1:
                raise SlicingError("invalid_statistics", "Orca mass and density-derived mass disagree")
    elif used_cm3 is not None and density_g_cm3 is not None:
        used_g = round(used_cm3 * density_g_cm3, 4)
        source = "pinned_profile_density"
    else:
        raise SlicingError("missing_mass", "Missing filament mass and trustworthy density")
    total = _minutes(gcode, "total estimated time")
    model = _minutes(gcode, "model printing time")
    first_layer = _minutes(gcode, "estimated first layer printing time (normal mode)")
    max_z = _numeric(gcode, "max_z_height")
    if max_z is None:
        zmatch = re.search(r"(?im)^\s*;?\s*max_z_height\s*[:=]\s*" + NUMBER, gcode)
        max_z = float(zmatch.group(1)) if zmatch else None
    for amount in (used_g, used_cm3, used_mm, total, model, first_layer, max_z):
        if amount is not None and (not math.isfinite(amount) or amount < 0):
            raise SlicingError("invalid_statistics", "Invalid numeric statistic from Orca")
    if not used_g or total is None or total <= 0:
        raise SlicingError("missing_statistics", "Missing positive mass or print time")
    return SliceMetrics(
        used_g=round(used_g, 4), used_cm3=used_cm3, used_mm=used_mm,
        total_time_min=total, model_time_min=model,
        first_layer_time_min=first_layer, max_z_mm=max_z, mass_source=source,
    )


def parse_sliced_3mf(path: Path, density_g_cm3: float | None = None) -> SliceMetrics:
    try:
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("Metadata/plate_1.gcode")
            if entry.file_size > MAX_GCODE_BYTES:
                raise SlicingError("invalid_output", "G-code is too large")
            with archive.open(entry) as data:
                gcode = data.read(MAX_GCODE_BYTES + 1)
                if len(gcode) > MAX_GCODE_BYTES:
                    raise SlicingError("invalid_output", "G-code exceeds size limit")
                text = gcode.decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, EOFError) as exc:
        raise SlicingError("invalid_output", "Missing or invalid sliced 3MF/G-code") from exc
    return parse_gcode(text, density_g_cm3=density_g_cm3)
