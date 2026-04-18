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
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #2d6a4f; border-radius: 8px; background: #f4fbf6; }"
        )
        layout = QVBoxLayout(self)
        label = QLabel("Перетащите TMX сюда или нажмите для выбора")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
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
