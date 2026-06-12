"""Dialog for Gemini connection settings."""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ModelsLoader = Callable[[str], list[str]]


class GeminiSettingsDialog(QDialog):
    """API key editor with a selectable Gemini model list."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        available_models: Iterable[str] | None = None,
        models_loader: ModelsLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._models_loader = models_loader
        self.setWindowTitle("Настройки Gemini")
        self.setModal(True)
        self.resize(560, 220)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(280)
        self._set_models(available_models, current=model)
        self.load_models_button = QPushButton("Загрузить модели")
        self.load_models_button.clicked.connect(self._on_load_models)
        self.load_models_button.setEnabled(self._models_loader is not None)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.load_models_button)
        form.addRow("Модель Gemini:", model_row)

        self.api_key_edit = QLineEdit(api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Оставьте пустым, чтобы использовать GEMINI_API_KEY из .env")
        form.addRow("API-ключ Gemini:", self.api_key_edit)

        root_layout.addLayout(form)

        self.status_label = QLabel(
            "Доступны только модели Gemini 3 и новее (без image/video/audio-моделей). "
            "Нажмите «Загрузить модели», чтобы обновить список по вашему ключу."
        )
        self.status_label.setObjectName("tabSubtitle")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def _set_models(self, models: Iterable[str] | None, current: str) -> None:
        items: list[str] = []
        for item in models or []:
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
        current = (current or "").strip()
        if current and current not in items:
            items.insert(0, current)

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(items)
        if current:
            index = self.model_combo.findText(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            else:
                self.model_combo.setEditText(current)
        self.model_combo.blockSignals(False)

    def _on_load_models(self) -> None:
        if self._models_loader is None:
            return
        api_key = self.api_key_edit.text().strip()
        current = self.model()
        self.load_models_button.setEnabled(False)
        self.status_label.setText("Загрузка списка моделей…")
        try:
            models = self._models_loader(api_key)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.status_label.setText(f"Не удалось загрузить модели: {exc}")
            self.load_models_button.setEnabled(True)
            return
        if not models:
            self.status_label.setText("Подходящих моделей Gemini 3+ не найдено для этого ключа.")
            self.load_models_button.setEnabled(True)
            return
        self._set_models(models, current=current)
        self.status_label.setText(f"Загружено моделей: {len(models)} (Gemini 3 и новее).")
        self.load_models_button.setEnabled(True)

    def model(self) -> str:
        return self.model_combo.currentText().strip()

    def api_key(self) -> str:
        return self.api_key_edit.text().strip()
