"""Processing stages panel for the TMX repair GUI."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget


@dataclass(slots=True)
class StageSettings:
    enable_split: bool
    enable_cleanup_spaces: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool


class StagesPanel(QWidget):
    """Owns the stage toggles and the service-markup help hint."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        stages_group = QGroupBox("Этапы обработки")
        stages_layout = QVBoxLayout(stages_group)
        stages_layout.setContentsMargins(10, 10, 10, 10)
        stages_layout.setSpacing(4)

        stages_hint = QLabel(
            "Выберите, какие этапы запускать. Можно выполнить только нужную часть пайплайна."
        )
        stages_hint.setWordWrap(True)
        stages_layout.addWidget(stages_hint)

        self.enable_split_checkbox = QCheckBox("Сплит сегментов по предложениям")
        self.enable_split_checkbox.setChecked(True)
        stages_layout.addWidget(self.enable_split_checkbox)

        self.enable_cleanup_spaces_checkbox = QCheckBox(
            "Очистка пробелов (дубли + края строки)"
        )
        self.enable_cleanup_spaces_checkbox.setChecked(True)
        stages_layout.addWidget(self.enable_cleanup_spaces_checkbox)

        self.enable_cleanup_service_markup_checkbox = QCheckBox(
            "Удаление служебной разметки (теги + игровой markup + %...%)"
        )
        self.enable_cleanup_service_markup_checkbox.setChecked(True)
        service_markup_help_button = QPushButton("?")
        service_markup_help_button.setFixedWidth(26)
        service_markup_help_button.setToolTip("Что входит в очистку служебной разметки")
        service_markup_help_button.clicked.connect(self._show_service_markup_hint)
        service_markup_row = QHBoxLayout()
        service_markup_row.setContentsMargins(0, 0, 0, 0)
        service_markup_row.setSpacing(6)
        service_markup_row.addWidget(self.enable_cleanup_service_markup_checkbox)
        service_markup_row.addWidget(service_markup_help_button, 0, Qt.AlignmentFlag.AlignLeft)
        service_markup_row.addStretch(1)
        stages_layout.addLayout(service_markup_row)

        self.enable_cleanup_garbage_checkbox = QCheckBox("Удаление мусорных TU")
        self.enable_cleanup_garbage_checkbox.setChecked(True)
        stages_layout.addWidget(self.enable_cleanup_garbage_checkbox)

        self.enable_cleanup_warnings_checkbox = QCheckBox(
            "Диагностика WARN (длина/язык/идентичность)"
        )
        self.enable_cleanup_warnings_checkbox.setChecked(True)
        stages_layout.addWidget(self.enable_cleanup_warnings_checkbox)

        root_layout.addWidget(stages_group)

    def values(self) -> StageSettings:
        return StageSettings(
            enable_split=self.enable_split_checkbox.isChecked(),
            enable_cleanup_spaces=self.enable_cleanup_spaces_checkbox.isChecked(),
            enable_cleanup_service_markup=self.enable_cleanup_service_markup_checkbox.isChecked(),
            enable_cleanup_garbage=self.enable_cleanup_garbage_checkbox.isChecked(),
            enable_cleanup_warnings=self.enable_cleanup_warnings_checkbox.isChecked(),
        )

    def _show_service_markup_hint(self) -> None:
        hint_text = (
            "Правило «Удаление служебной разметки» объединяет три очистки:\n\n"
            "1. Удаление inline-тегов внутри seg (bpt/ept/ph/...)\n"
            "2. Удаление игрового markup: ^{...}^, $m(...|...), <Color=...>...</Color>\n"
            "3. Удаление безопасных %...%-токенов (%Name%, %Name%%)\n\n"
            "После удаления выполняется аккуратная склейка текста, чтобы не было слипшихся слов.\n"
            "Нормализуются только обычные пробелы ASCII (NBSP/переносы не изменяются)."
        )
        QMessageBox.information(self, "Подсказка: служебная разметка", hint_text)
