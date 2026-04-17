"""Centralized design tokens and application stylesheet builder."""

from __future__ import annotations

TOKENS: dict[str, str] = {
    "primary": "#056687",
    "primary_dim": "#005977",
    "surface": "#f8f9fa",
    "surface_low": "#f1f4f6",
    "surface_lowest": "#ffffff",
    "inverse_surface": "#0c0f10",
}


def build_app_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget {{
        background: {TOKENS["surface"]};
        color: {TOKENS["inverse_surface"]};
        font-size: 13px;
    }}
    QTabWidget::pane {{
        border: 1px solid #d8e0eb;
        border-radius: 10px;
        background: {TOKENS["surface"]};
    }}
    QTabBar::tab {{
        background: {TOKENS["surface_low"]};
        border: 1px solid #d8e0eb;
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        padding: 8px 12px;
        min-height: 18px;
    }}
    QTabBar::tab:selected {{
        background: {TOKENS["surface_lowest"]};
        color: {TOKENS["inverse_surface"]};
    }}
    QGroupBox {{
        background: {TOKENS["surface_lowest"]};
        border: 1px solid #d8e0eb;
        border-radius: 10px;
        margin-top: 12px;
        padding: 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: #334155;
        background: {TOKENS["surface"]};
    }}
    QLineEdit {{
        min-height: 34px;
        border: 1px solid #c7d2e1;
        border-radius: 8px;
        background: {TOKENS["surface_lowest"]};
        padding: 0 10px;
    }}
    QTextEdit {{
        border: 1px solid #c7d2e1;
        border-radius: 8px;
        background: {TOKENS["surface_lowest"]};
        padding: 6px 8px;
    }}
    QPushButton {{
        min-height: 34px;
        border: 1px solid #c7d2e1;
        border-radius: 8px;
        background: {TOKENS["surface_low"]};
        padding: 0 12px;
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
        border: none;
    }}
    QPushButton:hover {{
        background: #e2e8f0;
    }}
    QPushButton:pressed {{
        background: #cbd5e1;
    }}
    QCheckBox {{
        min-height: 28px;
    }}
    QMenuBar, QMenu {{
        background: {TOKENS["surface_lowest"]};
        border: 1px solid #d8e0eb;
    }}
    """
