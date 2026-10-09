from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame
)

from ..config import APP_ICON, APP_TITLE
from ..settings_store import load_settings, save_settings
from .theme import stylesheet
from .generator_page import GeneratorPage
from .compare_page import ComparePage
from .template_page import TemplatePage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1500, 930)
        self.setMinimumSize(1180, 760)
        self.settings = load_settings()
        self.theme = self.settings.get("theme", "light")

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("HeaderBar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(22, 14, 18, 14)
        titles = QVBoxLayout()
        title = QLabel("KidsChamp")
        title.setObjectName("Title")
        sub = QLabel("Version 1 · automatic crop/route/frame stretch · transparent PNG/TIFF · up to 250 artworks")
        sub.setObjectName("Muted")
        titles.addWidget(title)
        titles.addWidget(sub)
        hl.addLayout(titles)
        hl.addStretch(1)

        # Requested top-right order: Compare | Generator | Template Settings | Theme
        self.compare_btn = QPushButton("Compare")
        self.gen_btn = QPushButton("Generator")
        self.set_btn = QPushButton("Template Settings")
        self.theme_btn = QPushButton()

        self.compare_btn.clicked.connect(lambda: self.open_compare(0))
        self.gen_btn.clicked.connect(lambda: self.show_page(1))
        self.set_btn.clicked.connect(lambda: self.show_page(2))
        self.theme_btn.clicked.connect(self.toggle_theme)
        hl.addWidget(self.compare_btn)
        hl.addWidget(self.gen_btn)
        hl.addWidget(self.set_btn)
        hl.addWidget(self.theme_btn)
        layout.addWidget(header)

        self.stack = QStackedWidget()
        self.generator = GeneratorPage()
        self.compare = ComparePage(self.generator)
        self.templates = TemplatePage()
        self.stack.addWidget(self.compare)      # 0
        self.stack.addWidget(self.generator)    # 1
        self.stack.addWidget(self.templates)    # 2
        layout.addWidget(self.stack, 1)

        footer = QFrame()
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(10, 5, 10, 5)
        credit = QLabel("Creative technology @Rusala_Media  |  @Shanal  |  @kawshal  |  @shehan")
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setObjectName("Muted")
        self.status = QLabel("Ready")
        self.status.setObjectName("Muted")
        fl.addWidget(credit)
        fl.addWidget(self.status)
        layout.addWidget(footer)

        self.generator.status.connect(self.status.setText)
        self.compare.status.connect(self.status.setText)
        self.templates.status.connect(self.status.setText)
        self.generator.compare_requested.connect(self.open_compare)
        self.templates.templates_changed.connect(self.generator.templates_updated)
        self.templates.templates_changed.connect(self.compare.templates_updated)
        self.templates.templates_changed.connect(lambda: self.status.setText("Templates updated and ready for Generator / Compare."))

        self.apply_theme()
        self.show_page(1)

    def open_compare(self, index=None):
        self.compare.activate(index)
        self.show_page(0)

    def show_page(self, index):
        self.stack.setCurrentIndex(index)
        buttons = [
            (self.compare_btn, 0),
            (self.gen_btn, 1),
            (self.set_btn, 2),
        ]
        for button, page_index in buttons:
            button.setObjectName("TabActive" if index == page_index else "TabInactive")
            button.style().unpolish(button)
            button.style().polish(button)

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.settings = load_settings()
        self.settings["theme"] = self.theme
        save_settings(self.settings)
        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(stylesheet(self.theme))
        self.theme_btn.setText("☀ Light" if self.theme == "dark" else "🌙 Dark")

    def closeEvent(self, event):
        if self.generator.shutdown():
            event.accept()
        else:
            event.ignore()
            self.status.setText("Still finishing a background image operation. Please close again shortly.")
