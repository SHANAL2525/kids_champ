from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Box:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {"x": int(self.x), "y": int(self.y), "width": int(self.width), "height": int(self.height)}

    @classmethod
    def from_dict(cls, data: dict, fallback: tuple[int, int, int, int]) -> "Box":
        return cls(
            int(data.get("x", fallback[0])), int(data.get("y", fallback[1])),
            int(data.get("width", fallback[2])), int(data.get("height", fallback[3])),
        )


@dataclass
class TemplateRecord:
    key: str
    folder: Path
    json_path: Path
    name: str
    template_type: str
    group: str
    group_color: str
    template_image: Path
    overlay_image: Optional[Path]
    thumbnail_image: Optional[Path]
    artwork_box: Box
    name_box: Box
    city_box: Box
    name_font_size: int = 53
    name_max_lines: int = 1
    city_font_size: int = 37
    text_color: str = "#10344B"
    show_location_pin: bool = True
    location_pin_size: int = 38
    location_pin_gap: int = 12
    artwork_fit_mode: str = "Fit Full Artwork"
    is_default: bool = False
    locked: bool = False


@dataclass
class BatchItem:
    source_path: Path
    filename: str
    submission_id: str
    child_name: str
    city: str
    detected_layout: str
    source_relative: str = ""
    template_mode: str = "Auto"
    status: str = "Ready"
    warning: str = ""
    artwork_fit_override: str = "Smart"
    artwork_zoom: float = 1.0
    artwork_offset_x: float = 0.0
    artwork_offset_y: float = 0.0
    artwork_rotation: int = 0
    auto_paper_detect: bool = True
    brightness: int = 0
    contrast: int = 0
    color_balance_red: int = 0
    color_balance_green: int = 0
    color_balance_blue: int = 0
    hue: int = 0
    saturation: int = 0
    lightness: int = 0
    preset_name: str = "None"
    manual_crop: tuple[float, float, float, float] | None = None
    manual_warp: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None = None
    stretch_strokes: tuple[tuple[float, float, float, float, float, float], ...] | None = None
    # Photoshop-style Free Transform destination corners, normalized to the active
    # template artwork frame and ordered TL, TR, BR, BL. Values may extend outside
    # 0..1; the renderer clips them safely at the locked frame boundary.
    artwork_transform: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None = None
    routing_confidence: float = 0.0
    routing_source: str = "raw"
    routing_needs_check: bool = False

    @property
    def selected_layout(self) -> str:
        # Template routing describes the submitted artwork/frame choice. Rotating the
        # artwork is an edit inside that frame and must never silently select another
        # Portrait/Landscape template.
        return self.detected_layout if self.template_mode == "Auto" else self.template_mode
