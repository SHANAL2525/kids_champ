from __future__ import annotations

import shutil
import tempfile
import threading
import zipfile
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from .config import (
    MAX_BATCH_IMAGES, MAX_ZIP_COMPRESSION_RATIO, MAX_ZIP_IMAGE_BYTES,
    MAX_ZIP_MEMBERS, MAX_ZIP_TOTAL_BYTES, SUPPORTED_EXTENSIONS,
)
from .filename_parser import parse_filename, detect_layout_info
from .models import BatchItem


class BatchCancelled(RuntimeError):
    pass


def _safe_zip_images(zf: zipfile.ZipFile, remaining: int) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    members = zf.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError(f"ZIP contains too many entries ({len(members)}; maximum {MAX_ZIP_MEMBERS}).")

    accepted: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total = 0
    for info in members:
        if info.is_dir():
            continue
        raw_name = info.filename.replace("\\", "/")
        relative = PurePosixPath(raw_name)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"Unsafe path in ZIP: {info.filename}")
        if ":" in relative.parts[0]:
            raise ValueError(f"Unsafe drive path in ZIP: {info.filename}")
        if relative.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted ZIP entries are not supported: {info.filename}")
        if info.file_size > MAX_ZIP_IMAGE_BYTES:
            raise ValueError(f"Image is too large inside ZIP: {info.filename}")
        ratio = info.file_size / float(max(1, info.compress_size))
        if info.file_size >= 1024 * 1024 and ratio > MAX_ZIP_COMPRESSION_RATIO:
            raise ValueError(f"Suspicious compression ratio in ZIP: {info.filename}")
        total += info.file_size
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("ZIP expands beyond the safe 2 GB batch limit.")
        key = relative.as_posix().lower()
        if key in seen:
            raise ValueError(f"Duplicate image path in ZIP: {info.filename}")
        seen.add(key)
        accepted.append((info, relative))

    if not accepted:
        raise ValueError("ZIP does not contain any supported artwork images.")
    if len(accepted) > remaining:
        raise ValueError(f"Maximum {MAX_BATCH_IMAGES} artworks. Available slots: {remaining}, new: {len(accepted)}")
    return accepted


class BatchSession:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="kidschamp_v3_"))
        self.items: list[BatchItem] = []
        self.zip_paths: list[Path] = []

    def clear(self):
        self.items.clear(); self.zip_paths.clear()
        shutil.rmtree(self.root, ignore_errors=True)
        self.root = Path(tempfile.mkdtemp(prefix="kidschamp_v3_"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add_zip(self, zip_path: Path, cancel_event: threading.Event | None = None) -> list[BatchItem]:
        dest = self.root / f"batch_{len(self.zip_paths)+1}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                selected = _safe_zip_images(zf, MAX_BATCH_IMAGES - len(self.items))
                files: list[tuple[Path, str]] = []
                resolved_dest = dest.resolve()
                for info, relative in selected:
                    if cancel_event and cancel_event.is_set():
                        raise BatchCancelled("ZIP loading cancelled.")
                    target = dest.joinpath(*relative.parts)
                    resolved_target = target.resolve()
                    if not resolved_target.is_relative_to(resolved_dest):
                        raise ValueError(f"Unsafe extraction path: {info.filename}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with zf.open(info, "r") as source, target.open("wb") as output:
                        while True:
                            if cancel_event and cancel_event.is_set():
                                raise BatchCancelled("ZIP loading cancelled.")
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_ZIP_IMAGE_BYTES or written > info.file_size + 1024:
                                raise ValueError(f"Image expanded beyond its declared safe size: {info.filename}")
                            output.write(chunk)
                    files.append((target, relative.as_posix()))
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        ordered_files = sorted(files, key=lambda pair: pair[1].lower())

        def analyze(pair: tuple[Path, str]) -> BatchItem:
            p, relative = pair
            if cancel_event and cancel_event.is_set():
                raise BatchCancelled("ZIP loading cancelled.")
            try:
                sid, name, city, _fmt = parse_filename(p.name)
                layout, route = detect_layout_info(p)
                confidence = float(route.get("confidence", 0.0))
                item = BatchItem(
                    p, p.name, sid, name, city, layout, source_relative=relative,
                    routing_confidence=confidence,
                    routing_source=str(route.get("source", "raw")),
                    routing_needs_check=bool(route.get("needs_check", confidence < 0.70)),
                )
            except Exception as exc:
                item = BatchItem(p, p.name, "", p.stem, "", "Landscape", source_relative=relative, status="Error", warning=str(exc))
            return item

        # OpenCV releases the GIL for its heavy operations, so a small bounded
        # thread pool makes 250-image Auto V4 analysis several times faster while
        # keeping deterministic input order.
        workers = max(2, min(8, os.cpu_count() or 4, len(ordered_files)))
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kidschamp-auto") as pool:
                futures = [pool.submit(analyze, pair) for pair in ordered_files]
                new_items = [future.result() for future in futures]
        except Exception:
            for future in locals().get("futures", []):
                future.cancel()
            shutil.rmtree(dest, ignore_errors=True)
            raise
        # Commit the batch only after extraction and analysis fully succeed.
        self.items.extend(new_items)
        self.zip_paths.append(zip_path)
        return new_items

    def add_images(self, image_paths: list[Path], cancel_event: threading.Event | None = None) -> list[BatchItem]:
        paths = [Path(path) for path in image_paths]
        if not paths:
            raise ValueError("Choose at least one image.")
        remaining = MAX_BATCH_IMAGES - len(self.items)
        if len(paths) > remaining:
            raise ValueError(f"Maximum {MAX_BATCH_IMAGES} artworks. Available slots: {remaining}, new: {len(paths)}")
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS or not path.is_file():
                raise ValueError(f"Unsupported or missing image: {path.name}")
            if path.stat().st_size > MAX_ZIP_IMAGE_BYTES:
                raise ValueError(f"Image is too large: {path.name}")
        dest = self.root / f"images_{len(self.items)+1}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            copied = []
            for index, source in enumerate(paths, 1):
                if cancel_event and cancel_event.is_set():
                    raise BatchCancelled("Image loading cancelled.")
                target = dest / f"{index:03d}_{source.name}"
                shutil.copy2(source, target)
                copied.append(target)

            def analyze(pair):
                source, copied_path = pair
                try:
                    layout, route = detect_layout_info(copied_path)
                    try:
                        sid, name, city, _fmt = parse_filename(source.name)
                    except ValueError:
                        sid, name, city = "", source.stem.replace("_", " ").strip(), ""
                    confidence = float(route.get("confidence", 0.0))
                    return BatchItem(
                        copied_path, source.name, sid, name, city, layout, source_relative=source.name,
                        routing_confidence=confidence, routing_source=str(route.get("source", "raw")),
                        routing_needs_check=bool(route.get("needs_check", confidence < 0.70)),
                    )
                except Exception as exc:
                    return BatchItem(copied_path, source.name, "", source.stem, "", "Landscape", source_relative=source.name, status="Error", warning=str(exc))

            workers = max(1, min(8, os.cpu_count() or 4, len(copied)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kidschamp-images") as pool:
                new_items = list(pool.map(analyze, zip(paths, copied)))
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        self.items.extend(new_items)
        return new_items
