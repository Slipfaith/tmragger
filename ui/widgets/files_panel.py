"""Files input/output panel for the TMX repair GUI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.drop_zone import DropZone
from ui.path_utils import normalize_input_path, normalize_path_obj


class FilesPanel(QWidget):
    """Owns the file list input, output directory, and drop target."""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.files_dropped.emit)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)
        root_layout.addWidget(self.drop_zone)

        files_group = QGroupBox("Файлы")
        files_form = QFormLayout(files_group)
        self._configure_form_layout(files_form)

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("По одному пути TMX на строку")
        self.input_edit.setMinimumHeight(112)

        add_files_btn = QPushButton("Добавить файлы")
        add_files_btn.clicked.connect(self._browse_inputs)
        clear_files_btn = QPushButton("Очистить")
        clear_files_btn.clicked.connect(self._clear_inputs)
        input_buttons = QHBoxLayout()
        input_buttons.setContentsMargins(0, 0, 0, 0)
        input_buttons.setSpacing(8)
        input_buttons.addWidget(add_files_btn)
        input_buttons.addWidget(clear_files_btn)
        input_buttons.addStretch(1)

        input_wrap = QWidget()
        input_wrap_layout = QVBoxLayout(input_wrap)
        input_wrap_layout.setContentsMargins(0, 0, 0, 0)
        input_wrap_layout.setSpacing(8)
        input_wrap_layout.addWidget(self.input_edit)
        input_wrap_layout.addLayout(input_buttons)
        files_form.addRow("Входные TMX:", input_wrap)

        self.output_edit = QLineEdit()
        browse_output_dir = QPushButton("Обзор")
        browse_output_dir.clicked.connect(self._browse_output_dir)
        row_out = QHBoxLayout()
        row_out.setContentsMargins(0, 0, 0, 0)
        row_out.setSpacing(8)
        row_out.addWidget(self.output_edit)
        row_out.addWidget(browse_output_dir)
        files_form.addRow("Папка output TMX:", self._wrap_layout(row_out))

        root_layout.addWidget(files_group)

    def input_paths(self) -> list[Path]:
        raw_lines = [line.strip() for line in self.input_edit.toPlainText().splitlines()]
        paths: list[Path] = []
        seen: set[str] = set()
        for line in raw_lines:
            if not line:
                continue
            normalized = normalize_input_path(line)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(normalize_path_obj(normalized))
        return paths

    def set_input_paths(self, paths: list[Path]) -> None:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        self.input_edit.setPlainText("\n".join(str(path) for path in unique))

    def output_dir(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text) if text else None

    def set_output_dir(self, output_dir: Path | str | None) -> None:
        self.output_edit.setText("" if output_dir is None else str(output_dir))

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        form_layout.setContentsMargins(6, 6, 6, 6)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    @staticmethod
    def _wrap_layout(inner_layout: QHBoxLayout) -> QWidget:
        wrap = QWidget()
        wrap.setLayout(inner_layout)
        return wrap

    def _browse_inputs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите TMX-файлы",
            "",
            "TMX Files (*.tmx);;All Files (*)",
        )
        if not paths:
            return
        current = self.input_paths()
        merged = current + [Path(p) for p in paths]
        self.set_input_paths(merged)

    def _clear_inputs(self) -> None:
        self.input_edit.clear()

    def _browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку для результатов")
        if selected:
            self.set_output_dir(selected)
