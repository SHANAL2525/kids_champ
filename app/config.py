from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "KidsChamp"
VERSION = "1.0"
APP_VERSION = "Version 1"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
MAX_BATCH_IMAGES = 250
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
OUTPUT_FORMATS = {"PNG": ".png", "TIFF": ".tif"}

# ZIPs are treated as untrusted input. These limits are deliberately generous for
# 250 high-resolution artworks while still blocking path traversal and ZIP bombs.
MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_IMAGE_BYTES = 256 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 250.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base / relative


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = PROJECT_ROOT

TEMPLATE_DIR = APP_DIR / "templates"
DEFAULT_TEMPLATE_DIR = TEMPLATE_DIR / "defaults"
CUSTOM_TEMPLATE_DIR = TEMPLATE_DIR / "custom"
GROUPS_FILE = TEMPLATE_DIR / "groups.json"
SETTINGS_DIR = APP_DIR / "app_data"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
ICON_DIR = resource_path("assets/icons")
APP_ICON = resource_path("assets/kidsChamp.png")

for p in (TEMPLATE_DIR, DEFAULT_TEMPLATE_DIR, CUSTOM_TEMPLATE_DIR, SETTINGS_DIR):
    p.mkdir(parents=True, exist_ok=True)

LIGHT = {
    "bg": "#F4F7FB", "card": "#FFFFFF", "card_alt": "#F8FAFC", "text": "#172033", "muted": "#64748B",
    "border": "#D7E0EA", "primary": "#2563EB", "primary_hover": "#1D4ED8", "primary_soft": "#E8F0FF",
    "success": "#16A34A", "danger": "#DC2626", "warning": "#B45309", "charcoal": "#202A38",
    "input": "#FFFFFF", "selection": "#E8F0FF", "header": "#EAF0F7",
}
DARK = {
    "bg": "#101722", "card": "#182230", "card_alt": "#202D3D", "text": "#EEF4FA", "muted": "#A7B6C8",
    "border": "#3A4B60", "primary": "#568CC4", "primary_hover": "#69A0D5", "primary_soft": "#29435F",
    "success": "#22C55E", "danger": "#EF4444", "warning": "#F59E0B", "charcoal": "#0B1220",
    "input": "#131C28", "selection": "#2A4562", "header": "#1D2A39",
}
GROUP_COLORS = {
    "Blue": "#3B82F6",
    "Green": "#22C55E",
    "Orange": "#F97316",
}
