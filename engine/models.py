"""Versioned v2 request/result contracts; units are explicit."""

from dataclasses import dataclass
from pathlib import Path
import math


class SlicingError(RuntimeError):
    """Stable internal error code; no secret paths or input URLs in public messages."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class SliceSettings:
    supports: bool = False
    layer_height_mm: float | None = None
    auto_orient: bool = True
    auto_arrange: bool = True

    def __post_init__(self):
        for key in ("supports", "auto_orient", "auto_arrange"):
            if type(getattr(self, key)) is not bool:
                raise SlicingError("invalid_settings", f"{key} must be a boolean")
        if self.layer_height_mm is not None:
            if isinstance(self.layer_height_mm, bool):
                raise SlicingError("invalid_layer_height", "Layer height must be a number in mm")
            try:
                height = float(self.layer_height_mm)
            except (ValueError, TypeError) as exc:
                raise SlicingError("invalid_layer_height", "Layer height must be a number in mm") from exc
            if not math.isfinite(height) or not (0.05 <= height <= 0.4):
                raise SlicingError("invalid_layer_height", "Layer height must be 0.05–0.4 mm")
            object.__setattr__(self, "layer_height_mm", height)

    @classmethod
    def from_mapping(cls, data: dict):
        if not isinstance(data, dict):
            raise SlicingError("invalid_settings", "Settings must be a JSON object")
        unknown = set(data) - {"supports", "layer_height_mm", "auto_orient", "auto_arrange"}
        if unknown:
            raise SlicingError("invalid_settings", "Unsupported setting: " + ", ".join(sorted(unknown)))
        return cls(**data)


@dataclass(frozen=True)
class SliceRequest:
    source: Path
    printer_id: str = "bambu_p1s_04"
    process_id: str = "standard_020"
    filament_id: str = "bambu_petg_basic"
    settings: SliceSettings = SliceSettings()


@dataclass(frozen=True)
class SliceMetrics:
    used_g: float
    used_cm3: float | None
    used_mm: float | None
    total_time_min: float
    model_time_min: float | None
    first_layer_time_min: float | None
    max_z_mm: float | None
    mass_source: str


@dataclass(frozen=True)
class SliceResult:
    job_id: str
    output_3mf: Path
    metrics: SliceMetrics
    printer_id: str
    process_id: str
    filament_id: str
    orca_executable: str
    settings: SliceSettings
