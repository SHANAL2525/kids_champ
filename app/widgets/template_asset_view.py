from __future__ import annotations

import json
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QSize, QMimeData
from PySide6.QtGui import QIcon, QPixmap, QBrush, QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QMenu, QLabel, QListView, QScrollArea,
    QFrame, QApplication
)

from ..models import TemplateRecord
from ..config import GROUP_COLORS, ICON_DIR

MIME_TEMPLATE_KEYS = "application/x-kidschamp-template-keys"
DEFAULT_GROUP = "Defaults"
UNGROUPED = "Ungrouped"


class GroupTree(QTreeWidget):
    move_to_group = Signal(list, str)
    group_selected = Signal(str)
    activate_key = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Template", "Type", "Lock"])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(False)
        self.setUniformRowHeights(True)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item and item.data(0, Qt.UserRole + 1) and not item.data(0, Qt.UserRole):
            self.group_selected.emit(str(item.data(0, Qt.UserRole + 1)))
        elif item and item.data(0, Qt.UserRole):
            # The radio marker is the left-most part of the template row.
            rect = self.visualItemRect(item)
            if pos.x() <= rect.left() + 30:
                self.activate_key.emit(str(item.data(0, Qt.UserRole)))
        super().mousePressEvent(event)

    def dropEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        if not target:
            event.ignore(); return
        group = target.data(0, Qt.UserRole + 1)
        if not group and target.parent():
            group = target.parent().data(0, Qt.UserRole + 1)
        group = str(group or "")
        if not group or group.lower() == DEFAULT_GROUP.lower():
            event.ignore(); return
        keys = [i.data(0, Qt.UserRole) for i in self.selectedItems() if i.data(0, Qt.UserRole)]
        if keys:
            self.move_to_group.emit(keys, group)
            event.acceptProposedAction()
            return
        event.ignore()


class GroupListWidget(QListWidget):
    move_keys = Signal(list, str)
    context_requested = Signal(object, object)
    selection_updated = Signal(object)
    activate_key = Signal(str)

    def __init__(self, group_name: str, owner, parent=None):
        super().__init__(parent)
        self.group_name = group_name
        self.owner = owner
        self.setViewMode(QListView.IconMode)
        self.setIconSize(QSize(166, 86))
        self.setGridSize(QSize(184, 132))
        self.setResizeMode(QListView.Adjust)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(group_name.lower() != DEFAULT_GROUP.lower())
        self.setAcceptDrops(group_name.lower() != DEFAULT_GROUP.lower())
        self.setDropIndicatorShown(group_name.lower() != DEFAULT_GROUP.lower())
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda p: self.context_requested.emit(self, p))
        self.itemSelectionChanged.connect(lambda: self.selection_updated.emit(self))

    def mousePressEvent(self, event):
        self.owner.set_active_group(self.group_name)
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualItemRect(item)
            # The full left strip of every card behaves as the radio selector.
            if pos.x() <= rect.left() + 30 and pos.y() <= rect.top() + 90:
                key = item.data(Qt.UserRole)
                if key:
                    self.activate_key.emit(str(key))
        if event.button() == Qt.MouseButton.LeftButton and not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier):
            self.owner.clear_other_grid_selections(self)
        super().mousePressEvent(event)

    def startDrag(self, supportedActions):
        if self.group_name.lower() == DEFAULT_GROUP.lower():
            return
        keys = self.owner.selected_keys()
        if not keys:
            return
        mime = QMimeData()
        mime.setData(MIME_TEMPLATE_KEYS, json.dumps(keys).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        selected = self.selectedItems()
        if selected and not selected[0].icon().isNull():
            drag.setPixmap(selected[0].icon().pixmap(QSize(96, 64)))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if self.group_name.lower() == DEFAULT_GROUP.lower():
            event.ignore(); return
        if event.mimeData().hasFormat(MIME_TEMPLATE_KEYS):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.group_name.lower() == DEFAULT_GROUP.lower():
            event.ignore(); return
        if event.mimeData().hasFormat(MIME_TEMPLATE_KEYS):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.group_name.lower() == DEFAULT_GROUP.lower():
            event.ignore(); return
        if event.mimeData().hasFormat(MIME_TEMPLATE_KEYS):
            try:
                keys = json.loads(bytes(event.mimeData().data(MIME_TEMPLATE_KEYS)).decode("utf-8"))
            except Exception:
                keys = []
            if keys:
                self.move_keys.emit(keys, self.group_name)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class TemplateAssetView(QWidget):
    selection_changed = Signal(list)
    mode_changed = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(list)
    duplicate_requested = Signal(list)
    lock_requested = Signal(list, bool)
    reveal_requested = Signal(str)
    move_requested = Signal(list, str)
    new_group_requested = Signal()
    new_template_requested = Signal(str)
    group_edit_requested = Signal(str)
    group_delete_requested = Signal(str)
    group_duplicate_requested = Signal(str)
    group_lock_requested = Signal(str, bool)
    active_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records: dict[str, TemplateRecord] = {}
        self.groups: list[dict] = []
        self.mode = "grid"
        self.grid_lists: dict[str, GroupListWidget] = {}
        self.group_buttons: dict[str, QPushButton] = {}
        self._syncing_selection = False
        self.active_group = UNGROUPED
        self.active_template_keys: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        # Only the view switch stays at the top. Creation/actions live in the Photoshop-style bottom toolbar.
        viewbar = QHBoxLayout()
        viewbar.addStretch(1)
        self.grid_btn = QPushButton("▦")
        self.list_btn = QPushButton("☰")
        self.grid_btn.setToolTip("Grid view")
        self.list_btn.setToolTip("List view")
        self.grid_btn.setFixedWidth(38)
        self.list_btn.setFixedWidth(38)
        self.grid_btn.clicked.connect(lambda: self.set_mode("grid"))
        self.list_btn.clicked.connect(lambda: self.set_mode("list"))
        viewbar.addWidget(self.grid_btn)
        viewbar.addWidget(self.list_btn)
        root.addLayout(viewbar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_content = QWidget()
        self.grid_layout = QVBoxLayout(self.grid_content)
        self.grid_layout.setContentsMargins(3, 3, 3, 3)
        self.grid_layout.setSpacing(8)
        self.grid_scroll.setWidget(self.grid_content)
        self.stack.addWidget(self.grid_scroll)

        self.tree = GroupTree()
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda p: self._menu(self.tree, p))
        self.tree.itemDoubleClicked.connect(self._tree_double)
        self.tree.itemClicked.connect(self._tree_click)
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.tree.move_to_group.connect(self.move_requested)
        self.tree.group_selected.connect(self.select_group_header)
        self.tree.activate_key.connect(self.active_requested)
        self.stack.addWidget(self.tree)

        # Bottom asset toolbar: Duplicate / New Template / New Group / Lock / Delete.
        self.action_bar = QFrame()
        self.action_bar.setObjectName("AssetToolbar")
        bar = QHBoxLayout(self.action_bar)
        bar.setContentsMargins(5, 5, 5, 5)
        bar.setSpacing(5)
        self.duplicate_btn = QPushButton()
        self.new_btn = QPushButton()
        self.group_btn = QPushButton()
        self.lock_btn = QPushButton()
        self.delete_btn = QPushButton()
        self.duplicate_btn.setIcon(QIcon(str(ICON_DIR / "duplicate.svg")))
        self.new_btn.setIcon(QIcon(str(ICON_DIR / "new_template.svg")))
        self.group_btn.setIcon(QIcon(str(ICON_DIR / "new_group.svg")))
        self.lock_btn.setIcon(QIcon(str(ICON_DIR / "lock.svg")))
        self.delete_btn.setIcon(QIcon(str(ICON_DIR / "delete.svg")))
        for _b in (self.duplicate_btn, self.new_btn, self.group_btn, self.lock_btn, self.delete_btn):
            _b.setIconSize(QSize(20, 20))
        for button, tip in [
            (self.duplicate_btn, "Duplicate selected template"),
            (self.new_btn, "New Template"),
            (self.group_btn, "New Group"),
            (self.lock_btn, "Lock / Unlock selected template"),
            (self.delete_btn, "Delete selected template or group"),
        ]:
            button.setObjectName("IconButton")
            button.setFixedSize(40, 36)
            button.setToolTip(tip)
            bar.addWidget(button)
        bar.addStretch(1)
        root.addWidget(self.action_bar)

        self.duplicate_btn.clicked.connect(self._toolbar_duplicate)
        self.new_btn.clicked.connect(lambda: self.new_template_requested.emit(self.target_group_for_new_template()))
        self.group_btn.clicked.connect(self.new_group_requested.emit)
        self.lock_btn.clicked.connect(self._toolbar_lock)
        self.delete_btn.clicked.connect(self._toolbar_delete)

        self.set_mode("grid")
        self._update_toolbar_state()

    @staticmethod
    def _radio_thumbnail(path, active: bool) -> QIcon:
        """Draw an unmistakable radio control on the left of every template card."""
        source = QPixmap(str(path))
        thumb = source.scaled(136, 82, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        canvas = QPixmap(166, 86)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#2563EB" if active else "#94A3B8"), 2))
        painter.setBrush(QColor("#2563EB") if active else Qt.transparent)
        painter.drawEllipse(4, 33, 18, 18)
        if active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(10, 39, 6, 6)
        painter.drawPixmap(28 + max(0, (136 - thumb.width()) // 2), max(0, (86 - thumb.height()) // 2), thumb)
        painter.end()
        return QIcon(canvas)

    def _ordered_group_names(self, records: list[TemplateRecord], groups: list[dict]) -> list[str]:
        # Custom groups first, then Ungrouped if used, permanent Defaults always last.
        names = []
        seen = set()
        for g in groups:
            name = str(g.get("name", "")).strip()
            if not name or name.lower() in {DEFAULT_GROUP.lower(), UNGROUPED.lower()}:
                continue
            if name.lower() not in seen:
                names.append(name); seen.add(name.lower())
        for r in records:
            name = str(r.group or UNGROUPED).strip() or UNGROUPED
            if name.lower() == DEFAULT_GROUP.lower():
                continue
            if name.lower() == UNGROUPED.lower():
                continue
            if name.lower() not in seen:
                names.append(name); seen.add(name.lower())
        if any((r.group or UNGROUPED).lower() == UNGROUPED.lower() for r in records):
            names.append(UNGROUPED)
        names.append(DEFAULT_GROUP)
        return names

    def set_active_group(self, group_name: str):
        group_name = str(group_name or UNGROUPED)
        self.active_group = group_name
        for name, button in self.group_buttons.items():
            button.setProperty("selectedGroup", name == group_name)
            button.style().unpolish(button); button.style().polish(button)
        self._update_toolbar_state()

    def select_group_header(self, group_name: str):
        """Select a group itself (not a template) so the bottom toolbar acts on the group."""
        self._syncing_selection = True
        try:
            if self.mode == "grid":
                for lst in self.grid_lists.values():
                    lst.clearSelection()
            else:
                self.tree.clearSelection()
        finally:
            self._syncing_selection = False
        self.set_active_group(group_name)
        self.selection_changed.emit([])
        self._update_toolbar_state()

    def target_group_for_new_template(self) -> str:
        keys = self.selected_keys()
        if keys:
            rec = self.records.get(keys[0])
            if rec and not rec.is_default and rec.group.lower() != DEFAULT_GROUP.lower():
                return rec.group or UNGROUPED
        if self.active_group and self.active_group.lower() != DEFAULT_GROUP.lower():
            return self.active_group
        return UNGROUPED

    def set_mode(self, mode: str):
        previous = self.selected_keys() if hasattr(self, "tree") else []
        self.mode = mode
        self.mode_changed.emit(mode)
        self.stack.setCurrentWidget(self.grid_scroll if mode == "grid" else self.tree)
        self.grid_btn.setObjectName("SegmentActive" if mode == "grid" else "SegmentInactive")
        self.list_btn.setObjectName("SegmentActive" if mode == "list" else "SegmentInactive")
        for b in (self.grid_btn, self.list_btn):
            b.style().unpolish(b); b.style().polish(b)
        if previous:
            self.set_selected_keys(previous)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if child_layout:
                self._clear_layout(child_layout)
            if widget:
                widget.deleteLater()

    def set_data(self, records: list[TemplateRecord], groups: list[dict], active_keys=None):
        previous_selection = set(self.selected_keys())
        self._syncing_selection = True
        self.records = {r.key: r for r in records}
        self.groups = groups
        if isinstance(active_keys, dict):
            self.active_template_keys = {str(v) for v in active_keys.values() if v}
        else:
            self.active_template_keys = {str(v) for v in (active_keys or []) if v}
        grouped = defaultdict(list)
        for r in records:
            group = r.group or UNGROUPED
            # Safety migration in UI: custom content is never allowed inside Defaults.
            if not r.is_default and group.lower() == DEFAULT_GROUP.lower():
                group = UNGROUPED
            grouped[group].append(r)
        group_names = self._ordered_group_names(records, groups)

        # GRID VIEW: each group header followed by its own content, Defaults pinned last.
        self._clear_layout(self.grid_layout)
        self.grid_lists = {}
        self.group_buttons = {}
        for group_name in group_names:
            group_records = grouped.get(group_name, [])
            if not group_records and group_name in {UNGROUPED, DEFAULT_GROUP} and group_name != DEFAULT_GROUP:
                continue
            color_name = next((g.get("color", "Blue") for g in groups if str(g.get("name", "")).lower() == group_name.lower()), "Blue")
            color = GROUP_COLORS.get(color_name, "#3B82F6")
            section = QFrame(); section.setObjectName("GroupSection")
            section_layout = QVBoxLayout(section); section_layout.setContentsMargins(8, 7, 8, 7); section_layout.setSpacing(5)
            header = QHBoxLayout()
            dot = QLabel("●"); dot.setStyleSheet(f"color:{color};font-size:13px;")
            group_button = QPushButton(f"{group_name}   {len(group_records)}")
            group_button.setObjectName("GroupHeader")
            group_button.setToolTip("Click to select this group")
            group_button.clicked.connect(lambda _=False, g=group_name: self.select_group_header(g))
            self.group_buttons[group_name] = group_button
            options = QPushButton("⋯"); options.setObjectName("IconButton"); options.setFixedWidth(34)
            options.clicked.connect(lambda _=False, g=group_name, b=options: self._group_menu(g, b))
            header.addWidget(dot); header.addWidget(group_button, 1); header.addWidget(options)
            section_layout.addLayout(header)

            lst = GroupListWidget(group_name, self)
            lst.move_keys.connect(self.move_requested)
            lst.context_requested.connect(self._menu)
            lst.selection_updated.connect(self._grid_selection)
            lst.activate_key.connect(self.active_requested)
            lst.itemDoubleClicked.connect(self._grid_double)
            for r in group_records:
                thumb = r.thumbnail_image or r.template_image
                active = r.key in self.active_template_keys
                icon = self._radio_thumbnail(thumb, active)
                lock = "  🔒" if r.locked else ""
                item = QListWidgetItem(icon, f"{r.name}{lock}\n{r.template_type}")
                item.setData(Qt.UserRole, r.key)
                item.setToolTip(str(r.folder))
                lst.addItem(item)
                if r.key in previous_selection:
                    item.setSelected(True)
            rows = max(1, (len(group_records) + 1) // 2)
            height = min(500, max(138, rows * 134 + 8))
            lst.setMinimumHeight(height); lst.setMaximumHeight(height)
            self.grid_lists[group_name] = lst
            section_layout.addWidget(lst)
            self.grid_layout.addWidget(section)
        self.grid_layout.addStretch(1)

        # LIST VIEW: real parent-child hierarchy; Defaults pinned last.
        self.tree.clear()
        for group_name in group_names:
            group_records = grouped.get(group_name, [])
            if not group_records and group_name == UNGROUPED:
                continue
            gnode = QTreeWidgetItem([group_name, "", ""])
            gnode.setData(0, Qt.UserRole + 1, group_name)
            gnode.setFlags((gnode.flags() | Qt.ItemIsDropEnabled) if group_name.lower() != DEFAULT_GROUP.lower() else (gnode.flags() & ~Qt.ItemIsDropEnabled))
            color_name = next((g.get("color", "Blue") for g in groups if str(g.get("name", "")).lower() == group_name.lower()), "Blue")
            gnode.setForeground(0, QBrush(QColor(GROUP_COLORS.get(color_name, "#3B82F6"))))
            font = gnode.font(0); font.setBold(True); gnode.setFont(0, font)
            self.tree.addTopLevelItem(gnode)
            for r in group_records:
                radio = "●" if r.key in self.active_template_keys else "○"
                child = QTreeWidgetItem([f"{radio}  {r.name}", r.template_type, "🔒" if r.locked else "🔓"])
                child.setData(0, Qt.UserRole, r.key)
                child.setFlags(child.flags() | Qt.ItemIsDragEnabled)
                gnode.addChild(child)
                if r.key in previous_selection:
                    child.setSelected(True)
            gnode.setExpanded(True)
        self.tree.resizeColumnToContents(0); self.tree.resizeColumnToContents(1); self.tree.setColumnWidth(2, 58)
        self._syncing_selection = False
        self._update_toolbar_state()
        self.set_active_group(self.active_group if self.active_group in group_names else UNGROUPED)

    def clear_other_grid_selections(self, current: GroupListWidget):
        if self._syncing_selection:
            return
        self._syncing_selection = True
        for lst in self.grid_lists.values():
            if lst is not current:
                lst.clearSelection()
        self._syncing_selection = False

    def selected_keys(self):
        if self.mode == "grid":
            keys = []
            for lst in self.grid_lists.values():
                keys.extend([i.data(Qt.UserRole) for i in lst.selectedItems() if i.data(Qt.UserRole)])
            return keys
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems() if i.data(0, Qt.UserRole)]

    def set_selected_keys(self, keys: list[str]):
        wanted = set(keys); self._syncing_selection = True
        try:
            if self.mode == "grid":
                for lst in self.grid_lists.values():
                    lst.clearSelection()
                    for i in range(lst.count()):
                        item = lst.item(i)
                        if item.data(Qt.UserRole) in wanted:
                            item.setSelected(True)
            else:
                self.tree.clearSelection()
                for key in wanted:
                    item = self._tree_item_for_key(key)
                    if item: item.setSelected(True)
        finally:
            self._syncing_selection = False
        self._selection_changed_now()

    def select_key(self, key: str):
        rec = self.records.get(key)
        if rec:
            self.set_active_group(rec.group if not (not rec.is_default and rec.group.lower() == DEFAULT_GROUP.lower()) else UNGROUPED)
        if self.mode == "grid":
            for lst in self.grid_lists.values():
                for i in range(lst.count()):
                    item = lst.item(i)
                    if item.data(Qt.UserRole) == key:
                        self.clear_other_grid_selections(lst); item.setSelected(True); lst.scrollToItem(item); self._selection_changed_now(); return
        else:
            it = self._tree_item_for_key(key)
            if it:
                self.tree.clearSelection(); it.setSelected(True); self.tree.scrollToItem(it); self._selection_changed_now()

    def _tree_item_for_key(self, key: str):
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.UserRole) == key:
                    return child
        return None

    def _selection_changed_now(self):
        self._update_toolbar_state()
        self.selection_changed.emit(self.selected_keys())

    def _grid_selection(self, _source=None):
        if not self._syncing_selection:
            self._selection_changed_now()

    def _grid_double(self, item):
        key = item.data(Qt.UserRole)
        if key: self.edit_requested.emit(key)

    def _tree_selection(self):
        selected = self.tree.selectedItems()
        if len(selected) == 1 and selected[0].data(0, Qt.UserRole + 1) and not selected[0].data(0, Qt.UserRole):
            self.set_active_group(str(selected[0].data(0, Qt.UserRole + 1)))
        self._selection_changed_now()

    def _tree_double(self, item, _col):
        key = item.data(0, Qt.UserRole)
        if key: self.edit_requested.emit(key)

    def _tree_click(self, item, col):
        key = item.data(0, Qt.UserRole)
        if key and col == 2 and key in self.records:
            rec = self.records[key]
            if rec.is_default:
                return
            self.lock_requested.emit([key], not rec.locked)

    def _group_menu(self, group_name: str, button: QPushButton | None = None, global_pos=None):
        self.select_group_header(group_name)
        menu = QMenu(self)
        protected = group_name.lower() in {DEFAULT_GROUP.lower(), UNGROUPED.lower()}

        new_template = menu.addAction("New Template Here")
        new_template.setEnabled(group_name.lower() != DEFAULT_GROUP.lower())
        new_template.triggered.connect(lambda: self.new_template_requested.emit(group_name if group_name.lower() != DEFAULT_GROUP.lower() else UNGROUPED))

        if not protected:
            duplicate = menu.addAction("Duplicate Group")
            duplicate.triggered.connect(lambda: self.group_duplicate_requested.emit(group_name))
            menu.addSeparator()
            rename = menu.addAction("Edit Group")
            rename.triggered.connect(lambda: self.group_edit_requested.emit(group_name))
            delete = menu.addAction("Delete Group")
            delete.triggered.connect(lambda: self.group_delete_requested.emit(group_name))

        if global_pos is not None:
            menu.exec(global_pos)
        elif button is not None:
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _menu(self, widget, pos):
        if widget is self.tree:
            target = self.tree.itemAt(pos)
            if target and target.data(0, Qt.UserRole + 1) and not target.data(0, Qt.UserRole):
                group_name = str(target.data(0, Qt.UserRole + 1))
                self._group_menu(group_name, global_pos=widget.mapToGlobal(pos)); return
        else:
            item = widget.itemAt(pos)
            if item and item.data(Qt.UserRole) and not item.isSelected():
                if not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier):
                    self.clear_other_grid_selections(widget); widget.clearSelection()
                item.setSelected(True)

        keys = self.selected_keys()
        menu = QMenu(self)
        edit = menu.addAction("Edit")
        edit.setEnabled(len(keys) == 1)
        edit.triggered.connect(lambda: self.edit_requested.emit(keys[0]) if keys else None)
        dup = menu.addAction("Duplicate")
        dup.setEnabled(bool(keys)); dup.triggered.connect(lambda: self.duplicate_requested.emit(keys))
        selected = [self.records[k] for k in keys if k in self.records]
        lock = menu.addAction("Lock")
        lock.setEnabled(any(not r.locked and not r.is_default for r in selected))
        lock.triggered.connect(lambda: self.lock_requested.emit(keys, True))
        unlock = menu.addAction("Unlock")
        unlock.setEnabled(any(r.locked and not r.is_default for r in selected))
        unlock.triggered.connect(lambda: self.lock_requested.emit(keys, False))
        move = menu.addMenu("Move to Group")
        move.setEnabled(bool(keys) and any(not r.is_default for r in selected))
        for g in self._movable_group_names():
            a = move.addAction(g); a.triggered.connect(lambda _=False, n=g: self.move_requested.emit(keys, n))
        reveal = menu.addAction("Show local file")
        reveal.setEnabled(len(keys) == 1); reveal.triggered.connect(lambda: self.reveal_requested.emit(keys[0]) if keys else None)
        menu.addSeparator()
        delete = menu.addAction("Delete")
        delete.setEnabled(bool(keys)); delete.triggered.connect(lambda: self.delete_requested.emit(keys))
        menu.exec(widget.mapToGlobal(pos))

    def _movable_group_names(self):
        out = []
        for g in self.groups:
            name = str(g.get("name", ""))
            if name and name.lower() != DEFAULT_GROUP.lower() and name not in out:
                out.append(name)
        if UNGROUPED not in out: out.append(UNGROUPED)
        return out

    def _toolbar_duplicate(self):
        keys = self.selected_keys()
        if keys:
            self.duplicate_requested.emit(keys)
            return
        if self.active_group and self.active_group.lower() not in {DEFAULT_GROUP.lower(), UNGROUPED.lower()}:
            self.group_duplicate_requested.emit(self.active_group)

    def _toolbar_lock(self):
        keys = self.selected_keys()
        if keys:
            selected = [self.records[k] for k in keys if k in self.records and not self.records[k].is_default]
            if not selected:
                return
            lock_value = any(not r.locked for r in selected)
            self.lock_requested.emit([r.key for r in selected], lock_value)
            return
        # Group selected: lock/unlock all custom templates in that group.
        if self.active_group and self.active_group.lower() not in {DEFAULT_GROUP.lower(), UNGROUPED.lower()}:
            group_records = [r for r in self.records.values() if r.group == self.active_group and not r.is_default]
            if group_records:
                lock_value = any(not r.locked for r in group_records)
                self.group_lock_requested.emit(self.active_group, lock_value)

    def _toolbar_delete(self):
        keys = self.selected_keys()
        if keys:
            self.delete_requested.emit(keys)
            return
        if self.active_group and self.active_group.lower() not in {DEFAULT_GROUP.lower(), UNGROUPED.lower()}:
            self.group_delete_requested.emit(self.active_group)

    def _update_toolbar_state(self):
        keys = self.selected_keys()
        records = [self.records[k] for k in keys if k in self.records]
        custom_group_selected = bool(self.active_group and self.active_group.lower() not in {DEFAULT_GROUP.lower(), UNGROUPED.lower()})
        group_records = [r for r in self.records.values() if custom_group_selected and r.group == self.active_group and not r.is_default]

        # Toolbar follows Photoshop-style selection: selected template first, otherwise selected group.
        self.duplicate_btn.setEnabled(bool(keys) or custom_group_selected)
        self.new_btn.setEnabled(True)
        self.group_btn.setEnabled(True)
        self.lock_btn.setEnabled(any(not r.is_default for r in records) or bool(group_records))
        self.delete_btn.setEnabled(bool(keys) or custom_group_selected)

        lock_candidates = [r for r in records if not r.is_default] if keys else group_records
        if lock_candidates and all(r.locked for r in lock_candidates):
            self.lock_btn.setIcon(QIcon(str(ICON_DIR / "unlock.svg")))
            self.lock_btn.setToolTip("Unlock selected template/group")
        else:
            self.lock_btn.setIcon(QIcon(str(ICON_DIR / "lock.svg")))
            self.lock_btn.setToolTip("Lock selected template/group")

        if keys:
            self.duplicate_btn.setToolTip("Duplicate selected template")
            self.delete_btn.setToolTip("Delete selected template")
        elif custom_group_selected:
            self.duplicate_btn.setToolTip("Duplicate selected group")
            self.delete_btn.setToolTip("Delete selected group")
        else:
            self.duplicate_btn.setToolTip("Select a template or custom group to duplicate")
            self.delete_btn.setToolTip("Select a template or custom group to delete")
