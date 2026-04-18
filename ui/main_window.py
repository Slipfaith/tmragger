"""PySide6 desktop app for TMX repair."""

from __future__ import annotations

import os
from pathlib import Path
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.env_utils import load_project_env
from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT
from ui.controllers import RunController
from ui.review_view import ReviewDialog
from ui.theme import build_app_stylesheet
from ui.state import ViewState
from ui.widgets.gemini_settings_dialog import GeminiSettingsDialog
from ui.widgets.files_panel import FilesPanel
from ui.widgets.status_panel import StatusPanel
from ui.widgets.stages_panel import StagesPanel
from ui.types import BatchRunResult, PlanPhaseResult, RepairRunConfig


class MainWindow(QMainWindow):
    DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
    DEFAULT_GEMINI_INPUT_PRICE = 0.10
    DEFAULT_GEMINI_OUTPUT_PRICE = 0.40
    DEFAULT_LOG_FILE = "tmx-repair.log"
    DEFAULT_REPORT_ROOT = Path("tmx-reports")
    GEMINI_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "gemini-color.svg"
    LOG_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "log.ico"

    def __init__(self) -> None:
        super().__init__()
        self._loaded_env_files = load_project_env()
        self._gemini_model = (os.getenv("GEMINI_MODEL", self.DEFAULT_GEMINI_MODEL).strip() or self.DEFAULT_GEMINI_MODEL)
        self._gemini_api_key_override = ""
        self._gemini_input_price_per_1m = self._read_env_float(
            "GEMINI_PRICE_INPUT_PER_1M_USD",
            self.DEFAULT_GEMINI_INPUT_PRICE,
        )
        self._gemini_output_price_per_1m = self._read_env_float(
            "GEMINI_PRICE_OUTPUT_PER_1M_USD",
            self.DEFAULT_GEMINI_OUTPUT_PRICE,
        )
        self.setWindowTitle("TMX Repair")
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
        self.logs_tab = self._build_logs_tab()
        self.page_stack.addWidget(self.repair_tab)
        self.page_stack.addWidget(self.prompt_tab)
        self.page_stack.addWidget(self.logs_tab)
        self.tabs = self.page_stack
        canvas_layout.addWidget(self.page_stack, stretch=1)
        # Keep the status strip object for internal state/tests, but keep it hidden
        # so the token/cost row is not visible in UI.
        self._hidden_status_strip = self._build_status_strip()
        self._hidden_status_strip.setParent(self.main_canvas)
        self._hidden_status_strip.hide()

        shell_layout.addWidget(self.main_canvas, stretch=1)
        self.setCentralWidget(shell)
        self._switch_page(0)

    def _build_left_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("LeftRail")
        rail.setFixedWidth(96)

        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 20, 12, 20)
        rail_layout.setSpacing(10)

        self.nav_repair_button = QPushButton("")
        self.nav_repair_button.setCheckable(True)
        self.nav_repair_button.setProperty("nav", True)
        self.nav_repair_button.setToolTip("Repair")
        self.nav_repair_button.setAccessibleName("Repair")
        self.nav_repair_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.nav_repair_button.setIconSize(QSize(24, 24))
        self.nav_repair_button.clicked.connect(lambda: self._switch_page(0))
        rail_layout.addWidget(self.nav_repair_button)

        self.nav_prompt_button = QPushButton("")
        self.nav_prompt_button.setCheckable(True)
        self.nav_prompt_button.setProperty("nav", True)
        self.nav_prompt_button.setToolTip("Gemini Prompt")
        self.nav_prompt_button.setAccessibleName("Gemini Prompt")
        self.nav_prompt_button.setIcon(QIcon(str(self.GEMINI_ICON_PATH)))
        self.nav_prompt_button.setIconSize(QSize(24, 24))
        self.nav_prompt_button.clicked.connect(lambda: self._switch_page(1))
        rail_layout.addWidget(self.nav_prompt_button)

        self.nav_logs_button = QPushButton("")
        self.nav_logs_button.setCheckable(True)
        self.nav_logs_button.setProperty("nav", True)
        self.nav_logs_button.setToolTip("Logs")
        self.nav_logs_button.setAccessibleName("Logs")
        self.nav_logs_button.setIcon(QIcon(str(self.LOG_ICON_PATH)))
        self.nav_logs_button.setIconSize(QSize(24, 24))
        self.nav_logs_button.clicked.connect(lambda: self._switch_page(2))
        rail_layout.addWidget(self.nav_logs_button)

        rail_layout.addStretch(1)

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

        self.canvas_title_label = QLabel()
        self.canvas_title_label.setObjectName("CanvasTitleLabel")
        heading_layout.addWidget(self.canvas_title_label)

        top_bar_layout.addLayout(heading_layout, stretch=1)

        self.run_btn = QPushButton("Погнали")
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
        self.nav_repair_button.setChecked(index == 0)
        self.nav_prompt_button.setChecked(index == 1)
        self.nav_logs_button.setChecked(index == 2)

        if index == 0:
            self.canvas_title_label.setText("Repair")
        elif index == 1:
            self.canvas_title_label.setText("Gemini Prompt")
        else:
            self.canvas_title_label.setText("Logs")

        self._sync_status_strip()

    def _sync_status_strip(self) -> None:
        self.status_strip_label.setText(f"tok: {self._live_tokens_total:,} | ${self._live_cost:.6f}")

    def _set_runtime_status(self, text: str) -> None:
        self._shell_status_text = text
        self.status_panel.set_status(text)
        self._sync_status_strip()

    def _set_runtime_progress(self, text: str) -> None:
        self._shell_progress_text = text
        self.status_panel.set_progress(text)
        self._sync_status_strip()

    def _build_menu(self) -> None:
        gemini_settings_action = QAction("Настройки Gemini", self)
        gemini_settings_action.triggered.connect(self._open_gemini_settings_dialog)

        copy_action = QAction("Скопировать промпт Gemini", self)
        copy_action.triggered.connect(self._copy_prompt)

        cleanup_help_action = QAction("Как работает очистка ТМ", self)
        cleanup_help_action.triggered.connect(self._show_tm_cleanup_help)

        tools_menu = self.menuBar().addMenu("Инструменты")
        tools_menu.addAction(gemini_settings_action)
        tools_menu.addAction(copy_action)

        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction(cleanup_help_action)

    def _build_repair_tab(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

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

        settings_layout.addStretch(1)
        root_layout.addWidget(settings_scroll, stretch=1)

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

    def _build_logs_tab(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.status_panel = StatusPanel()
        self.status_panel.setObjectName("StatusPanelCard")
        root_layout.addWidget(self.status_panel, stretch=1)
        return widget

    def _read_view_state(self) -> ViewState:
        stage_values = self.stages_panel.values()
        return ViewState(
            input_paths=self.files_panel.input_paths(),
            output_dir=self.files_panel.output_dir(),
            dry_run=False,
            enable_split=stage_values.enable_split,
            enable_split_short_sentence_pair_guard=stage_values.enable_split_short_sentence_pair_guard,
            enable_cleanup_spaces=stage_values.enable_cleanup_spaces,
            enable_cleanup_service_markup=stage_values.enable_cleanup_service_markup,
            enable_cleanup_garbage=stage_values.enable_cleanup_garbage,
            enable_cleanup_warnings=stage_values.enable_cleanup_warnings,
            verify_with_gemini=stage_values.verify_with_gemini,
            gemini_api_key=self._gemini_api_key_override,
            gemini_model=self._gemini_model,
            gemini_input_price_per_1m=f"{self._gemini_input_price_per_1m:.2f}",
            gemini_output_price_per_1m=f"{self._gemini_output_price_per_1m:.2f}",
            log_file=self.DEFAULT_LOG_FILE,
            report_dir=self.DEFAULT_REPORT_ROOT,
            html_report_dir=self.DEFAULT_REPORT_ROOT,
            xlsx_report_dir=self.DEFAULT_REPORT_ROOT,
        )

    def _apply_view_state(self, state: ViewState) -> None:
        self.files_panel.set_input_paths(state.input_paths)
        self.files_panel.set_output_dir(state.output_dir)

        self.stages_panel.enable_split_checkbox.setChecked(state.enable_split)
        self.stages_panel.enable_split_short_sentence_pair_guard_checkbox.setChecked(
            state.enable_split_short_sentence_pair_guard
        )
        self.stages_panel.enable_cleanup_spaces_checkbox.setChecked(state.enable_cleanup_spaces)
        self.stages_panel.enable_cleanup_service_markup_checkbox.setChecked(
            state.enable_cleanup_service_markup
        )
        self.stages_panel.enable_cleanup_garbage_checkbox.setChecked(state.enable_cleanup_garbage)
        self.stages_panel.enable_cleanup_warnings_checkbox.setChecked(state.enable_cleanup_warnings)
        self.stages_panel.enable_gemini_verification_checkbox.setChecked(state.verify_with_gemini)
        self._gemini_api_key_override = state.gemini_api_key

    def _on_files_dropped(self, paths: list[str]) -> None:
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
        gemini_model = self._gemini_model
        gemini_input_price_per_1m = self._gemini_input_price_per_1m
        gemini_output_price_per_1m = self._gemini_output_price_per_1m
        report_dir = None
        if view_state.verify_with_gemini:
            if view_state.gemini_api_key:
                gemini_api_key = view_state.gemini_api_key
                gemini_key_source = "Gemini settings"
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
            report_dir = self.DEFAULT_REPORT_ROOT

        output_dir = view_state.output_dir

        config = RepairRunConfig(
            input_paths=input_paths,
            output_dir=output_dir,
            dry_run=False,
            enable_split=view_state.enable_split,
            enable_split_short_sentence_pair_guard=view_state.enable_split_short_sentence_pair_guard,
            enable_cleanup_spaces=view_state.enable_cleanup_spaces,
            enable_cleanup_service_markup=view_state.enable_cleanup_service_markup,
            enable_cleanup_garbage=view_state.enable_cleanup_garbage,
            enable_cleanup_warnings=view_state.enable_cleanup_warnings,
            log_file=self.DEFAULT_LOG_FILE,
            verify_with_gemini=view_state.verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_input_price_per_1m=gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_output_price_per_1m,
            gemini_prompt_template=gemini_prompt_template,
            report_dir=report_dir,
            html_report_dir=self.DEFAULT_REPORT_ROOT,
            xlsx_report_dir=self.DEFAULT_REPORT_ROOT,
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
            f"verify_gemini={config.verify_with_gemini}, "
            f"split={config.enable_split}, split_short_pair_guard={config.enable_split_short_sentence_pair_guard}, "
            f"cleanup_spaces={config.enable_cleanup_spaces}, "
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
        """Plan phase finished вЂ” show review dialog, then launch apply phase."""
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

        done_message = "Пакетная правка завершена."
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

    def _open_gemini_settings_dialog(self) -> None:
        dialog = GeminiSettingsDialog(
            model=self._gemini_model,
            api_key=self._gemini_api_key_override,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._gemini_api_key_override = dialog.api_key()
        key_source = "Gemini settings" if self._gemini_api_key_override else "GEMINI_API_KEY env"
        self._append_log(f"Gemini settings updated: model={self._gemini_model}, key_source={key_source}")

    def _refresh_prompt(self) -> None:
        self.prompt_editor.setPlainText(self._render_prompt())

    def _copy_prompt(self) -> None:
        QApplication.clipboard().setText(self.prompt_editor.toPlainText())
        self._append_log("Промпт Gemini скопирован в буфер обмена.")

    def _render_prompt(self) -> str:
        return GEMINI_VERIFICATION_PROMPT

    @staticmethod
    def _read_env_float(env_name: str, default: float) -> float:
        raw = os.getenv(env_name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            return value if value >= 0 else default
        except ValueError:
            return default

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
        dialog = QDialog(self)
        dialog.setWindowTitle("Справка: очистка ТМ")
        dialog.setModal(True)
        dialog.resize(980, 700)
        dialog.setMinimumSize(860, 620)

        root_layout = QVBoxLayout(dialog)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        intro_label = QLabel(
            "Ниже — краткая схема этапов очистки TMX и примеры того, что именно меняется."
        )
        intro_label.setWordWrap(True)
        root_layout.addWidget(intro_label)

        help_view = QTextBrowser(dialog)
        help_view.setOpenExternalLinks(False)
        help_view.setHtml(
            """
            <h2>1) Сплит сегментов по предложениям</h2>
            <p>Разбивает один TU на несколько, если source/target корректно выравниваются по предложениям.</p>
            <p><b>Guard:</b> если получаются 2 короткие части (обычно 2-3 слова каждая), сплит пропускается.</p>
            <p><b>Пример:</b><br/>
            До: <code>The battle is almost over. Gather your team and strike now!</code> /
            <code>Битва почти окончена. Собери команду и атакуй прямо сейчас!</code><br/>
            После: 2 отдельных TU.</p>

            <h2>2) Очистка пробелов (AUTO normalize_spaces)</h2>
            <ul>
              <li>Схлопывает повторяющиеся обычные пробелы <code>ASCII ' '</code> до одного.</li>
              <li>Удаляет обычные пробелы в начале и конце сегмента.</li>
              <li><b>Не меняет</b> NBSP/NNBSP, табы и переносы строк.</li>
            </ul>
            <p><b>Пример:</b><br/>
            До: <code>"  Hero   Wars  "</code><br/>
            После: <code>"Hero Wars"</code></p>

            <h2>3) Удаление служебной разметки (AUTO remove_service_markup)</h2>
            <ul>
              <li>Удаляет inline-теги внутри <code>&lt;seg&gt;</code> (<code>bpt/ept/ph/...</code>).</li>
              <li>Удаляет игровой markup: <code>^{...}^</code>, <code>$m(...|...)</code>, <code>&lt;Color=...&gt;...&lt;/Color&gt;</code>.</li>
              <li>Удаляет безопасные токены вида <code>%Name%</code> и <code>%Name%%</code>.</li>
              <li>Сохраняет обычные проценты (например, <code>100%</code>).</li>
              <li>После удаления восстанавливает пробелы, чтобы не было слипшихся слов.</li>
            </ul>
            <p><b>Пример:</b><br/>
            До: <code>&lt;bpt/&gt;Hello %param%&lt;ept/&gt;</code><br/>
            После: <code>Hello</code></p>

            <h2>4) Удаление мусорных TU (AUTO remove_garbage_segment)</h2>
            <ul>
              <li>Удаляет TU, где source и target состоят только из чисел.</li>
              <li>Удаляет TU, где source содержательный, а в target нет букв/цифр.</li>
              <li>Удаляет TU, где обе стороны состоят только из пунктуации/тегов/пустых значений.</li>
            </ul>

            <h2>5) WARN-диагностика (без удаления TU)</h2>
            <ul>
              <li>Аномалия длины source/target.</li>
              <li>Несоответствие скрипта (латиница/кириллица/CJK) значению <code>xml:lang</code>.</li>
              <li>Полностью одинаковые source/target при разных языках.</li>
            </ul>

            <h2>6) Опциональная проверка Gemini</h2>
            <p>Если включена, Gemini проверяет только решения сплита и выставляет confidence.</p>

            <h2>Отчеты</h2>
            <p>HTML и XLSX показывают изменения по каждому TU и сводки по правилам.</p>
            """
        )
        root_layout.addWidget(help_view, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        root_layout.addWidget(buttons)

        dialog.exec()

    def _append_log(self, message: str) -> None:
        self.status_panel.append_log(message)


