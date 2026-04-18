"""Dialog for Gemini connection settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class GeminiSettingsDialog(QDialog):
    """API key editor with read-only model field."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки Gemini")
        self.setModal(True)
        self.resize(560, 180)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.model_edit = QLineEdit(model)
        self.model_edit.setReadOnly(True)
        form.addRow("Модель Gemini:", self.model_edit)

        self.api_key_edit = QLineEdit(api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Оставьте пустым, чтобы использовать GEMINI_API_KEY из .env")
        form.addRow("API-ключ Gemini:", self.api_key_edit)

        root_layout.addLayout(form)

        note = QLabel("Модель фиксирована и меняется только через переменную окружения GEMINI_MODEL.")
        note.setWordWrap(True)
        root_layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def api_key(self) -> str:
        return self.api_key_edit.text().strip()
