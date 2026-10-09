from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QObject, QEvent
from PySide6.QtGui import QIcon, QPixmap, QKeyEvent
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLineEdit, QComboBox,
    QHeaderView, QAbstractItemView, QApplication, QWidget
)

from ..models import BatchItem
from ..config import ICON_DIR


class _EditorFilter(QObject):
    """Finishes a row edit on Enter, cancels on Escape."""
    def __init__(self, table: "BatchTable"):
        super().__init__(table)
        self.table = table

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.table.finish_edit(save=True)
                return True
            if key == Qt.Key.Key_Escape:
                self.table.finish_edit(save=False)
                return True
        if event.type() == QEvent.MouseButtonPress and self.table._editing_row is not None:
            target = obj if isinstance(obj, QWidget) else None
            inside = False
            if target is not None:
                for editor in self.table._edit_widgets:
                    if target is editor or editor.isAncestorOf(target):
                        inside = True
                        break
            if not inside:
                # Commit immediately so the edited row returns to its normal text style
                # before the outside click changes selection/navigation.
                self.table.finish_edit(save=True)
        return False


class BatchTable(QTableWidget):
    edit_changed = Signal(int)
    delete_requested = Signal(int)
    preview_requested = Signal(int)
    compare_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(0, 8, parent)
        self.setHorizontalHeaderLabels(["Art", "ID", "Name", "Location", "Template", "Status", "", ""])
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setIconSize(QSize(58, 44))
        self.verticalHeader().setDefaultSectionSize(68)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.setColumnWidth(0, 72)
        self.setColumnWidth(6, 46)
        self.setColumnWidth(7, 46)

        self._items: list[BatchItem] = []
        self._editing_row: int | None = None
        self._editing_idx: int | None = None
        self._edit_widgets: list[QWidget] = []
        self._edit_original: tuple[str, str, str, str] | None = None
        self._icon_cache = {}
        self._editor_filter = _EditorFilter(self)
        self.itemSelectionChanged.connect(self._selection_changed)
        self.cellDoubleClicked.connect(self._open_compare_from_row)

        app = QApplication.instance()
        if app:
            app.focusChanged.connect(self._global_focus_changed)
            app.installEventFilter(self._editor_filter)

    def set_items(self, items: list[BatchItem], visible_indices: list[int] | None = None):
        # Commit any active editor before rebuilding the rows.
        if self._editing_row is not None:
            self.finish_edit(save=True)
        self._items = items
        indices = visible_indices if visible_indices is not None else list(range(len(items)))
        self.setUpdatesEnabled(False)
        self.clearContents()
        self.setRowCount(len(indices))
        self._row_to_index = {}
        for row, idx in enumerate(indices):
            self._row_to_index[row] = idx
            self._populate_row(row, idx)
        self.setUpdatesEnabled(True)

    def set_visible_indices(self, visible_indices):
        """Hide/show existing rows without rebuilding widgets or thumbnails."""
        visible = set(visible_indices if visible_indices is not None else range(len(self._items)))
        self.setUpdatesEnabled(False)
        try:
            for row, idx in self._row_to_index.items():
                self.setRowHidden(row, idx not in visible)
        finally:
            self.setUpdatesEnabled(True)
            self.viewport().update()

    def _thumb_icon(self, path: Path):
        key = str(path)
        if key in self._icon_cache:
            return self._icon_cache[key]
        pix = QPixmap(key)
        icon = QIcon() if pix.isNull() else QIcon(pix.scaled(58, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self._icon_cache[key] = icon
        return icon

    def _populate_row(self, row: int, idx: int):
        # IMPORTANT: remove any inline edit widgets first. QTableWidget keeps a cell
        # widget above the normal item text unless it is explicitly removed.
        for col in (1, 2, 3, 4, 6, 7):
            old_widget = self.cellWidget(row, col)
            if old_widget is not None:
                self.removeCellWidget(row, col)
                old_widget.deleteLater()
        item = self._items[idx]
        art = QTableWidgetItem()
        art.setIcon(self._thumb_icon(item.source_path))
        art.setData(Qt.UserRole, idx)
        self.setItem(row, 0, art)
        template_label = item.template_mode
        if item.template_mode == "Auto":
            confidence = int(round(max(0.0, min(1.0, float(item.routing_confidence))) * 100))
            template_label = f"Auto → {item.selected_layout} ({confidence}%)" if confidence else f"Auto → {item.selected_layout}"
            if item.routing_needs_check:
                template_label += " ⚠"
        for col, text in [
            (1, item.submission_id or "—"),
            (2, item.child_name),
            (3, item.city),
            (4, template_label),
            (5, item.status),
        ]:
            cell = QTableWidgetItem(text)
            cell.setData(Qt.UserRole, idx)
            self.setItem(row, col, cell)

        edit = QPushButton()
        edit.setToolTip("Edit ID, name, location and template mode")
        edit.setIcon(QIcon(str(ICON_DIR / "edit.jpg")))
        edit.setFixedSize(34, 34)
        edit.clicked.connect(lambda _=False, i=idx: self.toggle_edit(i))
        self.setCellWidget(row, 6, edit)

        delete = QPushButton()
        delete.setToolTip("Delete this artwork from current batch")
        delete.setIcon(QIcon(str(ICON_DIR / "delete.webp")))
        delete.setFixedSize(34, 34)
        delete.clicked.connect(lambda _=False, i=idx: self.delete_requested.emit(i))
        self.setCellWidget(row, 7, delete)

    def refresh_index(self, idx: int):
        """Refresh one visible row only; avoids rebuilding 250 rows after Compare Save."""
        row = self.find_row_for_index(idx)
        if row >= 0 and 0 <= idx < len(self._items):
            selected = self.currentRow() == row
            self._populate_row(row, idx)
            if selected:
                self.selectRow(row)

    def find_row_for_index(self, idx: int) -> int:
        for row, original in self._row_to_index.items():
            if original == idx:
                return row
        return -1

    def select_index(self, idx: int):
        row = self.find_row_for_index(idx)
        if row >= 0:
            self.selectRow(row)
            self.scrollToItem(self.item(row, 2), QAbstractItemView.EnsureVisible)

    def _selection_changed(self):
        rows = self.selectionModel().selectedRows()
        if rows:
            idx = self._row_to_index.get(rows[0].row())
            if idx is not None:
                self.preview_requested.emit(idx)

    def _open_compare_from_row(self, row: int, _column: int):
        idx = self._row_to_index.get(row)
        if idx is not None:
            self.preview_requested.emit(idx)
            self.compare_requested.emit(idx)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.rowCount() > 0:
            current = self.currentRow()
            if current < 0:
                current = 0
            else:
                current += -1 if event.key() == Qt.Key.Key_Up else 1
            current = max(0, min(self.rowCount() - 1, current))
            self.selectRow(current)
            idx = self._row_to_index.get(current)
            if idx is not None:
                self.preview_requested.emit(idx)
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle_edit(self, idx: int):
        row = self.find_row_for_index(idx)
        if row < 0:
            return
        if self._editing_row is not None and self._editing_row != row:
            self.finish_edit(save=True)
        if self._editing_row == row:
            self.finish_edit(save=True)
            return

        self._editing_row = row
        self._editing_idx = idx
        item = self._items[idx]
        self._edit_original = (item.submission_id, item.child_name, item.city, item.template_mode)

        id_edit = QLineEdit(item.submission_id)
        name_edit = QLineEdit(item.child_name)
        city_edit = QLineEdit(item.city)
        mode = QComboBox()
        mode.addItems(["Auto", "Portrait", "Landscape", "Square"])
        mode.setCurrentText(item.template_mode)
        self._edit_widgets = [id_edit, name_edit, city_edit, mode]
        for w in self._edit_widgets:
            w.installEventFilter(self._editor_filter)
        for c, w in [(1, id_edit), (2, name_edit), (3, city_edit), (4, mode)]:
            self.setCellWidget(row, c, w)
        name_edit.setFocus()
        name_edit.selectAll()

    def _global_focus_changed(self, old, new):
        if self._editing_row is None:
            return
        if old not in self._edit_widgets:
            return
        # Keep editing while focus moves between the four editors or into a combo popup.
        if new in self._edit_widgets:
            return
        if new is not None:
            for editor in self._edit_widgets:
                if editor.isAncestorOf(new):
                    return
        QTimer.singleShot(0, lambda: self.finish_edit(save=True))

    def finish_edit(self, save: bool = True):
        if self._editing_row is None:
            return
        row = self._editing_row
        idx = self._editing_idx
        widgets = list(self._edit_widgets)
        original = self._edit_original

        self._editing_row = None
        self._editing_idx = None
        self._edit_widgets = []
        self._edit_original = None

        if idx is None or not (0 <= idx < len(self._items)):
            return
        item = self._items[idx]
        if save and len(widgets) == 4:
            id_edit, name_edit, city_edit, mode = widgets
            item.submission_id = id_edit.text().strip()
            item.child_name = name_edit.text().strip()
            item.city = city_edit.text().strip()
            item.template_mode = mode.currentText()
            self.edit_changed.emit(idx)
        elif not save and original:
            item.submission_id, item.child_name, item.city, item.template_mode = original

        # Replace the editors immediately with normal row text.
        if row in self._row_to_index and self._row_to_index[row] == idx:
            self._populate_row(row, idx)
            self.selectRow(row)
