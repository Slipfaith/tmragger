"""Small visual effects shared by major application surfaces."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget


def apply_surface_shadow(widget: QWidget) -> QGraphicsDropShadowEffect:
    """Apply restrained depth to a major card-like surface."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(18.0)
    effect.setOffset(0.0, 3.0)
    effect.setColor(QColor(0, 0, 0, 28))
    widget.setGraphicsEffect(effect)
    return effect
