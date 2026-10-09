from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPixmap, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QLabel


class InteractiveImageLabel(QLabel):
    """A smooth preview label that can emit pan and zoom gestures.

    It does not edit template geometry. It only controls the current artwork
    placement override used by Compare.
    """

    pan_requested = Signal(float, float)
    zoom_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)
        self._source_pixmap = QPixmap()
        self._shown_size = None
        self._dragging = False
        self._last_pos = QPoint()
        self.edit_enabled = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_edit_enabled(self, enabled: bool):
        self.edit_enabled = bool(enabled)
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_source_pixmap(self, pixmap: QPixmap | None):
        self._source_pixmap = pixmap or QPixmap()
        self._refresh_scaled()

    def _refresh_scaled(self):
        if self._source_pixmap.isNull():
            self.clear()
            self._shown_size = None
            return
        target = self.contentsRect().adjusted(8, 8, -8, -8).size()
        scaled = self._source_pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._shown_size = scaled.size()
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_scaled()

    def mousePressEvent(self, event: QMouseEvent):
        if self.edit_enabled and event.button() == Qt.MouseButton.LeftButton and not self._source_pixmap.isNull():
            self._dragging = True
            self._last_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and self.edit_enabled and self._shown_size:
            pos = event.position().toPoint()
            delta = pos - self._last_pos
            self._last_pos = pos
            if self._shown_size.width() > 0 and self._shown_size.height() > 0:
                sx = self._source_pixmap.width() / self._shown_size.width()
                sy = self._source_pixmap.height() / self._shown_size.height()
                self.pan_requested.emit(delta.x() * sx, delta.y() * sy)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.edit_enabled else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if self.edit_enabled and not self._source_pixmap.isNull():
            self.zoom_requested.emit(1.08 if event.angleDelta().y() > 0 else 1 / 1.08)
            event.accept()
            return
        super().wheelEvent(event)
