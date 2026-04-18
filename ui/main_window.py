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
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.env_utils import load_project_env
from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT
from ui.controllers import RunController
from ui.path_utils import normalize_path_obj
from ui.review_view import ReviewDialog
from ui.theme import build_app_stylesheet
from ui.state import ViewState
from ui.widgets.gemini_panel import GeminiPanel
from ui.widgets.files_panel import FilesPanel
from ui.widgets.reports_panel import ReportsPanel
from ui.widgets.status_panel import StatusPanel
from ui.widgets.stages_panel import StagesPanel
from ui.types import BatchRunResult, FileRunResult, PlanPhaseResult, RepairRunConfig


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._loaded_env_files = load_project_env()
        self.setWindowTitle("TMX Repair — пакетная обработка с верификацией через Gemini")
        self.resize(1260, 820)
        self.setMinimumSize(980, 700)
        self._apply_minimal_style()

        self._last_stats: BatchRunResult | None = None
        self._run_controller = RunController(parent=self)
        self._run_controller.log_message.connect(self._append_log)
        self._run_controller.progress_event.connect(self._on_progress_event)
        self._run_controller.plans_ready.connect(self._on_plans_ready)
        self._run_controller.apply_completed.connect(self._on_worker_completed)
        self._run_controller.failed.connect(self._on_worker_failed)
        self._run_controller.run_finished.connect(self._on_worker_finished)
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
        self._shell_status_text = "ожидание"
        self._shell_progress_text = "готово"

        self._build_shell()
        self._build_menu()

    def _apply_minimal_style(self) -> None:
        self.setStyleSheet(build_app_stylesheet())

    def _build_shell(self) -> None:
        shell = QWidget()
        shell.setObjectName("AppShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(18, 18, 18, 18)
        shell_layout.setSpacing(18)

        shell_layout.addWidget(self._build_left_rail())

        self.main_canvas = QWidget()
        self.main_canvas.setObjectName("MainCanvas")
        canvas_layout = QVBoxLayout(self.main_canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(16)
        canvas_layout.addWidget(self._build_top_bar())

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.repair_tab = self._build_repair_tab()
        self.prompt_tab = self._build_prompt_tab()
        self.page_stack.addWidget(self.repair_tab)
        self.page_stack.addWidget(self.prompt_tab)
        self.tabs = self.page_stack
        canvas_layout.addWidget(self.page_stack, stretch=1)

        canvas_layout.addWidget(self._build_status_strip())

        shell_layout.addWidget(self.main_canvas, stretch=1)
        self.setCentralWidget(shell)
        self._switch_page(0)

    def _build_left_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("LeftRail")
        rail.setMinimumWidth(220)

        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(18, 20, 18, 20)
        rail_layout.setSpacing(14)

        eyebrow = QLabel("PRECISION ARCHITECT")
        eyebrow.setObjectName("RailEyebrow")
        rail_layout.addWidget(eyebrow)

        title = QLabel("TMX Repair")
        title.setObjectName("RailTitle")
        rail_layout.addWidget(title)

        summary = QLabel(
            "Editorial shell for batch TMX repair, Gemini review, and report generation."
        )
        summary.setWordWrap(True)
        summary.setObjectName("RailSummary")
        rail_layout.addWidget(summary)

        self.nav_repair_button = QPushButton("Repair")
        self.nav_repair_button.setCheckable(True)
        self.nav_repair_button.setProperty("nav", True)
        self.nav_repair_button.clicked.connect(lambda: self._switch_page(0))
        rail_layout.addWidget(self.nav_repair_button)

        self.nav_prompt_button = QPushButton("Gemini Prompt")
        self.nav_prompt_button.setCheckable(True)
        self.nav_prompt_button.setProperty("nav", True)
        self.nav_prompt_button.clicked.connect(lambda: self._switch_page(1))
        rail_layout.addWidget(self.nav_prompt_button)

        rail_layout.addStretch(1)

        rail_hint = QLabel("Rail buttons switch pages while keeping the existing run workflow intact.")
        rail_hint.setWordWrap(True)
        rail_hint.setObjectName("RailHint")
        rail_layout.addWidget(rail_hint)

        return rail

    def _build_top_bar(self) -> QWidget:
        top_bar = QWidget()
        top_bar.setObjectName("CanvasTopBar")

        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(20, 18, 20, 18)
        top_bar_layout.setSpacing(16)

        heading_layout = QVBoxLayout()
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(4)

        self.canvas_section_label = QLabel()
        self.canvas_section_label.setObjectName("CanvasSectionLabel")
        heading_layout.addWidget(self.canvas_section_label)

        self.canvas_title_label = QLabel()
        self.canvas_title_label.setObjectName("CanvasTitleLabel")
        heading_layout.addWidget(self.canvas_title_label)

        self.canvas_subtitle_label = QLabel()
        self.canvas_subtitle_label.setObjectName("CanvasSubtitleLabel")
        self.canvas_subtitle_label.setWordWrap(True)
        heading_layout.addWidget(self.canvas_subtitle_label)

        top_bar_layout.addLayout(heading_layout, stretch=1)

        self.run_btn = QPushButton("Запустить правку")
        self.run_btn.setProperty("role", "primary")
        self.run_btn.clicked.connect(self._run_repair)
        self.run_btn.setMinimumWidth(190)
        top_bar_layout.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignTop)

        return top_bar

    def _build_status_strip(self) -> QWidget:
        strip = QWidget()
        strip.setObjectName("StatusStrip")

        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(18, 12, 18, 12)
        strip_layout.setSpacing(12)

        self.status_strip_label = QLabel()
        self.status_strip_label.setObjectName("StatusStripLabel")
        self.status_strip_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        strip_layout.addWidget(self.status_strip_label, stretch=1)

        self._sync_status_strip()
        return strip

    def _switch_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        is_repair = index == 0
        self.nav_repair_button.setChecked(is_repair)
        self.nav_prompt_button.setChecked(not is_repair)

        if is_repair:
            self.canvas_section_label.setText("REPAIR WORKBENCH")
            self.canvas_title_label.setText("Batch repair canvas")
            self.canvas_subtitle_label.setText(
                "Stage files, tune cleanup and Gemini review, then launch the repair flow."
            )
        else:
            self.canvas_section_label.setText("PROMPT EDITOR")
            self.canvas_title_label.setText("Gemini verification prompt")
            self.canvas_subtitle_label.setText(
                "Adjust the exact prompt template used when Gemini validation is enabled."
            )

        self._sync_status_strip()

    def _sync_status_strip(self) -> None:
        page_name = "Repair" if self.page_stack.currentIndex() == 0 else "Gemini Prompt"
        self.status_strip_label.setText(
            (
                f"{page_name} | status: {self._shell_status_text} | "
                f"progress: {self._shell_progress_text} | "
                f"tokens: {self._live_tokens_total:,} | cost: ${self._live_cost:.6f}"
            )
        )

    def _set_runtime_status(self, text: str) -> None:
        self._shell_status_text = text
        self.status_panel.set_status(text)
        self._sync_status_strip()

    def _set_runtime_progress(self, text: str) -> None:
        self._shell_progress_text = text
        self.status_panel.set_progress(text)
        self._sync_status_strip()

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
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter, stretch=1)

        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("SettingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(8, 8, 8, 8)
        settings_layout.setSpacing(16)
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

        settings_layout.addStretch(1)

        splitter.addWidget(settings_scroll)

        self.status_panel = StatusPanel()
        self.status_panel.setObjectName("StatusPanelCard")
        self.status_panel.log_output.setProperty("logSurface", True)
        self.status_panel.log_output.style().unpolish(self.status_panel.log_output)
        self.status_panel.log_output.style().polish(self.status_panel.log_output)
        self.status_panel.setMinimumWidth(360)
        splitter.addWidget(self.status_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 420])

        return widget

    def _build_prompt_tab(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("PromptPage")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        intro_card = QWidget()
        intro_card.setObjectName("CanvasCard")
        intro_layout = QVBoxLayout(intro_card)
        intro_layout.setContentsMargins(20, 20, 20, 20)
        intro_layout.setSpacing(12)

        tip = QLabel(
            "Отредактируйте промпт. Именно этот текст будет использоваться при верификации через Gemini."
        )
        tip.setWordWrap(True)
        intro_layout.addWidget(tip)

        self.prompt_editor = QTextEdit()
        self.prompt_editor.setObjectName("PromptEditor")
        self.prompt_editor.setPlainText(self._render_prompt())
        intro_layout.addWidget(self.prompt_editor, stretch=1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        refresh_btn = QPushButton("Сбросить промпт")
        refresh_btn.clicked.connect(self._refresh_prompt)
        copy_btn = QPushButton("Скопировать промпт")
        copy_btn.clicked.connect(self._copy_prompt)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        intro_layout.addLayout(buttons)

        layout.addWidget(intro_card, stretch=1)
        return widget

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        form_layout.setContentsMargins(6, 6, 6, 6)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def _read_view_state(self) -> ViewState:
        stage_values = self.stages_panel.values()
        gemini_values = self.gemini_panel.values()
        report_values = self.reports_panel.values()
        return ViewState(
            input_paths=self.files_panel.input_paths(),
            output_dir=self.files_panel.output_dir(),
            dry_run=self.dry_run_checkbox.isChecked(),
            enable_split=stage_values.enable_split,
            enable_cleanup_spaces=stage_values.enable_cleanup_spaces,
            enable_cleanup_service_markup=stage_values.enable_cleanup_service_markup,
            enable_cleanup_garbage=stage_values.enable_cleanup_garbage,
            enable_cleanup_warnings=stage_values.enable_cleanup_warnings,
            verify_with_gemini=gemini_values.verify_with_gemini,
            gemini_api_key=gemini_values.gemini_api_key,
            gemini_model=gemini_values.gemini_model,
            gemini_input_price_per_1m=gemini_values.gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_values.gemini_output_price_per_1m,
            log_file=report_values.log_file,
            report_dir=report_values.report_dir,
            html_report_dir=report_values.html_report_dir,
            xlsx_report_dir=report_values.xlsx_report_dir,
        )

    def _apply_view_state(self, state: ViewState) -> None:
        self.files_panel.set_input_paths(state.input_paths)
        self.files_panel.set_output_dir(state.output_dir)
        self.dry_run_checkbox.setChecked(state.dry_run)

        self.stages_panel.enable_split_checkbox.setChecked(state.enable_split)
        self.stages_panel.enable_cleanup_spaces_checkbox.setChecked(state.enable_cleanup_spaces)
        self.stages_panel.enable_cleanup_service_markup_checkbox.setChecked(
            state.enable_cleanup_service_markup
        )
        self.stages_panel.enable_cleanup_garbage_checkbox.setChecked(state.enable_cleanup_garbage)
        self.stages_panel.enable_cleanup_warnings_checkbox.setChecked(state.enable_cleanup_warnings)

        self.gemini_panel.verify_checkbox.setChecked(state.verify_with_gemini)
        self.gemini_panel.gemini_api_key_edit.setText(state.gemini_api_key)
        self.gemini_panel.gemini_model_edit.setText(state.gemini_model)
        self.gemini_panel.gemini_input_price_edit.setText(state.gemini_input_price_per_1m)
        self.gemini_panel.gemini_output_price_edit.setText(state.gemini_output_price_per_1m)

        self.reports_panel.log_file_edit.setText(state.log_file or "")
        self.reports_panel.html_report_edit.setText(
            "" if state.html_report_dir is None else str(state.html_report_dir)
        )
        self.reports_panel.report_dir_edit.setText(
            "" if state.report_dir is None else str(state.report_dir)
        )
        self.reports_panel.xlsx_report_edit.setText(
            "" if state.xlsx_report_dir is None else str(state.xlsx_report_dir)
        )
        self.reports_panel.set_report_dir_enabled(state.verify_with_gemini)

    def _on_files_dropped(self, paths: list[str]) -> None:
        current = self.files_panel.input_paths()
        merged = current + [normalize_path_obj(p) for p in paths]
        self.files_panel.set_input_paths(merged)
        self._append_log(f"Files dropped: {len(paths)}")

    def _run_repair(self) -> None:
        if self._run_controller.is_running():
            QMessageBox.information(self, "Выполняется", "Правка уже запущена.")
            return

        view_state = self._read_view_state()
        input_paths = view_state.input_paths
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

        if not any(
            (
                view_state.enable_split,
                view_state.enable_cleanup_spaces,
                view_state.enable_cleanup_service_markup,
                view_state.enable_cleanup_garbage,
                view_state.enable_cleanup_warnings,
            )
        ):
            QMessageBox.warning(
                self,
                "Нет активных этапов",
                "Включите хотя бы один этап: сплит или любую очистку/диагностику.",
            )
            return

        gemini_prompt_template = None
        gemini_api_key = ""
        gemini_key_source = ""
        gemini_model = view_state.gemini_model.strip() or "gemini-3.1-flash-lite-preview"
        gemini_input_price_raw = view_state.gemini_input_price_per_1m.strip() or "0.10"
        gemini_output_price_raw = view_state.gemini_output_price_per_1m.strip() or "0.40"
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
        if view_state.verify_with_gemini:
            if view_state.gemini_api_key:
                gemini_api_key = view_state.gemini_api_key
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
            report_dir = view_state.report_dir

        output_dir = view_state.output_dir

        config = RepairRunConfig(
            input_paths=input_paths,
            output_dir=output_dir,
            dry_run=view_state.dry_run,
            enable_split=view_state.enable_split,
            enable_cleanup_spaces=view_state.enable_cleanup_spaces,
            enable_cleanup_service_markup=view_state.enable_cleanup_service_markup,
            enable_cleanup_garbage=view_state.enable_cleanup_garbage,
            enable_cleanup_warnings=view_state.enable_cleanup_warnings,
            log_file=view_state.log_file,
            verify_with_gemini=view_state.verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_input_price_per_1m=gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_output_price_per_1m,
            gemini_prompt_template=gemini_prompt_template,
            report_dir=report_dir,
            html_report_dir=view_state.html_report_dir,
            xlsx_report_dir=view_state.xlsx_report_dir,
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
        self._set_runtime_status(f"выполняется ({len(input_paths)} файлов)...")
        self._set_runtime_progress("инициализация")
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

        self._run_controller.start_run(config)

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

        dialog = ReviewDialog(plans, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
            self._append_log(f"Отменено пользователем. Было бы применено: {accepted}.")
            self._set_runtime_progress("отменено")
            self._set_runtime_status("отменено")
            return

        accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
        rejected = total_proposals - accepted
        self._append_log(
            f"Review: принято={accepted}, отклонено={rejected}. Запускаю apply-фазу."
        )

        self._run_controller.start_apply(plans)

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
        self._set_runtime_status(
            (
                f"Done: files={len(batch.files)}, total={batch.total_tu}, split={batch.split_tu}, "
                f"skipped={batch.skipped_tu}, output_tu={batch.output_tu}, high={batch.high_conf}, "
                f"medium={batch.medium_conf}, gemini_checked={batch.gemini_checked}, "
                f"gemini_rejected={batch.gemini_rejected}, gemini_tokens={batch.gemini_total_tokens}, "
                f"est_cost=${batch.gemini_estimated_cost_usd:.6f}"
            )
        )
        self._set_runtime_progress("завершено")

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
        self._set_runtime_status("ошибка")
        self._set_runtime_progress("ошибка")
        QMessageBox.critical(self, "Сбой правки", error_text)

    def _on_worker_finished(self) -> None:
        self.run_btn.setEnabled(True)

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
            self._set_runtime_progress(f"файл {file_index}/{file_total} ({short_name})")
        elif event == "file_complete" and file_index > 0 and file_total > 0:
            self._set_runtime_progress(f"завершено {file_index}/{file_total} файлов")

        self._live_tokens_in = int(payload.get("batch_gemini_input_tokens", self._live_tokens_in) or 0)
        self._live_tokens_out = int(payload.get("batch_gemini_output_tokens", self._live_tokens_out) or 0)
        self._live_tokens_total = int(payload.get("batch_gemini_total_tokens", self._live_tokens_total) or 0)
        self._live_cost = float(payload.get("batch_gemini_estimated_cost_usd", self._live_cost) or 0.0)
        self._update_live_rate(self._live_tokens_total)
        self._update_current_file_forecast(payload)
        self._render_live_usage()
        self._render_live_rate()

    def _render_live_usage(self) -> None:
        self.status_panel.set_usage(
            self._live_tokens_in,
            self._live_tokens_out,
            self._live_tokens_total,
            self._live_cost,
        )
        self._sync_status_strip()

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
        self.status_panel.set_rate(
            self._live_rate_tokens_per_sec,
            self._live_rate_avg_tokens_per_sec,
            self._current_file_cost_forecast,
        )
        self._sync_status_strip()

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
        self.status_panel.append_log(message)

