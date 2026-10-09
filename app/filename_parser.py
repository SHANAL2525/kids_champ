from __future__ import annotations

import re
from pathlib import Path
from PIL import Image, ImageOps
from .image_processing import detect_routing_layout_path


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"[\x00-\x1f]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:100].rstrip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if value.upper() in reserved:
        value = "_" + value
    return value or "Untitled"


def strip_image_extensions(value: str) -> str:
    cleaned = value.strip()
    pattern = re.compile(r"(?i)\.(?:jpe?g|png|webp|bmp|tiff?)$")
    while pattern.search(cleaned):
        cleaned = pattern.sub("", cleaned).strip()
    return cleaned


def clean_metadata(value: str) -> str:
    value = strip_image_extensions(value).replace("_", " ")
    return re.sub(r"\s+", " ", value).strip(" ._-")


def looks_like_submission_id(value: str) -> bool:
    token = value.strip()
    return bool(token and re.fullmatch(r"(?i)[a-z-]*\d[a-z0-9-]*", token))


def parse_filename(filename: str) -> tuple[str, str, str, str]:
    stem = strip_image_extensions(Path(filename).stem).strip(" ._-")
    if not stem:
        raise ValueError("Filename is empty")

    if "__" in stem:
        parts = [p.strip() for p in stem.split("__")]
        if len(parts) == 3 and all(parts):
            return clean_metadata(parts[0]), clean_metadata(parts[1]), clean_metadata(parts[2]), "ID__Name__City"
        if len(parts) == 2 and all(parts):
            return "", clean_metadata(parts[0]), clean_metadata(parts[1]), "Name__City"
        raise ValueError("Use Name__City or ID__Name__City")

    tokens = [t.strip() for t in stem.split("_") if t.strip()]
    if len(tokens) < 2:
        raise ValueError("Use Name_City.jpg or ID_Name_City.jpg")
    if len(tokens) >= 3 and looks_like_submission_id(tokens[0]):
        return clean_metadata(tokens[0]), clean_metadata("_".join(tokens[1:-1])), clean_metadata(tokens[-1]), "ID_Name_City"
    return "", clean_metadata("_".join(tokens[:-1])), clean_metadata(tokens[-1]), "Name_City"


def detect_layout(image_path: str | Path) -> str:
    return detect_routing_layout_path(image_path)


def detect_layout_info(image_path: str | Path) -> tuple[str, dict]:
    """Return the Auto V4 route and its user-visible confidence metadata."""
    from .image_processing import detect_routing_layout_path_info
    return detect_routing_layout_path_info(image_path)
