"""PySide6 desktop app for TMX repair."""

from __future__ import annotations

import os
from pathlib import Path
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.env_utils import load_project_env
from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT
from ui.drop_zone import DropZone
from ui.path_utils import normalize_input_path, normalize_path_obj
from ui.review_view import ReviewDialog
from ui.types import BatchRunResult, FileRunResult, PlanPhaseResult, RepairRunConfig
from ui.worker import RepairWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._loaded_env_files = load_project_env()
        self.setWindowTitle("TMX Repair — пакетная обработка с верификацией через Gemini")
        self.resize(920, 680)
        self.setMinimumSize(860, 620)
        self._apply_minimal_style()

        self._last_stats: BatchRunResult | None = None
        self._worker: RepairWorker | None = None
        self._pending_config: RepairRunConfig | None = None
        self._live_tokens_in = 0
        self._live_tokens_out = 0
        self._live_tokens_total = 0
        self._live_cost = 0.0
        self._live_rate_tokens_per_sec = 0.0
        self._live_rate_avg_tokens_per_sec = 0.0
        self._current_file_cost_forecast = 0.0
        self._run_started_at = 0.0
        self._last_rate_tick_at = 0.0
        self._last_rate_total_tokens = 0

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.repair_tab = self._build_repair_tab()
        self.prompt_tab = self._build_prompt_tab()

        self.tabs.addTab(self.repair_tab, "Правка")
        self.tabs.addTab(self.prompt_tab, "Промпт Gemini")
        self._build_menu()

    def _apply_minimal_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f8fafc;
                color: #0f172a;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #d8e0eb;
                border-radius: 10px;
                background: #f8fafc;
            }
            QTabBar::tab {
                background: #eef2f7;
                border: 1px solid #d8e0eb;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px 12px;
                min-height: 18px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0f172a;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d8e0eb;
                border-radius: 10px;
                margin-top: 12px;
                padding: 12px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #334155;
                background: #f8fafc;
            }
            QLineEdit {
                min-height: 34px;
                border: 1px solid #c7d2e1;
                border-radius: 8px;
                background: #ffffff;
                padding: 0 10px;
            }
            QTextEdit {
                border: 1px solid #c7d2e1;
                border-radius: 8px;
                background: #ffffff;
                padding: 6px 8px;
            }
            QPushButton {
                min-height: 34px;
                border: 1px solid #c7d2e1;
                border-radius: 8px;
                background: #f1f5f9;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #e2e8f0;
            }
            QPushButton:pressed {
                background: #cbd5e1;
            }
            QCheckBox {
                min-height: 28px;
            }
            QMenuBar, QMenu {
                background: #ffffff;
                border: 1px solid #d8e0eb;
            }
            """
        )

    def _build_menu(self) -> None:
        copy_action = QAction("Скопировать промпт Gemini", self)
        copy_action.triggered.connect(self._copy_prompt)

        cleanup_help_action = QAction("Как работает очистка ТМ", self)
        cleanup_help_action.triggered.connect(self._show_tm_cleanup_help)

        tools_menu = self.menuBar().addMenu("Инструменты")
        tools_menu.addAction(copy_action)

        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction(cleanup_help_action)

    def _build_repair_tab(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter, stretch=1)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(2, 2, 2, 2)
        settings_layout.setSpacing(10)
        settings_scroll.setWidget(settings_widget)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files_dropped)
        settings_layout.addWidget(self.drop_zone)

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
        settings_layout.addWidget(files_group)

        cleanup_group = QGroupBox("Этапы обработки")
        cleanup_layout = QVBoxLayout(cleanup_group)
        cleanup_layout.setContentsMargins(10, 10, 10, 10)
        cleanup_layout.setSpacing(4)

        cleanup_hint = QLabel(
            "Выберите, какие этапы запускать. Можно выполнить только нужную часть пайплайна."
        )
        cleanup_hint.setWordWrap(True)
        cleanup_layout.addWidget(cleanup_hint)

        self.enable_split_checkbox = QCheckBox("Сплит сегментов по предложениям")
        self.enable_split_checkbox.setChecked(True)
        cleanup_layout.addWidget(self.enable_split_checkbox)

        self.enable_cleanup_spaces_checkbox = QCheckBox("Очистка пробелов (дубли + края строки)")
        self.enable_cleanup_spaces_checkbox.setChecked(True)
        cleanup_layout.addWidget(self.enable_cleanup_spaces_checkbox)

        self.enable_cleanup_tags_checkbox = QCheckBox(
            "Удаление inline-тегов (bpt/ept/ph/...) с безопасной склейкой текста"
        )
        self.enable_cleanup_tags_checkbox.setChecked(False)
        cleanup_layout.addWidget(self.enable_cleanup_tags_checkbox)

        self.enable_cleanup_garbage_checkbox = QCheckBox("Удаление мусорных TU")
        self.enable_cleanup_garbage_checkbox.setChecked(True)
        cleanup_layout.addWidget(self.enable_cleanup_garbage_checkbox)

        self.enable_cleanup_warnings_checkbox = QCheckBox("Диагностика WARN (длина/язык/идентичность)")
        self.enable_cleanup_warnings_checkbox.setChecked(True)
        cleanup_layout.addWidget(self.enable_cleanup_warnings_checkbox)

        settings_layout.addWidget(cleanup_group)

        reports_group = QGroupBox("Отчеты и лог")
        reports_form = QFormLayout(reports_group)
        self._configure_form_layout(reports_form)

        self.log_file_edit = QLineEdit("tmx-repair.log")
        reports_form.addRow("Файл лога:", self.log_file_edit)

        self.html_report_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень HTML отчетов:", self.html_report_edit)

        self.report_file_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень JSON отчетов:", self.report_file_edit)

        self.xlsx_report_edit = QLineEdit("tmx-reports")
        reports_form.addRow("Корень XLSX отчетов:", self.xlsx_report_edit)
        settings_layout.addWidget(reports_group)

        gemini_group = QGroupBox("Gemini")
        gemini_form = QFormLayout(gemini_group)
        self._configure_form_layout(gemini_form)

        self.verify_gemini_checkbox = QCheckBox("Включить Gemini verification")
        self.verify_gemini_checkbox.toggled.connect(self._toggle_gemini_controls)
        gemini_form.addRow("", self.verify_gemini_checkbox)

        self.gemini_api_key_edit = QLineEdit()
        self.gemini_api_key_edit.setPlaceholderText("Можно оставить пустым, если GEMINI_API_KEY задан в .env")
        self.gemini_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        gemini_form.addRow("API-ключ Gemini:", self.gemini_api_key_edit)

        self.gemini_model_edit = QLineEdit(os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"))
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
        settings_layout.addWidget(gemini_group)

        mode_group = QGroupBox("Режим")
        mode_form = QFormLayout(mode_group)
        self._configure_form_layout(mode_form)
        self.dry_run_checkbox = QCheckBox("Dry run (не записывать repaired TMX)")
        mode_form.addRow("", self.dry_run_checkbox)
        settings_layout.addWidget(mode_group)

        controls = QHBoxLayout()
        controls.setContentsMargins(2, 0, 2, 0)
        controls.setSpacing(8)
        self.run_btn = QPushButton("Запустить правку")
        self.run_btn.clicked.connect(self._run_repair)
        self.run_btn.setMinimumWidth(180)
        controls.addStretch(1)
        controls.addWidget(self.run_btn)
        settings_layout.addLayout(controls)
        settings_layout.addStretch(1)

        self._toggle_gemini_controls(False)
        splitter.addWidget(settings_scroll)

        status_widget = QWidget()
        status_layout = QVBoxLayout(status_widget)
        status_layout.setContentsMargins(2, 2, 2, 2)
        status_layout.setSpacing(6)

        self.stats_label = QLabel("Статус: ожидание")
        status_layout.addWidget(self.stats_label)
        self.progress_label = QLabel("Прогресс: ожидание")
        status_layout.addWidget(self.progress_label)
        self.token_usage_label = QLabel("Gemini: вход=0 | выход=0 | всего=0 | оценка ~$0.000000")
        status_layout.addWidget(self.token_usage_label)
        self.token_rate_label = QLabel(
            "Gemini speed: now~0.0 tok/s | avg~0.0 tok/s | current file forecast~$0.000000"
        )
        status_layout.addWidget(self.token_rate_label)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(180)
        status_layout.addWidget(self.log_output, stretch=1)

        splitter.addWidget(status_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([430, 240])

        return widget

    def _build_prompt_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        tip = QLabel(
            "Отредактируйте промпт. Именно этот текст будет использоваться при верификации через Gemini."
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.prompt_editor = QTextEdit()
        self.prompt_editor.setPlainText(self._render_prompt())
        layout.addWidget(self.prompt_editor, stretch=1)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("Сбросить промпт")
        refresh_btn.clicked.connect(self._refresh_prompt)
        copy_btn = QPushButton("Скопировать промпт")
        copy_btn.clicked.connect(self._copy_prompt)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return widget

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        form_layout.setContentsMargins(6, 6, 6, 6)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    @staticmethod
    def _wrap_layout(inner_layout: QHBoxLayout) -> QWidget:
        wrap = QWidget()
        wrap.setLayout(inner_layout)
        return wrap

    def _toggle_gemini_controls(self, enabled: bool) -> None:
        self.gemini_api_key_edit.setEnabled(enabled)
        self.gemini_model_edit.setEnabled(enabled)
        self.report_file_edit.setEnabled(enabled)
        self.gemini_input_price_edit.setEnabled(enabled)
        self.gemini_output_price_edit.setEnabled(enabled)

    def _on_files_dropped(self, paths: list[str]) -> None:
        current = self._collect_input_paths()
        merged = current + [Path(p) for p in paths]
        self._set_input_paths(merged)
        self._append_log(f"Files dropped: {len(paths)}")

    def _browse_inputs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите TMX-файлы",
            "",
            "TMX Files (*.tmx);;All Files (*)",
        )
        if not paths:
            return
        current = self._collect_input_paths()
        merged = current + [Path(p) for p in paths]
        self._set_input_paths(merged)

    def _clear_inputs(self) -> None:
        self.input_edit.clear()

    def _browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку для результатов")
        if selected:
            self.output_edit.setText(selected)

    def _collect_input_paths(self) -> list[Path]:
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

    def _set_input_paths(self, paths: list[Path]) -> None:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        self.input_edit.setPlainText("\n".join(str(path) for path in unique))

    def _run_repair(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Выполняется", "Правка уже запущена.")
            return

        input_paths = self._collect_input_paths()
        if not input_paths:
            QMessageBox.warning(self, "Нет входных файлов", "Добавьте хотя бы один TMX-файл.")
            return
        missing = [str(p) for p in input_paths if not p.exists()]
        if missing:
            QMessageBox.warning(
                self,
                "Файлы не найдены",
                "Эти файлы не существуют:\n" + "\n".join(missing[:10]),
            )
            return

        enable_split = self.enable_split_checkbox.isChecked()
        enable_cleanup_spaces = self.enable_cleanup_spaces_checkbox.isChecked()
        enable_cleanup_tags = self.enable_cleanup_tags_checkbox.isChecked()
        enable_cleanup_garbage = self.enable_cleanup_garbage_checkbox.isChecked()
        enable_cleanup_warnings = self.enable_cleanup_warnings_checkbox.isChecked()
        if not any(
            (
                enable_split,
                enable_cleanup_spaces,
                enable_cleanup_tags,
                enable_cleanup_garbage,
                enable_cleanup_warnings,
            )
        ):
            QMessageBox.warning(
                self,
                "Нет активных этапов",
                "Включите хотя бы один этап: сплит или любую очистку/диагностику.",
            )
            return

        verify_with_gemini = self.verify_gemini_checkbox.isChecked()
        gemini_prompt_template = None
        gemini_api_key = ""
        gemini_key_source = ""
        gemini_model = self.gemini_model_edit.text().strip() or "gemini-3.1-flash-lite-preview"
        gemini_input_price_raw = self.gemini_input_price_edit.text().strip() or "0.10"
        gemini_output_price_raw = self.gemini_output_price_edit.text().strip() or "0.40"
        try:
            gemini_input_price_per_1m = float(gemini_input_price_raw)
            gemini_output_price_per_1m = float(gemini_output_price_raw)
            if gemini_input_price_per_1m < 0 or gemini_output_price_per_1m < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "Некорректная стоимость Gemini",
                "Стоимость входных/выходных токенов должна быть неотрицательным числом.",
            )
            return
        report_dir = None
        if verify_with_gemini:
            api_key_from_ui = self.gemini_api_key_edit.text().strip()
            if api_key_from_ui:
                gemini_api_key = api_key_from_ui
                gemini_key_source = "поле UI"
            else:
                gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
                gemini_key_source = "GEMINI_API_KEY env"
            if not gemini_api_key:
                env_hint = ""
                if self._loaded_env_files:
                    env_hint = "\nЗагруженные .env:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                QMessageBox.warning(
                    self,
                    "API-ключ Gemini не задан",
                    "Укажите API-ключ Gemini в поле или переменной окружения GEMINI_API_KEY." + env_hint,
                )
                return
            gemini_prompt_template = self.prompt_editor.toPlainText()
            if self._loaded_env_files:
                self._append_log(
                    "Загруженные .env:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                )
            self._append_log(f"Источник API-ключа Gemini: {gemini_key_source}")
            self._append_log(
                "Шаблон промпта Gemini взят из редактора UI:\n"
                f"{gemini_prompt_template}"
            )
            report_dir_raw = self.report_file_edit.text().strip()
            if report_dir_raw:
                report_dir = Path(report_dir_raw)

        output_dir_raw = self.output_edit.text().strip()
        output_dir = Path(output_dir_raw) if output_dir_raw else None

        html_report_dir_raw = self.html_report_edit.text().strip()
        html_report_dir = Path(html_report_dir_raw) if html_report_dir_raw else None

        xlsx_report_dir_raw = self.xlsx_report_edit.text().strip()
        xlsx_report_dir = Path(xlsx_report_dir_raw) if xlsx_report_dir_raw else None

        config = RepairRunConfig(
            input_paths=input_paths,
            output_dir=output_dir,
            dry_run=self.dry_run_checkbox.isChecked(),
            enable_split=enable_split,
            enable_cleanup_spaces=enable_cleanup_spaces,
            enable_cleanup_tags=enable_cleanup_tags,
            enable_cleanup_garbage=enable_cleanup_garbage,
            enable_cleanup_warnings=enable_cleanup_warnings,
            log_file=self.log_file_edit.text().strip() or None,
            verify_with_gemini=verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_input_price_per_1m=gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_output_price_per_1m,
            gemini_prompt_template=gemini_prompt_template,
            report_dir=report_dir,
            html_report_dir=html_report_dir,
            xlsx_report_dir=xlsx_report_dir,
        )

        self._live_tokens_in = 0
        self._live_tokens_out = 0
        self._live_tokens_total = 0
        self._live_cost = 0.0
        self._live_rate_tokens_per_sec = 0.0
        self._live_rate_avg_tokens_per_sec = 0.0
        self._current_file_cost_forecast = 0.0
        self._run_started_at = time.monotonic()
        self._last_rate_tick_at = self._run_started_at
        self._last_rate_total_tokens = 0
        self._render_live_usage()
        self._render_live_rate()
        self.run_btn.setEnabled(False)
        self.stats_label.setText(f"Статус: выполняется ({len(input_paths)} файлов)...")
        self.progress_label.setText("Прогресс: инициализация")
        self._append_log(f"Старт пакетной правки: файлов={len(input_paths)}")
        self._append_log(
            "Настройки: "
            f"dry_run={config.dry_run}, verify_gemini={config.verify_with_gemini}, "
            f"split={config.enable_split}, cleanup_spaces={config.enable_cleanup_spaces}, "
            f"cleanup_tags={config.enable_cleanup_tags}, cleanup_garbage={config.enable_cleanup_garbage}, "
            f"cleanup_warnings={config.enable_cleanup_warnings}, "
            f"model={config.gemini_model}, input_price={config.gemini_input_price_per_1m}, "
            f"output_price={config.gemini_output_price_per_1m}, "
            f"output_dir={config.output_dir or '<same as input>'}, "
            f"html_reports={config.html_report_dir or 'tmx-reports/<file>'}, "
            f"xlsx_reports={config.xlsx_report_dir or 'tmx-reports/<file>'}, "
            f"json_reports={config.report_dir or 'tmx-reports/<file>' if config.verify_with_gemini else 'disabled'}"
        )

        # Two-phase flow: plan → (auto-accept for now; Stage 2.3 will add
        # the review UI) → apply. Keep the config around so we can spawn a
        # fresh worker for the apply phase with the same settings.
        self._pending_config = config
        self._worker = RepairWorker(config, phase="plan")
        self._worker.log_message.connect(self._append_log)
        self._worker.progress_event.connect(self._on_progress_event)
        self._worker.plans_ready.connect(self._on_plans_ready)
        self._worker.apply_completed.connect(self._on_worker_completed)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_plans_ready(self, plans: object) -> None:
        """Plan phase finished — show review dialog, then launch apply phase."""
        if not isinstance(plans, PlanPhaseResult):
            self._on_worker_failed("Внутренняя ошибка: план воркера имеет неверный тип.")
            return
        total_proposals = sum(len(f.plan.proposals) for f in plans.files)
        self._append_log(
            f"Анализ завершён: файлов={len(plans.files)}, кандидатов={total_proposals}. "
            "Открываю окно проверки правок."
        )
        if self._pending_config is None:
            self._on_worker_failed("Внутренняя ошибка: конфигурация apply-фазы потеряна.")
            return
        apply_config = self._pending_config

        dialog = ReviewDialog(plans, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
            self._append_log(f"Отменено пользователем. Было бы применено: {accepted}.")
            self.progress_label.setText("Прогресс: отменено")
            self.stats_label.setText("Статус: отменено")
            # Drop the pending worker reference so _on_worker_finished
            # re-enables the Run button when the plan worker thread exits.
            return

        accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
        rejected = total_proposals - accepted
        self._append_log(
            f"Review: принято={accepted}, отклонено={rejected}. Запускаю apply-фазу."
        )

        self._worker = RepairWorker(apply_config, phase="apply", plans=plans)
        self._worker.log_message.connect(self._append_log)
        self._worker.progress_event.connect(self._on_progress_event)
        self._worker.apply_completed.connect(self._on_worker_completed)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_completed(self, batch: object) -> None:
        if not isinstance(batch, BatchRunResult):
            self._on_worker_failed("Внутренняя ошибка: воркер вернул некорректный результат.")
            return

        self._last_stats = batch
        self._live_tokens_in = batch.gemini_input_tokens
        self._live_tokens_out = batch.gemini_output_tokens
        self._live_tokens_total = batch.gemini_total_tokens
        self._live_cost = batch.gemini_estimated_cost_usd
        self._current_file_cost_forecast = batch.gemini_estimated_cost_usd
        self._update_live_rate(batch.gemini_total_tokens)
        self._render_live_usage()
        self._render_live_rate()
        self.stats_label.setText(
            (
                f"Done: files={len(batch.files)}, total={batch.total_tu}, split={batch.split_tu}, "
                f"skipped={batch.skipped_tu}, output_tu={batch.output_tu}, high={batch.high_conf}, "
                f"medium={batch.medium_conf}, gemini_checked={batch.gemini_checked}, "
                f"gemini_rejected={batch.gemini_rejected}, gemini_tokens={batch.gemini_total_tokens}, "
                f"est_cost=${batch.gemini_estimated_cost_usd:.6f}"
            )
        )
        self.progress_label.setText("Прогресс: завершено")

        dry_run = self.dry_run_checkbox.isChecked()
        done_message = (
            "Dry-run завершён. Исправленные TMX-файлы не записывались."
            if dry_run
            else "Пакетная правка завершена."
        )
        if batch.files:
            first = batch.files[0]
            done_message = (
                f"{done_message}\nПример HTML-отчёта:\n{first.html_report_path}"
            )
            done_message = f"{done_message}\nПример XLSX-отчёта:\n{first.xlsx_report_path}"
            if first.report_path is not None:
                done_message = f"{done_message}\nПример JSON-отчёта:\n{first.report_path}"
        QMessageBox.information(self, "Готово", done_message)

    def _on_worker_failed(self, error_text: str) -> None:
        self._append_log(f"Ошибка: {error_text}")
        self.stats_label.setText("Статус: ошибка")
        self.progress_label.setText("Прогресс: ошибка")
        QMessageBox.critical(self, "Сбой правки", error_text)

    def _on_worker_finished(self) -> None:
        # Only fully re-enable the UI when the chain has ended: the plan
        # worker will immediately spawn the apply worker in
        # ``_on_plans_ready``, so we key off whether a follow-up worker
        # was started.
        if self._worker is not None and self._worker.isRunning():
            return
        self.run_btn.setEnabled(True)
        self._worker = None
        self._pending_config = None

    def _on_progress_event(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return

        event = str(payload.get("event", "")).strip()
        file_index = int(payload.get("file_index", 0) or 0)
        file_total = int(payload.get("file_total", 0) or 0)
        input_path = str(payload.get("input_path", "")).strip()
        short_name = Path(input_path).name if input_path else "unknown"

        # Quiet GUI mode: show only file-level batch progress.
        if event == "file_start" and file_index > 0 and file_total > 0:
            self.progress_label.setText(f"Прогресс: файл {file_index}/{file_total} ({short_name})")
        elif event == "file_complete" and file_index > 0 and file_total > 0:
            self.progress_label.setText(f"Прогресс: завершено {file_index}/{file_total} файлов")

        self._live_tokens_in = int(payload.get("batch_gemini_input_tokens", self._live_tokens_in) or 0)
        self._live_tokens_out = int(payload.get("batch_gemini_output_tokens", self._live_tokens_out) or 0)
        self._live_tokens_total = int(payload.get("batch_gemini_total_tokens", self._live_tokens_total) or 0)
        self._live_cost = float(payload.get("batch_gemini_estimated_cost_usd", self._live_cost) or 0.0)
        self._update_live_rate(self._live_tokens_total)
        self._update_current_file_forecast(payload)
        self._render_live_usage()
        self._render_live_rate()

    def _render_live_usage(self) -> None:
        self.token_usage_label.setText(
            (
                f"Gemini: вход={self._live_tokens_in:,} | выход={self._live_tokens_out:,} | "
                f"всего={self._live_tokens_total:,} | оценка ~${self._live_cost:.6f}"
            )
        )

    def _update_live_rate(self, current_total_tokens: int) -> None:
        now = time.monotonic()
        if self._run_started_at <= 0:
            self._run_started_at = now
        if self._last_rate_tick_at <= 0:
            self._last_rate_tick_at = now
            self._last_rate_total_tokens = current_total_tokens

        delta_tokens = max(0, current_total_tokens - self._last_rate_total_tokens)
        delta_seconds = max(0.0, now - self._last_rate_tick_at)
        if delta_seconds > 0:
            instant_rate = delta_tokens / delta_seconds
            alpha = 0.35
            self._live_rate_tokens_per_sec = (
                instant_rate
                if self._live_rate_tokens_per_sec <= 0
                else (1.0 - alpha) * self._live_rate_tokens_per_sec + alpha * instant_rate
            )

        elapsed = max(0.0, now - self._run_started_at)
        self._live_rate_avg_tokens_per_sec = current_total_tokens / elapsed if elapsed > 0 else 0.0

        self._last_rate_tick_at = now
        self._last_rate_total_tokens = current_total_tokens

    def _update_current_file_forecast(self, payload: dict[str, object]) -> None:
        current_file_cost = float(payload.get("gemini_estimated_cost_usd", 0.0) or 0.0)
        total_tus = int(payload.get("total_tus", 0) or 0)
        split_tus = int(payload.get("split_tus", 0) or 0)
        skipped_tus = int(payload.get("skipped_tus", 0) or 0)
        tu_index = int(payload.get("tu_index", 0) or 0)
        event = str(payload.get("event", "")).strip()

        processed_tus = max(0, split_tus + skipped_tus)
        if event == "file_complete":
            processed_tus = total_tus
        elif processed_tus <= 0 and tu_index > 0:
            processed_tus = max(0, tu_index - 1)

        if total_tus > 0 and processed_tus > 0:
            progress_ratio = min(1.0, processed_tus / total_tus)
            self._current_file_cost_forecast = current_file_cost / progress_ratio
        else:
            self._current_file_cost_forecast = current_file_cost

    def _render_live_rate(self) -> None:
        self.token_rate_label.setText(
            (
                f"Gemini speed: now~{self._live_rate_tokens_per_sec:,.1f} tok/s | "
                f"avg~{self._live_rate_avg_tokens_per_sec:,.1f} tok/s | "
                f"current file forecast~${self._current_file_cost_forecast:.6f}"
            )
        )

    def _refresh_prompt(self) -> None:
        self.prompt_editor.setPlainText(self._render_prompt())

    def _copy_prompt(self) -> None:
        QApplication.clipboard().setText(self.prompt_editor.toPlainText())
        self._append_log("Промпт Gemini скопирован в буфер обмена.")

    def _render_prompt(self) -> str:
        return GEMINI_VERIFICATION_PROMPT

    def _show_tm_cleanup_help(self) -> None:
        help_text = (
            "Очистка ТМ настраивается блоком «Этапы обработки»:\n\n"
            "1. Сплит сегментов по предложениям\n"
            "  - Делит TU на несколько TU при корректном выравнивании source/target.\n\n"
            "2. AUTO normalize_spaces\n"
            "  - Схлопывает только повторяющиеся обычные пробелы (ASCII ' ') до одного.\n"
            "  - Убирает обычные пробелы по краям сегмента.\n"
            "  - НЕ меняет NBSP/NNBSP, табы и переносы строк.\n\n"
            "3. AUTO remove_inline_tags\n"
            "  - Удаляет inline-теги внутри seg (например, bpt/ept/ph).\n"
            "  - Если тег стоял между фрагментами текста, аккуратно восстанавливает пробел, "
            "чтобы фразы не слипались.\n"
            "  - Если тег был в начале/конце, не оставляет лишние пробелы по краям.\n\n"
            "4. AUTO remove_garbage_segment\n"
            "  - Удаляет TU, если source и target состоят только из чисел.\n"
            "  - Удаляет TU, если в source есть осмысленный текст, а в target нет букв/цифр.\n"
            "  - Удаляет TU, если и source, и target состоят только из пунктуации/тегов/пустых значений.\n\n"
            "5. WARN-проверки (TU не удаляется)\n"
            "  - Аномалия длины: подозрительное соотношение длины source/target.\n"
            "  - Несоответствие скрипта (латиница/кириллица/CJK) значению xml:lang.\n"
            "  - Полностью одинаковые source/target при разных языках.\n\n"
            "6. Опциональная проверка Gemini\n"
            "  - При включении Gemini проверяет качество сплита и решений очистки.\n\n"
            "Отчеты:\n"
            "  - HTML и XLSX показывают изменения по каждому TU и сводки по правилам."
        )
        QMessageBox.information(self, "Справка: очистка ТМ", help_text)

    def _append_log(self, message: str) -> None:
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()
