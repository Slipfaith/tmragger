"""Stacked widget with a short, interruptible page fade."""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget


class FadingStackedWidget(QStackedWidget):
    """Switch pages immediately, then fade the selected page into view."""

    ANIMATION_DURATION_MS = 140

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._animation: QPropertyAnimation | None = None
        self._animation_page: QWidget | None = None
        self._opacity_effect: QGraphicsOpacityEffect | None = None

    def set_current_index(self, index: int, *, animate: bool) -> None:
        if index < 0 or index >= self.count():
            return

        self._stop_animation()
        if index == self.currentIndex():
            return

        super().setCurrentIndex(index)
        if not animate:
            return

        page = self.currentWidget()
        if page is None:
            return

        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(self.ANIMATION_DURATION_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda: self._finish_animation(animation, page, effect)
        )

        self._animation = animation
        self._animation_page = page
        self._opacity_effect = effect
        animation.start()

    def is_animating(self) -> bool:
        return (
            self._animation is not None
            and self._animation.state() == QAbstractAnimation.State.Running
        )

    def _stop_animation(self) -> None:
        animation = self._animation
        if animation is not None:
            animation.stop()
        self._clear_effect()

    def _finish_animation(
        self,
        animation: QPropertyAnimation,
        page: QWidget,
        effect: QGraphicsOpacityEffect,
    ) -> None:
        if animation is not self._animation:
            return
        effect.setOpacity(1.0)
        if page.graphicsEffect() is effect:
            page.setGraphicsEffect(None)
        self._animation = None
        self._animation_page = None
        self._opacity_effect = None

    def _clear_effect(self) -> None:
        page = self._animation_page
        effect = self._opacity_effect
        if effect is not None:
            effect.setOpacity(1.0)
        if page is not None and page.graphicsEffect() is effect:
            page.setGraphicsEffect(None)
        self._animation = None
        self._animation_page = None
        self._opacity_effect = None
