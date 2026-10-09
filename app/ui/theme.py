from __future__ import annotations

from ..config import LIGHT, DARK


def palette(theme: str) -> dict:
    return DARK if theme == "dark" else LIGHT


def stylesheet(theme: str) -> str:
    c = palette(theme)
    return f"""
    QWidget {{
        background: {c['bg']};
        color: {c['text']};
        font-family: 'Segoe UI';
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{ background: {c['bg']}; }}
    QLabel {{ background: transparent; }}
    QFrame#HeaderBar {{ background: {c['card']}; border: none; border-bottom: 1px solid {c['border']}; border-radius: 0px; }}
    QFrame#Card, QWidget#Card {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}
    QLabel#Title {{ font-size: 21pt; font-weight: 700; color: {c['text']}; }}
    QLabel#SectionTitle {{ font-size: 15pt; font-weight: 700; color: {c['text']}; }}
    QLabel#Muted {{ color: {c['muted']}; }}
    QLabel#GroupTitle {{ font-size: 11pt; font-weight: 700; }}

    QPushButton {{
        background: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 8px 14px;
        border-radius: 11px;
        font-weight: 600;
        min-height: 20px;
    }}
    QPushButton:hover {{ background: {c['card_alt']}; border-color: {c['primary']}; }}
    QPushButton:pressed {{ background: {c['selection']}; }}
    QPushButton:disabled {{ color: {c['muted']}; background: {c['card_alt']}; border-color: {c['border']}; }}
    QPushButton#Primary {{ background: {c['primary']}; color: white; border-color: {c['primary']}; }}
    QPushButton#Primary:hover {{ background: {c['primary_hover']}; }}
    QPushButton#Download {{ background: {c['success']}; color: white; border-color: {c['success']}; font-size: 11pt; border-radius: 11px; }}
    QPushButton#Danger {{ color: {c['danger']}; border-color: {c['danger']}; background: {c['card']}; }}
    QPushButton#Danger:hover {{ background: rgba(220,38,38,0.08); }}
    QPushButton#TabActive {{ background: {c['primary']}; color: white; border-color: {c['primary']}; padding: 11px 18px; border-radius: 11px; }}
    QPushButton#TabInactive {{ background: {c['card']}; color: {c['text']}; padding: 11px 18px; border-radius: 11px; }}
    QPushButton#SegmentActive {{ background: {c['primary']}; color: white; border-color: {c['primary']}; padding: 6px 12px; border-radius: 9px; }}
    QPushButton#SegmentInactive {{ background: {c['card']}; color: {c['text']}; padding: 6px 12px; border-radius: 9px; }}
    QPushButton#IconButton {{ padding: 5px; min-width: 28px; min-height: 28px; border-radius: 9px; }}
    QPushButton#ToolButton {{
        padding: 5px;
        min-width: 30px;
        min-height: 30px;
        border-radius: 9px;
        background: {c['card']};
    }}
    QPushButton#ToolButton:checked {{
        background: {c['selection']};
        border: 2px solid {c['primary']};
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {c['border']};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        width: 16px;
        margin: -5px 0;
        background: {c['primary']};
        border: 1px solid {c['primary']};
        border-radius: 8px;
    }}

    QLineEdit, QComboBox, QSpinBox {{
        background: {c['input']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 7px;
        border-radius: 9px;
        selection-background-color: {c['primary']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {c['primary']}; }}
    QLineEdit[error='true'], QComboBox[error='true'] {{ border: 2px solid {c['danger']}; }}

    QTableWidget, QTreeWidget, QListWidget {{
        background: {c['card']};
        alternate-background-color: {c['card_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        gridline-color: {c['border']};
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
        outline: 0;
    }}
    QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 4px; }}
    QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
        background: {c['selection']}; color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['header']};
        color: {c['text']};
        border: none;
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
        padding: 8px;
        font-weight: 700;
    }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: {c['card_alt']}; width: 12px; margin: 2px; border-radius: 6px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 30px; border-radius: 5px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: {c['card_alt']}; height: 12px; margin: 2px; border-radius: 6px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; min-width: 30px; border-radius: 5px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QProgressBar {{ border: 1px solid {c['border']}; background: {c['card_alt']}; text-align: center; border-radius: 9px; }}
    QProgressBar::chunk {{ background: {c['primary']}; border-radius: 6px; }}
    QMenu {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; border-radius: 8px; }}
    QMenu::item {{ padding: 8px 28px 8px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c['selection']}; }}
    QToolTip {{ background: {c['charcoal']}; color: white; border: 0; padding: 6px; border-radius: 5px; }}
    QSplitter::handle {{ background: transparent; width: 5px; }}

    QFrame#AssetToolbar {{
        background: {c['card_alt']};
        border: 1px solid {c['border']};
        border-radius: 12px;
    }}
    QFrame#GroupSection {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 12px;
    }}
    QPushButton#GroupHeader {{
        text-align: left;
        background: transparent;
        border: none;
        padding: 5px 8px;
        border-radius: 8px;
        font-weight: 700;
    }}
    QPushButton#GroupHeader[selectedGroup='true'] {{
        background: {c['selection']};
        color: {c['primary']};
    }}
    """
