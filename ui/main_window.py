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
from ui.path_utils import normalize_path_obj
from ui.review_view import ReviewDialog
from ui.theme import build_app_stylesheet
from ui.widgets.gemini_panel import GeminiPanel
from ui.widgets.files_panel import FilesPanel
from ui.widgets.reports_panel import ReportsPanel
from ui.widgets.stages_panel import StagesPanel
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
        self.setStyleSheet(build_app_stylesheet())

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

        self.files_panel = FilesPanel()
        self.files_panel.files_dropped.connect(self._on_files_dropped)
        settings_layout.addWidget(self.files_panel)

        self.stages_panel = StagesPanel()
        settings_layout.addWidget(self.stages_panel)

        self.reports_panel = ReportsPanel()
        settings_layout.addWidget(self.reports_panel)

        self.gemini_panel = GeminiPanel()
        self.gemini_panel.verify_toggled.connect(self.reports_panel.set_report_dir_enabled)
        settings_layout.addWidget(self.gemini_panel)
        self.reports_panel.set_report_dir_enabled(self.gemini_panel.values().verify_with_gemini)

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

    def _on_files_dropped(self, paths: list[str]) -> None:
        current = self.files_panel.input_paths()
        merged = current + [normalize_path_obj(p) for p in paths]
        self.files_panel.set_input_paths(merged)
        self._append_log(f"Files dropped: {len(paths)}")

    def _run_repair(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Выполняется", "Правка уже запущена.")
            return

        input_paths = self.files_panel.input_paths()
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

        stage_values = self.stages_panel.values()
        if not any(
            (
                stage_values.enable_split,
                stage_values.enable_cleanup_spaces,
                stage_values.enable_cleanup_service_markup,
                stage_values.enable_cleanup_garbage,
                stage_values.enable_cleanup_warnings,
            )
        ):
            QMessageBox.warning(
                self,
                "Нет активных этапов",
                "Включите хотя бы один этап: сплит или любую очистку/диагностику.",
            )
            return

        gemini_values = self.gemini_panel.values()
        report_values = self.reports_panel.values()
        gemini_prompt_template = None
        gemini_api_key = ""
        gemini_key_source = ""
        gemini_model = gemini_values.gemini_model.strip() or "gemini-3.1-flash-lite-preview"
        gemini_input_price_raw = gemini_values.gemini_input_price_per_1m.strip() or "0.10"
        gemini_output_price_raw = gemini_values.gemini_output_price_per_1m.strip() or "0.40"
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
        if gemini_values.verify_with_gemini:
            if gemini_values.gemini_api_key:
                gemini_api_key = gemini_values.gemini_api_key
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
            report_dir = report_values.report_dir

        output_dir = self.files_panel.output_dir()

        config = RepairRunConfig(
            input_paths=input_paths,
            output_dir=output_dir,
            dry_run=self.dry_run_checkbox.isChecked(),
            enable_split=stage_values.enable_split,
            enable_cleanup_spaces=stage_values.enable_cleanup_spaces,
            enable_cleanup_service_markup=stage_values.enable_cleanup_service_markup,
            enable_cleanup_garbage=stage_values.enable_cleanup_garbage,
            enable_cleanup_warnings=stage_values.enable_cleanup_warnings,
            log_file=report_values.log_file,
            verify_with_gemini=gemini_values.verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_input_price_per_1m=gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_output_price_per_1m,
            gemini_prompt_template=gemini_prompt_template,
            report_dir=report_dir,
            html_report_dir=report_values.html_report_dir,
            xlsx_report_dir=report_values.xlsx_report_dir,
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
            f"cleanup_service_markup={config.enable_cleanup_service_markup}, "
            f"cleanup_garbage={config.enable_cleanup_garbage}, "
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

    def _show_service_markup_hint(self) -> None:
        hint_text = (
            "Правило «Удаление служебной разметки» объединяет три очистки:\n\n"
            "1. Удаление inline-тегов внутри seg (bpt/ept/ph/...)\n"
            "2. Удаление игрового markup: ^{...}^, $m(...|...), &lt;Color=...&gt;...&lt;/Color&gt;\n"
            "3. Удаление безопасных токенов вида %Name% и %Name%%\n\n"
            "После удаления выполняется аккуратная склейка текста, чтобы не было слипшихся слов.\n"
            "Нормализуются только обычные пробелы ASCII (NBSP/переносы не изменяются)."
        )
        QMessageBox.information(self, "Подсказка: служебная разметка", hint_text)

    def _show_tm_cleanup_help(self) -> None:
        help_text = (
            "Очистка ТМ настраивается блоком «Этапы обработки»:\n\n"
            "1. Сплит сегментов по предложениям\n"
            "  - Делит TU на несколько TU при корректном выравнивании source/target.\n\n"
            "2. AUTO normalize_spaces\n"
            "  - Схлопывает только повторяющиеся обычные пробелы (ASCII ' ') до одного.\n"
            "  - Убирает обычные пробелы по краям сегмента.\n"
            "  - НЕ меняет NBSP/NNBSP, табы и переносы строк.\n\n"
            "3. AUTO remove_service_markup\n"
            "  - Удаляет служебную разметку в одном шаге:\n"
            "    • inline-теги (bpt/ept/ph/...)\n"
            "    • игровой markup ^{...}^, $m(...|...), &lt;Color=...&gt;...&lt;/Color&gt;\n"
            "    • безопасные %...%-токены (%Name%, %Name%%)\n"
            "  - Сохраняет обычные проценты (например, 100%).\n"
            "  - После удаления аккуратно восстанавливает пробелы между фрагментами.\n\n"
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
