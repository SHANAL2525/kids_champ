from __future__ import annotations

from dataclasses import replace
import copy

from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QPushButton,
    QLineEdit, QComboBox, QSlider, QSpinBox, QMenu, QSplitter, QCheckBox,
    QRadioButton, QButtonGroup, QDialog, QDialogButtonBox,
)

from ..config import ICON_DIR
from ..widgets.crop_warp_view import CropWarpView
from ..image_processing import list_presets, crop_image_normalized, geometry_for_path, apply_color_stage
from ..image_renderer import default_transform_quad, place_artwork
from ..models import Box
from ..template_store import get_active_template
from ..preview_service import PreviewTask, PreviewPool
from .dialogs import MessageDialog


class ColorAdjustmentDialog(QDialog):
    values_changed = Signal(object)

    def __init__(self, title, controls, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.sliders = {}
        self.value_labels = {}
        for row, (key, label, minimum, maximum) in enumerate(controls):
            grid.addWidget(QLabel(label), row, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(minimum, maximum)
            slider.setValue(int(values.get(key, 0)))
            value_label = QLabel(str(slider.value()))
            value_label.setMinimumWidth(38)
            slider.valueChanged.connect(lambda value, k=key, v=value_label: self._changed(k, value, v))
            self.sliders[key] = slider
            self.value_labels[key] = value_label
            grid.addWidget(slider, row, 1)
            grid.addWidget(value_label, row, 2)
        layout.addLayout(grid)
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

    def _changed(self, key, value, label):
        label.setText(str(value))
        self.values_changed.emit(self.values())

    def _reset(self):
        for slider in self.sliders.values():
            slider.setValue(0)

    def values(self):
        return {key: slider.value() for key, slider in self.sliders.items()}


class ComparePage(QWidget):
    """Per-image Compare editor.

    Global template geometry belongs to Template Settings. Compare edits only one child
    artwork. RIGHT = source editor (Crop/Wrap/colour). LEFT = final template placement
    (Move/Zoom/Fit). Crop and Wrap never edit or resize the locked template graphics.
    """

    status = Signal(str)

    def __init__(self, generator, parent=None):
        super().__init__(parent)
        self.generator = generator
        self.current_index = -1
        self.editing = True
        self.dirty = False
        self.show_original = False
        self.undo_stack = []
        self.redo_stack = []
        self.work: dict = {}
        self._loading = False
        self.active_tool = "move"
        self.transform_surface = "right"
        self._transform_session_snapshot = None
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(140)
        self.render_timer.timeout.connect(self._request_preview)
        self.transform_proxy_timer = QTimer(self)
        self.transform_proxy_timer.setSingleShot(True)
        self.transform_proxy_timer.setInterval(90)
        self.transform_proxy_timer.timeout.connect(lambda: self._render_edit_source("transform"))
        self._preview_request_id = 0
        self._preview_tasks = {}
        self._preview_cache = {}
        self._preview_active = False
        self._pending_preview = None
        self._build()
        self._shortcuts()
        self._set_empty_state()

    def _icon(self, name):
        return QIcon(str(ICON_DIR / name))

    def _tool(self, icon, tip, callback, check=False):
        button = QPushButton()
        button.setObjectName("ToolButton")
        button.setIcon(self._icon(icon))
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tip)
        button.setFixedSize(40, 40)
        button.setCheckable(check)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _segment(text):
        button = QPushButton(text)
        button.setCheckable(True)
        button.setObjectName("SegmentInactive")
        return button

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(5)

        # Navigation deliberately stays in the familiar upper-right position.
        top = QHBoxLayout()
        title = QLabel("Compare")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        self.counter = QLabel("0 / 0")
        self.counter.setObjectName("Muted")
        top.addWidget(self.counter)
        top.addStretch(1)
        self.prev_btn = QPushButton("‹ Previous")
        self.next_btn = QPushButton("Next ›")
        self.prev_btn.clicked.connect(lambda: self.navigate(-1))
        self.next_btn.clicked.connect(lambda: self.navigate(1))
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        # LEFT — final output. Edit/template choice is above the image, actions below it.
        left = QFrame()
        left.setObjectName("Card")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(7, 7, 7, 7)
        ll.setSpacing(4)

        head = QHBoxLayout()
        head.addWidget(QLabel("Template"))
        self.template_buttons = {}
        for name in ("Auto", "Portrait", "Landscape", "Square"):
            b = self._segment(name)
            b.clicked.connect(lambda _=False, value=name: self.quick_template(value))
            self.template_buttons[name] = b
            head.addWidget(b)
        self.auto_route_label = QLabel("")
        self.auto_route_label.setObjectName("Muted")
        head.addWidget(self.auto_route_label)
        head.addStretch(1)
        self.preview_hint = QLabel("LEFT framed result • RIGHT original/source editing • live update")
        self.preview_hint.setObjectName("Muted")
        head.addWidget(self.preview_hint)
        ll.addLayout(head)

        preview_row = QHBoxLayout()
        self.tool_bar = QFrame()
        self.tool_bar.setObjectName("AssetToolbar")
        tb = QVBoxLayout(self.tool_bar)
        tb.setContentsMargins(4, 4, 4, 4)
        tb.setSpacing(4)

        self.move_tool = self._tool("tool_move.svg", "Move (V): drag artwork; mouse wheel zoom", self.activate_move, True)
        self.zoom_in_tool = self._tool("tool_zoom_in.svg", "Zoom in", lambda: self.change_zoom(1.1))
        self.zoom_out_tool = self._tool("tool_zoom_out.svg", "Zoom out", lambda: self.change_zoom(1 / 1.1))
        self.smart_tool = self._tool("tool_smart.svg", "Smart: auto-clean and stretch the complete artwork to the exact frame", lambda: self.set_fit_mode("Smart"), True)
        self.fit_tool = self._tool("tool_fit.svg", "Fit full artwork", lambda: self.set_fit_mode("Fit Full Artwork"), True)
        self.fill_tool = self._tool("tool_fill.svg", "Fill frame", lambda: self.set_fit_mode("Fill Frame"), True)
        self.rotate_left = self._tool("tool_rotate_left.svg", "Rotate left", lambda: self.rotate(90))
        self.rotate_right = self._tool("tool_rotate_right.svg", "Rotate right", lambda: self.rotate(-90))
        self.reset_tool = self._tool("tool_reset.svg", "Reset current artwork edits", self.reset_adjustment)
        for w in (
            self.move_tool, self.zoom_in_tool, self.zoom_out_tool,
            self.smart_tool, self.fit_tool, self.fill_tool,
            self.rotate_left, self.rotate_right, self.reset_tool,
        ):
            tb.addWidget(w)
        tb.addStretch(1)
        preview_row.addWidget(self.tool_bar)

        self.final_view = CropWarpView()
        self.final_view.setObjectName("Card")
        self.final_view.pan_requested.connect(self.pan_by)
        self.final_view.zoom_requested.connect(self.change_zoom)
        self.final_view.pan_committed.connect(self._interaction_committed)
        self.final_view.zoom_committed.connect(self._interaction_committed)
        self.final_view.operation_started.connect(self._history_operation_started)
        self.final_view.edit_requested.connect(self.activate_left_transform)
        self.final_view.transform_changed.connect(self._transform_changed)
        self.final_view.transform_committed.connect(self._transform_committed)
        preview_row.addWidget(self.final_view, 1)
        ll.addLayout(preview_row, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.apply_all_btn = QPushButton(" Apply All")
        self.apply_all_btn.setIcon(self._icon("tool_apply_all.svg"))
        self.apply_all_btn.clicked.connect(self.open_apply_all_menu)
        self.save_btn = QPushButton(" Save")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setIcon(self._icon("tool_save.svg"))
        self.save_btn.clicked.connect(self._save_and_clear_selection)
        actions.addWidget(self.apply_all_btn)
        actions.addWidget(self.save_btn)
        ll.addLayout(actions)

        controls = QFrame()
        controls.setObjectName("Card")
        g = QGridLayout(controls)
        g.setContentsMargins(7, 5, 7, 5)
        g.setSpacing(5)
        g.addWidget(QLabel("Fit"), 0, 0)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["Smart", "Fit Full Artwork", "Fill Frame"])
        self.fit_combo.currentTextChanged.connect(self._fit_changed)
        g.addWidget(self.fit_combo, 0, 1)
        g.addWidget(QLabel("Zoom"), 0, 2)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(25, 300)
        self.zoom_slider.valueChanged.connect(self._zoom_changed)
        self.zoom_slider.sliderPressed.connect(self._push_history)
        g.addWidget(self.zoom_slider, 0, 3)
        self.zoom_value = QLabel("100%")
        g.addWidget(self.zoom_value, 0, 4)
        g.addWidget(QLabel("X"), 1, 0)
        self.x_spin = QSpinBox()
        self.x_spin.setRange(-2000, 2000)
        self.x_spin.valueChanged.connect(self._offset_changed)
        g.addWidget(self.x_spin, 1, 1)
        g.addWidget(QLabel("Y"), 1, 2)
        self.y_spin = QSpinBox()
        self.y_spin.setRange(-2000, 2000)
        self.y_spin.valueChanged.connect(self._offset_changed)
        g.addWidget(self.y_spin, 1, 3)
        self.rotation_label = QLabel("0°")
        g.addWidget(self.rotation_label, 1, 4)

        ll.addWidget(controls)
        splitter.addWidget(left)

        # RIGHT — untouched uploaded source editor. Crop/Wrap live ONLY here.
        right = QFrame()
        right.setObjectName("Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(7, 7, 7, 7)
        rl.setSpacing(4)
        rh = QHBoxLayout()
        rt = QLabel("Original / Editable Source")
        rt.setObjectName("GroupTitle")
        rh.addWidget(rt)
        rh.addStretch(1)
        self.original_info = QLabel("")
        self.original_info.setObjectName("Muted")
        rh.addWidget(self.original_info)
        rl.addLayout(rh)

        source_tools = QHBoxLayout()
        source_tools.addWidget(QLabel("Source tools"))
        self.org_btn = self._tool("tool_original.svg", "Org Img: show the untouched uploaded image", self.toggle_original, True)
        self.crop_tool = self._tool("tool_crop.svg", "Crop (C): keep inside blue box; outside is removed", self.activate_crop, True)
        self.warp_tool = self._tool("tool_warp.svg", "Wrap (W): drag the 4 cropped artwork corners independently", self.activate_warp, True)
        self.transform_tool = self._tool("tool_transform.svg", "Free Transform (Ctrl+T): move, scale, stretch, rotate, distort", self.activate_transform, True)
        self.stretch_tool = self._tool("tool_stretch.svg", "Stretch / Push-Pull: drag a small area to reshape it", self.activate_stretch, True)
        self.color_balance_btn = QPushButton("Color Balance")
        self.color_balance_btn.setToolTip("Color Balance (Ctrl+B): adjust Cyan/Red, Magenta/Green and Yellow/Blue")
        self.color_balance_btn.clicked.connect(self.open_color_balance)
        self.hue_saturation_btn = QPushButton("Hue/Saturation")
        self.hue_saturation_btn.setToolTip("Hue/Saturation (Ctrl+U): adjust hue, saturation and lightness")
        self.hue_saturation_btn.clicked.connect(self.open_hue_saturation)
        self.undo_btn = self._tool("tool_undo.svg", "Undo", self.undo)
        self.redo_btn = self._tool("tool_redo.svg", "Redo", self.redo)
        self.source_reset_tool = self._tool("tool_reset.svg", "Reset source Crop/Wrap/Stretch", self.reset_source_geometry)
        for w in (self.org_btn, self.crop_tool, self.warp_tool, self.transform_tool, self.stretch_tool, self.color_balance_btn, self.hue_saturation_btn, self.undo_btn, self.redo_btn, self.source_reset_tool):
            source_tools.addWidget(w)
        source_tools.addStretch(1)
        rl.addLayout(source_tools)

        self.original_view = CropWarpView()
        self.original_view.setObjectName("Card")
        self.original_view.crop_changed.connect(self._crop_changed)
        self.original_view.warp_changed.connect(self._warp_changed)
        self.original_view.crop_committed.connect(self._geometry_committed)
        self.original_view.warp_committed.connect(self._geometry_committed)
        self.original_view.stretch_stroke.connect(self._stretch_stroke)
        self.original_view.stretch_committed.connect(self._geometry_committed)
        self.original_view.operation_started.connect(self._history_operation_started)
        self.original_view.transform_changed.connect(self._transform_changed)
        self.original_view.transform_committed.connect(self._transform_committed)
        rl.addWidget(self.original_view, 1)
        source_controls = QFrame()
        source_controls.setObjectName("Card")
        sg = QGridLayout(source_controls)
        sg.setContentsMargins(7, 5, 7, 5)
        sg.setSpacing(5)
        self.auto_check = QCheckBox("Safe Auto paper/background cleanup")
        self.auto_check.setToolTip("Auto crops only when confidence is high. If unsure, the complete original is preserved.")
        self.auto_check.toggled.connect(self._auto_changed)
        sg.addWidget(self.auto_check, 0, 0, 1, 2)
        sg.addWidget(QLabel("Brightness"), 0, 2)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(-80, 80)
        self.brightness_slider.valueChanged.connect(self._tone_changed)
        self.brightness_slider.sliderPressed.connect(self._push_history)
        self.brightness_slider.sliderReleased.connect(lambda: self._render_edit_source("view"))
        sg.addWidget(self.brightness_slider, 0, 3)
        self.brightness_value = QLabel("0")
        sg.addWidget(self.brightness_value, 0, 4)
        sg.addWidget(QLabel("Contrast"), 1, 0)
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(-80, 80)
        self.contrast_slider.valueChanged.connect(self._tone_changed)
        self.contrast_slider.sliderPressed.connect(self._push_history)
        self.contrast_slider.sliderReleased.connect(lambda: self._render_edit_source("view"))
        sg.addWidget(self.contrast_slider, 1, 1, 1, 3)
        self.contrast_value = QLabel("0")
        sg.addWidget(self.contrast_value, 1, 4)

        sg.addWidget(QLabel("Stretch size"), 2, 0)
        self.stretch_size = QSlider(Qt.Orientation.Horizontal)
        self.stretch_size.setRange(2, 30)
        self.stretch_size.setValue(10)
        self.stretch_size.valueChanged.connect(self._stretch_brush_changed)
        sg.addWidget(self.stretch_size, 2, 1, 1, 2)
        sg.addWidget(QLabel("Strength"), 2, 3)
        self.stretch_strength = QSlider(Qt.Orientation.Horizontal)
        self.stretch_strength.setRange(5, 100)
        self.stretch_strength.setValue(45)
        self.stretch_strength.valueChanged.connect(self._stretch_brush_changed)
        sg.addWidget(self.stretch_strength, 2, 4)

        preset_frame = QFrame()
        preset_row = QHBoxLayout(preset_frame)
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)
        preset_row.addWidget(QLabel("Preset"))
        self.preset_group = QButtonGroup(self)
        self.preset_radios = {}
        for value in ["None"] + list_presets():
            label = "None" if value == "None" else value.replace(".ACV", "")
            rb = QRadioButton(label)
            rb.setToolTip(value)
            rb.toggled.connect(lambda checked, v=value: checked and self._preset(v))
            self.preset_group.addButton(rb)
            self.preset_radios[value] = rb
            preset_row.addWidget(rb)
        preset_row.addStretch(1)
        sg.addWidget(preset_frame, 3, 0, 1, 5)
        rl.addWidget(source_controls)

        meta = QFrame()
        meta.setObjectName("Card")
        mg = QGridLayout(meta)
        mg.setContentsMargins(7, 5, 7, 5)
        mg.addWidget(QLabel("ID"), 0, 0)
        self.id_edit = QLineEdit()
        mg.addWidget(self.id_edit, 0, 1)
        mg.addWidget(QLabel("Name"), 1, 0)
        self.name_edit = QLineEdit()
        mg.addWidget(self.name_edit, 1, 1)
        mg.addWidget(QLabel("Location"), 2, 0)
        self.city_edit = QLineEdit()
        mg.addWidget(self.city_edit, 2, 1)
        for w in (self.id_edit, self.name_edit, self.city_edit):
            w.textChanged.connect(self._meta_changed)
        rl.addWidget(meta)
        splitter.addWidget(right)
        splitter.setSizes([830, 590])

        self._edit_widgets = [
            self.fit_combo, self.zoom_slider, self.x_spin, self.y_spin,
            self.auto_check, self.brightness_slider, self.contrast_slider,
            self.color_balance_btn, self.hue_saturation_btn,
            self.stretch_size, self.stretch_strength,
            self.id_edit, self.name_edit, self.city_edit,
        ]
        self._set_edit_enabled(True)

    def _shortcuts(self):
        self.transform_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.transform_shortcut.activated.connect(self.activate_transform)
        self.color_balance_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.color_balance_shortcut.activated.connect(self.open_color_balance)
        self.hue_saturation_shortcut = QShortcut(QKeySequence("Ctrl+U"), self)
        self.hue_saturation_shortcut.activated.connect(self.open_hue_saturation)
        self.move_shortcut = QShortcut(QKeySequence("V"), self)
        self.move_shortcut.activated.connect(lambda: self._tool_shortcut(self.activate_move))
        self.crop_shortcut = QShortcut(QKeySequence("C"), self)
        self.crop_shortcut.activated.connect(lambda: self._tool_shortcut(self.activate_crop))
        self.wrap_shortcut = QShortcut(QKeySequence("W"), self)
        self.wrap_shortcut.activated.connect(lambda: self._tool_shortcut(self.activate_warp))
        self.zoom_in_shortcut = QShortcut(QKeySequence("+"), self)
        self.zoom_in_shortcut.activated.connect(lambda: self.change_zoom(1.1))
        self.zoom_out_shortcut = QShortcut(QKeySequence("-"), self)
        self.zoom_out_shortcut.activated.connect(lambda: self.change_zoom(1 / 1.1))

        # Direct-edit mode has no Edit gate. Left/Right always navigate; use
        # Alt+Arrow for placement nudging so navigation never becomes ambiguous.
        self.previous_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.previous_shortcut.activated.connect(lambda: self._arrow_shortcut(-1, 0))
        self.next_shortcut.activated.connect(lambda: self._arrow_shortcut(1, 0))
        self.up_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        self.down_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        self.up_shortcut.activated.connect(lambda: self._arrow_shortcut(0, -1))
        self.down_shortcut.activated.connect(lambda: self._arrow_shortcut(0, 1))
        self.shift_left_shortcut = QShortcut(QKeySequence("Shift+Left"), self)
        self.shift_right_shortcut = QShortcut(QKeySequence("Shift+Right"), self)
        self.shift_up_shortcut = QShortcut(QKeySequence("Shift+Up"), self)
        self.shift_down_shortcut = QShortcut(QKeySequence("Shift+Down"), self)
        self.shift_left_shortcut.activated.connect(lambda: self._arrow_shortcut(-10, 0))
        self.shift_right_shortcut.activated.connect(lambda: self._arrow_shortcut(10, 0))
        self.shift_up_shortcut.activated.connect(lambda: self._arrow_shortcut(0, -10))
        self.shift_down_shortcut.activated.connect(lambda: self._arrow_shortcut(0, 10))
        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shift_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcut.activated.connect(self.redo)
        self.redo_shift_shortcut.activated.connect(self.redo)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self._save_from_shortcut)
        self.save_return_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self.save_enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        self.transform_cancel_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.transform_reset_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        self.save_return_shortcut.activated.connect(self._save_from_enter)
        self.save_enter_shortcut.activated.connect(self._save_from_enter)
        self.transform_cancel_shortcut.activated.connect(self.cancel_transform)
        self.transform_reset_shortcut.activated.connect(self.reset_transform)
        self._set_transform_shortcuts_enabled(False)

    def _set_transform_shortcuts_enabled(self, enabled):
        for shortcut in (
            getattr(self, "transform_cancel_shortcut", None),
            getattr(self, "transform_reset_shortcut", None),
        ):
            if shortcut is not None:
                shortcut.setEnabled(bool(enabled))

    def _text_input_has_focus(self):
        focused = self.focusWidget()
        return isinstance(focused, (QLineEdit, QSpinBox, QComboBox))

    def _tool_shortcut(self, callback):
        # Single-letter tool shortcuts must never steal student metadata text.
        if self.current_index >= 0 and not self._text_input_has_focus():
            callback()

    def _save_from_shortcut(self):
        if self.current_index >= 0 and self.editing:
            self._save_and_clear_selection()

    def _arrow_shortcut(self, dx, dy):
        if self.current_index < 0:
            return
        if self.active_tool == "transform":
            self._push_history()
            step = 0.0015
            quad = self._ensure_transform_quad()
            moved = tuple((x + dx * step, y + dy * step) for x, y in quad)
            self.work["transform"] = moved
            self.original_view.set_transform_quad(moved)
            self._transform_committed()
        elif dy == 0:
            self.navigate(dx)

    # ---------- lifecycle ----------
    def activate(self, index=None):
        if not self.generator.items:
            self._set_empty_state()
            return
        if index is None or not 0 <= index < len(self.generator.items):
            index = self.generator.current_index if 0 <= self.generator.current_index < len(self.generator.items) else 0
        self.load_index(index, True)

    def templates_updated(self):
        self._preview_cache.clear()
        if self.current_index >= 0 and self.generator.items:
            self.load_index(self.current_index, True)

    def _set_empty_state(self):
        self.current_index = -1
        self.counter.setText("0 / 0")
        self.final_view.set_source_pixmap(QPixmap())
        self.original_view.set_source_pixmap(QPixmap())
        for b in (self.apply_all_btn, self.save_btn, self.prev_btn, self.next_btn):
            b.setEnabled(False)
        self._set_edit_enabled(False)

    def load_index(self, index, force=False):
        if not self.generator.items or not 0 <= index < len(self.generator.items):
            self._set_empty_state()
            return
        if self.dirty and not force:
            if MessageDialog.confirm(self, "Unsaved changes", "Save current edit before moving?", primary="Save", secondary="Discard"):
                self.save_current()

        self.current_index = index
        item = self.generator.items[index]
        self.work = {
            "submission_id": item.submission_id,
            "child_name": item.child_name,
            "city": item.city,
            "template_mode": item.template_mode,
            "fit": item.artwork_fit_override or "Smart",
            "zoom": float(item.artwork_zoom),
            "x": float(item.artwork_offset_x),
            "y": float(item.artwork_offset_y),
            "rotation": int(item.artwork_rotation) % 360,
            "auto": bool(getattr(item, "auto_paper_detect", True)),
            "brightness": int(getattr(item, "brightness", 0)),
            "contrast": int(getattr(item, "contrast", 0)),
            "color_balance_red": int(getattr(item, "color_balance_red", 0)),
            "color_balance_green": int(getattr(item, "color_balance_green", 0)),
            "color_balance_blue": int(getattr(item, "color_balance_blue", 0)),
            "hue": int(getattr(item, "hue", 0)),
            "saturation": int(getattr(item, "saturation", 0)),
            "lightness": int(getattr(item, "lightness", 0)),
            "preset": str(getattr(item, "preset_name", "None") or "None"),
            "crop": getattr(item, "manual_crop", None),
            "warp": getattr(item, "manual_warp", None),
            "stretch": getattr(item, "stretch_strokes", None),
            "transform": getattr(item, "artwork_transform", None),
        }
        self.dirty = False
        self.editing = True
        # Start each student on the untouched upload. Selecting any source
        # editing tool switches the panel back to the editable result.
        self.show_original = True
        self.undo_stack = []
        self.redo_stack = []
        self._update_history_buttons()
        self.active_tool = "move"
        self.transform_surface = "right"
        self._transform_session_snapshot = None
        self._load_controls()
        self._set_edit_enabled(True)
        self._render_edit_source("original")
        self._set_view_modes("move")
        self._check_tools("move")
        self._request_preview()
        self._update_template_buttons()
        total = len(self.generator.items)
        self.counter.setText(f"{index + 1} / {total}")
        self.prev_btn.setEnabled(total > 1)
        self.next_btn.setEnabled(total > 1)
        self.apply_all_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.status.emit(f"Compare: {item.filename}")

    def navigate(self, step):
        if self.generator.items:
            self.load_index((self.current_index + step) % len(self.generator.items))

    def quick_template(self, mode):
        if self.current_index < 0 or self.work.get("template_mode") == mode:
            return
        self._push_history()
        self.work["template_mode"] = mode
        self._update_template_buttons()
        self._dirty(render=True, delay=0)

    def _update_template_buttons(self):
        mode = self.work.get("template_mode", "Auto")
        for name, button in self.template_buttons.items():
            button.setChecked(name == mode)
            button.setObjectName("SegmentActive" if name == mode else "SegmentInactive")
            button.style().unpolish(button)
            button.style().polish(button)
        if hasattr(self, "auto_route_label") and 0 <= self.current_index < len(self.generator.items):
            item = self.generator.items[self.current_index]
            if mode == "Auto":
                layout = item.detected_layout
                confidence = int(round(max(0.0, min(1.0, float(item.routing_confidence))) * 100))
                suffix = f" ({confidence}%)" if confidence else ""
                warning = " · Check" if item.routing_needs_check else ""
                self.auto_route_label.setText(f"Auto → {layout}{suffix}{warning}")
            else:
                self.auto_route_label.setText("")

    # ---------- mode / tool ----------
    def toggle_edit(self, checked=True):
        # Kept only as a compatibility hook for old callers. V4.1 is direct-edit:
        # tools are ready as soon as an artwork is loaded.
        self.editing = self.current_index >= 0
        self._set_edit_enabled(self.editing)

    def _set_edit_enabled(self, enabled):
        self.tool_bar.setVisible(enabled)
        for w in getattr(self, "_edit_widgets", []):
            w.setEnabled(enabled)
        for rb in getattr(self, "preset_radios", {}).values():
            rb.setEnabled(enabled)
        self.save_btn.setEnabled(enabled and self.current_index >= 0)
        if not enabled:
            self._set_view_modes("view")

    def _set_view_modes(self, mode):
        self._set_transform_shortcuts_enabled(mode in {"transform", "transform_left"})
        if mode == "move":
            self.final_view.set_mode("move")
            self.original_view.set_mode("view")
        elif mode == "crop":
            self.final_view.set_mode("view")
            self.original_view.set_mode("crop")
        elif mode == "warp":
            self.final_view.set_mode("view")
            self.original_view.set_mode("warp")
        elif mode == "transform_left" or (mode == "transform" and self.transform_surface == "left"):
            self.final_view.set_mode("transform")
            self.original_view.set_mode("view")
        elif mode == "transform":
            self.final_view.set_mode("view")
            self.original_view.set_mode("transform")
        elif mode == "stretch":
            self.final_view.set_mode("view")
            self.original_view.set_mode("stretch")
        else:
            self.final_view.set_mode("view")
            self.original_view.set_mode("view")

    def activate_move(self):
        if self.current_index < 0:
            return
        self.active_tool = "move"
        self._render_edit_source("view")
        self._set_view_modes("move")
        self._check_tools("move")

    def activate_crop(self):
        if self.current_index < 0:
            return
        self._exit_original_view()
        self.active_tool = "crop"
        if self.work.get("auto", False):
            self._push_history()
        self.work["auto"] = False
        self._loading = True
        self.auto_check.setChecked(False)
        self._loading = False
        crop = self.work.get("crop") or (0.0, 0.0, 1.0, 1.0)
        self.work["crop"] = tuple(crop)
        self.original_view.set_crop(crop)
        self._render_edit_source("crop")  # always the untouched uploaded source
        self._set_view_modes("crop")
        self._check_tools("crop")
        self.status.emit("Crop source: keep inside blue box; outside will be removed.")
        self._dirty(render=False)

    def activate_warp(self):
        if self.current_index < 0:
            return
        self._exit_original_view()
        self.active_tool = "warp"
        if self.work.get("auto", False):
            self._push_history()
        self.work["auto"] = False
        self._loading = True
        self.auto_check.setChecked(False)
        self._loading = False
        # IMPORTANT: Wrap points start on the FOUR CORNERS of the CROPPED student
        # artwork, never on the template/background. They select source geometry;
        # renderer maps that quadrilateral to the active template artwork frame ratio.
        warp = self.work.get("warp") or ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        self.work["warp"] = tuple(tuple(p) for p in warp)
        self.original_view.set_warp(warp)
        self._render_edit_source("warp")
        self._set_view_modes("warp")
        self._check_tools("warp")
        self.status.emit("Wrap source: drag the 4 cropped-artwork corners; left preview fits them to the template frame.")
        self._dirty(render=False)

    def _ensure_transform_quad(self):
        if self.work.get("transform"):
            return tuple(tuple(float(value) for value in point) for point in self.work["transform"])
        item = self.generator.items[self.current_index]
        try:
            shown, _ = geometry_for_path(
                item.source_path,
                auto_paper=bool(self.work.get("auto", True)),
                source_rotation=int(self.work.get("rotation", 0)),
                manual_crop=self.work.get("crop"),
                manual_warp=self.work.get("warp"),
                stretch_strokes=self.work.get("stretch"),
                preview_max=900,
                warp_target_aspect=self._current_warp_target_aspect(),
            )
            working = self._working_item()
            record = get_active_template(working.selected_layout)
            quad = default_transform_quad(
                shown.size, record.artwork_box, self.work.get("fit", "Smart"),
                zoom=float(self.work.get("zoom", 1.0)),
                offset_x=float(self.work.get("x", 0.0)),
                offset_y=float(self.work.get("y", 0.0)),
            )
        except Exception:
            quad = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        self.work["transform"] = tuple(tuple(point) for point in quad)
        return self.work["transform"]

    def activate_transform(self):
        self._activate_transform_surface("right")

    def activate_left_transform(self):
        self._activate_transform_surface("left")

    def _activate_transform_surface(self, surface):
        if self.current_index < 0:
            return
        self._exit_original_view()
        if self.active_tool != "transform":
            self._transform_session_snapshot = self._snapshot()
        self.active_tool = "transform"
        self.transform_surface = "left" if surface == "left" else "right"
        quad = self._ensure_transform_quad()
        self.original_view.set_transform_quad(quad)
        self.final_view.set_transform_quad(quad)
        if self.transform_surface == "right":
            self._render_edit_source("transform")
            self._set_view_modes("transform")
        else:
            self._render_edit_source("view")
            self._set_view_modes("transform_left")
        self._check_tools("transform")
        self._dirty(render=True, delay=0)
        self.status.emit("Free Transform: drag image/handles • Ctrl=Distort • Shift=free scale • Alt=center • Enter=Save • Esc=Cancel")

    def _transform_changed(self, value):
        if self._loading or not self.editing or self.active_tool != "transform":
            return
        self.work["transform"] = tuple(tuple(float(v) for v in point) for point in value)
        self.original_view.set_transform_quad(self.work["transform"])
        self.final_view.set_transform_quad(self.work["transform"])
        # Keep handle movement entirely on the UI surface. Heavy Pillow/OpenCV work
        # is throttled so a long drag cannot block events or flood preview workers.
        if self.transform_surface == "right":
            self.transform_proxy_timer.start()
        self._dirty(render=True, delay=180 if self.transform_surface == "right" else 110)

    def _transform_committed(self):
        if self.active_tool != "transform":
            return
        self.transform_proxy_timer.stop()
        if self.transform_surface == "right":
            self._render_edit_source("transform")
        self._dirty(render=True, delay=0)

    def commit_transform(self):
        if self.active_tool != "transform":
            return
        self._transform_session_snapshot = None
        self._transform_committed()
        self.activate_move()
        self.status.emit("Free Transform applied. Save stores it with this artwork.")

    def _save_from_enter(self):
        if self.current_index < 0 or not self.editing:
            return
        focused = self.focusWidget()
        if focused is not None:
            focused.clearFocus()
        self._save_and_clear_selection()

    def _save_and_clear_selection(self):
        """Persist the current artwork edit and leave no live edit handles."""
        self.render_timer.stop()
        self.transform_proxy_timer.stop()
        if self.active_tool == "transform":
            self._transform_session_snapshot = None
        self.save_current()
        self.activate_move()

    def cancel_transform(self):
        if self.active_tool != "transform" or self._transform_session_snapshot is None:
            return
        snapshot = self._transform_session_snapshot
        self._transform_session_snapshot = None
        self._restore_history(snapshot)
        self.activate_move()
        self.status.emit("Free Transform cancelled.")

    def reset_transform(self):
        if self.current_index < 0:
            return
        self._push_history()
        self.work["transform"] = None
        self.original_view.set_transform_quad(None)
        self.final_view.set_transform_quad(None)
        if self.active_tool == "transform":
            self._ensure_transform_quad()
            self.original_view.set_transform_quad(self.work["transform"])
            self.final_view.set_transform_quad(self.work["transform"])
            self._render_edit_source("transform" if self.transform_surface == "right" else "view")
        self._dirty(render=True, delay=0)
        self.status.emit("Free Transform reset to current Smart/Fit placement.")

    def activate_stretch(self):
        if self.current_index < 0:
            return
        self._exit_original_view()
        self.active_tool = "stretch"
        if self.work.get("auto", False):
            self._push_history()
        self.work["auto"] = False
        self._loading = True
        self.auto_check.setChecked(False)
        self._loading = False
        self._render_edit_source("stretch")
        self.original_view.set_stretch_brush(self.stretch_size.value() / 100.0, self.stretch_strength.value() / 100.0)
        self._set_view_modes("stretch")
        self._check_tools("stretch")
        self.status.emit("Stretch / Push-Pull: drag a small difficult area. Original upload stays untouched.")

    def _stretch_brush_changed(self, _=None):
        self.original_view.set_stretch_brush(self.stretch_size.value() / 100.0, self.stretch_strength.value() / 100.0)

    def _stretch_stroke(self, stroke):
        if self.active_tool != "stretch" or self.show_original:
            return
        strokes = list(self.work.get("stretch") or ())
        strokes.append(tuple(float(v) for v in stroke))
        self.work["stretch"] = tuple(strokes[-40:])
        self._dirty(render=True, delay=0)
        self._render_edit_source("stretch")

    def _snapshot(self):
        return copy.deepcopy(self.work)

    def _push_history(self):
        if self.current_index < 0 or self._loading:
            return
        snap = self._snapshot()
        if not self.undo_stack or self.undo_stack[-1] != snap:
            self.undo_stack.append(snap)
            if len(self.undo_stack) > 20:
                self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_history_buttons()

    def _history_operation_started(self, _mode):
        self._push_history()

    def _restore_history(self, state):
        self._loading = True
        try:
            self.work = copy.deepcopy(state)
            self._load_controls()
        finally:
            self._loading = False
        self.dirty = True
        self.save_btn.setEnabled(True)
        self._update_template_buttons()
        self._render_edit_source(self.active_tool if self.active_tool in {"crop", "warp", "stretch", "transform"} else "view")
        self._request_preview()
        self._check_tools(self.active_tool)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot())
        state = self.undo_stack.pop()
        self._restore_history(state)
        self._update_history_buttons()
        self.status.emit("Undo")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        state = self.redo_stack.pop()
        self._restore_history(state)
        self._update_history_buttons()
        self.status.emit("Redo")

    def _update_history_buttons(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(bool(self.undo_stack))
            self.redo_btn.setEnabled(bool(self.redo_stack))

    def toggle_original(self):
        if self.current_index < 0:
            return
        self.show_original = not self.show_original
        self.org_btn.setChecked(self.show_original)
        if self.show_original:
            self.original_view.set_mode("view")
            self._render_edit_source("original")
            self.status.emit("Org Img: untouched uploaded image (read-only).")
        else:
            self._render_edit_source(self.active_tool if self.active_tool in {"crop", "warp", "stretch", "transform"} else "view")
            self._set_view_modes(self.active_tool)
            self.status.emit("Editable Source")

    def _exit_original_view(self):
        if self.show_original:
            self.show_original = False
            self.org_btn.setChecked(False)

    def _check_tools(self, active):
        self.move_tool.setChecked(active == "move")
        self.crop_tool.setChecked(active == "crop")
        self.warp_tool.setChecked(active == "warp")
        self.transform_tool.setChecked(active == "transform")
        self.stretch_tool.setChecked(active == "stretch")
        self.org_btn.setChecked(self.show_original)

    def _crop_changed(self, value):
        if self._loading or not self.editing or self.active_tool != "crop":
            return
        self.work["crop"] = tuple(float(v) for v in value)
        self.original_view.set_crop(self.work["crop"])
        # Debounced live update: the left framed result follows the right-side crop
        # without flooding the bounded preview worker while a handle is moving.
        self._dirty(render=True, delay=180)

    def _warp_changed(self, value):
        if self._loading or not self.editing or self.active_tool != "warp":
            return
        self.work["warp"] = tuple(tuple(float(v) for v in point) for point in value)
        self.original_view.set_warp(self.work["warp"])
        # Mirror the right-side corner edit into the left frame after a short pause;
        # mouse release still commits an immediate full preview.
        self._dirty(render=True, delay=180)

    # ---------- controls ----------
    def _load_controls(self):
        self._loading = True
        try:
            self.id_edit.setText(self.work.get("submission_id", ""))
            self.name_edit.setText(self.work.get("child_name", ""))
            self.city_edit.setText(self.work.get("city", ""))
            self.fit_combo.setCurrentText(self.work.get("fit", "Smart"))
            zoom = int(round(self.work.get("zoom", 1.0) * 100))
            self.zoom_slider.setValue(max(25, min(300, zoom)))
            self.zoom_value.setText(f"{zoom}%")
            self.x_spin.setValue(int(round(self.work.get("x", 0))))
            self.y_spin.setValue(int(round(self.work.get("y", 0))))
            self.rotation_label.setText(f"{self.work.get('rotation', 0)}°")
            self.auto_check.setChecked(bool(self.work.get("auto", True)))
            self.brightness_slider.setValue(int(self.work.get("brightness", 0)))
            self.contrast_slider.setValue(int(self.work.get("contrast", 0)))
            self.brightness_value.setText(str(self.work.get("brightness", 0)))
            self.contrast_value.setText(str(self.work.get("contrast", 0)))
            (self.preset_radios.get(self.work.get("preset", "None")) or self.preset_radios["None"]).setChecked(True)
            self.original_view.set_crop(self.work.get("crop"))
            self.original_view.set_warp(self.work.get("warp"))
            self.original_view.set_transform_quad(self.work.get("transform"))
            self.final_view.set_transform_quad(self.work.get("transform"))
            self.original_view.set_stretch_brush(self.stretch_size.value() / 100.0, self.stretch_strength.value() / 100.0)
            self._fit_checks()
            self._update_history_buttons()
        finally:
            self._loading = False

    def _dirty(self, *, render=True, delay=140):
        if self._loading or not self.editing:
            return
        self.dirty = True
        self.save_btn.setEnabled(True)
        if render:
            self.render_timer.start(max(0, int(delay)))

    def _geometry_committed(self):
        """Crop/Wrap mouse release: update left preview once, never per drag pixel."""
        if not self.editing:
            return
        if self.active_tool == "crop":
            # Crop changes the source coordinate system. Downstream geometry restarts
            # from the new cropped artwork so stale coordinates can never distort it.
            self.work["warp"] = None
            self.work["stretch"] = None
            self.work["transform"] = None
            self.original_view.set_warp(None)
            self.status.emit("Crop updated. Wrap/Stretch reset to the new cropped artwork.")
        elif self.active_tool == "warp":
            self.work["stretch"] = None
            self.work["transform"] = None
            self.status.emit("Wrap updated. Stretch restarts from the new wrapped artwork.")
        self._dirty(render=True, delay=0)
        self._render_edit_source(self.active_tool if self.active_tool in {"crop", "warp", "stretch", "transform"} else "view")

    def _interaction_committed(self):
        if not self.editing:
            return
        self._dirty(render=True, delay=0)

    def _meta_changed(self, _=None):
        if self._loading or not self.editing:
            return
        self.work["submission_id"] = self.id_edit.text().strip()
        self.work["child_name"] = self.name_edit.text().strip()
        self.work["city"] = self.city_edit.text().strip()
        self._dirty()

    def _fit_changed(self, text):
        if self._loading or not self.editing:
            return
        if self.work.get("fit") != text:
            self._push_history()
        self.work["fit"] = text
        # Fit is a new automatic starting point. A prior explicit quad would
        # otherwise hide this control's effect.
        if self.work.get("transform") is not None:
                self.work["transform"] = None
                if self.active_tool == "transform":
                    self._ensure_transform_quad()
                    self.original_view.set_transform_quad(self.work["transform"])
                    self.final_view.set_transform_quad(self.work["transform"])
                    self._render_edit_source("transform" if self.transform_surface == "right" else "view")
        self._fit_checks()
        self._dirty()

    def set_fit_mode(self, mode):
        if self.editing:
            self.fit_combo.setCurrentText(mode)

    def _fit_checks(self):
        mode = self.work.get("fit", "Smart")
        self.smart_tool.setChecked(mode == "Smart")
        self.fit_tool.setChecked(mode == "Fit Full Artwork")
        self.fill_tool.setChecked(mode == "Fill Frame")

    def _zoom_changed(self, value):
        if self._loading or not self.editing:
            return
        old_zoom = max(0.01, float(self.work.get("zoom", 1.0)))
        self.work["zoom"] = value / 100.0
        if self.work.get("transform") is not None:
            self._scale_transform(self.work["zoom"] / old_zoom)
        self.zoom_value.setText(f"{value}%")
        self._dirty(delay=120)

    def change_zoom(self, factor):
        if not self.editing:
            return
        self._push_history()
        old_zoom = float(self.work.get("zoom", 1.0))
        zoom = max(0.25, min(3.0, old_zoom * factor))
        self.work["zoom"] = zoom
        if self.work.get("transform") is not None:
            self._scale_transform(zoom / max(0.01, old_zoom))
        self._loading = True
        self.zoom_slider.setValue(int(round(zoom * 100)))
        self.zoom_value.setText(f"{int(round(zoom * 100))}%")
        self._loading = False
        self._dirty(delay=110)

    def pan_by(self, dx, dy):
        if not self.editing or self.active_tool != "move":
            return
        self.work["x"] = float(self.work.get("x", 0)) + dx
        self.work["y"] = float(self.work.get("y", 0)) + dy
        if self.work.get("transform") is not None:
            try:
                item = self._working_item()
                box = get_active_template(item.selected_layout).artwork_box
                q = self.work["transform"]
                self.work["transform"] = tuple((x + dx / max(1, box.width), y + dy / max(1, box.height)) for x, y in q)
                self.original_view.set_transform_quad(self.work["transform"])
            except Exception:
                pass
        self._loading = True
        self.x_spin.setValue(int(round(self.work["x"])))
        self.y_spin.setValue(int(round(self.work["y"])))
        self._loading = False
        self._dirty(delay=110)

    def _scale_transform(self, factor):
        quad = self.work.get("transform")
        if not quad:
            return
        cx = sum(x for x, _ in quad) / 4
        cy = sum(y for _, y in quad) / 4
        factor = max(0.05, min(20.0, float(factor)))
        self.work["transform"] = tuple((cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in quad)
        self.original_view.set_transform_quad(self.work["transform"])

    def _offset_changed(self, _=None):
        if self._loading or not self.editing:
            return
        new_x = float(self.x_spin.value())
        new_y = float(self.y_spin.value())
        dx = new_x - float(self.work.get("x", 0.0))
        dy = new_y - float(self.work.get("y", 0.0))
        self.work["x"] = new_x
        self.work["y"] = new_y
        if self.work.get("transform") is not None and (dx or dy):
            try:
                item = self._working_item()
                box = get_active_template(item.selected_layout).artwork_box
                self.work["transform"] = tuple(
                    (x + dx / max(1, box.width), y + dy / max(1, box.height))
                    for x, y in self.work["transform"]
                )
                self.original_view.set_transform_quad(self.work["transform"])
            except Exception:
                pass
        self._dirty(delay=120)

    @staticmethod
    def _rotate_norm_point(point, amount):
        x, y = float(point[0]), float(point[1])
        a = int(amount) % 360
        if a == 90:
            return (y, 1.0 - x)
        if a == 180:
            return (1.0 - x, 1.0 - y)
        if a == 270:
            return (1.0 - y, x)
        return (x, y)

    @classmethod
    def _rotate_crop_value(cls, crop, amount):
        if not crop:
            return None
        l, t, r, b = crop
        pts = [cls._rotate_norm_point(p, amount) for p in ((l,t),(r,t),(r,b),(l,b))]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    @classmethod
    def _rotate_warp_value(cls, warp, amount):
        if not warp:
            return None
        pts = [cls._rotate_norm_point(p, amount) for p in warp]
        # Re-order to TL, TR, BR, BL after rotation.
        tl = min(pts, key=lambda p: p[0] + p[1])
        br = max(pts, key=lambda p: p[0] + p[1])
        tr = min(pts, key=lambda p: p[1] - p[0])
        bl = max(pts, key=lambda p: p[1] - p[0])
        return (tl, tr, br, bl)

    @classmethod
    def _rotate_strokes_value(cls, strokes, amount):
        if not strokes:
            return None
        out = []
        for sx, sy, ex, ey, radius, strength in strokes:
            a = cls._rotate_norm_point((sx, sy), amount)
            b = cls._rotate_norm_point((ex, ey), amount)
            out.append((a[0], a[1], b[0], b[1], radius, strength))
        return tuple(out)

    def rotate(self, amount):
        if not self.editing:
            return
        self._push_history()
        self.work["crop"] = self._rotate_crop_value(self.work.get("crop"), amount)
        self.work["warp"] = self._rotate_warp_value(self.work.get("warp"), amount)
        self.work["stretch"] = self._rotate_strokes_value(self.work.get("stretch"), amount)
        self.work["rotation"] = (int(self.work.get("rotation", 0)) + amount) % 360
        self.rotation_label.setText(f"{self.work['rotation']}°")
        self._update_template_buttons()
        self.original_view.set_crop(self.work.get("crop"))
        self.original_view.set_warp(self.work.get("warp"))
        stage = self.active_tool if self.active_tool in {"crop", "warp", "stretch"} else "view"
        if self.active_tool == "transform" and self.transform_surface == "right":
            stage = "transform"
        self._render_edit_source(stage)
        self._dirty(render=True, delay=0)

    def _auto_changed(self, checked):
        if self._loading or not self.editing:
            return
        self._push_history()
        self.work["auto"] = bool(checked)
        if checked:
            # Auto and manual geometry are mutually exclusive by design.
            self.work["crop"] = None
            self.work["warp"] = None
            self.work["stretch"] = None
            self.original_view.set_crop(None)
            self.original_view.set_warp(None)
        self._render_edit_source("view")
        self._dirty()

    def _tone_changed(self, _=None):
        if self._loading or not self.editing:
            return
        self.work["brightness"] = self.brightness_slider.value()
        self.work["contrast"] = self.contrast_slider.value()
        self.brightness_value.setText(str(self.work["brightness"]))
        self.contrast_value.setText(str(self.work["contrast"]))
        self._dirty(delay=180)

    def _open_color_dialog(self, title, controls):
        if self.current_index < 0 or not self.editing:
            return
        snapshot = copy.deepcopy(self.work)
        was_dirty = self.dirty
        dialog = ColorAdjustmentDialog(title, controls, self.work, self)

        def preview(values):
            self.work.update(values)
            self.show_original = False
            self._render_edit_source("view")
            self._dirty(delay=100)

        dialog.values_changed.connect(preview)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            if any(snapshot.get(key, 0) != value for key, value in values.items()):
                self.undo_stack.append(snapshot)
                self.redo_stack.clear()
                self._update_history_buttons()
                self.work.update(values)
                self._render_edit_source("view")
                self._dirty(delay=0)
        else:
            self.work = snapshot
            self._render_edit_source("view")
            self._request_preview()
            self.dirty = was_dirty
            self.save_btn.setEnabled(self.dirty)

    def open_color_balance(self):
        self._open_color_dialog("Color Balance", (
            ("color_balance_red", "Cyan  —  Red", -100, 100),
            ("color_balance_green", "Magenta  —  Green", -100, 100),
            ("color_balance_blue", "Yellow  —  Blue", -100, 100),
        ))

    def open_hue_saturation(self):
        self._open_color_dialog("Hue / Saturation", (
            ("hue", "Hue", -180, 180),
            ("saturation", "Saturation", -100, 100),
            ("lightness", "Lightness", -100, 100),
        ))

    def _preset(self, value):
        if self._loading or not self.editing:
            return
        if self.work.get("preset") != value:
            self._push_history()
        self.work["preset"] = value
        self._render_edit_source("view")
        self._dirty()

    def reset_source_geometry(self):
        if not self.editing:
            return
        self._push_history()
        self.work["crop"] = None
        self.work["warp"] = None
        self.work["stretch"] = None
        self.work["transform"] = None
        self.original_view.set_crop(None)
        self.original_view.set_warp(None)
        self._render_edit_source("view")
        self.activate_move()
        self._dirty(render=True, delay=0)
        self.status.emit("Source Crop/Wrap/Stretch and Free Transform reset. Original artwork preserved.")

    def reset_adjustment(self):
        if not self.editing:
            return
        self._push_history()
        self.work.update({
            "fit": "Smart", "zoom": 1.0, "x": 0.0, "y": 0.0, "rotation": 0,
            "auto": True, "brightness": 0, "contrast": 0, "preset": "None",
            "color_balance_red": 0, "color_balance_green": 0, "color_balance_blue": 0,
            "hue": 0, "saturation": 0, "lightness": 0,
            "crop": None, "warp": None, "stretch": None, "transform": None,
        })
        self._load_controls()
        self.activate_move()
        self._dirty()

    # ---------- rendering ----------
    def _working_item(self):
        if not 0 <= self.current_index < len(self.generator.items):
            return None
        item = self.generator.items[self.current_index]
        return replace(
            item,
            submission_id=self.work.get("submission_id", item.submission_id),
            child_name=self.work.get("child_name", item.child_name),
            city=self.work.get("city", item.city),
            template_mode=self.work.get("template_mode", item.template_mode),
            artwork_fit_override=self.work.get("fit", item.artwork_fit_override),
            artwork_zoom=float(self.work.get("zoom", item.artwork_zoom)),
            artwork_offset_x=float(self.work.get("x", item.artwork_offset_x)),
            artwork_offset_y=float(self.work.get("y", item.artwork_offset_y)),
            artwork_rotation=int(self.work.get("rotation", item.artwork_rotation)),
            auto_paper_detect=bool(self.work.get("auto", True)),
            brightness=int(self.work.get("brightness", 0)),
            contrast=int(self.work.get("contrast", 0)),
            color_balance_red=int(self.work.get("color_balance_red", 0)),
            color_balance_green=int(self.work.get("color_balance_green", 0)),
            color_balance_blue=int(self.work.get("color_balance_blue", 0)),
            hue=int(self.work.get("hue", 0)),
            saturation=int(self.work.get("saturation", 0)),
            lightness=int(self.work.get("lightness", 0)),
            preset_name=self.work.get("preset", "None"),
            manual_crop=self.work.get("crop"),
            manual_warp=self.work.get("warp"),
            stretch_strokes=self.work.get("stretch"),
            artwork_transform=self.work.get("transform"),
        )

    @staticmethod
    def _pix(image):
        rgba = image.convert("RGBA")
        qimg = QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(qimg)

    def _set_final_artwork_region(self, item):
        try:
            record = get_active_template(item.selected_layout)
            with Image.open(record.template_image) as template:
                tw, th = template.size
            box = record.artwork_box
            self.final_view.set_edit_region((
                box.x / tw, box.y / th,
                (box.x + box.width) / tw, (box.y + box.height) / th,
            ))
        except Exception:
            self.final_view.set_edit_region(None)

    def _preview_key(self, item):
        return (
            str(item.source_path), item.submission_id, item.child_name, item.city,
            item.template_mode, item.artwork_fit_override,
            round(float(item.artwork_zoom), 4), round(float(item.artwork_offset_x), 2),
            round(float(item.artwork_offset_y), 2), int(item.artwork_rotation),
            bool(item.auto_paper_detect), int(item.brightness), int(item.contrast),
            int(getattr(item, "color_balance_red", 0)),
            int(getattr(item, "color_balance_green", 0)),
            int(getattr(item, "color_balance_blue", 0)),
            int(getattr(item, "hue", 0)), int(getattr(item, "saturation", 0)),
            int(getattr(item, "lightness", 0)),
            str(item.preset_name or "None"), item.manual_crop, item.manual_warp,
            getattr(item, "stretch_strokes", None),
            getattr(item, "artwork_transform", None),
        )

    def _start_preview_task(self, request_id, item, key):
        self._preview_active = True
        task = PreviewTask(request_id, item, max_width=900)
        task.signals.finished.connect(self._preview_ready)
        self._preview_tasks[request_id] = (task, key)
        PreviewPool.start(task)

    def _request_preview(self):
        item = self._working_item()
        if not item:
            return
        # Each state invalidates older output. Keep at most ONE worker active and ONE
        # newest pending request so slider/drag activity can never flood the queue.
        self._preview_request_id += 1
        request_id = self._preview_request_id
        self._pending_preview = None
        key = self._preview_key(item)
        cached = self._preview_cache.get(key)
        if cached is not None:
            self.final_view.set_source_pixmap(cached)
            self._set_final_artwork_region(item)
            self.final_view.set_transform_quad(self.work.get("transform"))
            if self.editing:
                final_mode = "transform" if self.active_tool == "transform" and self.transform_surface == "left" else "move" if self.active_tool == "move" else "view"
                self.final_view.set_mode(final_mode)
            return
        if self._preview_active:
            self._pending_preview = (request_id, item, key)
            return
        self._start_preview_task(request_id, item, key)

    def _preview_ready(self, request_id, qimage, error):
        entry = self._preview_tasks.pop(request_id, None)
        self._preview_active = False
        if request_id == self._preview_request_id:
            if error or qimage is None:
                self.status.emit(f"Compare preview error: {error}")
            else:
                key = entry[1] if entry else None
                pix = QPixmap.fromImage(qimage)
                if key is not None:
                    if len(self._preview_cache) >= 24:
                        self._preview_cache.pop(next(iter(self._preview_cache)))
                    self._preview_cache[key] = pix
                self.final_view.set_source_pixmap(pix)
                item = self._working_item()
                if item:
                    self._set_final_artwork_region(item)
                self.final_view.set_transform_quad(self.work.get("transform"))
                if self.editing:
                    final_mode = "transform" if self.active_tool == "transform" and self.transform_surface == "left" else "move" if self.active_tool == "move" else "view"
                    self.final_view.set_mode(final_mode)

        pending = self._pending_preview
        self._pending_preview = None
        if pending is not None and pending[0] == self._preview_request_id:
            self._start_preview_task(*pending)

    def _render_edit_source(self, stage="view"):
        if not 0 <= self.current_index < len(self.generator.items):
            return
        item = self.generator.items[self.current_index]
        try:
            if stage == "original" or self.show_original:
                with Image.open(item.source_path) as im:
                    shown = ImageOps.exif_transpose(im).convert("RGBA")
                    if max(shown.size) > 1600:
                        shown.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                label = "ORG"
            else:
                common = dict(
                    image_path=item.source_path,
                    auto_paper=False,
                    source_rotation=int(self.work.get("rotation", 0)),
                    preview_max=1200,
                )
                if stage == "crop":
                    shown, _ = geometry_for_path(**common)
                elif stage == "warp":
                    shown, _ = geometry_for_path(**common, manual_crop=self.work.get("crop"))
                elif stage == "stretch":
                    shown, _ = geometry_for_path(
                        **common,
                        manual_crop=self.work.get("crop"),
                        manual_warp=self.work.get("warp"),
                        stretch_strokes=self.work.get("stretch"),
                        warp_target_aspect=self._current_warp_target_aspect(),
                    )
                else:
                    shown, _ = geometry_for_path(
                        item.source_path,
                        auto_paper=bool(self.work.get("auto", True)),
                        source_rotation=int(self.work.get("rotation", 0)),
                        manual_crop=self.work.get("crop"),
                        manual_warp=self.work.get("warp"),
                        stretch_strokes=self.work.get("stretch"),
                        preview_max=1200,
                        warp_target_aspect=self._current_warp_target_aspect(),
                    )
                shown = apply_color_stage(
                    shown,
                    brightness=int(self.work.get("brightness", 0)),
                    contrast=int(self.work.get("contrast", 0)),
                    color_balance_red=int(self.work.get("color_balance_red", 0)),
                    color_balance_green=int(self.work.get("color_balance_green", 0)),
                    color_balance_blue=int(self.work.get("color_balance_blue", 0)),
                    hue=int(self.work.get("hue", 0)),
                    saturation=int(self.work.get("saturation", 0)),
                    lightness=int(self.work.get("lightness", 0)),
                    preset_name=str(self.work.get("preset", "None") or "None"),
                )
                label = "EDIT"
            transform_region = None
            if stage == "transform":
                shown.thumbnail((900, 900), Image.Resampling.LANCZOS)
                aspect = self._current_warp_target_aspect() or 1.0
                frame_w = 640
                frame_h = max(180, int(round(frame_w / aspect)))
                pad = max(90, int(round(min(frame_w, frame_h) * 0.22)))
                canvas = Image.new("RGBA", (frame_w + pad * 2, frame_h + pad * 2), (18, 24, 38, 255))
                place_artwork(canvas, shown, Box(pad, pad, frame_w, frame_h), "Smart", transform_quad=self.work.get("transform"))
                ImageDraw.Draw(canvas).rectangle(
                    (pad, pad, pad + frame_w - 1, pad + frame_h - 1),
                    outline=(34, 199, 242, 255), width=2,
                )
                shown = canvas
                label = "TRANSFORM"
                transform_region = (
                    pad / shown.width, pad / shown.height,
                    (pad + frame_w) / shown.width, (pad + frame_h) / shown.height,
                )
            self.original_info.setText(f"{label} · {shown.width} × {shown.height} · {item.source_path.suffix.lower()}")
            self.original_view.set_source_pixmap(self._pix(shown))
            self.original_view.set_edit_region(transform_region)
            self.original_view.set_crop(self.work.get("crop"))
            self.original_view.set_warp(self.work.get("warp"))
            self.original_view.set_stretch_brush(self.stretch_size.value() / 100.0, self.stretch_strength.value() / 100.0)
            self.original_view.set_transform_quad(self.work.get("transform"))
        except Exception as exc:
            self.original_info.setText(str(exc))

    def _current_warp_target_aspect(self):
        try:
            item = self._working_item()
            if not item:
                return None
            layout = self.work.get("template_mode", "Auto")
            if layout == "Auto":
                layout = item.detected_layout
            record = get_active_template(layout)
            box = record.artwork_box
            return box.width / float(max(1, box.height))
        except Exception:
            return None

    # ---------- save/apply ----------
    def save_current(self, stay_edit=False):
        item = self._working_item()
        if not item:
            return
        target = self.generator.items[self.current_index]
        attrs = (
            "submission_id", "child_name", "city", "template_mode",
            "artwork_fit_override", "artwork_zoom", "artwork_offset_x", "artwork_offset_y",
            "artwork_rotation", "auto_paper_detect", "brightness", "contrast",
            "color_balance_red", "color_balance_green", "color_balance_blue",
            "hue", "saturation", "lightness",
            "preset_name", "manual_crop", "manual_warp", "stretch_strokes", "artwork_transform",
        )
        for attr in attrs:
            setattr(target, attr, getattr(item, attr))
        self.dirty = False
        self.generator.refresh_after_compare(self.current_index)
        self._request_preview()
        self.editing = True
        self._set_edit_enabled(True)
        self.preview_hint.setText("Saved • selection cleared")
        self.save_btn.setEnabled(False)
        self.status.emit(f"Saved Compare adjustment for {target.filename}.")

    def open_apply_all_menu(self):
        if self.current_index < 0:
            return
        menu = QMenu(self)
        same = QAction("Apply image settings to same template type", self)
        all_items = QAction("Apply image settings to all artworks", self)
        same.triggered.connect(lambda: self.apply_all("same"))
        all_items.triggered.connect(lambda: self.apply_all("all"))
        menu.addAction(same)
        menu.addAction(all_items)
        menu.exec(self.apply_all_btn.mapToGlobal(self.apply_all_btn.rect().bottomLeft()))

    def apply_all(self, scope):
        current = self._working_item()
        if not current:
            return
        if not MessageDialog.confirm(
            self, "Apply settings?",
            "Apply placement, crop/wrap/stretch, color and preset settings? Names/ID/location are not copied.",
            primary="Apply", secondary="Cancel",
        ):
            return
        attrs = (
            "artwork_fit_override", "artwork_zoom", "artwork_offset_x", "artwork_offset_y",
            "artwork_rotation", "auto_paper_detect", "brightness", "contrast",
            "color_balance_red", "color_balance_green", "color_balance_blue",
            "hue", "saturation", "lightness",
            "preset_name", "manual_crop", "manual_warp", "stretch_strokes", "artwork_transform",
        )
        changed = 0
        for item in self.generator.items:
            if scope == "same" and item.selected_layout != current.selected_layout:
                continue
            for attr in attrs:
                setattr(item, attr, getattr(current, attr))
            changed += 1
        self.generator.refresh_after_compare(self.current_index)
        self.status.emit(f"Applied image settings to {changed} artworks.")

    def keyPressEvent(self, event):
        # Left/Right are reserved for Previous/Next. Placement nudging uses Alt+Arrow.
        if self.current_index >= 0 and self.active_tool == "move" and (event.modifiers() & Qt.KeyboardModifier.AltModifier) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0
            dy = -step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0
            self.pan_by(dx, dy)
            event.accept()
            return
        super().keyPressEvent(event)
