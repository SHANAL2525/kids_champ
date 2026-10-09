from __future__ import annotations

from dataclasses import replace

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage

from .image_renderer import render_item_preview
from .models import BatchItem


class PreviewSignals(QObject):
    finished = Signal(int, object, str)  # request_id, QImage|None, error


class PreviewTask(QRunnable):
    def __init__(self, request_id: int, item: BatchItem, max_width: int = 1100):
        super().__init__()
        self.request_id = request_id
        self.item = replace(item)
        self.max_width = max_width
        self.signals = PreviewSignals()
        self.setAutoDelete(True)

    @staticmethod
    def _to_qimage(image: Image.Image) -> QImage:
        rgba = image.convert("RGBA")
        return QImage(
            rgba.tobytes("raw", "RGBA"),
            rgba.width,
            rgba.height,
            QImage.Format_RGBA8888,
        ).copy()

    @Slot()
    def run(self):
        try:
            image = render_item_preview(self.item, max_width=self.max_width)
            self.signals.finished.emit(self.request_id, self._to_qimage(image), "")
        except Exception as exc:
            self.signals.finished.emit(self.request_id, None, str(exc))


class PreviewPool:
    """Small shared pool so preview work cannot flood the CPU/UI."""

    _pool: QThreadPool | None = None

    @classmethod
    def pool(cls) -> QThreadPool:
        if cls._pool is None:
            cls._pool = QThreadPool.globalInstance()
            # Two preview jobs keep navigation responsive without starving final export.
            cls._pool.setMaxThreadCount(2)
        return cls._pool

    @classmethod
    def start(cls, task: PreviewTask) -> None:
        cls.pool().start(task)
