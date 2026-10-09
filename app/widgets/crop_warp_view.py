from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPixmap, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QLabel


class CropWarpView(QLabel):
    """Image surface with intentionally separate Move, Crop and Wrap behaviors.

    Crop edits a rectangular keep-area using 8 handles. Wrap edits four independent
    perspective corners. Move only pans/zooms when the explicit Move tool is active.
    The same normalized crop/wrap values can be mirrored into another view.
    """

    crop_changed = Signal(tuple)
    warp_changed = Signal(tuple)
    crop_committed = Signal()
    warp_committed = Signal()
    pan_requested = Signal(float, float)
    pan_committed = Signal()
    zoom_requested = Signal(float)
    zoom_committed = Signal()
    stretch_stroke = Signal(tuple)
    stretch_committed = Signal()
    transform_changed = Signal(tuple)
    transform_committed = Signal()
    edit_requested = Signal()
    operation_started = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)
        self._source = QPixmap()
        self.mode = "view"  # view/move/crop/warp/stretch
        self.crop = (0.0, 0.0, 1.0, 1.0)
        self.warp = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        self.transform_quad = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        # Region inside the displayed pixmap where crop/wrap handles are drawn.
        # Right/original view uses full image; left/final view uses artwork frame.
        self.edit_region = (0.0, 0.0, 1.0, 1.0)
        self._active = None
        self._last = QPointF()
        self._display_rect = QRectF()
        self.view_zoom = 1.0
        self.stretch_radius = 0.10
        self.stretch_strength = 0.45
        self._stretch_start = None
        self._stretch_current = None
        self._hover_pos = None
        self._transform_start = None
        self._press_position = None
        self._pan_moved = False

    def set_source_pixmap(self, pixmap: QPixmap | None):
        self._source = pixmap or QPixmap()
        self.update()

    def set_mode(self, mode: str):
        new_mode = mode if mode in {"view", "move", "crop", "warp", "stretch", "transform"} else "view"
        # An async preview can arrive while a handle is being dragged. Re-applying the
        # same mode must not cancel that live gesture.
        if new_mode == self.mode:
            self.update()
            return
        self.mode = new_mode
        self._active = None
        if self.mode == "move":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.mode in {"crop", "warp", "stretch", "transform"}:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_view_zoom(self, value: float):
        self.view_zoom = max(0.5, min(4.0, float(value)))
        self.update()

    def set_stretch_brush(self, radius: float, strength: float):
        self.stretch_radius = max(0.015, min(0.40, float(radius)))
        self.stretch_strength = max(0.05, min(1.0, float(strength)))
        self.update()

    def set_edit_region(self, region=None):
        if region and len(region) == 4:
            l, t, r, b = [max(0.0, min(1.0, float(v))) for v in region]
            if r > l and b > t:
                self.edit_region = (l, t, r, b)
            else:
                self.edit_region = (0.0, 0.0, 1.0, 1.0)
        else:
            self.edit_region = (0.0, 0.0, 1.0, 1.0)
        self.update()

    def set_crop(self, crop):
        if crop and len(crop) == 4:
            l, t, r, b = [max(0.0, min(1.0, float(x))) for x in crop]
            self.crop = (min(l, r - 0.001), min(t, b - 0.001), max(r, l + 0.001), max(b, t + 0.001))
        else:
            self.crop = (0.0, 0.0, 1.0, 1.0)
        self.update()

    def set_warp(self, points):
        if points and len(points) == 4:
            self.warp = tuple((max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))) for x, y in points)
        else:
            self.warp = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        self.update()

    def set_transform_quad(self, points):
        if points and len(points) == 4:
            try:
                parsed = tuple((float(x), float(y)) for x, y in points)
                if all(-8.0 <= value <= 8.0 for point in parsed for value in point):
                    self.transform_quad = parsed
            except (TypeError, ValueError):
                pass
        else:
            self.transform_quad = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        self.update()

    def _pix_rect(self) -> QRectF:
        if self._source.isNull():
            return QRectF()
        available = self.contentsRect().adjusted(10, 10, -10, -10)
        sw, sh = self._source.width(), self._source.height()
        if sw <= 0 or sh <= 0 or available.width() <= 0 or available.height() <= 0:
            return QRectF()
        scale = min(available.width() / sw, available.height() / sh) * self.view_zoom
        w, h = sw * scale, sh * scale
        x = available.x() + (available.width() - w) / 2
        y = available.y() + (available.height() - h) / 2
        return QRectF(x, y, w, h)

    def _region_rect(self) -> QRectF:
        r = self._display_rect
        l, t, rr, b = self.edit_region
        return QRectF(
            r.left() + l * r.width(),
            r.top() + t * r.height(),
            (rr - l) * r.width(),
            (b - t) * r.height(),
        )

    def _to_widget(self, nx: float, ny: float) -> QPointF:
        r = self._region_rect()
        return QPointF(r.left() + nx * r.width(), r.top() + ny * r.height())

    def _to_norm(self, p: QPointF) -> tuple[float, float]:
        r = self._region_rect()
        if r.width() <= 0 or r.height() <= 0:
            return 0.0, 0.0
        return (
            max(0.0, min(1.0, (p.x() - r.left()) / r.width())),
            max(0.0, min(1.0, (p.y() - r.top()) / r.height())),
        )

    def _to_norm_unbounded(self, p: QPointF) -> tuple[float, float]:
        r = self._region_rect()
        if r.width() <= 0 or r.height() <= 0:
            return 0.0, 0.0
        return ((p.x() - r.left()) / r.width(), (p.y() - r.top()) / r.height())

    def paintEvent(self, event):
        # QLabel's normal pixmap rendering is not used. We draw once ourselves so the
        # interactive overlay always shares the exact same display rectangle.
        QLabel.paintEvent(self, event)
        if self._source.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self._pix_rect()
        self._display_rect = r
        p.drawPixmap(r.toRect(), self._source)

        if self.mode == "crop":
            self._paint_crop(p)
        elif self.mode == "warp":
            self._paint_warp(p)
        elif self.mode == "stretch":
            self._paint_stretch(p)
        elif self.mode == "transform":
            self._paint_transform(p)
        p.end()

    def _paint_crop(self, p: QPainter):
        region = self._region_rect()
        l, t, rr, b = self.crop
        a = self._to_widget(l, t)
        c = self._to_widget(rr, b)
        cr = QRectF(a, c).normalized()
        dim = QColor(0, 0, 0, 125)
        # Dim ONLY the rejected area. The selected keep-area stays clear.
        p.fillRect(QRectF(region.left(), region.top(), region.width(), max(0, cr.top() - region.top())), dim)
        p.fillRect(QRectF(region.left(), cr.bottom(), region.width(), max(0, region.bottom() - cr.bottom())), dim)
        p.fillRect(QRectF(region.left(), cr.top(), max(0, cr.left() - region.left()), cr.height()), dim)
        p.fillRect(QRectF(cr.right(), cr.top(), max(0, region.right() - cr.right()), cr.height()), dim)
        p.setPen(QPen(QColor("#22C7F2"), 2))
        p.drawRect(cr)
        for name, hp in self._crop_handles().items():
            corner = name in {"tl", "tr", "br", "bl"}
            self._draw_handle(p, hp, QColor("#22C7F2"), 11 if corner else 9)

    def _paint_warp(self, p: QPainter):
        pts = [self._to_widget(x, y) for x, y in self.warp]
        p.setPen(QPen(QColor("#A855F7"), 2))
        for i in range(4):
            p.drawLine(pts[i], pts[(i + 1) % 4])
        for i, hp in enumerate(pts):
            self._draw_handle(p, hp, QColor("#A855F7"), 12)
            p.setPen(QPen(QColor("white"), 1))
            p.drawText(QRectF(hp.x() - 10, hp.y() - 20, 20, 15), Qt.AlignmentFlag.AlignCenter, str(i + 1))

    def _paint_stretch(self, p: QPainter):
        pos = self._stretch_current or self._hover_pos
        if pos is None:
            return
        r = self._region_rect()
        radius_px = self.stretch_radius * min(r.width(), r.height())
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#F59E0B"), 2))
        p.drawEllipse(QRectF(pos.x() - radius_px, pos.y() - radius_px, radius_px * 2, radius_px * 2))
        if self._stretch_start is not None and self._stretch_current is not None:
            p.drawLine(self._stretch_start, self._stretch_current)

    def _transform_handles(self):
        points = [self._to_widget(x, y) for x, y in self.transform_quad]
        mids = [QPointF((points[i].x() + points[(i + 1) % 4].x()) / 2,
                        (points[i].y() + points[(i + 1) % 4].y()) / 2) for i in range(4)]
        center = QPointF(sum(p.x() for p in points) / 4, sum(p.y() for p in points) / 4)
        top = mids[0]
        vx, vy = top.x() - center.x(), top.y() - center.y()
        length = max(1.0, math.hypot(vx, vy))
        rotate = QPointF(top.x() + vx / length * 28.0, top.y() + vy / length * 28.0)
        return {
            "tl": points[0], "tr": points[1], "br": points[2], "bl": points[3],
            "t": mids[0], "r": mids[1], "b": mids[2], "l": mids[3],
            "rotate": rotate, "center": center,
        }

    def _paint_transform(self, p: QPainter):
        handles = self._transform_handles()
        points = [handles[name] for name in ("tl", "tr", "br", "bl")]
        colour = QColor("#2563EB")
        p.setPen(QPen(colour, 2, Qt.DashLine))
        for i in range(4):
            p.drawLine(points[i], points[(i + 1) % 4])
        p.drawLine(handles["t"], handles["rotate"])
        for name in ("tl", "tr", "br", "bl"):
            self._draw_handle(p, handles[name], colour, 12)
        for name in ("t", "r", "b", "l"):
            self._draw_handle(p, handles[name], QColor("#0EA5E9"), 10)
        p.setPen(QPen(QColor("white"), 1))
        p.setBrush(QBrush(QColor("#F97316")))
        p.drawEllipse(QRectF(handles["rotate"].x() - 6, handles["rotate"].y() - 6, 12, 12))
        p.setPen(QPen(QColor("#F97316"), 1))
        p.drawLine(QPointF(handles["center"].x() - 5, handles["center"].y()), QPointF(handles["center"].x() + 5, handles["center"].y()))
        p.drawLine(QPointF(handles["center"].x(), handles["center"].y() - 5), QPointF(handles["center"].x(), handles["center"].y() + 5))

    @staticmethod
    def _draw_handle(p: QPainter, pos: QPointF, color: QColor, size: int = 10):
        p.setPen(QPen(QColor("white"), 1))
        p.setBrush(QBrush(color))
        p.drawRect(QRectF(pos.x() - size / 2, pos.y() - size / 2, size, size))

    def _crop_handles(self):
        l, t, r, b = self.crop
        return {
            "tl": self._to_widget(l, t),
            "t": self._to_widget((l + r) / 2, t),
            "tr": self._to_widget(r, t),
            "r": self._to_widget(r, (t + b) / 2),
            "br": self._to_widget(r, b),
            "b": self._to_widget((l + r) / 2, b),
            "bl": self._to_widget(l, b),
            "l": self._to_widget(l, (t + b) / 2),
        }

    def _hit(self, pos: QPointF):
        radius = 14
        if self.mode == "crop":
            for name, hp in self._crop_handles().items():
                if abs(pos.x() - hp.x()) <= radius and abs(pos.y() - hp.y()) <= radius:
                    return name
            l, t, r, b = self.crop
            a = self._to_widget(l, t)
            c = self._to_widget(r, b)
            if QRectF(a, c).normalized().contains(pos):
                return "crop_move"
        elif self.mode == "warp":
            for i, (x, y) in enumerate(self.warp):
                hp = self._to_widget(x, y)
                if abs(pos.x() - hp.x()) <= radius and abs(pos.y() - hp.y()) <= radius:
                    return i
        elif self.mode == "transform":
            handles = self._transform_handles()
            for name in ("rotate", "tl", "tr", "br", "bl", "t", "r", "b", "l"):
                hp = handles[name]
                if abs(pos.x() - hp.x()) <= radius and abs(pos.y() - hp.y()) <= radius:
                    return name
            # Convex quad containment is approximated by its painter path through
            # four same-side cross products; sufficient for transform dragging.
            pts = [handles[name] for name in ("tl", "tr", "br", "bl")]
            signs = []
            for i in range(4):
                a, b = pts[i], pts[(i + 1) % 4]
                signs.append((b.x() - a.x()) * (pos.y() - a.y()) - (b.y() - a.y()) * (pos.x() - a.x()))
            if all(value >= 0 for value in signs) or all(value <= 0 for value in signs):
                return "transform_move"
        return None

    def _set_hover_cursor(self, hit):
        if self.mode == "crop":
            if hit in {"tl", "br"}:
                self.setCursor(Qt.SizeFDiagCursor)
            elif hit in {"tr", "bl"}:
                self.setCursor(Qt.SizeBDiagCursor)
            elif hit in {"t", "b"}:
                self.setCursor(Qt.SizeVerCursor)
            elif hit in {"l", "r"}:
                self.setCursor(Qt.SizeHorCursor)
            elif hit == "crop_move":
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.CrossCursor)
        elif self.mode == "warp":
            self.setCursor(Qt.SizeAllCursor if isinstance(hit, int) else Qt.CrossCursor)
        elif self.mode == "stretch":
            self.setCursor(Qt.CrossCursor)
        elif self.mode == "transform":
            if hit == "rotate": self.setCursor(Qt.CrossCursor)
            elif hit in {"l", "r"}: self.setCursor(Qt.SizeHorCursor)
            elif hit in {"t", "b"}: self.setCursor(Qt.SizeVerCursor)
            elif hit in {"tl", "br"}: self.setCursor(Qt.SizeFDiagCursor)
            elif hit in {"tr", "bl"}: self.setCursor(Qt.SizeBDiagCursor)
            elif hit == "transform_move": self.setCursor(Qt.SizeAllCursor)
            else: self.setCursor(Qt.CrossCursor)
        elif self.mode == "move":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent):
        if self._source.isNull() or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        if self.mode in {"crop", "warp", "transform"}:
            self._active = self._hit(event.position())
            self._last = event.position()
            if self._active is not None:
                self.operation_started.emit(self.mode)
                if self.mode == "transform":
                    self._transform_start = tuple(self.transform_quad)
                event.accept()
                return
        elif self.mode == "stretch":
            if self._region_rect().contains(event.position()):
                self._active = "stretch"
                self._stretch_start = event.position()
                self._stretch_current = event.position()
                self._last = event.position()
                self.operation_started.emit("stretch")
                self.update()
                event.accept()
                return
        elif self.mode == "move":
            self._active = "pan"
            self._last = event.position()
            self._press_position = event.position()
            self._pan_moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        self._hover_pos = pos
        if self._active is None:
            self._set_hover_cursor(self._hit(pos))
            if self.mode == "stretch":
                self.update()
            return super().mouseMoveEvent(event)

        if self.mode == "move" and self._active == "pan":
            if self._press_position is not None and not self._pan_moved:
                moved = pos - self._press_position
                if abs(moved.x()) + abs(moved.y()) < 4.0:
                    event.accept()
                    return
                self._pan_moved = True
                self.operation_started.emit("move")
            delta = pos - self._last
            self._last = pos
            if self._display_rect.width() > 0 and self._display_rect.height() > 0:
                sx = self._source.width() / self._display_rect.width()
                sy = self._source.height() / self._display_rect.height()
                self.pan_requested.emit(delta.x() * sx, delta.y() * sy)
            event.accept()
            return

        if self.mode == "transform" and self._transform_start:
            self._update_transform(pos, event.modifiers())
            event.accept()
            return

        nx, ny = self._to_norm(pos)
        if self.mode == "stretch" and self._active == "stretch":
            self._stretch_current = pos
            self.update()
            event.accept()
            return
        if self.mode == "warp" and isinstance(self._active, int):
            pts = list(self.warp)
            pts[self._active] = (nx, ny)  # ONLY this corner changes.
            self.warp = tuple(pts)
            self.warp_changed.emit(self.warp)
            self.update()
            event.accept()
            return

        if self.mode == "crop":
            l, t, r, b = self.crop
            min_s = 0.035
            if self._active == "crop_move":
                ox, oy = self._to_norm(self._last)
                dx, dy = nx - ox, ny - oy
                w, h = r - l, b - t
                l = max(0.0, min(1.0 - w, l + dx))
                t = max(0.0, min(1.0 - h, t + dy))
                r, b = l + w, t + h
            else:
                handle = str(self._active)
                if "l" in handle:
                    l = min(nx, r - min_s)
                if "r" in handle:
                    r = max(nx, l + min_s)
                if "t" in handle:
                    t = min(ny, b - min_s)
                if "b" in handle:
                    b = max(ny, t + min_s)
            self.crop = (max(0.0, l), max(0.0, t), min(1.0, r), min(1.0, b))
            self.crop_changed.emit(self.crop)
            self.update()
            self._last = pos
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._active is not None and event.button() == Qt.MouseButton.LeftButton:
            finished_mode = self.mode
            self._active = None
            self._set_hover_cursor(self._hit(event.position()))
            if finished_mode == "crop":
                self.crop_committed.emit()
            elif finished_mode == "warp":
                self.warp_committed.emit()
            elif finished_mode == "move":
                if self._pan_moved:
                    self.pan_committed.emit()
                elif self._region_rect().contains(event.position()):
                    # A click on the artwork in the final/template preview selects it
                    # for Photoshop-style Free Transform.
                    self.edit_requested.emit()
                self._press_position = None
                self._pan_moved = False
            elif finished_mode == "stretch":
                if self._stretch_start is not None and self._stretch_current is not None:
                    sx, sy = self._to_norm(self._stretch_start)
                    ex, ey = self._to_norm(self._stretch_current)
                    stroke = (sx, sy, ex, ey, self.stretch_radius, self.stretch_strength)
                    if abs(ex - sx) + abs(ey - sy) > 0.001:
                        self.stretch_stroke.emit(stroke)
                        self.stretch_committed.emit()
                self._stretch_start = None
                self._stretch_current = None
                self.update()
            elif finished_mode == "transform":
                self._transform_start = None
                self.transform_committed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @staticmethod
    def _rotate_point(point, center, angle):
        x, y = point[0] - center[0], point[1] - center[1]
        c, s = math.cos(angle), math.sin(angle)
        return (center[0] + x * c - y * s, center[1] + x * s + y * c)

    def _update_transform(self, pos: QPointF, modifiers):
        start = tuple(self._transform_start)
        active = str(self._active)
        current = self._to_norm_unbounded(pos)
        previous = self._to_norm_unbounded(self._last)
        points = list(start)
        center = (sum(x for x, _ in start) / 4, sum(y for _, y in start) / 4)
        corner_index = {"tl": 0, "tr": 1, "br": 2, "bl": 3}
        edge_indices = {"t": (0, 1), "r": (1, 2), "b": (2, 3), "l": (3, 0)}

        if active == "transform_move":
            dx, dy = current[0] - previous[0], current[1] - previous[1]
            self._transform_start = tuple((x + dx, y + dy) for x, y in start)
            self._last = pos
            points = list(self._transform_start)
        elif active == "rotate":
            a0 = math.atan2(previous[1] - center[1], previous[0] - center[0])
            a1 = math.atan2(current[1] - center[1], current[0] - center[0])
            delta = a1 - a0
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                delta = round(delta / math.radians(15)) * math.radians(15)
            self._transform_start = tuple(self._rotate_point(point, center, delta) for point in start)
            self._last = pos
            points = list(self._transform_start)
        elif active in corner_index:
            index = corner_index[active]
            opposite = (index + 2) % 4
            ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
            if ctrl:
                # Ctrl = Distort. Ctrl+Alt+Shift mirrors the selected delta at the
                # opposite corner to provide the familiar perspective gesture.
                dx, dy = current[0] - start[index][0], current[1] - start[index][1]
                points[index] = current
                if alt and shift:
                    ox, oy = points[opposite]
                    points[opposite] = (ox - dx, oy - dy)
            elif shift:
                # Shift unlocks width/height: move the selected corner and its two
                # adjacent edge endpoints along the matching axes.
                dx, dy = current[0] - start[index][0], current[1] - start[index][1]
                if index in (0, 3):
                    for i in (0, 3): points[i] = (start[i][0] + dx, start[i][1])
                else:
                    for i in (1, 2): points[i] = (start[i][0] + dx, start[i][1])
                if index in (0, 1):
                    for i in (0, 1): points[i] = (points[i][0], start[i][1] + dy)
                else:
                    for i in (2, 3): points[i] = (points[i][0], start[i][1] + dy)
            else:
                anchor = center if alt else start[opposite]
                old = (start[index][0] - anchor[0], start[index][1] - anchor[1])
                new = (current[0] - anchor[0], current[1] - anchor[1])
                denom = old[0] * old[0] + old[1] * old[1]
                scale = max(0.03, (new[0] * old[0] + new[1] * old[1]) / max(1e-9, denom))
                points = [(anchor[0] + (x - anchor[0]) * scale, anchor[1] + (y - anchor[1]) * scale) for x, y in start]
        elif active in edge_indices:
            selected = edge_indices[active]
            opposite_edge = edge_indices[{"t": "b", "r": "l", "b": "t", "l": "r"}[active]]
            dx, dy = current[0] - previous[0], current[1] - previous[1]
            if (modifiers & Qt.KeyboardModifier.ControlModifier) and (modifiers & Qt.KeyboardModifier.ShiftModifier):
                # Skew: slide a whole selected edge parallel to itself.
                for i in selected: points[i] = (points[i][0] + dx, points[i][1] + dy)
                self._transform_start = tuple(points); self._last = pos
            else:
                anchor_points = [start[i] for i in opposite_edge]
                anchor = center if modifiers & Qt.KeyboardModifier.AltModifier else (
                    (anchor_points[0][0] + anchor_points[1][0]) / 2,
                    (anchor_points[0][1] + anchor_points[1][1]) / 2,
                )
                start_mid = ((start[selected[0]][0] + start[selected[1]][0]) / 2,
                             (start[selected[0]][1] + start[selected[1]][1]) / 2)
                vector = (start_mid[0] - anchor[0], start_mid[1] - anchor[1])
                denom = vector[0] * vector[0] + vector[1] * vector[1]
                factor = max(0.03, ((current[0] - anchor[0]) * vector[0] + (current[1] - anchor[1]) * vector[1]) / max(1e-9, denom))
                unit = (vector[0] / math.sqrt(max(1e-9, denom)), vector[1] / math.sqrt(max(1e-9, denom)))
                for i, point in enumerate(start):
                    rel = (point[0] - anchor[0], point[1] - anchor[1])
                    along = rel[0] * unit[0] + rel[1] * unit[1]
                    perp = (rel[0] - along * unit[0], rel[1] - along * unit[1])
                    points[i] = (anchor[0] + perp[0] + along * factor * unit[0], anchor[1] + perp[1] + along * factor * unit[1])

        self.transform_quad = tuple(points)
        self.transform_changed.emit(self.transform_quad)
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        if self._source.isNull():
            return super().wheelEvent(event)
        if self.mode == "move":
            self.zoom_requested.emit(1.08 if event.angleDelta().y() > 0 else 1 / 1.08)
            self.zoom_committed.emit()
            event.accept()
            return
        # Source-editor view zoom is visual only and persists across Crop/Wrap/Stretch
        # tool switches. It never changes the artwork scale saved to the template.
        factor = 1.10 if event.angleDelta().y() > 0 else 1 / 1.10
        self.set_view_zoom(self.view_zoom * factor)
        event.accept()
