"""PySide6 desktop app for TMX repair."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import time

from app_meta import APP_ICON_SVG_PATH, APP_NAME, APP_VERSION
from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyle,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.env_utils import load_project_env
from core.gemini_client import list_gemini_models
from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT
from core.offline_package import export_tmrepair_package, import_tmrepair_package
from core.repair import repair_tmx_file
from ui.app_settings import create_app_settings
from ui.controllers import RunController
from ui.review_view import ReviewDialog
from ui.theme import build_app_stylesheet
from ui.state import ViewState
from ui.widgets.fading_stack import FadingStackedWidget
from ui.widgets.surface_effects import apply_surface_shadow
from ui.widgets.gemini_settings_dialog import GeminiSettingsDialog
from ui.widgets.files_panel import FilesPanel
from ui.widgets.status_panel import StatusPanel
from ui.widgets.stages_panel import StagesPanel
from ui.types import BatchRunResult, PlanPhaseResult, RepairRunConfig
from tmx2csv_app.gui import ConvertTab, CleanTab, ExcelToTmxTab, _build_logger


class MainWindow(QMainWindow):
    DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
    DEFAULT_GEMINI_INPUT_PRICE = 0.10
    DEFAULT_GEMINI_OUTPUT_PRICE = 0.40
    DEFAULT_GEMINI_MAX_PARALLEL = 4
    DEFAULT_GEMINI_MAX_CHECKS = 1200
    DEFAULT_LOG_FILE = "tmx-repair.log"
    DEFAULT_REPORT_ROOT = Path("tmx-reports")
    GEMINI_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "gemini-color.svg"
    XLSX_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "xlsx.svg"
    LOG_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "log.ico"
    SETTINGS_ORG = APP_NAME
    SETTINGS_APP = f"{APP_NAME}-gui"
    SETTINGS_WINDOW_GEOMETRY_KEY = "window/geometry"
    SETTINGS_WINDOW_STATE_KEY = "window/state"
    SETTINGS_GEMINI_MODEL_KEY = "gemini/model"
    SETTINGS_GEMINI_API_KEY_KEY = "gemini/api_key"

    def __init__(self) -> None:
        super().__init__()
        self._loaded_env_files = load_project_env()
        self._gemini_model = (os.getenv("GEMINI_MODEL", self.DEFAULT_GEMINI_MODEL).strip() or self.DEFAULT_GEMINI_MODEL)
        persisted_model = self._read_persisted_gemini_model()
        if persisted_model:
            self._gemini_model = persisted_model
        self._gemini_available_models: list[str] = [self._gemini_model]
        self._gemini_api_key_override = self._read_persisted_gemini_api_key()
        self._gemini_input_price_per_1m = self._read_env_float(
            "GEMINI_PRICE_INPUT_PER_1M_USD",
            self.DEFAULT_GEMINI_INPUT_PRICE,
        )
        self._gemini_output_price_per_1m = self._read_env_float(
            "GEMINI_PRICE_OUTPUT_PER_1M_USD",
            self.DEFAULT_GEMINI_OUTPUT_PRICE,
        )
        self._gemini_max_parallel = max(
            1,
            int(os.getenv("GEMINI_MAX_PARALLEL", str(self.DEFAULT_GEMINI_MAX_PARALLEL)).strip() or "1"),
        )
        self._gemini_max_checks = self._read_env_optional_int(
            "GEMINI_MAX_CHECKS",
            self.DEFAULT_GEMINI_MAX_CHECKS,
        )
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        if APP_ICON_SVG_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_SVG_PATH)))
        self.resize(1260, 820)
        self.setMinimumSize(980, 700)
        self._apply_minimal_style()

        self._last_stats: BatchRunResult | None = None
        self._latest_plan_phase: PlanPhaseResult | None = None
        self._last_run_config: RepairRunConfig | None = None
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
        self._run_finished_at = 0.0
        self._last_rate_tick_at = 0.0
        self._last_rate_total_tokens = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed_timer)
        self._shell_status_text = "idle"
        self._shell_progress_text = "ready"

        self._build_shell()
        self._build_menu()
        self._restore_window_persistence()

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

        self.page_stack = FadingStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.repair_tab = self._build_repair_tab()
        self.prompt_tab = self._build_prompt_tab()
        self.logs_tab = self._build_logs_tab()
        self.page_stack.addWidget(self.repair_tab)
        self.page_stack.addWidget(self.prompt_tab)
        self.page_stack.addWidget(self.logs_tab)

        self._converter_logger = _build_logger(Path.cwd() / "logs")
        self.convert_tab = ConvertTab(base_dir=Path.cwd(), logger=self._converter_logger)
        self.clean_tab = CleanTab(base_dir=Path.cwd(), logger=self._converter_logger)
        self.excel_tmx_tab = ExcelToTmxTab(base_dir=Path.cwd(), logger=self._converter_logger)
        self.page_stack.addWidget(self.convert_tab)
        self.page_stack.addWidget(self.clean_tab)
        self.page_stack.addWidget(self.excel_tmx_tab)

        self._page_titles = {
            0: "Исправление",
            1: "Промпт Gemini",
            2: "Журнал",
            3: "Конвертация",
            4: "Очистка",
            5: "Excel → TMX",
        }
        self.tabs = self.page_stack
        canvas_layout.addWidget(self.page_stack, stretch=1)
        # Keep the status strip object for internal state/tests, but keep it hidden
        # so the token/cost row is not visible in UI.
        self._hidden_status_strip = self._build_status_strip()
        self._hidden_status_strip.setParent(self.main_canvas)
        self._hidden_status_strip.hide()

        shell_layout.addWidget(self.main_canvas, stretch=1)
        self.setCentralWidget(shell)
        self._switch_page(0, animate=False)

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
        self.nav_repair_button.setToolTip("Исправление")
        self.nav_repair_button.setAccessibleName("Исправление")
        self.nav_repair_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.nav_repair_button.setIconSize(QSize(24, 24))
        self.nav_repair_button.clicked.connect(lambda: self._switch_page(0))
        rail_layout.addWidget(self.nav_repair_button)

        self.nav_prompt_button = QPushButton("")
        self.nav_prompt_button.setCheckable(True)
        self.nav_prompt_button.setProperty("nav", True)
        self.nav_prompt_button.setToolTip("Промпт Gemini")
        self.nav_prompt_button.setAccessibleName("Промпт Gemini")
        self.nav_prompt_button.setIcon(QIcon(str(self.GEMINI_ICON_PATH)))
        self.nav_prompt_button.setIconSize(QSize(24, 24))
        self.nav_prompt_button.clicked.connect(lambda: self._switch_page(1))
        rail_layout.addWidget(self.nav_prompt_button)

        self.nav_logs_button = QPushButton("")
        self.nav_logs_button.setCheckable(True)
        self.nav_logs_button.setProperty("nav", True)
        self.nav_logs_button.setToolTip("Журнал")
        self.nav_logs_button.setAccessibleName("Журнал")
        self.nav_logs_button.setIcon(QIcon(str(self.LOG_ICON_PATH)))
        self.nav_logs_button.setIconSize(QSize(24, 24))
        self.nav_logs_button.clicked.connect(lambda: self._switch_page(2))
        rail_layout.addWidget(self.nav_logs_button)

        self.nav_convert_button = QPushButton("")
        self.nav_convert_button.setCheckable(True)
        self.nav_convert_button.setProperty("nav", True)
        self.nav_convert_button.setToolTip("Конвертация")
        self.nav_convert_button.setAccessibleName("Конвертация")
        self.nav_convert_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self.nav_convert_button.setIconSize(QSize(24, 24))
        self.nav_convert_button.clicked.connect(lambda: self._switch_page(3))
        rail_layout.addWidget(self.nav_convert_button)

        self.nav_clean_button = QPushButton("")
        self.nav_clean_button.setCheckable(True)
        self.nav_clean_button.setProperty("nav", True)
        self.nav_clean_button.setToolTip("Очистка")
        self.nav_clean_button.setAccessibleName("Очистка")
        self.nav_clean_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self.nav_clean_button.setIconSize(QSize(24, 24))
        self.nav_clean_button.clicked.connect(lambda: self._switch_page(4))
        rail_layout.addWidget(self.nav_clean_button)

        self.nav_excel_button = QPushButton("")
        self.nav_excel_button.setCheckable(True)
        self.nav_excel_button.setProperty("nav", True)
        self.nav_excel_button.setToolTip("Excel → TMX")
        self.nav_excel_button.setAccessibleName("Excel → TMX")
        self.nav_excel_button.setIcon(QIcon(str(self.XLSX_ICON_PATH)))
        self.nav_excel_button.setIconSize(QSize(24, 24))
        self.nav_excel_button.clicked.connect(lambda: self._switch_page(5))
        rail_layout.addWidget(self.nav_excel_button)

        rail_layout.addStretch(1)

        self._nav_buttons = {
            0: self.nav_repair_button,
            1: self.nav_prompt_button,
            2: self.nav_logs_button,
            3: self.nav_convert_button,
            4: self.nav_clean_button,
            5: self.nav_excel_button,
        }

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
        self.run_btn.setToolTip("Погнали")
        self.run_btn.setAccessibleName("Погнали")
        self.run_btn.setProperty("role", "primary")
        self.run_btn.clicked.connect(self._run_repair)
        self.run_btn.setFixedHeight(40)
        self.run_btn.setMinimumWidth(120)
        top_bar_layout.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.pause_btn = QPushButton("")
        self.pause_btn.setToolTip("Пауза")
        self.pause_btn.setAccessibleName("Пауза")
        self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_btn.setIconSize(QSize(22, 22))
        self.pause_btn.clicked.connect(self._pause_repair)
        self.pause_btn.setFixedSize(44, 40)
        top_bar_layout.addWidget(self.pause_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.resume_btn = QPushButton("")
        self.resume_btn.setToolTip("Продолжить")
        self.resume_btn.setAccessibleName("Продолжить")
        self.resume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.resume_btn.setIconSize(QSize(22, 22))
        self.resume_btn.clicked.connect(self._resume_repair)
        self.resume_btn.setFixedSize(44, 40)
        top_bar_layout.addWidget(self.resume_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.stop_btn = QPushButton("")
        self.stop_btn.setToolTip("Остановить")
        self.stop_btn.setAccessibleName("Остановить")
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setIconSize(QSize(22, 22))
        self.stop_btn.clicked.connect(self._stop_repair)
        self.stop_btn.setFixedSize(44, 40)
        top_bar_layout.addWidget(self.stop_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._sync_transport_buttons()
        apply_surface_shadow(top_bar)

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

    def _switch_page(self, index: int, *, animate: bool = True) -> None:
        self.page_stack.set_current_index(index, animate=animate)
        for page_index, button in self._nav_buttons.items():
            button.setChecked(page_index == index)

        self.canvas_title_label.setText(self._page_titles.get(index, ""))
        self.run_btn.setVisible(index == 0)
        show_transport = index == 2
        self.pause_btn.setVisible(show_transport)
        self.resume_btn.setVisible(show_transport)
        self.stop_btn.setVisible(show_transport)

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

    def _sync_transport_buttons(self) -> None:
        running = bool(self._run_controller.is_running())
        is_paused = getattr(self._run_controller, "is_paused", None)
        paused = bool(is_paused()) if callable(is_paused) else False
        self.run_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running and not paused)
        self.resume_btn.setEnabled(running and paused)
        self.stop_btn.setEnabled(running)

    def _pause_repair(self) -> None:
        if self._run_controller.pause():
            self._set_runtime_status("paused")
            self._set_runtime_progress("paused")
            self._sync_transport_buttons()

    def _resume_repair(self) -> None:
        if self._run_controller.resume():
            self._set_runtime_status("running")
            self._set_runtime_progress("resumed")
            self._sync_transport_buttons()

    def _stop_repair(self) -> None:
        if self._run_controller.stop():
            self._set_runtime_status("stopping...")
            self._set_runtime_progress("stopping...")
            self._append_log("Stop requested by user.")
            self._sync_transport_buttons()

    def _build_menu(self) -> None:
        gemini_settings_action = QAction("Настройки Gemini…", self)
        gemini_settings_action.triggered.connect(self._open_gemini_settings_dialog)

        copy_action = QAction("Скопировать Gemini-промпт", self)
        copy_action.triggered.connect(self._copy_prompt)

        export_package_action = QAction("Экспорт пакета .tmrepair…", self)
        export_package_action.triggered.connect(self._export_tmrepair_from_current_plan)

        import_package_action = QAction("Импорт пакета .tmrepair…", self)
        import_package_action.triggered.connect(self._import_tmrepair_package)

        app_help_action = QAction("Руководство по приложению", self)
        app_help_action.triggered.connect(self._show_tm_cleanup_help)

        tools_menu = self.menuBar().addMenu("Инструменты")
        tools_menu.addAction(gemini_settings_action)
        tools_menu.addAction(copy_action)
        tools_menu.addSeparator()
        tools_menu.addAction(export_package_action)
        tools_menu.addAction(import_package_action)

        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction(app_help_action)

    def _build_repair_tab(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        self.files_panel = FilesPanel(include_drop_zone=False)
        self.files_panel.files_dropped.connect(self._on_files_dropped)
        self.files_panel.drop_zone.setMinimumHeight(72)
        root_layout.addWidget(self.files_panel.drop_zone, stretch=0)

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
        apply_surface_shadow(intro_card)
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
        copy_btn = QPushButton("Копировать промпт")
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
        apply_surface_shadow(self.status_panel)
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
            enable_dedup_tus=stage_values.enable_dedup_tus,
            verify_with_gemini=stage_values.verify_with_gemini,
            gemini_api_key=self._gemini_api_key_override,
            gemini_model=self._gemini_model,
            gemini_input_price_per_1m=f"{self._gemini_input_price_per_1m:.2f}",
            gemini_output_price_per_1m=f"{self._gemini_output_price_per_1m:.2f}",
            log_file=self.DEFAULT_LOG_FILE,
            report_dir=self.DEFAULT_REPORT_ROOT,
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
        self.stages_panel.enable_dedup_tus_checkbox.setChecked(state.enable_dedup_tus)
        self.stages_panel.enable_gemini_verification_checkbox.setChecked(state.verify_with_gemini)
        self._gemini_api_key_override = state.gemini_api_key

    def _on_files_dropped(self, paths: list[str]) -> None:
        self._append_log(f"Files dropped: {len(paths)}")

    def _run_repair(self) -> None:
        if self._run_controller.is_running():
            QMessageBox.information(self, "Уже выполняется", "Исправление TMX уже запущено.")
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
                "Files Not Found",
                "These files do not exist:\n" + "\n".join(missing[:10]),
            )
            return

        if not any(
            (
                view_state.enable_split,
                view_state.enable_cleanup_spaces,
                view_state.enable_cleanup_service_markup,
                view_state.enable_cleanup_garbage,
                view_state.enable_cleanup_warnings,
                view_state.enable_dedup_tus,
            )
        ):
            QMessageBox.warning(
                self,
                "No Active Stages",
                "Enable at least one stage: split or any cleanup/diagnostics stage.",
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
                    env_hint = "\nLoaded .env files:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                QMessageBox.warning(
                    self,
                    "Gemini API key is missing",
                    "Set the Gemini API key in settings or GEMINI_API_KEY env variable." + env_hint,
                )
                return
            gemini_prompt_template = self.prompt_editor.toPlainText()
            if self._loaded_env_files:
                self._append_log(
                    "Loaded .env files:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                )
            self._append_log(f"Gemini API key source: {gemini_key_source}")
            self._append_log(
                    "Gemini prompt template loaded from UI editor:\n"
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
            enable_dedup_tus=view_state.enable_dedup_tus,
            log_file=self.DEFAULT_LOG_FILE,
            verify_with_gemini=view_state.verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_max_parallel=self._gemini_max_parallel,
            max_gemini_checks=self._gemini_max_checks,
            gemini_input_price_per_1m=gemini_input_price_per_1m,
            gemini_output_price_per_1m=gemini_output_price_per_1m,
            gemini_prompt_template=gemini_prompt_template,
            report_dir=report_dir,
            xlsx_report_dir=self.DEFAULT_REPORT_ROOT,
        )
        self._last_run_config = config
        self._latest_plan_phase = None
        self._live_tokens_in = 0
        self._live_tokens_out = 0
        self._live_tokens_total = 0
        self._live_cost = 0.0
        self._live_rate_tokens_per_sec = 0.0
        self._live_rate_avg_tokens_per_sec = 0.0
        self._current_file_cost_forecast = 0.0
        self._run_started_at = time.monotonic()
        self._run_finished_at = 0.0
        self._last_rate_tick_at = self._run_started_at
        self._last_rate_total_tokens = 0
        self._elapsed_timer.start()
        self._tick_elapsed_timer()
        self._render_live_usage()
        self._render_live_rate()
        self._switch_page(2)
        self._sync_transport_buttons()
        self._set_runtime_status(f"running ({len(input_paths)} files)...")
        self._set_runtime_progress("initializing")
        self._append_log(f"Batch run started: files={len(input_paths)}")
        self._append_log(
            "Settings: "
            f"verify_gemini={config.verify_with_gemini}, "
            f"split={config.enable_split}, split_short_pair_guard={config.enable_split_short_sentence_pair_guard}, "
            f"cleanup_spaces={config.enable_cleanup_spaces}, "
            f"cleanup_service_markup={config.enable_cleanup_service_markup}, "
            f"cleanup_garbage={config.enable_cleanup_garbage}, "
            f"cleanup_warnings={config.enable_cleanup_warnings}, "
            f"dedup_tus={config.enable_dedup_tus}, "
                f"model={config.gemini_model}, input_price={config.gemini_input_price_per_1m}, "
                f"output_price={config.gemini_output_price_per_1m}, gemini_max_parallel={config.gemini_max_parallel}, "
                f"max_gemini_checks={config.max_gemini_checks if config.max_gemini_checks is not None else 'unlimited'}, "
                f"output_dir={config.output_dir or '<same as input>'}, "
            f"xlsx_reports={config.xlsx_report_dir or 'tmx-reports/<file>'}, "
            f"json_reports={config.report_dir or 'tmx-reports/<file>' if config.verify_with_gemini else 'disabled'}"
        )

        self._run_controller.start_run(config)
        self._sync_transport_buttons()

    def _current_package_settings(self) -> dict[str, object]:
        view_state = self._read_view_state()
        return {
            "enable_split": view_state.enable_split,
            "enable_split_short_sentence_pair_guard": view_state.enable_split_short_sentence_pair_guard,
            "enable_cleanup_spaces": view_state.enable_cleanup_spaces,
            "enable_cleanup_service_markup": view_state.enable_cleanup_service_markup,
            "enable_cleanup_garbage": view_state.enable_cleanup_garbage,
            "enable_cleanup_warnings": view_state.enable_cleanup_warnings,
            "enable_dedup_tus": view_state.enable_dedup_tus,
            "verify_with_gemini": view_state.verify_with_gemini,
        }

    def _export_tmrepair_from_current_plan(self) -> None:
        plans = self._latest_plan_phase
        if plans is None or not plans.files:
            QMessageBox.information(
                self,
                "No Plan Data",
                "Run analysis first to generate a plan before exporting a package.",
            )
            return
        self._export_tmrepair_from_plans(plans)

    def _export_tmrepair_from_plans(self, plans: PlanPhaseResult) -> None:
        if not plans.files:
            QMessageBox.information(self, "Export Package", "No files available for export.")
            return

        settings = self._current_package_settings()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exported_paths: list[Path] = []

        if len(plans.files) == 1:
            file_item = plans.files[0]
            default_name = f"{file_item.input_path.stem}_{timestamp}.tmrepair"
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Export .tmrepair Package",
                str(file_item.input_path.parent / default_name),
                "TMRepair Package (*.tmrepair)",
            )
            if not selected:
                return
            target_path = Path(selected)
            try:
                export_tmrepair_package(
                    package_path=target_path,
                    input_tmx_path=file_item.input_path,
                    plan=file_item.plan,
                    settings=settings,
                )
                exported_paths.append(target_path)
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка экспорта", str(exc))
                return
        else:
            target_dir = QFileDialog.getExistingDirectory(
                self,
                "Choose folder for exported .tmrepair packages",
                str(plans.files[0].input_path.parent),
            )
            if not target_dir:
                return
            target_dir_path = Path(target_dir)
            for file_item in plans.files:
                package_name = f"{file_item.input_path.stem}_{timestamp}.tmrepair"
                target_path = target_dir_path / package_name
                export_tmrepair_package(
                    package_path=target_path,
                    input_tmx_path=file_item.input_path,
                    plan=file_item.plan,
                    settings=settings,
                )
                exported_paths.append(target_path)

        if not exported_paths:
            return
        self._append_log(f"Exported package(s): {', '.join(str(path) for path in exported_paths)}")
        QMessageBox.information(
            self,
            "Export Completed",
            "\n".join(str(path) for path in exported_paths),
        )

    def _import_tmrepair_package(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Import .tmrepair Package",
            "",
            "TMRepair Package (*.tmrepair)",
        )
        if not selected:
            return

        package_path = Path(selected)
        try:
            result = import_tmrepair_package(package_path=package_path)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return

        source_temp_path = result.source_tmx_path
        source_name = str(result.manifest.get("source_file_name", "source.tmx")).strip() or "source.tmx"
        output_path = package_path.parent / f"{Path(source_name).stem}.repaired.tmx"
        settings = result.manifest.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}

        if result.hash_mismatch_warning:
            QMessageBox.warning(self, "TMX Hash Warning", result.hash_mismatch_warning)

        summary = (
            f"accepted={result.accepted_count}, "
            f"rejected={result.rejected_count}, "
            f"skipped={result.skipped_count}, "
            f"unrecognized={result.unrecognized_count}"
        )
        proceed = QMessageBox.question(
            self,
            "Apply Imported Decisions",
            (
                f"Import summary: {summary}\n\n"
                f"Apply accepted decisions to TMX now?\nOutput: {output_path}"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if proceed != QMessageBox.StandardButton.Yes:
            source_temp_path.unlink(missing_ok=True)
            self._append_log(f"Imported package without apply: {package_path} | {summary}")
            return

        try:
            repair_tmx_file(
                input_path=source_temp_path,
                output_path=output_path,
                dry_run=False,
                mode="apply",
                verify_with_gemini=False,
                accepted_split_ids=result.plan.accepted_split_ids(),
                accepted_cleanup_ids=result.plan.accepted_cleanup_ids(),
                enable_split=bool(settings.get("enable_split", True)),
                enable_split_short_sentence_pair_guard=bool(
                    settings.get("enable_split_short_sentence_pair_guard", True)
                ),
                enable_cleanup_spaces=bool(settings.get("enable_cleanup_spaces", True)),
                enable_cleanup_percent_wrapped=bool(settings.get("enable_cleanup_service_markup", True)),
                enable_cleanup_game_markup=bool(settings.get("enable_cleanup_service_markup", True)),
                enable_cleanup_tag_removal=bool(settings.get("enable_cleanup_service_markup", True)),
                enable_cleanup_garbage_removal=bool(settings.get("enable_cleanup_garbage", True)),
                enable_cleanup_warnings=bool(settings.get("enable_cleanup_warnings", True)),
                enable_dedup_tus=bool(settings.get("enable_dedup_tus", False)),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка применения", str(exc))
            source_temp_path.unlink(missing_ok=True)
            return
        source_temp_path.unlink(missing_ok=True)

        self._append_log(f"Imported package: {package_path} | {summary} | output={output_path}")
        QMessageBox.information(
            self,
            "Import Completed",
            f"{summary}\nSaved repaired TMX:\n{output_path}",
        )

    def _on_plans_ready(self, plans: object) -> None:
        """Plan phase finished - show review dialog, then launch apply phase."""
        if not isinstance(plans, PlanPhaseResult):
            self._on_worker_failed("Internal error: plan worker returned invalid payload.")
            return
        self._latest_plan_phase = plans
        total_proposals = sum(len(f.plan.proposals) for f in plans.files)
        self._append_log(
            f"Plan done: files={len(plans.files)}, proposals={total_proposals}. Opening review window."
        )

        dialog = ReviewDialog(plans, parent=self)
        dialog._export_callback = self._export_tmrepair_from_plans
        if dialog.exec() != dialog.DialogCode.Accepted:
            accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
            self._append_log(f"Cancelled by user. Accepted before cancel: {accepted}.")
            self._set_runtime_progress("cancelled")
            self._set_runtime_status("cancelled")
            self._run_finished_at = time.monotonic()
            self._elapsed_timer.stop()
            self._tick_elapsed_timer()
            self._append_completion_log(status="cancelled")
            return

        accepted = sum(1 for f in plans.files for p in f.plan.proposals if p.accepted)
        rejected = total_proposals - accepted
        self._append_log(
            f"Review accepted: accepted={accepted}, rejected={rejected}. Starting apply phase."
        )

        self._run_controller.start_apply(plans)

    def _on_worker_completed(self, batch: object) -> None:
        if not isinstance(batch, BatchRunResult):
            self._on_worker_failed("Internal error: apply worker returned invalid payload.")
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
        self._set_runtime_progress("done")
        self._run_finished_at = time.monotonic()
        self._elapsed_timer.stop()
        self._tick_elapsed_timer()
        self._append_completion_log(status="done", batch=batch)
        self._show_completion_dialog(batch)

    def _on_worker_failed(self, error_text: str) -> None:
        stopped_by_user = error_text == "STOPPED_BY_USER"
        self._append_log(f"Error: {error_text}")
        self._set_runtime_status("stopped" if stopped_by_user else "error")
        self._set_runtime_progress("stopped" if stopped_by_user else "error")
        self._run_finished_at = time.monotonic()
        self._elapsed_timer.stop()
        self._tick_elapsed_timer()
        self._append_completion_log(status="stopped" if stopped_by_user else "failed")
        if not stopped_by_user:
            QMessageBox.critical(self, "Ошибка исправления", error_text)

    def _on_worker_finished(self) -> None:
        self._sync_transport_buttons()

    def _append_completion_log(self, status: str, batch: BatchRunResult | None = None) -> None:
        if self._run_started_at > 0:
            elapsed_seconds = max(0, int((self._run_finished_at or time.monotonic()) - self._run_started_at))
            elapsed_text = self._format_elapsed(elapsed_seconds)
        else:
            elapsed_text = "00:00"
        self._append_log(f"Process {status}. Time: {elapsed_text}.")
        if batch is None:
            return
        self._append_log("Edits per file:")
        for file_result in batch.files:
            edits_count = int(file_result.stats.split_tus + file_result.stats.auto_actions)
            self._append_log(
                f"{file_result.input_path.name}: edits={edits_count} "
                f"(split={file_result.stats.split_tus}, cleanup={file_result.stats.auto_actions})"
            )

    def _show_completion_dialog(self, batch: BatchRunResult) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Done")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        root_layout = QVBoxLayout(dialog)
        root_layout.setContentsMargins(20, 18, 20, 18)
        root_layout.setSpacing(14)

        title_label = QLabel("Обработка завершена")
        title_label.setObjectName("CanvasTitleLabel")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(title_label)

        summary_label = QLabel(
            (
                f"Обработано файлов: {len(batch.files)}\n"
                f"Разделено сегментов: {batch.split_tu}\n"
                f"Правок очистки: {sum(item.stats.auto_actions for item in batch.files)}\n"
                f"{self.status_panel.elapsed_text()}"
            )
        )
        summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        summary_label.setWordWrap(True)
        root_layout.addWidget(summary_label)

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)
        buttons_layout.addStretch(1)

        open_files_btn = QPushButton("Открыть папку файлов")
        open_reports_btn = QPushButton("Открыть папку отчётов")
        close_btn = QPushButton("Закрыть")

        for btn in (open_files_btn, open_reports_btn, close_btn):
            btn.setMinimumWidth(160)
            btn.setFixedHeight(36)
            buttons_layout.addWidget(btn)

        buttons_layout.addStretch(1)
        root_layout.addLayout(buttons_layout)

        open_files_btn.clicked.connect(lambda: self._open_output_folder(batch))
        open_reports_btn.clicked.connect(lambda: self._open_reports_folder(batch))
        close_btn.clicked.connect(dialog.accept)

        dialog.exec()

    @staticmethod
    def _resolve_common_dir(paths: list[Path]) -> Path:
        if not paths:
            return Path(".")
        try:
            common_dir = Path(os.path.commonpath([str(path) for path in paths]))
        except ValueError:
            common_dir = paths[0]
        return common_dir if common_dir.exists() else paths[0]

    def _open_output_folder(self, batch: BatchRunResult) -> None:
        if not batch.files:
            return
        output_dirs = [file_result.output_path.parent for file_result in batch.files]
        folder = self._resolve_common_dir(output_dirs)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        if not opened:
            QMessageBox.warning(self, "Не удалось открыть папку", f"Папка: {folder}")

    def _open_reports_folder(self, batch: BatchRunResult) -> None:
        if not batch.files:
            return
        report_dirs = [file_result.xlsx_report_path.parent for file_result in batch.files]
        folder = self._resolve_common_dir(report_dirs)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        if not opened:
            QMessageBox.warning(self, "Не удалось открыть папку", f"Папка: {folder}")

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
            self._set_runtime_progress(f"file {file_index}/{file_total} ({short_name})")
        elif event == "file_complete" and file_index > 0 and file_total > 0:
            self._set_runtime_progress(f"completed {file_index}/{file_total} files")

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

    def _tick_elapsed_timer(self) -> None:
        if self._run_started_at <= 0:
            self.status_panel.set_elapsed("00:00")
            return
        end_point = self._run_finished_at if self._run_finished_at > 0 else time.monotonic()
        elapsed = max(0, int(end_point - self._run_started_at))
        self.status_panel.set_elapsed(self._format_elapsed(elapsed))

    @staticmethod
    def _format_elapsed(total_seconds: int) -> str:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

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
            available_models=self._gemini_available_models,
            models_loader=self._load_gemini_models,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._gemini_api_key_override = dialog.api_key()
        self._persist_gemini_api_key(self._gemini_api_key_override)
        selected_model = dialog.model()
        if selected_model and selected_model != self._gemini_model:
            self._gemini_model = selected_model
            self._persist_gemini_model(selected_model)
        if selected_model and selected_model not in self._gemini_available_models:
            self._gemini_available_models.insert(0, selected_model)
        key_source = "Gemini settings" if self._gemini_api_key_override else "GEMINI_API_KEY env"
        self._append_log(f"Gemini settings updated: model={self._gemini_model}, key_source={key_source}")

    def _load_gemini_models(self, api_key: str) -> list[str]:
        """Fetch eligible Gemini models, falling back to the env key, and cache them."""
        key = api_key.strip() or os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise ValueError("Укажите API-ключ Gemini здесь или в GEMINI_API_KEY (.env).")
        models = list_gemini_models(key)
        if models:
            self._gemini_available_models = list(models)
        return models

    def _read_persisted_gemini_model(self) -> str:
        try:
            value = self._create_qsettings().value(self.SETTINGS_GEMINI_MODEL_KEY)
        except Exception:
            return ""
        return str(value).strip() if value else ""

    def _persist_gemini_model(self, model: str) -> None:
        settings = self._create_qsettings()
        settings.setValue(self.SETTINGS_GEMINI_MODEL_KEY, model)
        settings.sync()

    def _read_persisted_gemini_api_key(self) -> str:
        try:
            value = self._create_qsettings().value(self.SETTINGS_GEMINI_API_KEY_KEY)
        except Exception:
            return ""
        return str(value).strip() if value else ""

    def _persist_gemini_api_key(self, api_key: str) -> None:
        settings = self._create_qsettings()
        key = (api_key or "").strip()
        if key:
            settings.setValue(self.SETTINGS_GEMINI_API_KEY_KEY, key)
        else:
            settings.remove(self.SETTINGS_GEMINI_API_KEY_KEY)
        settings.sync()

    def _refresh_prompt(self) -> None:
        self.prompt_editor.setPlainText(self._render_prompt())

    def _copy_prompt(self) -> None:
        QApplication.clipboard().setText(self.prompt_editor.toPlainText())
        self._append_log("Gemini prompt copied to clipboard.")

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

    @staticmethod
    def _read_env_optional_int(env_name: str, default: int) -> int | None:
        raw = os.getenv(env_name, "").strip()
        if not raw:
            return max(1, int(default))
        if raw.lower() in {"none", "null", "unlimited", "inf", "infinite"}:
            return None
        try:
            value = int(raw)
        except ValueError:
            return max(1, int(default))
        if value <= 0:
            return None
        return value

    def _show_service_markup_hint(self) -> None:
        hint_text = (
            "The 'Remove service markup' stage combines three cleanups:\n\n"
            "1. Remove inline tags inside seg (bpt/ept/ph/...)\n"
            "2. Remove game markup patterns like ^{...}^, $m(...|...), and <Color=...>...</Color>\n"
            "3. Remove safe tokens like %Name% and %Name%%\n\n"
            "After cleanup, spacing is normalized to avoid merged words.\n"
            "Only regular ASCII spaces are normalized (NBSP/newlines are preserved)."
        )
        QMessageBox.information(self, "Hint: Service Markup", hint_text)

    def _show_tm_cleanup_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Руководство по приложению")
        dialog.setModal(True)
        dialog.resize(980, 720)
        dialog.setMinimumSize(860, 620)

        root_layout = QVBoxLayout(dialog)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        intro_label = QLabel(
            "Что умеет приложение, как устроены вкладки и как подключить Gemini."
        )
        intro_label.setWordWrap(True)
        root_layout.addWidget(intro_label)

        help_view = QTextBrowser(dialog)
        help_view.setOpenExternalLinks(True)
        help_view.setHtml(self._help_html())
        root_layout.addWidget(help_view, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        root_layout.addWidget(buttons)

        dialog.exec()

    @staticmethod
    def _help_html() -> str:
        return """
        <h1>Что это за приложение</h1>
        <p>Набор инструментов для работы с переводческими памятями (TMX) и
        двуязычными таблицами. Приложение умеет чинить и чистить TMX, разбивать
        сегменты по предложениям, конвертировать форматы и собирать TMX из Excel.
        Слева — панель навигации по вкладкам.</p>

        <h1>Вкладки</h1>

        <h2>🛠 Исправление и очистка TMX</h2>
        <p>Главная вкладка. Перетащите один или несколько <code>.tmx</code>-файлов
        (или добавьте кнопкой), включите нужные этапы и нажмите
        <b>«Погнали»</b>. Рядом с исходным файлом появится
        <code>имя_repaired.tmx</code>, исходник не меняется. Доступные этапы:</p>
        <ul>
          <li><b>Разбивка по предложениям</b> — одна единица перевода (TU)
              разбивается на несколько, если исходник и перевод выровнены по
              предложениям.</li>
          <li><b>Нормализация пробелов</b> — схлопывает повторяющиеся обычные
              пробелы и обрезает пробелы по краям. Не трогает неразрывные
              пробелы, табы и переносы строк.</li>
          <li><b>Очистка служебной разметки</b> — убирает inline-теги внутри
              <code>&lt;seg&gt;</code>, игровую разметку и безопасные
              <code>%токены%</code>. Обычные проценты (например, <code>100%</code>)
              сохраняются.</li>
          <li><b>Очистка «мусорных» TU</b> — удаляет малополезные или битые
              единицы перевода.</li>
          <li><b>Предупреждения</b> — помечает подозрительные пары (разная длина,
              разный алфавит, одинаковые source/target), но ничего не удаляет
              автоматически.</li>
          <li><b>Дедупликация</b> — убирает полные дубликаты пар.</li>
          <li><b>Проверка Gemini</b> (опционально) — ИИ перепроверяет решения о
              разбивке и выставляет уверенность. См. раздел про Gemini ниже.</li>
        </ul>
        <p>Перед записью результата открывается окно <b>ревью</b>, где можно
        просмотреть и принять/отклонить предложенные правки. Выбор фильтра по
        типам правок запоминается между запусками.</p>

        <h2>🔁 Конвертация TMX в CSV / XLSX / split-TMX</h2>
        <p>Перетащите TMX-файлы. Для <i>каждого</i> целевого языка в файле
        создаётся отдельный файл из двух колонок: исходный язык и один целевой.
        Форматы выбираются галочками (CSV, XLSX, TMX). Исходный язык берётся из
        заголовка TMX (<code>srclang</code>). Пустые и несопоставленные строки
        пропускаются. Папка вывода задаётся вверху карточки.</p>

        <h2>🧹 Очистка двухколоночных CSV/XLSX</h2>
        <p>Для уже выгруженных парных таблиц (две колонки: source/target).
        Кнопка <b>«Предпросмотр»</b> покажет, что изменится (только изменённые,
        удалённые и спорные строки), <b>«Очистить файлы»</b> запишет
        <code>__cleaned</code>-копии. Правила включаются галочками: удаление
        пустого target, обрезка краёв, нормализация пробелов и пунктуации,
        кавычки, тире, финальная пунктуация, дедупликация. Плейсхолдеры и теги
        (<code>{ph}</code>, <code>{bpt}</code>, <code>{{…}}</code>) сохраняются;
        если состав защищённых токенов меняется — строка не правится молча.</p>

        <h2>📄 Excel → TMX — сборка TMX из Excel</h2>
        <p>Перетащите <code>.xlsx</code>. Задайте коды языков (source/target),
        номера колонок source/target/comment и отметьте, есть ли строка
        заголовка. TMX сохраняется рядом с исходным Excel-файлом.</p>

        <h2>✨ Промпт проверки Gemini</h2>
        <p>Показывает текст промпта, по которому Gemini проверяет разбивки. Можно
        отредактировать для разовой проверки, скопировать или сбросить к
        исходному.</p>

        <h2>📋 Журнал и управление</h2>
        <p>Полный лог обработки. Здесь же кнопки управления долгим прогоном:
        пауза, продолжение и остановка.</p>

        <h1>Gemini API — проверка через ИИ</h1>
        <p>Gemini — это необязательный «второй контролёр»: он перепроверяет
        спорные разбивки сегментов и выставляет уверенность. Без ключа
        приложение работает полностью на правилах, ИИ-проверка просто выключена.</p>
        <h3>Как подключить</h3>
        <ol>
          <li>Получите API-ключ в
              <a href="https://aistudio.google.com/apikey">Google AI Studio</a>.</li>
          <li>Меню <b>Инструменты → Настройки Gemini…</b>, вставьте ключ.</li>
          <li>Нажмите <b>«Загрузить модели»</b> и выберите модель из списка.</li>
          <li>На вкладке «Исправление» включите этап <b>«Проверка Gemini»</b> и
              запустите обработку.</li>
        </ol>
        <h3>Выбор модели</h3>
        <p>В списке показываются только текстовые модели <b>Gemini 3 и новее</b>.
        Модели для изображений (в т.ч. «nano banana»), видео, аудio/TTS и
        эмбеддингов скрыты — они для этой задачи не подходят. Выбранная модель
        запоминается между запусками.</p>
        <h3>Где хранятся ключ и модель</h3>
        <p>В пользовательском файле настроек
        <code>%APPDATA%\\tmragger\\tmragger-gui.ini</code>. Он сохраняется между
        запусками и переживает сборку в один <code>.exe</code>. Файл настроек не
        входит в сборку, поэтому ключ не переносится вместе с <code>.exe</code>
        другому пользователю. Ключ хранится в
        открытом виде — не передавайте этот файл другим. Очистка поля API-ключа
        в настройках удаляет сохранённый ключ из файла. Альтернатива: задать ключ
        через переменную окружения <code>GEMINI_API_KEY</code> (в <code>.env</code>),
        тогда поле в настройках можно оставить пустым.</p>
        <h3>Стоимость и скорость</h3>
        <p>Запросы к Gemini платные и идут по сети — это медленнее, чем чистые
        правила. Число проверок ограничено настройками, а ответы кешируются, чтобы
        не платить повторно за одинаковые проверки.</p>

        <h1>Отчёты и пакеты</h1>
        <p>При проверке Gemini рядом формируются отчёты (JSON и многолистовой
        XLSX) с поменными изменениями и итогами по этапам. Через меню
        <b>Инструменты</b> можно экспортировать/импортировать пакет
        <code>.tmrepair</code> для передачи результата ревью.</p>
        """
    def _append_log(self, message: str) -> None:
        self.status_panel.append_log(message)

    def _create_qsettings(self) -> QSettings:
        # Shared INI file under %APPDATA%\<APP>\<APP>-gui.ini (Roaming). Survives a
        # PyInstaller one-file build (temp dir wiped on exit) and needs no admin
        # rights (unlike ProgramData). See ui/app_settings.create_app_settings.
        return create_app_settings()

    def _restore_window_persistence(self) -> None:
        settings = self._create_qsettings()
        geometry = settings.value(self.SETTINGS_WINDOW_GEOMETRY_KEY)
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

        window_state = settings.value(self.SETTINGS_WINDOW_STATE_KEY)
        if isinstance(window_state, QByteArray) and not window_state.isEmpty():
            self.restoreState(window_state)

    def _save_window_persistence(self) -> None:
        settings = self._create_qsettings()
        settings.setValue(self.SETTINGS_WINDOW_GEOMETRY_KEY, self.saveGeometry())
        settings.setValue(self.SETTINGS_WINDOW_STATE_KEY, self.saveState())
        settings.sync()

    def closeEvent(self, event: QCloseEvent) -> None:
        busy_converter = any(
            tab.is_busy()
            for tab in (self.convert_tab, self.clean_tab, self.excel_tmx_tab)
        )
        if busy_converter:
            QMessageBox.warning(
                self,
                "Задача выполняется",
                "Дождитесь завершения конвертации или очистки.",
            )
            event.ignore()
            return
        self._save_window_persistence()
        super().closeEvent(event)





