"""Status, progress, usage, and log surface for the TMX repair GUI."""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QLabel, QTextEdit, QVBoxLayout, QWidget


class StatusPanel(QWidget):
    """Owns the runtime status labels and condensed logging view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        status_group = QGroupBox("Статус")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(6, 6, 6, 6)
        status_layout.setSpacing(6)

        self.status_label = QLabel("Статус: ожидание")
        status_layout.addWidget(self.status_label)

        self.progress_label = QLabel("Прогресс: ожидание")
        status_layout.addWidget(self.progress_label)

        self.usage_label = QLabel("Gemini: вход=0 | выход=0 | всего=0 | оценка ~$0.000000")
        status_layout.addWidget(self.usage_label)

        self.rate_label = QLabel(
            "Gemini speed: now~0.0 tok/s | avg~0.0 tok/s | current file forecast~$0.000000"
        )
        status_layout.addWidget(self.rate_label)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(180)
        status_layout.addWidget(self.log_output, stretch=1)

        root_layout.addWidget(status_group)

    def set_status(self, text: str) -> None:
        self.status_label.setText(f"Статус: {text}")

    def set_progress(self, text: str) -> None:
        self.progress_label.setText(f"Прогресс: {text}")

    def set_usage(self, in_tokens: int, out_tokens: int, total_tokens: int, cost: float) -> None:
        self.usage_label.setText(
            (
                f"Gemini: вход={in_tokens:,} | выход={out_tokens:,} | "
                f"всего={total_tokens:,} | оценка ~${cost:.6f}"
            )
        )

    def set_rate(self, now_rate: float, avg_rate: float, forecast: float) -> None:
        self.rate_label.setText(
            (
                f"Gemini speed: now~{now_rate:,.1f} tok/s | "
                f"avg~{avg_rate:,.1f} tok/s | "
                f"current file forecast~${forecast:.6f}"
            )
        )

    def append_log(self, message: str) -> None:
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()

    def status_text(self) -> str:
        return self.status_label.text()

    def progress_text(self) -> str:
        return self.progress_label.text()

    def usage_text(self) -> str:
        return self.usage_label.text()

    def rate_text(self) -> str:
        return self.rate_label.text()

    def log_text(self) -> str:
        return self.log_output.toPlainText()

