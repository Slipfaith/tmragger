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
        border-radius: 16px;
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
        min-height: 36px;
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

    QPushButton {{
        min-height: 34px;
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
        min-height: 42px;
        text-align: left;
        padding-left: 14px;
        background: transparent;
        color: #44535b;
    }}
    QPushButton[nav="true"]:checked {{
        background: {TOKENS["surface_lowest"]};
        color: {TOKENS["on_surface"]};
        font-weight: 700;
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
    }}

    QCheckBox {{
        min-height: 28px;
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
    QTextEdit[logSurface="true"] {{
        background: {TOKENS["inverse_surface"]};
        color: {TOKENS["inverse_on_surface"]};
        border: 1px solid rgba(171, 179, 183, 38);
    }}

    QMenuBar, QMenu {{
        background: {TOKENS["surface_lowest"]};
        border: 1px solid rgba(171, 179, 183, 38);
    }}
    """
