from __future__ import annotations

import json
import os
from .config import SETTINGS_FILE

DEFAULTS = {
    "theme": "light",
    "template_view": "grid",
    "export_format": "PNG",
    "active_portrait_template": "",
    "active_landscape_template": "",
    "active_square_template": "",
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory replace is atomic on Windows. A crash can no longer leave a
    # half-written settings file that silently resets active template selections.
    temporary = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, SETTINGS_FILE)
