from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageOps

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # optional runtime fallback
    cv2 = None
    np = None

from .config import resource_path

PRESET_DIR = resource_path("presets")


def classify_layout(width: int, height: int) -> str:
    """Classify actual artwork geometry into Portrait, Landscape, or Square.

    Square is intentionally narrow. Many phone photos are nearly square even when
    the real paper/artwork inside them is clearly portrait or landscape. Routing
    therefore uses :func:`detect_routing_layout` below instead of trusting this
    raw-photo ratio by itself.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    ratio = width / float(height)
    # Processed artwork/content keeps the established production Square band.
    # Raw upload routing uses the narrower helper below so mildly rectangular
    # camera files still select Portrait/Landscape reliably.
    if 0.83 <= ratio <= 1.20:
        return "Square"
    return "Portrait" if height > width else "Landscape"


def classify_upload_layout(width: int, height: int) -> str:
    """Classify the uploaded canvas by size with a narrow Square tolerance."""
    width = max(1, int(width))
    height = max(1, int(height))
    ratio = width / float(height)
    if 0.94 <= ratio <= 1.06:
        return "Square"
    return "Portrait" if height > width else "Landscape"


def _dominant_foreground_layout(image: Image.Image) -> tuple[str | None, dict]:
    """Find a strong central artwork region without cropping the source.

    This is used ONLY for template routing. It is deliberately safer than auto
    crop: if a phone photo is almost square but contains a clear portrait drawing
    on a plain border/background, we can route it to Portrait without removing a
    single pixel from the child's upload.
    """
    if cv2 is None or np is None:
        return None, {"confidence": 0.0}
    working, _ = _bounded_copy(image, 900)
    rgb = np.asarray(working.convert("RGB"))
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    if h < 40 or w < 40:
        return None, {"confidence": 0.0}

    band = max(5, int(round(min(h, w) * 0.045)))
    border = np.concatenate([
        lab[:band, :, :].reshape(-1, 3),
        lab[-band:, :, :].reshape(-1, 3),
        lab[:, :band, :].reshape(-1, 3),
        lab[:, -band:, :].reshape(-1, 3),
    ], axis=0)
    median = np.median(border, axis=0)
    border_dist = np.linalg.norm(border - median, axis=1)
    threshold = max(18.0, float(np.percentile(border_dist, 88)) + 7.0)
    dist = np.linalg.norm(lab - median, axis=2)
    mask = (dist > threshold).astype(np.uint8) * 255

    k = max(3, int(round(min(h, w) * 0.008)))
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 30 or bh < 30:
            continue
        bbox_area = float(bw * bh)
        coverage = bbox_area / float(max(1, w * h))
        if coverage < 0.16 or coverage > 0.90:
            continue
        fill = float(cv2.contourArea(contour)) / max(1.0, bbox_area)
        cx = x + bw / 2.0
        cy = y + bh / 2.0
        center_score = 1.0 - (
            abs(cx - w / 2.0) / max(1.0, w / 2.0)
            + abs(cy - h / 2.0) / max(1.0, h / 2.0)
        ) / 2.0
        if fill < 0.55 or center_score < 0.65:
            continue
        score = coverage * (0.58 + 0.42 * fill) * (0.70 + 0.30 * center_score)
        if best is None or score > best[0]:
            best = (score, bw, bh, coverage, fill, center_score)

    if best is None:
        return None, {"confidence": 0.0}
    score, bw, bh, coverage, fill, center_score = best
    layout = classify_layout(bw, bh)
    confidence = min(1.0, score / 0.45)
    return layout, {
        "confidence": confidence,
        "coverage": coverage,
        "fill": fill,
        "center_score": center_score,
        "bbox_size": (int(bw), int(bh)),
    }


def detect_routing_layout(image: Image.Image) -> tuple[str, dict]:
    """Auto V4: route from the detected artwork instead of the camera canvas.

    The same two-stage geometry engine used by final rendering is run on a bounded
    copy.  Content geometry wins when reliable; then page geometry; raw dimensions
    are only the final fallback.  The uploaded source is never modified.
    """
    original = ImageOps.exif_transpose(image).convert("RGBA")
    raw_layout = classify_upload_layout(original.width, original.height)
    raw_ratio = original.width / float(max(1, original.height))
    raw_confidence = 0.72 if raw_layout == "Square" else min(0.99, 0.86 + abs(raw_ratio - 1.0) * 0.15)
    candidates = [{"layout": raw_layout, "source": "size", "confidence": raw_confidence}]
    best = candidates[0]
    geometry_info: dict = {}

    # Clear Portrait/Landscape dimensions are authoritative. Content/page
    # analysis is reserved for near-square camera canvases where size alone is
    # genuinely ambiguous.
    if raw_layout == "Square" and cv2 is not None and np is not None:
        try:
            proxy, _ = _bounded_copy(original, 900)
            cleaned, geometry_info = auto_detect_document(proxy, max_detect_side=900)
            content_layout = str(geometry_info.get("content_layout") or "")
            content_confidence = float(geometry_info.get("content_confidence", 0.0))
            if content_layout and content_confidence >= 0.52:
                candidate = {
                    "layout": content_layout,
                    "source": "content",
                    "confidence": min(0.995, max(0.74, content_confidence)),
                }
                candidates.append(candidate)
            elif geometry_info.get("page_layout"):
                page_confidence = float(geometry_info.get("page_confidence", geometry_info.get("confidence", 0.0)))
                if page_confidence >= 0.66:
                    candidate = {
                        "layout": str(geometry_info["page_layout"]),
                        "source": "page",
                        "confidence": min(0.97, max(0.70, page_confidence)),
                    }
                    candidates.append(candidate)
            elif geometry_info.get("detected"):
                candidate = {
                    "layout": classify_layout(cleaned.width, cleaned.height),
                    "source": "geometry",
                    "confidence": 0.74,
                }
                candidates.append(candidate)
        except Exception as exc:
            geometry_info = {"routing_error": str(exc)}

    best = max(candidates, key=lambda candidate: float(candidate["confidence"]))

    confidence = float(best["confidence"])
    info = {
        "version": "auto-v4",
        "raw_layout": raw_layout,
        "raw_ratio": raw_ratio,
        "layout": str(best["layout"]),
        "source": str(best["source"]),
        "confidence": confidence,
        "candidates": candidates,
        "needs_check": confidence < 0.70,
        "geometry": geometry_info,
    }
    return str(best["layout"]), info


def detect_routing_layout_path(path: str | Path) -> str:
    return detect_routing_layout_path_info(path)[0]


@lru_cache(maxsize=512)
def _detect_routing_layout_path_cached(path_string: str, mtime_ns: int, size: int) -> tuple[str, dict]:
    del mtime_ns, size  # values intentionally participate in the cache key
    with Image.open(path_string) as image:
        layout, info = detect_routing_layout(image)
    return layout, info


def detect_routing_layout_path_info(path: str | Path) -> tuple[str, dict]:
    path = Path(path)
    stat = path.stat()
    layout, info = _detect_routing_layout_path_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    return layout, dict(info)


# Performance design:
# - Detection runs on a bounded working copy instead of the full phone photo.
# - Geometry results are cached by file + crop/wrap state.
# - Preview can use a smaller cached geometry stage; Download stays full quality.


def _pil_to_cv(image: Image.Image):
    if cv2 is None or np is None:
        return None
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba)
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)


def _cv_to_pil(image) -> Image.Image:
    rgba = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    return Image.fromarray(rgba)


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _warp_from_quad(image, quad, *, target_aspect: float | None = None):
    """Perspective-correct a source quadrilateral.

    When ``target_aspect`` is supplied (manual Wrap), the four selected source
    corners are mapped to that aspect ratio. This is the manual "fit artwork to
    template frame" behavior: Crop removes unwanted source area first, then Wrap
    can make the kept artwork meet the template artwork-frame edges without a
    leftover white gap.
    """
    rect = _order_points(np.asarray(quad, dtype="float32"))
    tl, tr, br, bl = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    natural_w = max(1.0, float(max(width_a, width_b)))
    natural_h = max(1.0, float(max(height_a, height_b)))

    if target_aspect and float(target_aspect) > 0.05:
        # Preserve approximate pixel area while changing only the output rectangle
        # ratio. This avoids huge upscales and keeps final quality predictable.
        aspect = max(0.08, min(12.0, float(target_aspect)))
        area = max(1.0, natural_w * natural_h)
        max_w = max(1, int(round((area * aspect) ** 0.5)))
        max_h = max(1, int(round(max_w / aspect)))
    else:
        max_w = max(1, int(round(natural_w)))
        max_h = max(1, int(round(natural_h)))

    dst = np.array(
        [[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(
        image,
        matrix,
        (max_w, max_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _find_paper_quad_cv(rgb):
    """Return a conservative paper/page candidate plus confidence metadata.

    The old detector preferred the strongest rectangle, which could be the drawing
    itself. This version only accepts large, page-like, centered rectangles and
    reports enough metadata for a second safety check in ``auto_detect_document``.
    """
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    edge = cv2.Canny(blur, 45, 145)
    edge = cv2.dilate(edge, None, iterations=2)
    edge = cv2.erode(edge, None, iterations=1)
    _, threshold = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    candidates = []
    for mask in (edge, threshold, cv2.bitwise_not(threshold)):
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = float(cv2.contourArea(contour))
            coverage = area / float(max(1, w * h))
            # A safe auto page crop must be a substantial part of the photo.
            if coverage < 0.32:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.018 * perimeter, True)
            pts = approx.reshape(-1, 2)
            if len(pts) == 4 and cv2.isContourConvex(approx):
                quad = pts.astype("float32")
            else:
                quad = cv2.boxPoints(cv2.minAreaRect(contour)).astype("float32")

            rect = _order_points(quad)
            quad_area = abs(float(cv2.contourArea(rect.reshape(-1, 1, 2))))
            if quad_area <= 1:
                continue
            rectangularity = max(0.0, min(1.0, area / quad_area))
            box_w = max(np.linalg.norm(rect[1] - rect[0]), np.linalg.norm(rect[2] - rect[3]))
            box_h = max(np.linalg.norm(rect[3] - rect[0]), np.linalg.norm(rect[2] - rect[1]))
            if box_w < 30 or box_h < 30:
                continue
            aspect = max(box_w, box_h) / max(1.0, min(box_w, box_h))
            if aspect > 4.2:
                continue

            center = rect.mean(axis=0)
            dx = abs(float(center[0]) - w / 2.0) / max(1.0, w / 2.0)
            dy = abs(float(center[1]) - h / 2.0) / max(1.0, h / 2.0)
            center_score = max(0.0, 1.0 - (dx + dy) / 2.0)

            # Prefer page-scale objects, not small inner drawing rectangles.
            coverage_score = max(0.0, min(1.0, (coverage - 0.32) / 0.55))
            score = 0.46 * coverage_score + 0.34 * rectangularity + 0.20 * center_score
            candidates.append((score, rect, {
                "coverage": coverage,
                "rectangularity": rectangularity,
                "center_score": center_score,
            }))

    if not candidates:
        return None, {"confidence": 0.0, "reason": "no-page-candidate"}
    candidates.sort(key=lambda x: x[0], reverse=True)
    score, rect, meta = candidates[0]
    meta = dict(meta)
    meta["confidence"] = float(score)
    return rect, meta


def _bounded_copy(image: Image.Image, max_side: int | None) -> tuple[Image.Image, float]:
    working = ImageOps.exif_transpose(image).convert("RGBA")
    if not max_side or max(working.size) <= max_side:
        return working, 1.0
    scale = max_side / float(max(working.size))
    size = (max(1, int(round(working.width * scale))), max(1, int(round(working.height * scale))))
    return working.resize(size, Image.Resampling.LANCZOS), scale


def _expand_quad_safely(quad, image_w: int, image_h: int, padding_ratio: float = 0.025):
    """Expand a detected page quad slightly so pencil strokes are not clipped."""
    pts = np.asarray(quad, dtype="float32").copy()
    center = pts.mean(axis=0)
    expanded = center + (pts - center) * (1.0 + max(0.0, float(padding_ratio)) * 2.0)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, max(0, image_w - 1))
    expanded[:, 1] = np.clip(expanded[:, 1], 0, max(0, image_h - 1))
    return expanded.astype("float32")


def _page_edge_light_score(rgb, quad) -> float:
    """Estimate whether the candidate looks like paper near its inside edges.

    This is intentionally only a safety signal. Dark/colored paper can still pass
    when the rectangle occupies most of the photo, but a small inner drawing box is
    much less likely to be auto-cropped.
    """
    try:
        warped = _warp_from_quad(cv2.cvtColor(rgb, cv2.COLOR_BGR2BGRA), quad)
        bgr = cv2.cvtColor(warped, cv2.COLOR_BGRA2BGR)
        h, w = bgr.shape[:2]
        if h < 10 or w < 10:
            return 0.0
        band = max(2, int(round(min(h, w) * 0.06)))
        strips = np.concatenate([
            bgr[:band, :, :].reshape(-1, 3),
            bgr[-band:, :, :].reshape(-1, 3),
            bgr[:, :band, :].reshape(-1, 3),
            bgr[:, -band:, :].reshape(-1, 3),
        ], axis=0)
        hsv = cv2.cvtColor(strips.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        # white/light paper: high value and relatively low saturation
        light = (hsv[:, 2] >= 175) & (hsv[:, 1] <= 100)
        return float(light.mean()) if light.size else 0.0
    except Exception:
        return 0.0


def _find_light_page_bbox_cv(bgr) -> tuple[tuple[float, float, float, float] | None, dict]:
    """Find an axis-aligned light sheet when its outer edges touch the photo.

    Contour-only document detection often calls the whole camera image a rectangle
    when a sheet reaches the left/right edges. This fallback isolates the connected
    light, low-chroma page and removes table/floor strips without perspective warp.
    """
    h, w = bgr.shape[:2]
    if h < 40 or w < 40:
        return None, {"confidence": 0.0}
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    value_floor = max(145, int(np.percentile(value, 42)))
    mask = ((value >= value_floor) & (saturation <= 105)).astype(np.uint8) * 255
    k = max(5, int(round(min(h, w) * 0.025)))
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for label in range(1, count):
        x, y, bw, bh, area = [int(v) for v in stats[label]]
        bbox_area = float(max(1, bw * bh))
        coverage = bbox_area / float(max(1, w * h))
        fill = area / bbox_area
        if coverage < 0.28 or fill < 0.52:
            continue
        cx, cy = centroids[label]
        center_score = max(0.0, 1.0 - (abs(cx - w / 2) / max(1, w / 2) + abs(cy - h / 2) / max(1, h / 2)) / 2)
        if center_score < 0.48:
            continue
        score = 0.46 * min(1.0, coverage / 0.78) + 0.34 * fill + 0.20 * center_score
        if best is None or score > best[0]:
            best = (score, x, y, bw, bh, coverage, fill, center_score)
    if best is None:
        return None, {"confidence": 0.0}
    score, x, y, bw, bh, coverage, fill, center_score = best
    pad = max(2, int(round(min(h, w) * 0.012)))
    x0=max(0,x-pad); y0=max(0,y-pad); x1=min(w,x+bw+pad); y1=min(h,y+bh+pad)
    # A bright region inside a colourful drawing is not necessarily a sheet of
    # paper.  Compare it with the pixels that would be removed.  Real photographed
    # paper is normally light/low-chroma and visibly separated from its table,
    # fabric or floor; this guard prevents sky, sand and coloured artwork bands
    # from being destructively selected as a page.
    inside = np.zeros((h, w), dtype=bool)
    inside[y0:y1, x0:x1] = True
    outside = ~inside
    inside_value = float(np.median(value[inside])) if inside.any() else 0.0
    inside_saturation = float(np.median(saturation[inside])) if inside.any() else 255.0
    if outside.any():
        outside_value = float(np.median(value[outside]))
        outside_saturation = float(np.median(saturation[outside]))
    else:
        outside_value = inside_value
        outside_saturation = inside_saturation
    value_separation = inside_value - outside_value
    saturation_separation = outside_saturation - inside_saturation
    paper_colour = inside_value >= 170.0 and inside_saturation <= 70.0
    outside_separated = value_separation >= 18.0 or saturation_separation >= 18.0
    return (x0/w, y0/h, x1/w, y1/h), {
        "confidence": float(min(0.98, score)), "coverage": coverage,
        "fill": fill, "center_score": center_score,
        "retained_area": ((x1-x0)*(y1-y0))/float(max(1,w*h)),
        "inside_value": inside_value, "inside_saturation": inside_saturation,
        "value_separation": value_separation,
        "saturation_separation": saturation_separation,
        "paper_colour": paper_colour, "outside_separated": outside_separated,
    }


def _content_bbox_cv(bgr) -> tuple[tuple[float, float, float, float] | None, dict]:
    """Locate meaningful artwork using a border-derived paper colour model.

    The detector combines colour distance, locally-normalised contrast and edges.
    It therefore sees pale pencil/crayon marks but does not assume that paper is
    pure white.  Coordinates are normalized so the same result maps safely from a
    bounded detection proxy to the full-resolution source.
    """
    h, w = bgr.shape[:2]
    if h < 40 or w < 40:
        return None, {"reason": "content-image-too-small", "confidence": 0.0}

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    short = float(min(h, w))
    band = max(5, int(round(short * 0.045)))
    inset = max(2, int(round(short * 0.030)))
    # Keep corner samples compact. A large patch often reaches the drawing itself
    # and incorrectly teaches the detector that artwork colours are background.
    corner = max(band, int(round(short * 0.060)))
    corner_regions = [
        lab[inset:inset + corner, inset:inset + corner],
        lab[inset:inset + corner, w - inset - corner:w - inset],
        lab[h - inset - corner:h - inset, inset:inset + corner],
        lab[h - inset - corner:h - inset, w - inset - corner:w - inset],
    ]
    corner_medians = [np.median(region.reshape(-1, 3), axis=0) for region in corner_regions]

    # Select the largest mutually-similar corner family. Drawings can touch one or
    # even two corners; the remaining corners still describe the paper/background.
    best_group: list[int] = []
    for i, median in enumerate(corner_medians):
        group = [j for j, other in enumerate(corner_medians) if np.linalg.norm(median - other) <= 30.0]
        if len(group) > len(best_group):
            best_group = group
    if not best_group:
        best_group = [0]
    background_samples = np.concatenate([corner_regions[i].reshape(-1, 3) for i in best_group], axis=0)
    background = np.median(background_samples, axis=0)
    # Noise is measured around each corner's own median. Differences between clean
    # corners are usually illumination gradients, not paper texture.
    noise_samples = np.concatenate([
        np.linalg.norm(corner_regions[i].reshape(-1, 3) - corner_medians[i], axis=1)
        for i in best_group
    ])
    background_noise = float(np.percentile(noise_samples, 88))
    corner_agreement = len(best_group) / 4.0
    background_confidence = max(0.15, min(1.0, 0.25 + corner_agreement * 0.72 - background_noise / 95.0))

    # When at least three corners look like light, low-chroma paper, use a bilinear
    # colour field. This follows warm/cool lighting and shadows across the page and
    # stops those gradients being mistaken for artwork.
    paper_corner = [
        float(m[0]) >= 145.0 and float(np.linalg.norm(m[1:] - 128.0)) <= 42.0
        for m in corner_medians
    ]
    if sum(paper_corner) >= 3:
        safe_medians = [m.copy() for m in corner_medians]
        paper_median = np.median(np.stack([m for m, ok in zip(corner_medians, paper_corner) if ok]), axis=0)
        for i, ok in enumerate(paper_corner):
            if not ok:
                safe_medians[i] = paper_median.copy()
        tl, tr, bl, br = safe_medians
        xx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :, None]
        yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
        top = tl[None, None, :] * (1.0 - xx) + tr[None, None, :] * xx
        bottom = bl[None, None, :] * (1.0 - xx) + br[None, None, :] * xx
        background_field = top * (1.0 - yy) + bottom * yy
        global_distance = np.linalg.norm(lab - background_field, axis=2)
        paper_model = "bilinear-corners"
    else:
        global_distance = np.linalg.norm(lab - background, axis=2)
        paper_model = "dominant-corners"
    global_threshold = max(9.0, min(34.0, background_noise * 1.30 + 4.0))

    # A broad local illumination estimate removes yellow/grey lighting gradients.
    sigma = max(7.0, short * 0.055)
    local_background = cv2.GaussianBlur(lab, (0, 0), sigmaX=sigma, sigmaY=sigma)
    local_distance = np.linalg.norm(lab - local_background, axis=2)
    border_local = np.concatenate([
        local_distance[inset:inset + band, :].ravel(), local_distance[h - inset - band:h - inset, :].ravel(),
        local_distance[:, inset:inset + band].ravel(), local_distance[:, w - inset - band:w - inset].ravel(),
    ])
    local_noise = float(np.percentile(border_local, 82))
    local_threshold = max(5.5, min(24.0, local_noise * 1.25 + 2.5))

    paper_l = background_field[:, :, 0] if paper_model == "bilinear-corners" else float(background[0])
    local_marks = local_distance > local_threshold

    # Normalize lighting before looking for graphite/paint strokes. A broad blur is
    # the local paper level; smooth phone-camera shadows disappear, while actual
    # marks remain. Saturation independently captures pale coloured crayons.
    illumination = cv2.GaussianBlur(lab[:, :, 0], (0, 0), sigmaX=max(9.0, short * 0.085))
    local_darkness = illumination - lab[:, :, 0]
    border_darkness = np.concatenate([
        local_darkness[inset:inset + band, :].ravel(), local_darkness[h - inset - band:h - inset, :].ravel(),
        local_darkness[:, inset:inset + band].ravel(), local_darkness[:, w - inset - band:w - inset].ravel(),
    ])
    darkness_threshold = max(4.5, min(18.0, float(np.percentile(border_darkness, 88)) + 3.0))
    dark_marks = local_darkness > darkness_threshold

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)
    border_saturation = np.concatenate([
        saturation[inset:inset + band, :].ravel(), saturation[h - inset - band:h - inset, :].ravel(),
        saturation[:, inset:inset + band].ravel(), saturation[:, w - inset - band:w - inset].ravel(),
    ])
    saturation_threshold = max(22.0, min(90.0, float(np.percentile(border_saturation, 78)) + 8.0))
    colour_marks = (saturation > saturation_threshold) & (global_distance > max(7.0, global_threshold * 0.48))

    # Low-threshold edges rescue very faint pencil lines. Keep them only when they
    # neighbour another content signal, preventing paper texture from dominating.
    median_gray = float(np.median(gray))
    low = max(12, int(round(median_gray * 0.20)))
    high = max(low + 18, int(round(median_gray * 0.52)))
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), low, high) > 0
    edge_neighbour = cv2.dilate(edges.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    texture_window = max(7, int(round(short * 0.018)))
    if texture_window % 2 == 0:
        texture_window += 1
    gray_float = gray.astype(np.float32)
    local_mean = cv2.boxFilter(gray_float, -1, (texture_window, texture_window), normalize=True)
    local_square = cv2.boxFilter(gray_float * gray_float, -1, (texture_window, texture_window), normalize=True)
    local_std = np.sqrt(np.maximum(0.0, local_square - local_mean * local_mean))
    border_std = np.concatenate([
        local_std[inset:inset + band, :].ravel(), local_std[h - inset - band:h - inset, :].ravel(),
        local_std[:, inset:inset + band].ravel(), local_std[:, w - inset - band:w - inset].ravel(),
    ])
    texture_threshold = max(2.2, min(13.0, float(np.percentile(border_std, 70)) + 1.2))
    texture_marks = local_std > texture_threshold
    # Local contrast by itself also sees paper wrinkles. Requiring a nearby edge
    # retains pencil/crayon strokes without turning the whole sheet into foreground.
    structural_edges = edges & (
        (global_distance > max(6.0, global_threshold * 0.42))
        | (local_distance > max(3.5, local_threshold * 0.62))
    )
    colour_structure = colour_marks & (texture_marks | edge_neighbour | (saturation > saturation_threshold * 1.55))
    dark_structure = dark_marks & (texture_marks | edge_neighbour)
    seed = colour_structure | dark_structure | (local_marks & edge_neighbour) | structural_edges
    neighbour = cv2.dilate(seed.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0
    mask = (seed | (edges & neighbour)).astype(np.uint8) * 255

    close_size = max(3, int(round(short * 0.006)))
    if close_size % 2 == 0:
        close_size += 1
    close_size = min(close_size, 11)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    # Ignore the outermost detection seam. Content that genuinely reaches an edge
    # continues inside this guard and the later safety padding restores that edge.
    guard = max(3, int(round(short * 0.022)))
    mask[:guard, :] = 0; mask[-guard:, :] = 0
    mask[:, :guard] = 0; mask[:, -guard:] = 0

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    selected = np.zeros((h, w), dtype=np.uint8)
    total_pixels = float(max(1, w * h))
    min_area = max(10, int(round(total_pixels * 0.000025)))
    largest_area = max([int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, count)] or [0])
    kept = 0
    for label in range(1, count):
        x, y, bw, bh, area = [int(v) for v in stats[label]]
        if area < min_area:
            continue
        # Reject only border-length hairlines (paper edge / compression seam), not
        # small isolated stars or writing that may belong to the child's drawing.
        touches = x <= guard + 1 or y <= guard + 1 or x + bw >= w - guard - 1 or y + bh >= h - guard - 1
        fill_ratio = area / float(max(1, bw * bh))
        border_line = touches and (
            ((bw >= 0.62 * w or bh >= 0.62 * h) and fill_ratio < 0.14)
            or (bw >= 0.72 * w and bh <= max(7, guard // 2))
            or (bh >= 0.72 * h and bw <= max(7, guard // 2))
        )
        border_speck = touches and area < max(min_area * 3, int(round(largest_area * 0.012)))
        if border_line or border_speck:
            continue
        selected[labels == label] = 255
        kept += 1

    ys, xs = np.where(selected > 0)
    foreground_coverage = float(len(xs)) / total_pixels
    if kept == 0 or len(xs) < min_area or foreground_coverage < 0.00020:
        return None, {
            "reason": "no-meaningful-content", "confidence": 0.0,
            "background_confidence": background_confidence,
        }

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    content_w, content_h = x1 - x0, y1 - y0

    # Five percent content padding plus a small image-relative safety floor keeps
    # isolated strokes and avoids a visually tight crop.
    pad_x = max(3, int(round(content_w * 0.05)), int(round(w * 0.010)))
    pad_y = max(3, int(round(content_h * 0.05)), int(round(h * 0.010)))
    x0 = max(0, x0 - pad_x); x1 = min(w, x1 + pad_x)
    y0 = max(0, y0 - pad_y); y1 = min(h, y1 + pad_y)
    if x0 <= int(w * 0.018): x0 = 0
    if y0 <= int(h * 0.018): y0 = 0
    if x1 >= int(w * 0.982): x1 = w
    if y1 >= int(h * 0.982): y1 = h

    retained = ((x1 - x0) * (y1 - y0)) / total_pixels
    signal_confidence = min(1.0, 0.42 + (foreground_coverage ** 0.5) * 1.45)
    confidence = max(0.0, min(0.99, 0.58 * background_confidence + 0.42 * signal_confidence))
    bbox = (x0 / w, y0 / h, x1 / w, y1 / h)
    content_layout = classify_layout(x1 - x0, y1 - y0)
    removable = 1.0 - retained
    apply_crop = confidence >= 0.50 and retained >= 0.012 and removable >= 0.025
    return bbox, {
        "confidence": confidence,
        "background_confidence": background_confidence,
        "background_noise": background_noise,
        "background_model": paper_model,
        "foreground_coverage": foreground_coverage,
        "retained_area": retained,
        "components": kept,
        "layout": content_layout,
        "apply_crop": apply_crop,
    }


def _auto_crop_content(image: Image.Image, *, max_detect_side: int = 1200) -> tuple[Image.Image, dict]:
    """Crop blank paper/photo margins while keeping the original source untouched."""
    original = image.convert("RGBA")
    proxy, _ = _bounded_copy(original, max_detect_side)
    bgr = cv2.cvtColor(_pil_to_cv(proxy), cv2.COLOR_BGRA2BGR)
    bbox, meta = _content_bbox_cv(bgr)
    if bbox is None:
        return original, meta
    meta = dict(meta)
    meta["bbox"] = bbox
    if not meta.get("apply_crop"):
        meta["reason"] = "content-already-fills-image"
        return original, meta
    l, t, r, b = bbox
    crop_box = (
        max(0, int(round(l * original.width))),
        max(0, int(round(t * original.height))),
        min(original.width, max(1, int(round(r * original.width)))),
        min(original.height, max(1, int(round(b * original.height)))),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        meta.update({"apply_crop": False, "reason": "invalid-content-crop"})
        return original, meta
    meta["size"] = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
    return original.crop(crop_box), meta


def auto_detect_document(image: Image.Image, *, max_detect_side: int = 1400) -> tuple[Image.Image, dict]:
    """Auto V4 two-stage paper correction plus content-aware blank-margin crop."""
    original = ImageOps.exif_transpose(image).convert("RGBA")
    working = original
    info = {
        "detected": False,
        "safe_auto": True,
        "version": "auto-v4",
        "layout": classify_layout(original.width, original.height),
    }
    if cv2 is None or np is None:
        info["reason"] = "opencv-not-installed"
        return original, info

    detect_img, scale = _bounded_copy(original, max_detect_side)
    detect_cv = _pil_to_cv(detect_img)
    rgb = cv2.cvtColor(detect_cv, cv2.COLOR_BGRA2BGR)
    quad_small, meta = _find_paper_quad_cv(rgb)
    if quad_small is not None:
        light_score = _page_edge_light_score(rgb, quad_small)
        coverage = float(meta.get("coverage", 0.0))
        rectangularity = float(meta.get("rectangularity", 0.0))
        center_score = float(meta.get("center_score", 0.0))
        base_conf = float(meta.get("confidence", 0.0))
        rect = _order_points(quad_small)
        candidate_w = max(np.linalg.norm(rect[1] - rect[0]), np.linalg.norm(rect[2] - rect[3]))
        candidate_h = max(np.linalg.norm(rect[3] - rect[0]), np.linalg.norm(rect[2] - rect[1]))
        page_layout = classify_layout(int(round(candidate_w)), int(round(candidate_h)))
        page_confidence = min(1.0, 0.65 * base_conf + 0.35 * max(light_score, min(1.0, coverage)))
        info.update({
            "page_layout": page_layout,
            "page_confidence": page_confidence,
            "coverage": coverage,
            "edge_light_score": light_score,
            "rectangularity": rectangularity,
            "center_score": center_score,
        })

        paper_like = coverage >= 0.42 and rectangularity >= 0.82 and center_score >= 0.46 and light_score >= 0.42
        huge_page = coverage >= 0.74 and rectangularity >= 0.87 and center_score >= 0.52
        if (paper_like or huge_page) and page_confidence >= 0.68:
            info["detected"] = True
            info["page_detected"] = True
            if coverage < 0.955:
                quad = quad_small / max(scale, 1e-9)
                quad = _expand_quad_safely(quad, original.width, original.height, 0.025)
                warped = _warp_from_quad(_pil_to_cv(original), quad)
                working = _cv_to_pil(warped).convert("RGBA")
                info["page_crop"] = True
            else:
                info["page_preserved"] = True
        else:
            info["page_reason"] = "low-confidence-page"
    else:
        info["page_reason"] = meta.get("reason", "no-paper-found")

    if not info.get("page_crop"):
        light_bbox, light_meta = _find_light_page_bbox_cv(rgb)
        if (
            light_bbox is not None
            and float(light_meta.get("confidence", 0.0)) >= 0.66
            and float(light_meta.get("retained_area", 1.0)) <= 0.965
            and bool(light_meta.get("paper_colour"))
            and bool(light_meta.get("outside_separated"))
        ):
            working = crop_image_normalized(original, light_bbox)
            info.update({
                "detected": True,
                "page_detected": True,
                "page_crop": True,
                "page_source": "light-sheet",
                "light_page": light_meta,
                "page_layout": classify_layout(working.width, working.height),
                "page_confidence": float(light_meta.get("confidence", 0.0)),
            })

    # Stage 2 always runs. If stage 1 was uncertain this can still isolate a white
    # sheet on a dark table, a photographed craft, or blank margins inside a page.
    try:
        content, content_meta = _auto_crop_content(working, max_detect_side=min(max_detect_side, 1200))
        info["content"] = content_meta
        info["content_confidence"] = float(content_meta.get("confidence", 0.0))
        info["content_layout"] = content_meta.get("layout")
        info["content_bbox"] = content_meta.get("bbox")
        if content_meta.get("apply_crop"):
            working = content
            info["detected"] = True
            info["content_crop"] = True
    except Exception as exc:
        info["content_error"] = str(exc)

    info["layout"] = classify_layout(working.width, working.height)
    info["size"] = working.size
    if not info.get("detected"):
        info["reason"] = "no-safe-geometry-change"
    return working, info


def crop_image_normalized(image: Image.Image, crop=None) -> Image.Image:
    working = ImageOps.exif_transpose(image).convert("RGBA")
    if crop and len(crop) == 4:
        l, t, r, b = [max(0.0, min(1.0, float(v))) for v in crop]
        if r - l > 0.02 and b - t > 0.02:
            box = (
                int(round(l * working.width)),
                int(round(t * working.height)),
                max(int(round(r * working.width)), int(round(l * working.width)) + 1),
                max(int(round(b * working.height)), int(round(t * working.height)) + 1),
            )
            working = working.crop(box)
    return working


def _normalize_crop(crop):
    if not crop:
        return None
    return tuple(round(float(v), 5) for v in crop)


def _normalize_warp(warp):
    if not warp:
        return None
    return tuple(tuple(round(float(v), 5) for v in p) for p in warp)


@lru_cache(maxsize=8)
def _load_source_cached(path_str: str, mtime_ns: int, preview_max: int | None):
    with Image.open(path_str) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        if preview_max and max(image.size) > preview_max:
            image.thumbnail((preview_max, preview_max), Image.Resampling.LANCZOS)
        return image.copy()


def _normalize_strokes(strokes):
    if not strokes:
        return None
    out = []
    for stroke in strokes:
        if not stroke or len(stroke) != 6:
            continue
        out.append(tuple(round(float(v), 5) for v in stroke))
    return tuple(out) if out else None


def rotate_image_quarter_turns(image: Image.Image, rotation: int) -> Image.Image:
    rotation = int(rotation) % 360
    working = image.convert("RGBA")
    if rotation:
        working = working.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
    return working


def apply_stretch_strokes(image: Image.Image, strokes=None) -> Image.Image:
    """Apply lightweight local push/pull strokes non-destructively.

    Each stroke is (start_x, start_y, end_x, end_y, radius_norm, strength), all
    coordinates normalized to the current editable image. The implementation uses a
    local OpenCV remap, so a few small corrections stay much cheaper than a full mesh
    warp. The untouched upload is never overwritten.
    """
    if not strokes or cv2 is None or np is None:
        return image.convert("RGBA")
    working = image.convert("RGBA")
    bgra = _pil_to_cv(working)
    h, w = working.height, working.width
    min_side = float(max(1, min(w, h)))
    for stroke in strokes:
        if not stroke or len(stroke) != 6:
            continue
        sx, sy, ex, ey, radius_norm, strength = [float(v) for v in stroke]
        sx *= w; ex *= w; sy *= h; ey *= h
        radius = max(6.0, min_side * max(0.015, min(0.40, radius_norm)))
        strength = max(0.05, min(1.0, strength))
        dx = ex - sx; dy = ey - sy
        if abs(dx) + abs(dy) < 0.5:
            continue
        pad = int(radius + max(abs(dx), abs(dy)) + 4)
        x0 = max(0, int(min(sx, ex) - pad)); x1 = min(w, int(max(sx, ex) + pad) + 1)
        y0 = max(0, int(min(sy, ey) - pad)); y1 = min(h, int(max(sy, ey) + pad) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dist = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2)
        falloff = np.clip(1.0 - dist / radius, 0.0, 1.0)
        falloff = falloff * falloff * (3.0 - 2.0 * falloff)
        amount = falloff * strength
        map_x = xx - dx * amount
        map_y = yy - dy * amount
        patch = cv2.remap(
            bgra,
            map_x,
            map_y,
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        bgra[y0:y1, x0:x1] = patch
    return _cv_to_pil(bgra).convert("RGBA")


def _geometry_compute(
    path_str: str,
    mtime_ns: int,
    auto_paper: bool,
    rotation: int,
    crop_key,
    warp_key,
    stretch_key,
    preview_max: int | None,
    warp_target_aspect: float | None = None,
):
    if preview_max:
        working = _load_source_cached(path_str, mtime_ns, preview_max).copy()
    else:
        with Image.open(path_str) as source:
            working = ImageOps.exif_transpose(source).convert("RGBA")

    working = rotate_image_quarter_turns(working, rotation)
    info = {"detected": False, "layout": classify_layout(working.width, working.height)}
    manual_geometry = bool(crop_key or warp_key or stretch_key)

    if crop_key:
        before = working.size
        working = crop_image_normalized(working, crop_key)
        if working.size != before:
            info["manual_crop"] = True
            info["layout"] = classify_layout(working.width, working.height)

    if warp_key and cv2 is not None and np is not None:
        cv_img = _pil_to_cv(working)
        h, w = working.height, working.width
        quad = [(float(x) * w, float(y) * h) for x, y in warp_key]
        working = _cv_to_pil(_warp_from_quad(cv_img, quad, target_aspect=warp_target_aspect)).convert("RGBA")
        info.update({"detected": True, "manual": "warp", "layout": classify_layout(working.width, working.height)})
    elif auto_paper and not manual_geometry:
        try:
            working, info = auto_detect_document(working, max_detect_side=1200 if preview_max else 1400)
        except Exception:
            pass

    if stretch_key:
        working = apply_stretch_strokes(working, stretch_key)
        info["manual_stretch"] = True
        info["layout"] = classify_layout(working.width, working.height)

    return working.copy(), dict(info)


@lru_cache(maxsize=12)
def _geometry_cached(
    path_str: str,
    mtime_ns: int,
    auto_paper: bool,
    rotation: int,
    crop_key,
    warp_key,
    stretch_key,
    preview_max: int | None,
    warp_target_aspect: float | None,
):
    return _geometry_compute(
        path_str, mtime_ns, auto_paper, rotation, crop_key, warp_key,
        stretch_key, preview_max, warp_target_aspect,
    )


def geometry_for_path(
    image_path: str | Path,
    *,
    auto_paper: bool = True,
    source_rotation: int = 0,
    manual_crop=None,
    manual_warp=None,
    stretch_strokes=None,
    preview_max: int | None = None,
    warp_target_aspect: float | None = None,
) -> tuple[Image.Image, dict]:
    path = Path(image_path)
    args = (
        str(path),
        path.stat().st_mtime_ns,
        bool(auto_paper),
        int(source_rotation) % 360,
        _normalize_crop(manual_crop),
        _normalize_warp(manual_warp),
        _normalize_strokes(stretch_strokes),
        int(preview_max) if preview_max else None,
        round(float(warp_target_aspect), 6) if warp_target_aspect else None,
    )
    if preview_max:
        image, info = _geometry_cached(*args)
    else:
        image, info = _geometry_compute(*args)
    return image.copy(), dict(info)


@lru_cache(maxsize=32)
def list_presets() -> list[str]:
    if not PRESET_DIR.exists():
        return []
    return sorted([p.name for p in PRESET_DIR.glob("*.ACV")], key=lambda s: s.lower())


def _parse_acv(path: Path):
    with open(path, "rb") as file:
        data = file.read()
    pos = 0
    _version, curves = struct.unpack_from(">HH", data, pos)
    pos += 4
    parsed = []
    for _ in range(curves):
        points_count = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        points = []
        for _ in range(points_count):
            y, x = struct.unpack_from(">HH", data, pos)
            pos += 4
            points.append((x, y))
        parsed.append(points)
    return parsed


def _points_to_lut(points):
    if np is None:
        return None
    if not points:
        return np.arange(256, dtype=np.uint8)
    pts = sorted((max(0, min(255, int(x))), max(0, min(255, int(y)))) for x, y in points)
    xs = np.array([p[0] for p in pts], dtype=np.float32)
    ys = np.array([p[1] for p in pts], dtype=np.float32)
    if xs[0] > 0:
        xs = np.insert(xs, 0, 0.0)
        ys = np.insert(ys, 0, ys[0])
    if xs[-1] < 255:
        xs = np.append(xs, 255.0)
        ys = np.append(ys, ys[-1])
    lut = np.interp(np.arange(256, dtype=np.float32), xs, ys)
    return np.clip(lut, 0, 255).astype(np.uint8)


@lru_cache(maxsize=32)
def _preset_luts(name: str):
    if np is None:
        return None
    path = PRESET_DIR / name
    if not path.exists():
        return None
    return [_points_to_lut(curve) for curve in _parse_acv(path)]


def apply_preset(image: Image.Image, preset_name: str) -> Image.Image:
    if not preset_name or preset_name.lower() == "none" or np is None:
        return image.convert("RGBA")
    luts = _preset_luts(preset_name)
    if not luts:
        return image.convert("RGBA")
    arr = np.asarray(image.convert("RGB")).copy()
    if len(luts) >= 1 and luts[0] is not None:
        arr = luts[0][arr]
    if len(luts) >= 4:
        arr[:, :, 0] = luts[1][arr[:, :, 0]]
        arr[:, :, 1] = luts[2][arr[:, :, 1]]
        arr[:, :, 2] = luts[3][arr[:, :, 2]]
    return Image.fromarray(arr, mode="RGB").convert("RGBA")


def apply_color_stage(
    image: Image.Image,
    *,
    brightness: int = 0,
    contrast: int = 0,
    color_balance_red: int = 0,
    color_balance_green: int = 0,
    color_balance_blue: int = 0,
    hue: int = 0,
    saturation: int = 0,
    lightness: int = 0,
    preset_name: str = "None",
) -> Image.Image:
    # Neutral really means neutral: no hidden autocontrast. This keeps the editable
    # source and final output color identical to the uploaded artwork when preset is
    # None and both sliders are zero.
    working = apply_preset(image, preset_name)
    if brightness:
        working = ImageEnhance.Brightness(working).enhance(max(0.2, 1.0 + brightness / 100.0))
    if contrast:
        working = ImageEnhance.Contrast(working).enhance(max(0.2, 1.0 + contrast / 100.0))
    alpha = working.convert("RGBA").getchannel("A")
    rgb = working.convert("RGB")
    if color_balance_red or color_balance_green or color_balance_blue:
        channels = rgb.split()
        shifts = (color_balance_red, color_balance_green, color_balance_blue)
        channels = tuple(channel.point(lambda value, shift=shift: max(0, min(255, value + shift))) for channel, shift in zip(channels, shifts))
        rgb = Image.merge("RGB", channels)
    if hue:
        hsv = rgb.convert("HSV")
        h_channel, s_channel, v_channel = hsv.split()
        hue_shift = int(round(max(-180, min(180, hue)) * 255.0 / 360.0))
        h_channel = h_channel.point(lambda value: (value + hue_shift) % 256)
        rgb = Image.merge("HSV", (h_channel, s_channel, v_channel)).convert("RGB")
    if saturation:
        rgb = ImageEnhance.Color(rgb).enhance(max(0.0, 1.0 + saturation / 100.0))
    if lightness:
        rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, 1.0 + lightness / 100.0))
    working = rgb.convert("RGBA")
    working.putalpha(alpha)
    return working.convert("RGBA")


def preprocess_artwork_path(
    image_path: str | Path,
    *,
    auto_paper: bool = True,
    source_rotation: int = 0,
    brightness: int = 0,
    contrast: int = 0,
    color_balance_red: int = 0,
    color_balance_green: int = 0,
    color_balance_blue: int = 0,
    hue: int = 0,
    saturation: int = 0,
    lightness: int = 0,
    preset_name: str = "None",
    manual_crop=None,
    manual_warp=None,
    stretch_strokes=None,
    preview_max: int | None = None,
    warp_target_aspect: float | None = None,
) -> tuple[Image.Image, dict]:
    geometry, info = geometry_for_path(
        image_path,
        auto_paper=auto_paper,
        source_rotation=source_rotation,
        manual_crop=manual_crop,
        manual_warp=manual_warp,
        stretch_strokes=stretch_strokes,
        preview_max=preview_max,
        warp_target_aspect=warp_target_aspect,
    )
    return apply_color_stage(
        geometry,
        brightness=int(brightness),
        contrast=int(contrast),
        color_balance_red=int(color_balance_red),
        color_balance_green=int(color_balance_green),
        color_balance_blue=int(color_balance_blue),
        hue=int(hue),
        saturation=int(saturation),
        lightness=int(lightness),
        preset_name=str(preset_name or "None"),
    ), info


def preprocess_artwork(
    image: Image.Image,
    *,
    auto_paper: bool = True,
    source_rotation: int = 0,
    brightness: int = 0,
    contrast: int = 0,
    color_balance_red: int = 0,
    color_balance_green: int = 0,
    color_balance_blue: int = 0,
    hue: int = 0,
    saturation: int = 0,
    lightness: int = 0,
    preset_name: str = "None",
    manual_crop=None,
    manual_warp=None,
    stretch_strokes=None,
    warp_target_aspect: float | None = None,
) -> tuple[Image.Image, dict]:
    """Compatibility API for callers that already hold a PIL image."""
    working = ImageOps.exif_transpose(image).convert("RGBA")
    working = rotate_image_quarter_turns(working, source_rotation)
    info = {"detected": False, "layout": classify_layout(working.width, working.height)}
    manual_geometry = bool(manual_crop or manual_warp or stretch_strokes)
    if manual_crop:
        working = crop_image_normalized(working, manual_crop)
        info["manual_crop"] = True
    if manual_warp and cv2 is not None and np is not None:
        cv_img = _pil_to_cv(working)
        h, w = working.height, working.width
        quad = [(float(x) * w, float(y) * h) for x, y in manual_warp]
        working = _cv_to_pil(_warp_from_quad(cv_img, quad, target_aspect=warp_target_aspect)).convert("RGBA")
        info["manual"] = "warp"
    elif auto_paper and not manual_geometry:
        working, info = auto_detect_document(working)
    if stretch_strokes:
        working = apply_stretch_strokes(working, stretch_strokes)
        info["manual_stretch"] = True
    info["layout"] = classify_layout(working.width, working.height)
    return apply_color_stage(
        working, brightness=brightness, contrast=contrast,
        color_balance_red=color_balance_red, color_balance_green=color_balance_green,
        color_balance_blue=color_balance_blue, hue=hue, saturation=saturation,
        lightness=lightness, preset_name=preset_name,
    ), info

def guess_layout(image_path: str | Path) -> str:
    """Fast initial batch layout only. Heavy paper detection happens asynchronously."""
    path = Path(image_path)
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        w, h = image.size
    return classify_layout(w, h)


def clear_processing_caches() -> None:
    _load_source_cached.cache_clear()
    _geometry_cached.cache_clear()
    _detect_routing_layout_path_cached.cache_clear()
