"""Drag-and-drop target widget for TMX files."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class DropZone(QFrame):
    """Целевая область для перетаскивания TMX-файлов."""

    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #2d6a4f; border-radius: 8px; background: #f4fbf6; }"
        )
        layout = QVBoxLayout(self)
        label = QLabel("Перетащите сюда один или несколько TMX-файлов")
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

    @staticmethod
    def _extract_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        found: list[str] = []
        for url in mime.urls():
            local_path = url.toLocalFile()
            if local_path.lower().endswith(".tmx"):
                found.append(local_path)
        return found
