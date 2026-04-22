"""Files input/output panel for the TMX repair GUI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.drop_zone import DropZone
from ui.path_utils import normalize_input_path, normalize_path_obj


class FilesPanel(QWidget):
    """Owns the file list input, output directory, and drop target."""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None, *, include_drop_zone: bool = True) -> None:
        super().__init__(parent)

        self._input_paths: list[Path] = []

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_drop_paths)
        self.drop_zone.clicked.connect(self._browse_inputs)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)
        if include_drop_zone:
            root_layout.addWidget(self.drop_zone)

        files_group = QGroupBox("Файлы")
        files_layout = QVBoxLayout(files_group)
        files_layout.setContentsMargins(6, 6, 6, 6)
        files_layout.setSpacing(10)

        self.input_list = QListWidget()
        self.input_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.input_list.setMinimumHeight(132)

        self.counter_label = QLabel("Загружено: 0")
        self.counter_label.setObjectName("FileCounterLabel")

        add_files_btn = QPushButton("Добавить файлы")
        add_files_btn.clicked.connect(self._browse_inputs)
        remove_selected_btn = QPushButton("Удалить выбранные")
        remove_selected_btn.clicked.connect(self._remove_selected_inputs)
        clear_files_btn = QPushButton("Очистить")
        clear_files_btn.clicked.connect(self._clear_inputs)
        input_buttons = QHBoxLayout()
        input_buttons.setContentsMargins(0, 0, 0, 0)
        input_buttons.setSpacing(8)
        input_buttons.addWidget(add_files_btn)
        input_buttons.addWidget(remove_selected_btn)
        input_buttons.addWidget(clear_files_btn)
        input_buttons.addStretch(1)

        input_wrap = QWidget()
        input_wrap_layout = QVBoxLayout(input_wrap)
        input_wrap_layout.setContentsMargins(0, 0, 0, 0)
        input_wrap_layout.setSpacing(8)
        input_wrap_layout.addWidget(self.input_list)
        input_wrap_layout.addWidget(self.counter_label)
        input_wrap_layout.addLayout(input_buttons)
        files_layout.addWidget(input_wrap)

        self.output_edit = QLineEdit()
        browse_output_dir = QPushButton("Обзор")
        browse_output_dir.clicked.connect(self._browse_output_dir)
        row_out = QHBoxLayout()
        row_out.setContentsMargins(0, 0, 0, 0)
        row_out.setSpacing(8)
        row_out.addWidget(self.output_edit)
        row_out.addWidget(browse_output_dir)
        output_form = QFormLayout()
        self._configure_form_layout(output_form)
        output_form.addRow("Папка output TMX:", self._wrap_layout(row_out))
        files_layout.addLayout(output_form)

        root_layout.addWidget(files_group)

    def input_paths(self) -> list[Path]:
        return list(self._input_paths)

    def set_input_paths(self, paths: list[Path]) -> None:
        unique: list[Path] = []
        seen: set[str] = set()
        for raw_path in paths:
            normalized = normalize_path_obj(str(raw_path))
            key = str(normalized).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(normalized)
        self._input_paths = unique
        self._refresh_input_list()

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
        self._add_input_paths(paths)

    def _on_drop_paths(self, paths: list[str]) -> None:
        self._add_input_paths(paths)
        if paths:
            self.files_dropped.emit(paths)

    def _add_input_paths(self, raw_paths: list[str]) -> None:
        merged = self.input_paths()
        for value in raw_paths:
            normalized = normalize_input_path(str(value))
            if not normalized:
                continue
            merged.append(normalize_path_obj(normalized))
        self.set_input_paths(merged)

    def _remove_selected_inputs(self) -> None:
        selected_rows = sorted(
            (self.input_list.row(item) for item in self.input_list.selectedItems()),
            reverse=True,
        )
        if not selected_rows:
            return
        current = self.input_paths()
        for row in selected_rows:
            if 0 <= row < len(current):
                current.pop(row)
        self.set_input_paths(current)

    def _clear_inputs(self) -> None:
        self._input_paths = []
        self._refresh_input_list()

    def _refresh_input_list(self) -> None:
        self.input_list.clear()
        for index, path in enumerate(self._input_paths, start=1):
            item = QListWidgetItem(f"{index}. {path.name}")
            item.setToolTip(str(path))
            self.input_list.addItem(item)
        self.counter_label.setText(f"Загружено: {len(self._input_paths)}")

    def _browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку для результатов")
        if selected:
            self.set_output_dir(selected)
