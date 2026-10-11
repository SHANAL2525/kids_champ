from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image

from .config import TEMPLATE_DIR, CUSTOM_TEMPLATE_DIR, GROUPS_FILE, GROUP_COLORS
from .models import Box, TemplateRecord
from .settings_store import load_settings, save_settings


class LockedTemplateError(RuntimeError):
    pass


_CACHE_SIGNATURE = None
_CACHE_RECORDS: list[TemplateRecord] | None = None


def invalidate_cache() -> None:
    global _CACHE_SIGNATURE, _CACHE_RECORDS
    _CACHE_SIGNATURE = None
    _CACHE_RECORDS = None


def _invalidate_render_cache() -> None:
    try:
        from .image_renderer import clear_render_caches
        clear_render_caches()
    except (ImportError, AttributeError):
        pass


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _signature():
    sig = []
    tracked_names = {"template.json", "template.png", "overlay.png", "thumbnail.png"}
    for p in sorted(path for path in TEMPLATE_DIR.rglob("*") if path.is_file() and path.name in tracked_names):
        try:
            stat = p.stat()
            sig.append((str(p), stat.st_mtime_ns, stat.st_size))
        except OSError:
            pass
    return tuple(sig)


def slugify(value: str) -> str:
    import re
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return value.lower() or "template"


def _asset(folder: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = folder / value
    return p if p.exists() else None


def _record_from_json(path: Path) -> TemplateRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    folder = path.parent
    image = _asset(folder, data.get("template_image"))
    if not image:
        return None
    is_default = bool(data.get("is_default", False))
    group = data.get("group", "Ungrouped") or "Ungrouped"
    # Custom templates are never allowed inside the permanent Defaults group.
    if not is_default and str(group).lower() == "defaults":
        group = "Ungrouped"
    return TemplateRecord(
        key=data.get("id") or path.relative_to(TEMPLATE_DIR).with_suffix("").as_posix(),
        folder=folder,
        json_path=path,
        name=data.get("name", folder.name),
        template_type=data.get("type", "Any"),
        group=group,
        group_color=data.get("group_color", "Blue"),
        template_image=image,
        overlay_image=_asset(folder, data.get("overlay_image")),
        thumbnail_image=_asset(folder, data.get("thumbnail_image")),
        artwork_box=Box.from_dict(data.get("artwork_box", {}), (100, 100, 500, 500)),
        name_box=Box.from_dict(data.get("name_box", {}), (100, 650, 500, 80)),
        city_box=Box.from_dict(data.get("city_box", {}), (100, 740, 500, 60)),
        name_font_size=int(data.get("name_font_size", 53)),
        name_max_lines=max(1, min(2, int(data.get("name_max_lines", 1)))),
        city_font_size=int(data.get("city_font_size", 37)),
        text_color=data.get("text_color", "#10344B"),
        show_location_pin=bool(data.get("show_location_pin", True)),
        location_pin_size=int(data.get("location_pin_size", 38)),
        location_pin_gap=int(data.get("location_pin_gap", 12)),
        artwork_fit_mode=data.get("artwork_fit_mode", "Fill Frame"),
        is_default=is_default,
        locked=bool(data.get("locked", False)),
        artwork_under_template=bool(data.get("artwork_under_template", False)),
    )


def load_templates() -> list[TemplateRecord]:
    global _CACHE_SIGNATURE, _CACHE_RECORDS
    sig = _signature()
    if _CACHE_RECORDS is not None and sig == _CACHE_SIGNATURE:
        return list(_CACHE_RECORDS)
    records: list[TemplateRecord] = []
    for p in sorted(TEMPLATE_DIR.rglob("template.json")):
        rec = _record_from_json(p)
        if rec:
            records.append(rec)
    _CACHE_SIGNATURE = sig
    _CACHE_RECORDS = list(records)
    return records


def get_default(template_type: str) -> TemplateRecord:
    records = [r for r in load_templates() if r.template_type == template_type]
    defaults = [r for r in records if r.is_default]
    if defaults:
        return defaults[0]
    if records:
        return records[0]
    raise RuntimeError(f"No {template_type} template found")


def _active_setting_key(template_type: str) -> str:
    wanted = str(template_type or "Portrait").title()
    mapping = {
        "Portrait": "active_portrait_template",
        "Landscape": "active_landscape_template",
        "Square": "active_square_template",
    }
    if wanted not in mapping:
        raise ValueError(f"Unsupported template type: {template_type}")
    return mapping[wanted]


def _migrate_new_production_defaults(settings: dict, records: list[TemplateRecord]) -> bool:
    """Select the new framed defaults once without overriding custom choices."""
    generation = int(settings.get("template_defaults_generation", 0) or 0)
    if generation >= 2:
        return False
    available = {record.key for record in records}
    replacements = {
        "active_portrait_template": ("portrait", "portrait_current"),
        "active_landscape_template": ("landscape", "landscape_current"),
    }
    for setting_key, (old_key, new_key) in replacements.items():
        active_key = str(settings.get(setting_key, "") or "")
        if new_key in available and active_key in {"", old_key}:
            settings[setting_key] = new_key
    settings["template_defaults_generation"] = 2
    return True


def get_active_template(template_type: str) -> TemplateRecord:
    """Return the admin-selected active template for Portrait/Landscape/Square.

    The three locked Defaults remain safe fallbacks, but a custom template can be
    selected as active for each type and the choice persists across restarts.
    """
    wanted = str(template_type or "Portrait").title()
    setting_key = _active_setting_key(wanted)
    settings = load_settings()
    records = load_templates()
    if _migrate_new_production_defaults(settings, records):
        save_settings(settings)
    active_key = str(settings.get(setting_key, "") or "")
    if active_key:
        rec = next((r for r in records if r.key == active_key and r.template_type in {wanted, "Any"}), None)
        if rec:
            return rec
    fallback = get_default(wanted)
    settings[setting_key] = fallback.key
    save_settings(settings)
    return fallback


def get_active_template_keys() -> dict[str, str]:
    out = {}
    for kind in ("Portrait", "Landscape", "Square"):
        try:
            out[kind] = get_active_template(kind).key
        except Exception:
            out[kind] = ""
    return out


def set_active_template(record_or_key: TemplateRecord | str) -> dict[str, str]:
    records = load_templates()
    record = record_or_key if isinstance(record_or_key, TemplateRecord) else next((r for r in records if r.key == record_or_key), None)
    if record is None:
        raise ValueError("Template not found")
    settings = load_settings()
    if record.template_type in {"Portrait", "Landscape", "Square"}:
        settings[_active_setting_key(record.template_type)] = record.key
    elif record.template_type == "Any":
        settings["active_portrait_template"] = record.key
        settings["active_landscape_template"] = record.key
        settings["active_square_template"] = record.key
    else:
        raise ValueError(f"Unsupported template type: {record.template_type}")
    save_settings(settings)
    return {
        "Portrait": str(settings.get("active_portrait_template", "")),
        "Landscape": str(settings.get("active_landscape_template", "")),
        "Square": str(settings.get("active_square_template", "")),
    }


def save_record(record: TemplateRecord) -> None:
    record.folder.mkdir(parents=True, exist_ok=True)
    data = {
        "id": record.key,
        "name": record.name,
        "type": record.template_type,
        "group": record.group,
        "group_color": record.group_color,
        "template_image": record.template_image.name,
        "overlay_image": record.overlay_image.name if record.overlay_image else None,
        "thumbnail_image": record.thumbnail_image.name if record.thumbnail_image else None,
        "artwork_box": record.artwork_box.to_dict(),
        "name_box": record.name_box.to_dict(),
        "city_box": record.city_box.to_dict(),
        "name_font_size": record.name_font_size,
        "name_max_lines": record.name_max_lines,
        "city_font_size": record.city_font_size,
        "text_color": record.text_color,
        "show_location_pin": record.show_location_pin,
        "location_pin_size": record.location_pin_size,
        "location_pin_gap": record.location_pin_gap,
        "artwork_fit_mode": record.artwork_fit_mode,
        "is_default": record.is_default,
        "locked": record.locked,
        "artwork_under_template": record.artwork_under_template,
    }
    _atomic_write_json(record.json_path, data)
    invalidate_cache()
    _invalidate_render_cache()


def create_custom_template(name: str, template_type: str, image_path: Path, group: str = "Ungrouped", group_color: str = "Blue") -> TemplateRecord:
    if str(group).lower() == "defaults":
        group = "Ungrouped"
        group_color = "Blue"
    stem = slugify(name)
    folder = CUSTOM_TEMPLATE_DIR / stem
    counter = 2
    while folder.exists():
        folder = CUSTOM_TEMPLATE_DIR / f"{stem}_{counter}"
        counter += 1
    folder.mkdir(parents=True, exist_ok=True)
    src = Image.open(image_path).convert("RGBA")
    target = folder / "template.png"
    src.save(target)
    thumb = folder / "thumbnail.png"
    preview = src.copy()
    preview.thumbnail((500, 300), Image.Resampling.LANCZOS)
    preview.save(thumb)
    w, h = src.size
    rec = TemplateRecord(
        key=str(uuid.uuid4()), folder=folder, json_path=folder / "template.json", name=name,
        template_type=template_type, group=group, group_color=group_color,
        template_image=target, overlay_image=None, thumbnail_image=thumb,
        artwork_box=Box(int(w * .10), int(h * .10), int(w * .80), int(h * .58)),
        name_box=Box(int(w * .18), int(h * .72), int(w * .64), int(h * .08)),
        city_box=Box(int(w * .24), int(h * .81), int(w * .52), int(h * .07)),
        artwork_fit_mode="Fit Full Artwork", locked=False, is_default=False,
    )
    save_record(rec)
    return rec


def duplicate_template(record: TemplateRecord) -> TemplateRecord:
    name = f"{record.name} Copy"
    folder = CUSTOM_TEMPLATE_DIR / slugify(name)
    counter = 2
    while folder.exists():
        folder = CUSTOM_TEMPLATE_DIR / f"{slugify(name)}_{counter}"
        counter += 1
    shutil.copytree(record.folder, folder)
    json_path = folder / "template.json"
    dup = _record_from_json(json_path)
    if not dup:
        raise RuntimeError("Could not duplicate template")
    dup.key = str(uuid.uuid4())
    dup.name = name
    dup.is_default = False
    dup.locked = False
    dup.group = "Ungrouped" if record.is_default or record.group.lower() == "defaults" else record.group
    save_record(dup)
    return dup


def delete_template(record: TemplateRecord) -> None:
    if record.locked or record.is_default:
        raise LockedTemplateError(record.name)
    # If this custom template is active, clear the pointer first; the next lookup
    # immediately falls back to the matching locked default.
    settings = load_settings()
    changed = False
    for key in ("active_portrait_template", "active_landscape_template", "active_square_template"):
        if str(settings.get(key, "")) == record.key:
            settings[key] = ""
            changed = True
    if changed:
        save_settings(settings)
    shutil.rmtree(record.folder)
    invalidate_cache()


def set_locked(record: TemplateRecord, locked: bool) -> TemplateRecord:
    # Production default Portrait/Landscape/Square templates are permanently locked.
    record.locked = True if record.is_default else bool(locked)
    save_record(record)
    return record


def set_group(record: TemplateRecord, group: str, group_color: str) -> None:
    if record.is_default:
        return
    if str(group).lower() == "defaults":
        group = "Ungrouped"
        group_color = "Blue"
    record.group = group
    record.group_color = group_color
    save_record(record)


def load_groups() -> list[dict]:
    try:
        raw = json.loads(GROUPS_FILE.read_text(encoding="utf-8")).get("groups", [])
    except Exception:
        raw = []
    # Photoshop-style order: custom groups first, Ungrouped only when needed, permanent Defaults last.
    clean = []
    seen = set()
    defaults_color = "Blue"
    for g in raw:
        name = str(g.get("name", "")).strip()
        if not name:
            continue
        if name.lower() == "defaults":
            defaults_color = g.get("color", "Blue") if g.get("color", "Blue") in GROUP_COLORS else "Blue"
            continue
        if name.lower() == "ungrouped":
            continue
        if name.lower() not in seen:
            clean.append({"name": name, "color": g.get("color", "Blue") if g.get("color", "Blue") in GROUP_COLORS else "Blue"})
            seen.add(name.lower())
    # Ensure custom groups referenced by templates also appear. Custom records wrongly tagged Defaults are treated as Ungrouped.
    has_ungrouped = False
    for r in load_templates():
        if r.is_default:
            continue
        group = r.group or "Ungrouped"
        if group.lower() in {"defaults", "ungrouped"}:
            has_ungrouped = True
            continue
        if group.lower() not in seen:
            clean.append({"name": group, "color": r.group_color if r.group_color in GROUP_COLORS else "Blue"})
            seen.add(group.lower())
    if has_ungrouped:
        clean.append({"name": "Ungrouped", "color": "Blue"})
    clean.append({"name": "Defaults", "color": defaults_color})
    return clean


def save_groups(groups: list[dict]) -> None:
    ordered = []
    seen = set()
    defaults_color = "Blue"
    ungrouped_present = False
    for g in groups:
        name = str(g.get("name", "")).strip()
        if not name:
            continue
        color = g.get("color", "Blue") if g.get("color", "Blue") in GROUP_COLORS else "Blue"
        if name.lower() == "defaults":
            defaults_color = color
            continue
        if name.lower() == "ungrouped":
            ungrouped_present = True
            continue
        if name.lower() in seen:
            continue
        ordered.append({"name": name, "color": color})
        seen.add(name.lower())
    if ungrouped_present:
        ordered.append({"name": "Ungrouped", "color": "Blue"})
    ordered.append({"name": "Defaults", "color": defaults_color})
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(GROUPS_FILE, {"groups": ordered})


def create_group(name: str, color: str) -> dict:
    groups = load_groups()
    existing = {g["name"].lower() for g in groups}
    base = name.strip() or "New Group"
    if base.lower() in {"defaults", "ungrouped"}:
        base = "New Group"
    candidate = base
    n = 2
    while candidate.lower() in existing:
        candidate = f"{base} {n}"
        n += 1
    group = {"name": candidate, "color": color if color in GROUP_COLORS else "Blue"}
    groups.append(group)
    save_groups(groups)
    return group


def rename_group(old_name: str, new_name: str, color: str) -> str:
    old_name = old_name.strip()
    if old_name.lower() == "defaults":
        raise ValueError("Defaults group cannot be renamed")
    groups = load_groups()
    existing = {g["name"].lower() for g in groups if g["name"].lower() != old_name.lower()}
    base = new_name.strip() or old_name
    candidate = base
    n = 2
    while candidate.lower() in existing:
        candidate = f"{base} {n}"
        n += 1
    for g in groups:
        if g["name"].lower() == old_name.lower():
            g["name"] = candidate
            g["color"] = color if color in GROUP_COLORS else g.get("color", "Blue")
    for r in load_templates():
        if r.group.lower() == old_name.lower():
            r.group = candidate
            r.group_color = color if color in GROUP_COLORS else r.group_color
            save_record(r)
    save_groups(groups)
    return candidate


def delete_group(name: str, move_to: str = "Ungrouped") -> None:
    name = name.strip()
    if name.lower() == "defaults":
        raise ValueError("Defaults group cannot be deleted")
    groups = [g for g in load_groups() if g["name"].lower() != name.lower()]
    if move_to and move_to.lower() not in {g["name"].lower() for g in groups}:
        groups.append({"name": move_to, "color": "Blue"})
    target_color = next((g["color"] for g in groups if g["name"].lower() == move_to.lower()), "Blue")
    for r in load_templates():
        if r.group.lower() == name.lower():
            r.group = move_to
            r.group_color = target_color
            save_record(r)
    save_groups(groups)


def reveal_in_file_manager(path: Path) -> None:
    target = path if path.exists() else path.parent
    if os.name == "nt":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])


def duplicate_group(name: str) -> dict:
    """Duplicate a custom group and all templates inside it."""
    clean_name = name.strip()
    if clean_name.lower() in {"defaults", "ungrouped"}:
        raise ValueError("System groups cannot be duplicated")
    groups = load_groups()
    source = next((g for g in groups if g["name"].lower() == clean_name.lower()), None)
    if source is None:
        raise ValueError(f"Group not found: {name}")
    new_group = create_group(f"{source['name']} Copy", source.get("color", "Blue"))
    for record in list(load_templates()):
        if not record.is_default and record.group.lower() == clean_name.lower():
            dup = duplicate_template(record)
            set_group(dup, new_group["name"], new_group.get("color", "Blue"))
    return new_group


def set_group_locked(name: str, locked: bool) -> int:
    """Lock/unlock all custom templates in a custom group."""
    clean_name = name.strip()
    if clean_name.lower() in {"defaults", "ungrouped"}:
        return 0
    count = 0
    for record in list(load_templates()):
        if not record.is_default and record.group.lower() == clean_name.lower():
            set_locked(record, locked)
            count += 1
    return count
