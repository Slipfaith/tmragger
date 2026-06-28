from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.output_paths import sibling_output_dir

from tmx2csv_app.converter import (
    ConversionResult,
    TmxAnalysis,
    analyze_tmx,
    build_pair_specs,
    convert_tmx_file,
)
from tmx2csv_app.excel_to_tmx import ExcelToTmxResult, convert_excel_to_tmx


def run() -> int:
    app = QApplication([])
    app.setApplicationName("TMX Converter")
    window = MainWindow(base_dir=Path.cwd())
    window.show()
    return app.exec()


def _wrap_in_scroll(owner: QWidget) -> QVBoxLayout:
    """Put a scrollable content area on ``owner`` and return its content layout.

    Keeps every control reachable when the window shrinks to its minimum size.
    """
    outer_layout = QVBoxLayout(owner)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    scroll.setWidget(content)
    outer_layout.addWidget(scroll)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(14)
    return layout


class DropArea(QFrame):
    paths_dropped = Signal(object)

    def __init__(self, title_text: str, subtitle_text: str) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)
        title = QLabel(title_text)
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("dropSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = []
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path:
                paths.append(Path(local_path))
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ConversionWorker(QObject):
    log_message = Signal(str)
    analysis_ready = Signal(str, object)
    file_progress = Signal(str, int, int)
    file_done = Signal(str, object)
    file_failed = Signal(str, str)
    finished = Signal(object)

    def __init__(self, file_paths: list[Path], formats: list[str], logger: logging.Logger) -> None:
        super().__init__()
        self.file_paths = file_paths
        self.formats = formats
        self.logger = logger

    @Slot()
    def run(self) -> None:
        completed = 0
        failures = 0
        for file_path in self.file_paths:
            try:
                self._log(logging.INFO, f"Analyzing {file_path.name}")
                analysis = analyze_tmx(file_path)
                self.analysis_ready.emit(str(file_path), analysis)
                pair_text = ", ".join(f"{pair.source_lang}->{pair.target_lang}" for pair in build_pair_specs(analysis))
                self._log(logging.INFO, f"{file_path.name}: {analysis.tu_count} TU, exports [{pair_text}]")
                result = convert_tmx_file(
                    analysis,
                    sibling_output_dir(file_path),
                    self.formats,
                    progress_callback=lambda current, total, path=file_path: self.file_progress.emit(
                        str(path), current, total
                    ),
                )
                self.file_done.emit(str(file_path), result)
                outputs = ", ".join(path.name for path in result.output_files)
                self._log(logging.INFO, f"Finished {file_path.name} -> {outputs}")
            except Exception as exc:
                failures += 1
                self.file_failed.emit(str(file_path), str(exc))
                self._log(logging.ERROR, f"Failed {file_path.name}: {exc}")
            completed += 1
        self.finished.emit({"completed": completed, "failed": failures})

    def _log(self, level: int, message: str) -> None:
        self.logger.log(level, message)
        self.log_message.emit(message)


class ExcelToTmxWorker(QObject):
    log_message = Signal(str)
    file_done = Signal(str, object)
    file_failed = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal(object)

    def __init__(
        self,
        file_paths: list[Path],
        source_lang: str,
        target_lang: str,
        has_header: bool,
        source_column: int,
        target_column: int,
        comment_column: int,
        logger: logging.Logger,
    ) -> None:
        super().__init__()
        self.file_paths = file_paths
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.has_header = has_header
        self.source_column = source_column
        self.target_column = target_column
        self.comment_column = comment_column
        self.logger = logger

    @Slot()
    def run(self) -> None:
        completed = 0
        failures = 0
        total = len(self.file_paths)
        for index, file_path in enumerate(self.file_paths, start=1):
            try:
                self._log(logging.INFO, f"Converting {file_path.name} to TMX")
                result = convert_excel_to_tmx(
                    input_path=file_path,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    has_header=self.has_header,
                    source_column=self.source_column,
                    target_column=self.target_column,
                    comment_column=self.comment_column,
                    output_dir=sibling_output_dir(file_path),
                )
                self.file_done.emit(str(file_path), result)
                self._log(
                    logging.INFO,
                    f"Done {file_path.name} -> {result.output_file.name} ({result.rows_written} TU)",
                )
            except Exception as exc:
                failures += 1
                self.file_failed.emit(str(file_path), str(exc))
                self._log(logging.ERROR, f"Failed {file_path.name}: {exc}")
            completed += 1
            self.progress.emit(index, total)
        self.finished.emit({"completed": completed, "failed": failures})

    def _log(self, level: int, message: str) -> None:
        self.logger.log(level, message)
        self.log_message.emit(message)


class ConvertTab(QWidget):
    def __init__(self, base_dir: Path, logger: logging.Logger) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.logger = logger
        self.file_rows: dict[str, int] = {}
        self.thread: QThread | None = None
        self.worker: ConversionWorker | None = None
        self._build_ui()

    def is_busy(self) -> bool:
        return self.thread is not None and self.thread.isRunning()

    def _build_ui(self) -> None:
        layout = _wrap_in_scroll(self)

        subtitle = QLabel(
            "Для каждого целевого языка в TMX создаются отдельные CSV, XLSX или split-TMX."
        )
        subtitle.setObjectName("tabSubtitle")
        subtitle.setWordWrap(True)

        self.drop_area = DropArea(
            "Перетащите TMX-файлы или папки",
            "Каждый файл — две колонки: исходный и один целевой язык.",
        )
        self.drop_area.paths_dropped.connect(self.add_paths)
        self.drop_area.setMaximumHeight(84)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Файл", "Языки", "TU", "Статус", "Результаты"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(150)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        options_card = QWidget()
        options_card.setObjectName("CanvasCard")
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 16, 16, 16)
        options_layout.setSpacing(12)

        output_hint = QLabel(
            "Результаты сохраняются в папку output рядом с каждым исходным файлом."
        )
        output_hint.setObjectName("tabSubtitle")
        output_hint.setWordWrap(True)
        options_layout.addWidget(output_hint)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(16)
        fmt_label = QLabel("Форматы")
        fmt_label.setObjectName("fieldLabel")
        self.csv_checkbox = QCheckBox("CSV")
        self.xlsx_checkbox = QCheckBox("XLSX")
        self.tmx_checkbox = QCheckBox("TMX")
        for checkbox in (self.csv_checkbox, self.xlsx_checkbox, self.tmx_checkbox):
            checkbox.toggled.connect(self._update_convert_enabled)
        fmt_row.addWidget(fmt_label)
        fmt_row.addWidget(self.csv_checkbox)
        fmt_row.addWidget(self.xlsx_checkbox)
        fmt_row.addWidget(self.tmx_checkbox)
        fmt_row.addStretch(1)
        options_layout.addLayout(fmt_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.add_button = QPushButton("Добавить файлы…")
        self.add_button.clicked.connect(self.select_files)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_queue)
        self.convert_button = QPushButton("Конвертировать")
        self.convert_button.setProperty("role", "primary")
        self.convert_button.setMinimumWidth(150)
        self.convert_button.setFixedHeight(38)
        self.convert_button.clicked.connect(self.start_conversion)
        self.convert_button.setDefault(True)
        action_row.addWidget(self.add_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        action_row.addWidget(self.convert_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("Ожидание")
        self.progress_label.setObjectName("tabSubtitle")

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Здесь появятся логи конвертации.")
        self.log_output.setMinimumHeight(110)

        layout.addWidget(subtitle)
        layout.addWidget(self.drop_area)
        layout.addWidget(self.table, 1)
        layout.addWidget(options_card)
        layout.addLayout(action_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.log_output, 1)

        self._update_convert_enabled()

    @Slot()
    def select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select TMX files",
            str(self.base_dir),
            "TMX files (*.tmx);;All files (*.*)",
        )
        if paths:
            self.add_paths([Path(path) for path in paths])

    @Slot(object)
    def add_paths(self, raw_paths: Iterable[Path]) -> None:
        discovered = _discover_files(raw_paths, {".tmx"})
        if not discovered:
            self.append_log("No TMX files found in the dropped selection.")
            return
        added = 0
        for path in discovered:
            path_key = str(path.resolve())
            if path_key in self.file_rows:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            file_item = QTableWidgetItem(path.name)
            file_item.setToolTip(str(path))
            self.table.setItem(row, 0, file_item)
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem("Queued"))
            self.table.setItem(row, 4, QTableWidgetItem(""))
            self.file_rows[path_key] = row
            added += 1
        self._set_window_status(f"Queued files: {self.table.rowCount()}")
        self.append_log(f"Queued {added} TMX file(s).")

    @Slot()
    def clear_queue(self) -> None:
        if self.is_busy():
            QMessageBox.warning(self, "Conversion in progress", "Wait until the current conversion finishes.")
            return
        self.table.setRowCount(0)
        self.file_rows.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Ожидание")
        self._set_window_status("Очередь конвертации очищена")

    @Slot()
    def start_conversion(self) -> None:
        if self.is_busy():
            return
        file_paths = [Path(self.table.item(row, 0).toolTip()) for row in range(self.table.rowCount())]
        if not file_paths:
            QMessageBox.warning(self, "Нет файлов", "Добавьте хотя бы один TMX-файл или папку.")
            return
        formats = self._selected_formats()
        if not formats:
            QMessageBox.warning(self, "No format selected", "Select CSV, XLSX or both.")
            return
        self._set_running(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Обработка файлов: {len(file_paths)}")
        self.append_log(f"Starting conversion for {len(file_paths)} file(s)")

        self.thread = QThread(self)
        self.worker = ConversionWorker(
            file_paths=file_paths, formats=formats, logger=self.logger
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.append_log)
        self.worker.analysis_ready.connect(self.on_analysis_ready)
        self.worker.file_progress.connect(self.on_file_progress)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.file_failed.connect(self.on_file_failed)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_worker)
        self.thread.start()

    def _selected_formats(self) -> list[str]:
        formats: list[str] = []
        if self.csv_checkbox.isChecked():
            formats.append("csv")
        if self.xlsx_checkbox.isChecked():
            formats.append("xlsx")
        if self.tmx_checkbox.isChecked():
            formats.append("tmx")
        return formats

    def _update_convert_enabled(self) -> None:
        self.convert_button.setEnabled(bool(self._selected_formats()))

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_output.appendPlainText(message)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(str, object)
    def on_analysis_ready(self, path_text: str, analysis: TmxAnalysis) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        try:
            pair_text = " | ".join(f"{pair.source_lang}->{pair.target_lang}" for pair in build_pair_specs(analysis))
        except ValueError:
            pair_text = ", ".join(analysis.languages)
        self.table.item(row, 1).setText(pair_text)
        self.table.item(row, 2).setText(str(analysis.tu_count))
        self.table.item(row, 3).setText("Проанализирован")

    @Slot(str, int, int)
    def on_file_progress(self, path_text: str, current: int, total: int) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        percent = 0 if total == 0 else int(current * 100 / total)
        self.table.item(row, 3).setText(f"Конвертация {percent}%")
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{Path(path_text).name}: {current}/{total}")

    @Slot(str, object)
    def on_file_done(self, path_text: str, result: ConversionResult) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        self.table.item(row, 3).setText("Готово")
        self.table.item(row, 4).setText(", ".join(path.name for path in result.output_files))
        self.progress_bar.setValue(100)

    @Slot(str, str)
    def on_file_failed(self, path_text: str, error_text: str) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        self.table.item(row, 3).setText("Ошибка")
        self.table.item(row, 4).setText(error_text)

    @Slot(object)
    def on_finished(self, summary: object) -> None:
        payload = summary if isinstance(summary, dict) else {}
        completed = int(payload.get("completed", 0))
        failed = int(payload.get("failed", 0))
        self.progress_label.setText(f"Завершено: {completed}; ошибок: {failed}")
        self._set_window_status(self.progress_label.text())
        self._set_running(False)

    @Slot()
    def _reset_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self.thread = None

    def _set_running(self, running: bool) -> None:
        self.add_button.setDisabled(running)
        self.clear_button.setDisabled(running)
        self.csv_checkbox.setDisabled(running)
        self.xlsx_checkbox.setDisabled(running)
        self.tmx_checkbox.setDisabled(running)
        if running:
            self.convert_button.setDisabled(True)
        else:
            self._update_convert_enabled()

    def _set_window_status(self, message: str) -> None:
        window = self.window()
        if isinstance(window, QMainWindow):
            window.statusBar().showMessage(message)


class ExcelToTmxTab(QWidget):
    def __init__(self, base_dir: Path, logger: logging.Logger) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.logger = logger
        self.file_rows: dict[str, int] = {}
        self.thread: QThread | None = None
        self.worker: ExcelToTmxWorker | None = None
        self._build_ui()

    def is_busy(self) -> bool:
        return self.thread is not None and self.thread.isRunning()

    def _build_ui(self) -> None:
        layout = _wrap_in_scroll(self)

        subtitle = QLabel(
            "Конвертация Excel в TMX с настраиваемыми колонками источника, перевода и комментария. "
            "TMX сохраняется в папку output рядом с исходным файлом."
        )
        subtitle.setObjectName("tabSubtitle")
        subtitle.setWordWrap(True)

        self.drop_area = DropArea(
            "Перетащите XLSX-файлы или папки",
            "Номера колонок источника, перевода и комментария задаются ниже.",
        )
        self.drop_area.paths_dropped.connect(self.add_paths)
        self.drop_area.setMaximumHeight(84)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Файл", "Статус", "TU", "Результат"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(150)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        options_card = QWidget()
        options_card.setObjectName("CanvasCard")
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 16, 16, 16)
        options_layout.setSpacing(12)

        self.source_lang_edit = QLineEdit("ru")
        self.target_lang_edit = QLineEdit("en")
        self.has_header_checkbox = QCheckBox("Первая строка — заголовок")
        self.has_header_checkbox.setChecked(True)
        self.source_col_spin = QSpinBox()
        self.source_col_spin.setRange(1, 9999)
        self.source_col_spin.setValue(1)
        self.target_col_spin = QSpinBox()
        self.target_col_spin.setRange(1, 9999)
        self.target_col_spin.setValue(2)
        self.comment_col_spin = QSpinBox()
        self.comment_col_spin.setRange(1, 9999)
        self.comment_col_spin.setValue(3)

        lang_label = QLabel("Языки")
        lang_label.setObjectName("sectionLabel")
        options_layout.addWidget(lang_label)
        self.source_lang_edit.setFixedWidth(90)
        self.target_lang_edit.setFixedWidth(90)
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        src_lang_label = QLabel("Source")
        src_lang_label.setObjectName("fieldLabel")
        tgt_lang_label = QLabel("Target")
        tgt_lang_label.setObjectName("fieldLabel")
        lang_row.addWidget(src_lang_label)
        lang_row.addWidget(self.source_lang_edit)
        lang_row.addSpacing(8)
        lang_row.addWidget(tgt_lang_label)
        lang_row.addWidget(self.target_lang_edit)
        lang_row.addSpacing(8)
        lang_row.addWidget(self.has_header_checkbox)
        lang_row.addStretch(1)
        options_layout.addLayout(lang_row)

        col_label = QLabel("Колонки")
        col_label.setObjectName("sectionLabel")
        options_layout.addWidget(col_label)
        col_row = QHBoxLayout()
        col_row.setSpacing(10)
        for label_text, spin in (
            ("Source", self.source_col_spin),
            ("Target", self.target_col_spin),
            ("Comment", self.comment_col_spin),
        ):
            spin.setFixedWidth(76)
            col_field_label = QLabel(label_text)
            col_field_label.setObjectName("fieldLabel")
            col_row.addWidget(col_field_label)
            col_row.addWidget(spin)
            col_row.addSpacing(8)
        col_row.addStretch(1)
        options_layout.addLayout(col_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.add_button = QPushButton("Добавить файлы…")
        self.add_button.clicked.connect(self.select_files)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_queue)
        self.convert_button = QPushButton("Конвертировать в TMX")
        self.convert_button.setProperty("role", "primary")
        self.convert_button.setMinimumWidth(170)
        self.convert_button.setFixedHeight(38)
        self.convert_button.clicked.connect(self.start_conversion)
        action_row.addWidget(self.add_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        action_row.addWidget(self.convert_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("Ожидание")
        self.progress_label.setObjectName("tabSubtitle")

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Здесь появятся логи Excel→TMX.")
        self.log_output.setMinimumHeight(110)

        layout.addWidget(subtitle)
        layout.addWidget(self.drop_area)
        layout.addWidget(self.table, 1)
        layout.addWidget(options_card)
        layout.addLayout(action_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.log_output, 1)

    @Slot()
    def select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select XLSX files",
            str(self.base_dir),
            "Файлы Excel (*.xlsx);;Все файлы (*.*)",
        )
        if paths:
            self.add_paths([Path(path) for path in paths])

    @Slot(object)
    def add_paths(self, raw_paths: Iterable[Path]) -> None:
        discovered = _discover_files(raw_paths, {".xlsx"})
        if not discovered:
            self.append_log("No XLSX files found in the dropped selection.")
            return

        added = 0
        for path in discovered:
            path_key = str(path.resolve())
            if path_key in self.file_rows:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            file_item = QTableWidgetItem(path.name)
            file_item.setToolTip(str(path))
            self.table.setItem(row, 0, file_item)
            self.table.setItem(row, 1, QTableWidgetItem("Queued"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem(""))
            self.file_rows[path_key] = row
            added += 1

        self._set_window_status(f"Excel->TMX queued files: {self.table.rowCount()}")
        self.append_log(f"Queued {added} XLSX file(s).")

    @Slot()
    def clear_queue(self) -> None:
        if self.is_busy():
            QMessageBox.warning(self, "Task in progress", "Wait until the current task finishes.")
            return
        self.table.setRowCount(0)
        self.file_rows.clear()
        self.progress_label.setText("Ожидание")
        self.progress_bar.setValue(0)
        self._set_window_status("Excel->TMX queue cleared")

    @Slot()
    def start_conversion(self) -> None:
        if self.is_busy():
            return

        file_paths = [Path(self.table.item(row, 0).toolTip()) for row in range(self.table.rowCount())]
        if not file_paths:
            QMessageBox.warning(self, "Нет файлов", "Добавьте хотя бы один XLSX-файл.")
            return

        source_lang = self.source_lang_edit.text().strip()
        target_lang = self.target_lang_edit.text().strip()
        if not source_lang or not target_lang:
            QMessageBox.warning(self, "Languages required", "Set both source and target language codes.")
            return
        source_col = self.source_col_spin.value()
        target_col = self.target_col_spin.value()
        comment_col = self.comment_col_spin.value()
        if len({source_col, target_col, comment_col}) < 3:
            QMessageBox.warning(self, "Ошибка выбора колонок", "Колонки источника, перевода и комментария должны различаться.")
            return

        self._set_running(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Обработка файлов: {len(file_paths)}")
        self.append_log(
            f"Starting Excel->TMX for {len(file_paths)} file(s) "
            f"({source_lang}->{target_lang}, cols {source_col}/{target_col}/{comment_col})"
        )

        self.thread = QThread(self)
        self.worker = ExcelToTmxWorker(
            file_paths=file_paths,
            source_lang=source_lang,
            target_lang=target_lang,
            has_header=self.has_header_checkbox.isChecked(),
            source_column=source_col,
            target_column=target_col,
            comment_column=comment_col,
            logger=self.logger,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.append_log)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.file_failed.connect(self.on_file_failed)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._reset_worker)
        self.thread.start()

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_output.appendPlainText(message)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(str, object)
    def on_file_done(self, path_text: str, result: ExcelToTmxResult) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        self.table.item(row, 1).setText("Готово")
        self.table.item(row, 2).setText(str(result.rows_written))
        self.table.item(row, 3).setText(result.output_file.name)

    @Slot(str, str)
    def on_file_failed(self, path_text: str, error_text: str) -> None:
        row = self.file_rows.get(str(Path(path_text).resolve()))
        if row is None:
            return
        self.table.item(row, 1).setText("Ошибка")
        self.table.item(row, 3).setText(error_text)

    @Slot(int, int)
    def on_progress(self, current: int, total: int) -> None:
        percent = 0 if total == 0 else int(current * 100 / total)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"Обработано {current}/{total}")

    @Slot(object)
    def on_finished(self, summary: object) -> None:
        payload = summary if isinstance(summary, dict) else {}
        completed = int(payload.get("completed", 0))
        failed = int(payload.get("failed", 0))
        self.progress_label.setText(f"Завершено: {completed}; ошибок: {failed}")
        self._set_window_status(self.progress_label.text())
        self._set_running(False)

    @Slot()
    def _reset_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self.thread = None

    def _set_running(self, running: bool) -> None:
        self.add_button.setDisabled(running)
        self.clear_button.setDisabled(running)
        self.convert_button.setDisabled(running)
        self.source_lang_edit.setDisabled(running)
        self.target_lang_edit.setDisabled(running)
        self.has_header_checkbox.setDisabled(running)
        self.source_col_spin.setDisabled(running)
        self.target_col_spin.setDisabled(running)
        self.comment_col_spin.setDisabled(running)

    def _set_window_status(self, message: str) -> None:
        window = self.window()
        if isinstance(window, QMainWindow):
            window.statusBar().showMessage(message)


class MainWindow(QMainWindow):
    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.logger = _build_logger(base_dir / "logs")
        self.convert_tab = ConvertTab(base_dir=base_dir, logger=self.logger)
        self.excel_tmx_tab = ExcelToTmxTab(base_dir=base_dir, logger=self.logger)
        self._build_ui()
        self._build_menu()
        self.setWindowTitle("Инструменты TMX")
        self.resize(1200, 780)
        self.statusBar().showMessage("Готово")

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self.convert_tab, "Конвертация")
        tabs.addTab(self.excel_tmx_tab, "Excel -> TMX")
        self.setCentralWidget(tabs)
        self.setStyleSheet(
            """
            QMainWindow { background: #f5f1e8; }
            QLabel#titleLabel { font-size: 24px; font-weight: 700; color: #2c251f; }
            #dropArea {
                border: 2px dashed #7c5c3b;
                border-radius: 16px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fff9ef, stop:1 #efe1cc);
                min-height: 140px;
            }
            QTableWidget, QPlainTextEdit, QLineEdit, QTabWidget::pane {
                background: white;
                border: 1px solid #d7c6b1;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: #efe1cc;
                border: 1px solid #d7c6b1;
                padding: 8px 14px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background: #7c5c3b;
                color: white;
            }
            QPushButton {
                background: #7c5c3b;
                color: white;
                border-radius: 8px;
                padding: 8px 14px;
            }
            QPushButton:disabled { background: #b59d83; }
            """
        )

    def _build_menu(self) -> None:
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(exit_action)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self.convert_tab.is_busy() or self.excel_tmx_tab.is_busy():
            QMessageBox.warning(self, "Task in progress", "Wait until the current task finishes.")
            event.ignore()
            return
        super().closeEvent(event)


def _discover_files(paths: Iterable[Path], suffixes: set[str]) -> list[Path]:
    discovered: set[Path] = set()
    normalized_suffixes = {suffix.lower() for suffix in suffixes}
    for path in paths:
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() in normalized_suffixes:
            discovered.add(path.resolve())
        elif path.is_dir():
            for suffix in normalized_suffixes:
                discovered.update(item.resolve() for item in path.rglob(f"*{suffix}"))
    return sorted(discovered)


def _build_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tmx2csv")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_dir / "tmx2csv.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
