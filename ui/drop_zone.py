"""Drag-and-drop target widget for TMX files."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ui.path_utils import normalize_input_path


class DropZone(QFrame):
    """Target area for drag-and-drop and click-to-open TMX selection."""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("???????? TMX-?????")
        self.setProperty("dragActive", False)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropZone")
        layout = QVBoxLayout(self)
        label = QLabel("Перетащите TMX сюда или нажмите для выбора")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            self._set_drag_active(False)
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            self._set_drag_active(False)
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._set_drag_active(False)
        paths = self._extract_paths(event)
        if not paths:
            event.ignore()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        ):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_drag_active(self, active: bool) -> None:
        if bool(self.property("dragActive")) == active:
            return
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @staticmethod
    def _extract_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        found: list[str] = []
        for url in mime.urls():
            local_path = url.toLocalFile()
            if not local_path:
                local_path = normalize_input_path(url.toString())
            else:
                local_path = normalize_input_path(local_path)
            # On Windows, toLocalFile() can return /C:/... with a leading slash.
            if local_path and local_path[0] == "/" and len(local_path) > 2 and local_path[2] == ":":
                local_path = local_path[1:]
            if local_path.lower().endswith(".tmx"):
                found.append(local_path)
        return found
