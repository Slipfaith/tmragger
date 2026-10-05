"""Dialog for Codex CLI verification settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.codex_client import CODEX_REASONING_EFFORTS, DEFAULT_CODEX_MODEL


class CodexSettingsDialog(QDialog):
    """Model and reasoning-effort picker for Codex verification."""

    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str,
        codex_bin: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки Codex")
        self.setModal(True)
        self.resize(520, 160)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(280)
        models = [DEFAULT_CODEX_MODEL]
        current = (model or "").strip() or DEFAULT_CODEX_MODEL
        if current not in models:
            models.insert(0, current)
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(current)
        self.model_combo.setToolTip("Имя модели для codex exec -m. Можно ввести вручную.")
        form.addRow("Модель Codex:", self.model_combo)

        self.effort_combo = QComboBox()
        self.effort_combo.addItems(list(CODEX_REASONING_EFFORTS))
        index = self.effort_combo.findText((reasoning_effort or "").strip())
        self.effort_combo.setCurrentIndex(index if index >= 0 else CODEX_REASONING_EFFORTS.index("medium"))
        form.addRow("Reasoning effort:", self.effort_combo)

        root_layout.addLayout(form)

        bin_text = codex_bin or "не найден — установите Codex или задайте CODEX_BIN"
        self.status_label = QLabel(f"Codex CLI: {bin_text}")
        self.status_label.setObjectName("tabSubtitle")
        self.status_label.setToolTip(
            "Авторизация берётся из Codex (codex login). API-ключ не нужен.\n"
            "Путь к бинарю можно переопределить переменной окружения CODEX_BIN."
        )
        root_layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def model(self) -> str:
        return self.model_combo.currentText().strip()

    def reasoning_effort(self) -> str:
        return self.effort_combo.currentText().strip()
