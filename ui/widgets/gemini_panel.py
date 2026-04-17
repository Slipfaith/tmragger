"""Gemini verification settings panel for the TMX repair GUI."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QVBoxLayout, QWidget


@dataclass(slots=True)
class GeminiSettings:
    verify_with_gemini: bool
    gemini_api_key: str
    gemini_model: str
    gemini_input_price_per_1m: str
    gemini_output_price_per_1m: str


class GeminiPanel(QWidget):
    """Owns the Gemini verification toggle and pricing inputs."""

    verify_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        gemini_group = QGroupBox("Gemini")
        gemini_form = QFormLayout(gemini_group)
        self._configure_form_layout(gemini_form)

        self.verify_checkbox = QCheckBox("Включить Gemini verification")
        self.verify_checkbox.toggled.connect(self._on_verify_toggled)
        gemini_form.addRow("", self.verify_checkbox)

        self.gemini_api_key_edit = QLineEdit()
        self.gemini_api_key_edit.setPlaceholderText(
            "Можно оставить пустым, если GEMINI_API_KEY задан в .env"
        )
        self.gemini_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        gemini_form.addRow("API-ключ Gemini:", self.gemini_api_key_edit)

        self.gemini_model_edit = QLineEdit(
            os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
        )
        gemini_form.addRow("Модель Gemini:", self.gemini_model_edit)

        self.gemini_input_price_edit = QLineEdit(
            os.getenv("GEMINI_PRICE_INPUT_PER_1M_USD", "0.10")
        )
        gemini_form.addRow("Цена input ($/1M токенов):", self.gemini_input_price_edit)

        self.gemini_output_price_edit = QLineEdit(
            os.getenv("GEMINI_PRICE_OUTPUT_PER_1M_USD", "0.40")
        )
        gemini_form.addRow("Цена output ($/1M токенов):", self.gemini_output_price_edit)

        prompt_hint = QLabel("Шаблон prompt редактируется на вкладке Gemini Prompt.")
        prompt_hint.setWordWrap(True)
        gemini_form.addRow("", prompt_hint)
        root_layout.addWidget(gemini_group)

        self.verify_toggled.connect(self.set_controls_enabled)
        self.set_controls_enabled(False)

    def values(self) -> GeminiSettings:
        return GeminiSettings(
            verify_with_gemini=self.verify_checkbox.isChecked(),
            gemini_api_key=self.gemini_api_key_edit.text().strip(),
            gemini_model=self.gemini_model_edit.text().strip(),
            gemini_input_price_per_1m=self.gemini_input_price_edit.text().strip(),
            gemini_output_price_per_1m=self.gemini_output_price_edit.text().strip(),
        )

    def _on_verify_toggled(self, enabled: bool) -> None:
        self.verify_toggled.emit(enabled)

    def set_controls_enabled(self, enabled: bool) -> None:
        self.gemini_api_key_edit.setEnabled(enabled)
        self.gemini_model_edit.setEnabled(enabled)
        self.gemini_input_price_edit.setEnabled(enabled)
        self.gemini_output_price_edit.setEnabled(enabled)

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        form_layout.setContentsMargins(6, 6, 6, 6)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
