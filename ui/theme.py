"""Centralized design tokens and application stylesheet builder."""

from __future__ import annotations

TOKENS: dict[str, str] = {
    "primary": "#056687",
    "primary_dim": "#005977",
    "surface": "#f8f9fa",
    "surface_low": "#f1f4f6",
    "surface_lowest": "#ffffff",
    "surface_high": "#e3e9ec",
    "surface_highest": "#dbe4e7",
    "outline_variant": "#abb3b7",
    "inverse_surface": "#0c0f10",
    "inverse_on_surface": "#9b9d9e",
    "on_surface": "#2b3437",
    "secondary_container": "#cbe7f5",
    "on_secondary_container": "#3c5561",
}


def build_app_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget {{
        background: {TOKENS["surface"]};
        color: {TOKENS["on_surface"]};
        font-size: 13px;
    }}

    QWidget#AppShell {{
        background: {TOKENS["surface"]};
    }}
    QWidget#LeftRail {{
        background: {TOKENS["surface_low"]};
        border-radius: 20px;
    }}
    QLabel#RailEyebrow, QLabel#CanvasSectionLabel {{
        color: {TOKENS["primary_dim"]};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.12em;
    }}
    QLabel#RailTitle, QLabel#CanvasTitleLabel {{
        color: {TOKENS["on_surface"]};
        font-size: 26px;
        font-weight: 700;
    }}
    QLabel#RailSummary, QLabel#RailHint, QLabel#CanvasSubtitleLabel {{
        color: #526066;
        font-size: 13px;
        line-height: 1.4em;
    }}

    QWidget#MainCanvas {{
        background: transparent;
    }}
    QWidget#CanvasTopBar {{
        background: {TOKENS["surface_lowest"]};
        border-radius: 20px;
    }}
    QWidget#StatusStrip {{
        background: rgba(255, 255, 255, 204);
        border-radius: 16px;
    }}
    QLabel#StatusStripLabel {{
        color: #435057;
        font-size: 12px;
        font-weight: 600;
    }}

    QWidget#CanvasCard,
    QWidget#StatusPanelCard,
    QScrollArea#SettingsScroll {{
        background: {TOKENS["surface_lowest"]};
        border-radius: 20px;
    }}
    QScrollArea#SettingsScroll > QWidget > QWidget {{
        background: transparent;
    }}

    QGroupBox {{
        background: {TOKENS["surface_lowest"]};
        border: none;
        border-radius: 20px;
        margin-top: 16px;
        padding: 16px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: #5b6970;
        background: {TOKENS["surface_lowest"]};
    }}

    QLineEdit {{
        min-height: 40px;
        border: none;
        border-radius: 10px;
        background: {TOKENS["surface_high"]};
        padding: 0 10px;
    }}
    QTextEdit {{
        border: none;
        border-radius: 12px;
        background: {TOKENS["surface_high"]};
        padding: 8px 10px;
    }}
    QLineEdit:focus, QTextEdit:focus {{
        background: {TOKENS["surface_lowest"]};
        border: 2px solid rgba(5, 102, 135, 102);
    }}
    QLineEdit:disabled, QTextEdit:disabled {{
        background: {TOKENS["surface_low"]};
        color: rgba(43, 52, 55, 112);
    }}

    QPushButton {{
        min-height: 40px;
        min-width: 40px;
        border: none;
        border-radius: 10px;
        background: {TOKENS["surface_low"]};
        color: {TOKENS["on_surface"]};
        padding: 0 14px;
    }}
    QPushButton[role="primary"] {{
        background: qlineargradient(
            x1: 0,
            y1: 0,
            x2: 1,
            y2: 1,
            stop: 0 {TOKENS["primary"]},
            stop: 1 {TOKENS["primary_dim"]}
        );
        color: {TOKENS["surface_lowest"]};
    }}
    QPushButton[nav="true"] {{
        min-height: 52px;
        min-width: 52px;
        max-width: 52px;
        padding: 0;
        background: transparent;
        color: #44535b;
    }}
    QPushButton[nav="true"]:checked {{
        background: {TOKENS["surface_lowest"]};
        color: {TOKENS["on_surface"]};
        font-weight: 700;
    }}
    QPushButton:focus {{
        border: 2px solid rgba(5, 102, 135, 140);
    }}
    QPushButton:disabled {{
        background: {TOKENS["surface_low"]};
        color: rgba(43, 52, 55, 96);
    }}
    QPushButton:hover {{
        background: {TOKENS["surface_high"]};
    }}
    QPushButton[role="primary"]:hover {{
        background: qlineargradient(
            x1: 0,
            y1: 0,
            x2: 1,
            y2: 1,
            stop: 0 {TOKENS["primary_dim"]},
            stop: 1 {TOKENS["primary"]}
        );
    }}
    QPushButton:pressed {{
        background: {TOKENS["surface_highest"]};
        padding-top: 1px;
    }}

    QCheckBox {{
        min-height: 40px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 6px 2px 6px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {TOKENS["surface_highest"]};
        min-height: 28px;
        border-radius: 5px;
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        border: none;
        height: 0px;
    }}
    QSplitter::handle {{
        background: transparent;
    }}
    QMenuBar, QMenu {{
        background: {TOKENS["surface_lowest"]};
        border: 1px solid rgba(171, 179, 183, 38);
    }}
    QMenuBar::item {{
        padding: 6px 12px;
        border-radius: 8px;
        color: {TOKENS["on_surface"]};
        background: transparent;
    }}
    QMenuBar::item:selected {{
        background: rgba(5, 102, 135, 34);
        color: {TOKENS["on_surface"]};
    }}
    QMenu {{
        padding: 6px;
    }}
    QMenu::item {{
        padding: 8px 12px;
        border-radius: 8px;
        color: {TOKENS["on_surface"]};
        background: transparent;
    }}
    QMenu::item:selected {{
        background: {TOKENS["primary"]};
        color: {TOKENS["surface_lowest"]};
    }}
    QMenu::item:disabled {{
        color: rgba(43, 52, 55, 120);
    }}
    QMenu::separator {{
        height: 1px;
        margin: 6px 8px;
        background: rgba(171, 179, 183, 120);
    }}

    /* --- TMX converter tabs (Convert / Clean / Excel→TMX) --- */
    QLabel#titleLabel {{
        color: {TOKENS["on_surface"]};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#tabSubtitle {{
        color: #526066;
        font-size: 12px;
    }}
    QLabel#sectionLabel {{
        color: {TOKENS["primary_dim"]};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        padding-top: 2px;
    }}
    QLabel#fieldLabel {{
        color: #5b6970;
        font-weight: 600;
    }}
    QFrame#dropZone {{
        border: 2px dashed {TOKENS["outline_variant"]};
        border-radius: 16px;
        background: {TOKENS["surface_low"]};
        color: #526066;
    }}
    QFrame#dropZone:hover, QFrame#dropZone:focus {{
        border-color: {TOKENS["primary"]};
        background: {TOKENS["surface_high"]};
    }}
    QFrame#dropZone[dragActive="true"] {{
        border-color: {TOKENS["primary"]};
        background: {TOKENS["secondary_container"]};
    }}
    QFrame#dropArea {{
        border: 2px dashed {TOKENS["outline_variant"]};
        border-radius: 16px;
        background: {TOKENS["surface_low"]};
        min-height: 56px;
        color: #526066;
    }}
    QFrame#dropArea:hover {{
        border-color: {TOKENS["primary"]};
        background: {TOKENS["surface_high"]};
    }}
    QLabel#dropTitle {{
        color: {TOKENS["on_surface"]};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#dropSubtitle {{
        color: #6b777d;
        font-size: 11px;
    }}
    QPlainTextEdit {{
        border: none;
        border-radius: 12px;
        background: {TOKENS["surface_high"]};
        padding: 8px 10px;
    }}
    QPlainTextEdit:focus {{
        background: {TOKENS["surface_lowest"]};
        border: 2px solid rgba(5, 102, 135, 102);
    }}
    QSpinBox {{
        min-height: 40px;
        border: none;
        border-radius: 10px;
        background: {TOKENS["surface_high"]};
        padding: 0 20px 0 10px;
    }}
    QSpinBox:focus {{
        background: {TOKENS["surface_lowest"]};
        border: 2px solid rgba(5, 102, 135, 102);
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        subcontrol-origin: border;
        width: 16px;
        border: none;
        background: transparent;
        margin: 2px 3px;
    }}
    QSpinBox::up-button {{
        subcontrol-position: top right;
    }}
    QSpinBox::down-button {{
        subcontrol-position: bottom right;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background: {TOKENS["surface_highest"]};
    }}
    QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
        background: {TOKENS["secondary_container"]};
    }}
    QSpinBox::up-arrow {{
        image: none;
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-bottom: 5px solid #5b6970;
    }}
    QSpinBox::down-arrow {{
        image: none;
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #5b6970;
    }}
    QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled {{
        border-bottom-color: {TOKENS["outline_variant"]};
        border-top-color: {TOKENS["outline_variant"]};
    }}
    QComboBox {{
        min-height: 40px;
        border: none;
        border-radius: 10px;
        background: {TOKENS["surface_high"]};
        padding: 0 10px;
    }}
    QComboBox:focus {{
        background: {TOKENS["surface_lowest"]};
        border: 2px solid rgba(5, 102, 135, 102);
    }}
    QComboBox::drop-down {{
        subcontrol-origin: border;
        subcontrol-position: center right;
        width: 22px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #5b6970;
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        border: none;
        border-radius: 10px;
        background: {TOKENS["surface_lowest"]};
        outline: none;
        padding: 4px;
        selection-background-color: {TOKENS["secondary_container"]};
        selection-color: {TOKENS["on_secondary_container"]};
    }}
    QTableWidget {{
        background: {TOKENS["surface_lowest"]};
        border: none;
        border-radius: 12px;
        gridline-color: {TOKENS["surface_high"]};
        selection-background-color: {TOKENS["secondary_container"]};
        selection-color: {TOKENS["on_secondary_container"]};
    }}
    QTableWidget::item {{
        padding: 4px 6px;
    }}
    QHeaderView::section {{
        background: {TOKENS["surface_low"]};
        color: #5b6970;
        border: none;
        border-bottom: 1px solid {TOKENS["surface_high"]};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background: {TOKENS["surface_low"]};
        border: none;
    }}
    QProgressBar {{
        border: none;
        border-radius: 8px;
        background: {TOKENS["surface_high"]};
        min-height: 8px;
        max-height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        border-radius: 8px;
        background: qlineargradient(
            x1: 0, y1: 0, x2: 1, y2: 0,
            stop: 0 {TOKENS["primary"]},
            stop: 1 {TOKENS["primary_dim"]}
        );
    }}
    """
