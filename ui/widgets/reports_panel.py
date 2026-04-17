"""Reports and logging settings panel for the TMX repair GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLineEdit, QVBoxLayout, QWidget


@dataclass(slots=True)
class ReportSettings:
    log_file: str | None
    report_dir: Path | None
    html_report_dir: Path | None
    xlsx_report_dir: Path | None


class ReportsPanel(QWidget):
    """Owns log and report path settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        reports_group = QGroupBox("Отчеты и лог")
        reports_form = QFormLayout(reports_group)
        self._configure_form_layout(reports_form)

        self.log_file_edit = QLineEdit("tmx-repair.log")
        reports_form.addRow("Файл лога:", self.log_file_edit)

        self.html_report_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень HTML отчетов:", self.html_report_edit)

        self.report_dir_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень JSON отчетов:", self.report_dir_edit)

        self.xlsx_report_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень XLSX отчетов:", self.xlsx_report_edit)

        root_layout.addWidget(reports_group)

    def values(self) -> ReportSettings:
        return ReportSettings(
            log_file=self.log_file_edit.text().strip() or None,
            report_dir=self._path_or_none(self.report_dir_edit.text()),
            html_report_dir=self._path_or_none(self.html_report_edit.text()),
            xlsx_report_dir=self._path_or_none(self.xlsx_report_edit.text()),
        )

    def set_report_dir_enabled(self, enabled: bool) -> None:
        self.report_dir_edit.setEnabled(enabled)

    @staticmethod
    def _path_or_none(text: str) -> Path | None:
        stripped = text.strip()
        return Path(stripped) if stripped else None

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        form_layout.setContentsMargins(6, 6, 6, 6)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
