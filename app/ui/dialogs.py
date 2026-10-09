from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QFileDialog
)

from ..config import GROUP_COLORS


class MessageDialog(QDialog):
    def __init__(self, parent, title: str, message: str, kind: str = "info", primary: str = "OK", secondary: str | None = None, animation: bool = False):
        # `animation` is intentionally ignored. V3.2 removes all GIF/video popup content.
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(430)
        self.result_value = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title_lbl = QLabel(title); title_lbl.setObjectName("SectionTitle"); layout.addWidget(title_lbl)
        msg = QLabel(message); msg.setWordWrap(True); msg.setObjectName("Muted" if kind == "info" else ""); layout.addWidget(msg)
        row = QHBoxLayout(); row.addStretch(1)
        if secondary:
            b2 = QPushButton(secondary); b2.clicked.connect(self.reject); row.addWidget(b2)
        b1 = QPushButton(primary); b1.setObjectName("Primary" if kind != "danger" else "Danger")
        b1.clicked.connect(self._accept); row.addWidget(b1)
        layout.addLayout(row)

    def _accept(self):
        self.result_value = True; self.accept()

    @classmethod
    def confirm(cls, parent, title, message, primary="Save", secondary="Cancel", kind="info") -> bool:
        dlg = cls(parent, title, message, kind=kind, primary=primary, secondary=secondary)
        dlg.exec(); return dlg.result_value

    @classmethod
    def info(cls, parent, title, message, animation=False):
        cls(parent, title, message, primary="OK").exec()


class GroupDialog(QDialog):
    def __init__(self, parent, default_name="New Group", default_color="Blue", title="New Group", action_text="Create"):
        super().__init__(parent); self.setWindowTitle(title); self.setModal(True); self.setMinimumWidth(420)
        layout = QVBoxLayout(self); layout.setContentsMargins(22,22,22,20); layout.setSpacing(10)
        heading = QLabel("Create template group" if action_text == "Create" else "Edit template group"); heading.setObjectName("SectionTitle"); layout.addWidget(heading)
        layout.addWidget(QLabel("Group name")); self.name = QLineEdit(default_name); layout.addWidget(self.name)
        layout.addWidget(QLabel("Group colour")); self.color = QComboBox(); self.color.addItems(list(GROUP_COLORS)); self.color.setCurrentText(default_color); layout.addWidget(self.color)
        row=QHBoxLayout(); row.addStretch(1); cancel=QPushButton("Cancel"); save=QPushButton(action_text); save.setObjectName("Primary")
        cancel.clicked.connect(self.reject); save.clicked.connect(self._accept_valid); row.addWidget(cancel); row.addWidget(save); layout.addLayout(row)

    def _accept_valid(self):
        if not self.name.text().strip():
            self.name.setProperty("error", True); self.name.style().unpolish(self.name); self.name.style().polish(self.name); return
        self.accept()


class NewTemplateDialog(QDialog):
    def __init__(self, parent, groups: list[str], default_group: str = "Ungrouped"):
        super().__init__(parent); self.setWindowTitle("New Template"); self.setModal(True); self.setMinimumWidth(520)
        self.image_path: Path | None = None
        layout=QVBoxLayout(self); layout.setContentsMargins(24,22,24,20); layout.setSpacing(8)
        t=QLabel("New Template"); t.setObjectName("SectionTitle"); layout.addWidget(t)
        self.error=QLabel(""); self.error.setStyleSheet("color:#DC2626;"); layout.addWidget(self.error)
        layout.addWidget(QLabel("Template name *")); self.name=QLineEdit(); layout.addWidget(self.name)
        layout.addWidget(QLabel("Template type *")); self.type=QComboBox(); self.type.addItems(["Portrait","Landscape","Square","Any"]); layout.addWidget(self.type)
        layout.addWidget(QLabel("Group")); self.group=QComboBox(); self.group.addItems(groups or ["Ungrouped"]); layout.addWidget(self.group)
        if default_group in [self.group.itemText(i) for i in range(self.group.count())]:
            self.group.setCurrentText(default_group)
        layout.addWidget(QLabel("Choose image *")); ir=QHBoxLayout(); self.image_label=QLineEdit(); self.image_label.setReadOnly(True); choose=QPushButton("+ Choose image"); choose.clicked.connect(self._choose)
        ir.addWidget(self.image_label,1); ir.addWidget(choose); layout.addLayout(ir)
        row=QHBoxLayout(); row.addStretch(1); cancel=QPushButton("Cancel"); create=QPushButton("Create"); create.setObjectName("Primary"); cancel.clicked.connect(self.reject); create.clicked.connect(self._validate)
        row.addWidget(cancel); row.addWidget(create); layout.addLayout(row)

    def _choose(self):
        p,_=QFileDialog.getOpenFileName(self,"Choose template image","","Images (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp)")
        if p: self.image_path=Path(p); self.image_label.setText(p)

    def _validate(self):
        missing=[]
        self.name.setProperty("error", False); self.type.setProperty("error", False); self.image_label.setProperty("error", False)
        if not self.name.text().strip(): missing.append("Template name"); self.name.setProperty("error", True)
        if not self.type.currentText(): missing.append("Template type"); self.type.setProperty("error", True)
        if not self.image_path: missing.append("Image"); self.image_label.setProperty("error", True)
        for w in (self.name,self.type,self.image_label): w.style().unpolish(w); w.style().polish(w)
        if missing:
            self.error.setText("Required: " + ", ".join(missing)); return
        self.accept()
