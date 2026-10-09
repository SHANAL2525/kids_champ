from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import Box, BatchItem, TemplateRecord
from .filename_parser import safe_name
from .template_store import get_active_template
from .image_processing import preprocess_artwork_path, classify_layout
from .config import resource_path

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - Pillow-only fallback remains functional
    np = None

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - ordinary placement remains functional
    cv2 = None


def font_candidates(bold: bool = True, unicode_text: bool = False):
    out = []
    if os.name == "nt":
        if unicode_text:
            out += [r"C:\Windows\Fonts\NirmalaB.ttf", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\seguisb.ttf"]
        if bold:
            out += [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\calibrib.ttf"]
        out += [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"]
    out += ["DejaVuSans-Bold.ttf", "arialbd.ttf", "arial.ttf"]
    return out


@lru_cache(maxsize=256)
def _load_font_cached(size: int, unicode_text: bool, bold: bool = True):
    for p in font_candidates(bold, unicode_text):
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            pass
    return ImageFont.load_default()

def load_font(size: int, text: str, bold: bool = True):
    return _load_font_cached(size, any(ord(c) > 127 for c in text), bold)

@lru_cache(maxsize=24)
def _cached_rgba(path: str, mtime_ns: int):
    return Image.open(path).convert("RGBA")

def _load_rgba(path: Path):
    return _cached_rgba(str(path), path.stat().st_mtime_ns).copy()


def fit_font(draw, text, box: Box, start_size: int, min_size: int = 14):
    for size in range(start_size, min_size - 1, -1):
        font = load_font(size, text)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= box.width and h <= box.height:
            return font, w, h, bbox
    font = load_font(min_size, text)
    bbox = draw.textbbox((0, 0), text, font=font)
    return font, bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def _inner_box(box: Box, pad_x: int = 8, pad_y: int = 3) -> Box:
    px = max(0, min(int(pad_x), max(0, box.width // 4)))
    py = max(0, min(int(pad_y), max(0, box.height // 4)))
    return Box(box.x + px, box.y + py, max(1, box.width - 2 * px), max(1, box.height - 2 * py))


def _balanced_lines(draw, text: str, font, max_lines: int) -> list[str]:
    words = text.split()
    if max_lines < 2 or len(words) < 2:
        return [text]
    candidates = []
    for split in range(1, len(words)):
        lines = [" ".join(words[:split]), " ".join(words[split:])]
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        candidates.append((max(widths), abs(widths[0] - widths[1]), lines))
    return min(candidates, key=lambda value: (value[0], value[1]))[2]


def draw_centered(draw, text: str, box: Box, size: int, color: str, max_lines: int = 1):
    """Draw clear centered text with a safe inset so letters never touch/cut at box edges."""
    text = (text or "").upper().strip()
    if not text:
        return
    safe = _inner_box(box, max(6, int(round(box.width * 0.018))), max(2, int(round(box.height * 0.06))))
    if max_lines < 2:
        font, tw, th, bbox = fit_font(draw, text, safe, size, min_size=11)
        x = safe.x + (safe.width - tw) / 2 - bbox[0]
        y = safe.y + (safe.height - th) / 2 - bbox[1]
        draw.text((x, y), text, font=font, fill=color)
        return
    for font_size in range(size, 10, -1):
        font = load_font(font_size, text)
        lines = _balanced_lines(draw, text, font, max_lines)
        rendered = "\n".join(lines)
        spacing = max(2, int(round(font_size * 0.08)))
        bbox = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=spacing, align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= safe.width and th <= safe.height:
            x = safe.x + (safe.width - tw) / 2 - bbox[0]
            y = safe.y + (safe.height - th) / 2 - bbox[1]
            draw.multiline_text((x, y), rendered, font=font, fill=color, spacing=spacing, align="center")
            return
    # Extremely long names still render safely at the minimum size.
    font = load_font(11, text)
    lines = _balanced_lines(draw, text, font, max_lines)
    rendered = "\n".join(lines)
    bbox = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=2, align="center")
    x = safe.x + (safe.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = safe.y + (safe.height - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.multiline_text((x, y), rendered, font=font, fill=color, spacing=2, align="center")


@lru_cache(maxsize=32)
def _load_location_pin(size: int) -> Image.Image:
    path = resource_path("assets/location_pin.png")
    try:
        pin = Image.open(path).convert("RGBA")
        target_h = max(12, int(size))
        target_w = max(10, int(round(pin.width * (target_h / pin.height))))
        return pin.resize((target_w, target_h), Image.Resampling.LANCZOS)
    except Exception:
        # Safe fallback if the asset is missing.
        pin = Image.new("RGBA", (max(12, int(size)), max(12, int(size))), (0,0,0,0))
        d = ImageDraw.Draw(pin)
        s = pin.width; cx=s*.5; cy=s*.35; r=s*.28
        d.polygon([(cx-r*.7, cy+r*.55), (cx+r*.7, cy+r*.55), (cx, s*.96)], fill="#D92D20")
        d.ellipse((cx-r, cy-r, cx+r, cy+r), fill="#FF3B30")
        ir=r*.38; d.ellipse((cx-ir, cy-ir, cx+ir, cy+ir), fill="#FFFFFF")
        return pin


def draw_location(base: Image.Image, draw, text: str, record: TemplateRecord):
    """Render the exact supplied red location-pin asset + auto-fit location text.

    Uses explicit alpha compositing and safe padding so neither the pin point nor text
    can be clipped, including the compact Square template label strip.
    """
    box = record.city_box
    text = (text or "").upper().strip()
    if not text:
        return
    if not record.show_location_pin:
        draw_centered(draw, text, box, record.city_font_size, record.text_color)
        return

    safe = _inner_box(box, max(6, int(round(box.width * 0.018))), max(2, int(round(box.height * 0.06))))
    # Pin should visually track text size, while always fitting inside the city row.
    requested = max(12, int(record.location_pin_size))
    visual_cap = max(12, int(round(record.city_font_size * 1.18)))
    target_h = max(12, min(requested, visual_cap, max(12, safe.height - 2)))
    pin = _load_location_pin(target_h)
    gap = max(4, int(record.location_pin_gap))

    available_text_w = max(20, safe.width - pin.width - gap)
    tbox = Box(0, 0, available_text_w, safe.height)
    font, tw, th, bbox = fit_font(draw, text, tbox, record.city_font_size, min_size=10)
    total = pin.width + gap + tw
    gx = safe.x + max(0.0, (safe.width - total) / 2.0)
    py = safe.y + (safe.height - pin.height) / 2.0

    base.alpha_composite(pin, (int(round(gx)), int(round(py))))
    tx = gx + pin.width + gap - bbox[0]
    ty = safe.y + (safe.height - th) / 2.0 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=record.text_color)



def _foreground_bbox_normalized(artwork: Image.Image):
    """Estimate important content bounds from the border colour on a small proxy."""
    if np is None:
        return None
    sample = artwork.convert("RGB")
    sample.thumbnail((420, 420), Image.Resampling.BILINEAR)
    arr = np.asarray(sample).astype(np.float32)
    h, w = arr.shape[:2]
    if h < 24 or w < 24:
        return None
    band = max(2, int(round(min(w, h) * 0.035)))
    border = np.concatenate((
        arr[:band].reshape(-1, 3), arr[-band:].reshape(-1, 3),
        arr[:, :band].reshape(-1, 3), arr[:, -band:].reshape(-1, 3),
    ))
    background = np.median(border, axis=0)
    border_distance = np.linalg.norm(border - background, axis=1)
    threshold = max(26.0, float(np.percentile(border_distance, 90)) + 10.0)
    mask = np.linalg.norm(arr - background, axis=2) > threshold
    ys, xs = np.where(mask)
    coverage = float(mask.mean())
    if len(xs) < 20 or coverage < 0.008 or coverage > 0.88:
        return None
    return (
        float(xs.min()) / w, float(ys.min()) / h,
        float(xs.max() + 1) / w, float(ys.max() + 1) / h,
    )


def _smart_mode(artwork: Image.Image, box: Box) -> str:
    """Select the automatic frame-aware placement policy.

    Production comparisons show that the admin keeps the cleaned artwork and maps
    its width and height independently to the saved frame, like Photoshop Free
    Transform.  This avoids both letterbox gaps and the second destructive crop
    caused by proportional ``Fill Frame``.  Explicit Full/Fill modes remain
    available for exceptional images.
    """
    del artwork, box
    return "Smart"


def _placement_geometry(
    artwork_size: tuple[int, int],
    box: Box,
    mode: str,
    zoom: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[int, int, int, int, str]:
    """Return transformed width/height and clipped origin for legacy placement."""
    aw, ah = max(1, int(artwork_size[0])), max(1, int(artwork_size[1]))
    effective_mode = mode
    if effective_mode == "Smart":
        # Auto V4 frame-aware placement: at zoom 1 the complete cleaned source is
        # mapped to the exact artwork box.  Width/height may scale independently,
        # matching the measured admin workflow while never exposing an empty band.
        zoom = max(1.0, min(float(zoom), 5.0))
        tw = max(1, int(round(box.width * zoom)))
        th = max(1, int(round(box.height * zoom)))
        x = int(round((box.width - tw) / 2 + float(offset_x)))
        y = int(round((box.height - th) / 2 + float(offset_y)))
        x = max(box.width - tw, min(0, x))
        y = max(box.height - th, min(0, y))
        return tw, th, x, y, effective_mode
    if effective_mode == "Fill Frame":
        base_scale = max(box.width / aw, box.height / ah)
    else:
        base_scale = min(box.width / aw, box.height / ah)
    minimum_zoom = 1.0 if effective_mode == "Fill Frame" else 0.25
    zoom = max(minimum_zoom, min(float(zoom), 5.0))
    scale = base_scale * zoom
    tw = max(1, int(round(aw * scale)))
    th = max(1, int(round(ah * scale)))
    x = int(round((box.width - tw) / 2 + float(offset_x)))
    y = int(round((box.height - th) / 2 + float(offset_y)))
    if effective_mode == "Fill Frame":
        x = max(box.width - tw, min(0, x))
        y = max(box.height - th, min(0, y))
    return tw, th, x, y, effective_mode


def default_transform_quad(
    artwork_size: tuple[int, int],
    box: Box,
    mode: str = "Smart",
    *,
    zoom: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[tuple[float, float], ...]:
    """Convert current placement into frame-normalized Free Transform corners."""
    tw, th, x, y, _ = _placement_geometry(artwork_size, box, mode, zoom, offset_x, offset_y)
    bw, bh = float(max(1, box.width)), float(max(1, box.height))
    return (
        (x / bw, y / bh), ((x + tw) / bw, y / bh),
        ((x + tw) / bw, (y + th) / bh), (x / bw, (y + th) / bh),
    )


def normalize_transform_quad(quad) -> tuple[tuple[float, float], ...] | None:
    """Validate/canonicalize persisted Free Transform coordinates."""
    if not quad or len(quad) != 4:
        return None
    try:
        points = tuple((float(point[0]), float(point[1])) for point in quad)
    except (TypeError, ValueError, IndexError):
        return None
    if not all(-8.0 <= value <= 8.0 for point in points for value in point):
        return None
    # Shoelace area rejects collapsed/near-invisible transforms while allowing
    # affine, skew and perspective quads that extend beyond the frame.
    area = abs(sum(
        points[i][0] * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * points[i][1]
        for i in range(4)
    )) * 0.5
    return points if area >= 0.0001 else None


def _place_free_transform(base: Image.Image, artwork: Image.Image, box: Box, quad) -> bool:
    """Perspective-map all source pixels to a frame-relative destination quad."""
    points = normalize_transform_quad(quad)
    if points is None or cv2 is None or np is None:
        return False
    source = np.asarray(artwork.convert("RGBA"))
    ah, aw = source.shape[:2]
    if aw < 1 or ah < 1:
        return False
    src = np.float32(((0, 0), (max(0, aw - 1), 0), (max(0, aw - 1), max(0, ah - 1)), (0, max(0, ah - 1))))
    dst = np.float32(tuple((x * box.width, y * box.height) for x, y in points))
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        source, matrix, (max(1, box.width), max(1, box.height)),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    canvas = Image.fromarray(warped, mode="RGBA")
    base.alpha_composite(canvas, (box.x, box.y))
    return True


def place_artwork(
    base: Image.Image,
    artwork: Image.Image,
    box: Box,
    mode: str,
    *,
    zoom: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    rotation: int = 0,
    transform_quad=None,
):
    """Place artwork inside a fixed template box.

    The template box never changes here. Per-image editing only changes the artwork
    inside the box: fit mode, zoom, pan and 90-degree rotation.
    """
    artwork = ImageOps.exif_transpose(artwork).convert("RGBA")
    rotation = int(rotation) % 360
    if rotation:
        # Pillow rotates counter-clockwise for positive angles. expand=True keeps all pixels.
        artwork = artwork.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)

    # A saved Free Transform is the final placement authority. Crop/Wrap/Push-Pull
    # have already produced ``artwork``; this maps it non-destructively into the
    # template frame and clips everything outside that frame.
    if transform_quad and _place_free_transform(base, artwork, box, transform_quad):
        return

    effective_mode = mode
    if mode == "Smart":
        effective_mode = _smart_mode(artwork, box)

    tw, th, x, y, effective_mode = _placement_geometry(
        artwork.size, box, effective_mode, zoom, offset_x, offset_y,
    )
    transformed = artwork.resize((tw, th), Image.Resampling.LANCZOS)

    # Full Artwork intentionally keeps every source pixel. Fill any resulting
    # letterbox bands with the artwork's border-paper colour so transparent master
    # openings never appear as black bars in video editors/viewers. Smart already
    # fills the frame exactly and therefore does not need a synthetic background.
    canvas_color = (0, 0, 0, 0)
    if effective_mode == "Fit Full Artwork" and abs(float(zoom) - 1.0) < 1e-6 and not offset_x and not offset_y:
        sample = artwork.convert("RGB")
        sample.thumbnail((300, 300), Image.Resampling.BILINEAR)
        if np is not None and sample.width and sample.height:
            arr = np.asarray(sample)
            band = max(1, int(round(min(sample.size) * 0.04)))
            border = np.concatenate((
                arr[:band].reshape(-1, 3), arr[-band:].reshape(-1, 3),
                arr[:, :band].reshape(-1, 3), arr[:, -band:].reshape(-1, 3),
            ))
            color = np.median(border, axis=0).astype(int)
            canvas_color = (int(color[0]), int(color[1]), int(color[2]), 255)
    # Panning/zooming can never spill outside the saved artwork box.
    canvas = Image.new("RGBA", (box.width, box.height), canvas_color)
    canvas.alpha_composite(transformed, (x, y))
    base.alpha_composite(canvas, (box.x, box.y))

def _render_item_internal(
    item: BatchItem,
    record: TemplateRecord | None = None,
    *,
    preview_max_source: int | None = None,
) -> Image.Image:
    # Manual Wrap is source geometry, but its destination is the selected template
    # artwork frame. Pick that frame FIRST so the four source corners can map to the
    # correct target aspect ratio and remove unwanted frame white-space.
    manual_warp = getattr(item, "manual_warp", None)
    provisional_record = record
    if provisional_record is None and manual_warp:
        # Template routing is a stable per-item decision. Crop/Wrap processing must
        # never silently switch Portrait/Landscape/Square behind the admin's back.
        provisional_record = get_active_template(item.selected_layout)

    warp_target_aspect = None
    if provisional_record is not None and manual_warp:
        box = provisional_record.artwork_box
        warp_target_aspect = box.width / float(max(1, box.height))

    processed_artwork, info = preprocess_artwork_path(
        item.source_path,
        auto_paper=getattr(item, "auto_paper_detect", True),
        source_rotation=int(getattr(item, "artwork_rotation", 0)),
        brightness=int(getattr(item, "brightness", 0)),
        contrast=int(getattr(item, "contrast", 0)),
        color_balance_red=int(getattr(item, "color_balance_red", 0)),
        color_balance_green=int(getattr(item, "color_balance_green", 0)),
        color_balance_blue=int(getattr(item, "color_balance_blue", 0)),
        hue=int(getattr(item, "hue", 0)),
        saturation=int(getattr(item, "saturation", 0)),
        lightness=int(getattr(item, "lightness", 0)),
        preset_name=str(getattr(item, "preset_name", "None") or "None"),
        manual_crop=getattr(item, "manual_crop", None),
        manual_warp=manual_warp,
        stretch_strokes=getattr(item, "stretch_strokes", None),
        preview_max=preview_max_source,
        warp_target_aspect=warp_target_aspect,
    )

    if record is None:
        if manual_warp and provisional_record is not None:
            # Keep the same destination frame used by manual Wrap.
            record = provisional_record
        else:
            # IMPORTANT: preprocessing info is allowed to describe the source, but
            # it is NOT allowed to change the selected production template.
            record = get_active_template(item.selected_layout)

    template = _load_rgba(record.template_image)
    mode = item.artwork_fit_override or record.artwork_fit_mode
    if manual_warp and mode == "Smart":
        # Wrapped output already has the exact frame ratio; preserve all of it.
        mode = "Fit Full Artwork"
    place_artwork(
        template,
        processed_artwork,
        record.artwork_box,
        mode,
        zoom=item.artwork_zoom,
        offset_x=item.artwork_offset_x,
        offset_y=item.artwork_offset_y,
        rotation=0,
        transform_quad=getattr(item, "artwork_transform", None),
    )
    if record.overlay_image and record.overlay_image.exists():
        overlay = _load_rgba(record.overlay_image)
        template.alpha_composite(overlay)
    draw = ImageDraw.Draw(template)
    draw_centered(
        draw, item.child_name, record.name_box, record.name_font_size,
        record.text_color, max_lines=record.name_max_lines,
    )
    draw_location(template, draw, item.city, record)
    return template


def render_item(item: BatchItem, record: TemplateRecord | None = None) -> Image.Image:
    """Full-quality final render used for Download All."""
    return _render_item_internal(item, record, preview_max_source=None)


def render_item_preview(item: BatchItem, record: TemplateRecord | None = None, max_width: int = 1100) -> Image.Image:
    """Fast Compare/Generator preview.

    Source processing is bounded and cached. Final download still calls render_item()
    and therefore keeps full source quality.
    """
    image = _render_item_internal(item, record, preview_max_source=1400)
    if max_width and image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize((max_width, max(1, int(round(image.height * ratio)))), Image.Resampling.LANCZOS)
    return image


def clear_render_caches() -> None:
    """Drop cached template/font assets immediately after an admin saves a template."""
    _cached_rgba.cache_clear()
    _load_font_cached.cache_clear()
    _load_location_pin.cache_clear()

def output_filename(item: BatchItem, used: set[str], extension: str | None = None) -> str:
    extension = (extension or item.source_path.suffix or ".png").lower()
    if not extension.startswith("."):
        extension = "." + extension
    base = (
        f"{safe_name(item.submission_id)}__{safe_name(item.child_name)}__{safe_name(item.city)}"
        if item.submission_id
        else f"{safe_name(item.child_name)}__{safe_name(item.city)}"
    )
    name = base + extension
    n = 2
    while name.lower() in used:
        name = f"{base}_{n}{extension}"
        n += 1
    used.add(name.lower())
    return name
