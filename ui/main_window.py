"""PySide6 desktop app for TMX repair."""

from __future__ import annotations

import os
from pathlib import Path
import time

from app_meta import APP_ICON_SVG_PATH, APP_NAME, APP_VERSION
from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon
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
    DEFAULT_GEMINI_MAX_PARALLEL = 4
    DEFAULT_LOG_FILE = "tmx-repair.log"
    DEFAULT_REPORT_ROOT = Path("tmx-reports")
    GEMINI_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "gemini-color.svg"
    LOG_ICON_PATH = Path(__file__).resolve().parents[1] / "asset" / "log.ico"
    SETTINGS_ORG = APP_NAME
    SETTINGS_APP = f"{APP_NAME}-gui"
    SETTINGS_WINDOW_GEOMETRY_KEY = "window/geometry"
    SETTINGS_WINDOW_STATE_KEY = "window/state"

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
        self._gemini_max_parallel = max(
            1,
            int(os.getenv("GEMINI_MAX_PARALLEL", str(self.DEFAULT_GEMINI_MAX_PARALLEL)).strip() or "1"),
        )
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        if APP_ICON_SVG_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_SVG_PATH)))
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
        self._run_finished_at = 0.0
        self._last_rate_tick_at = 0.0
        self._last_rate_total_tokens = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed_timer)
        self._shell_status_text = "РѕР¶РёРґР°РЅРёРµ"
        self._shell_progress_text = "РіРѕС‚РѕРІРѕ"

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

        self.run_btn = QPushButton("")
        self.run_btn.setToolTip("Start")
        self.run_btn.setAccessibleName("Start")
        self.run_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_btn.setIconSize(QSize(22, 22))
        self.run_btn.clicked.connect(self._run_repair)
        self.run_btn.setFixedSize(44, 36)
        top_bar_layout.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.pause_btn = QPushButton("")
        self.pause_btn.setToolTip("Pause")
        self.pause_btn.setAccessibleName("Pause")
        self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_btn.setIconSize(QSize(22, 22))
        self.pause_btn.clicked.connect(self._pause_repair)
        self.pause_btn.setFixedSize(44, 36)
        top_bar_layout.addWidget(self.pause_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.resume_btn = QPushButton("")
        self.resume_btn.setToolTip("Resume")
        self.resume_btn.setAccessibleName("Resume")
        self.resume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.resume_btn.setIconSize(QSize(22, 22))
        self.resume_btn.clicked.connect(self._resume_repair)
        self.resume_btn.setFixedSize(44, 36)
        top_bar_layout.addWidget(self.resume_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.stop_btn = QPushButton("")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setAccessibleName("Stop")
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setIconSize(QSize(22, 22))
        self.stop_btn.clicked.connect(self._stop_repair)
        self.stop_btn.setFixedSize(44, 36)
        top_bar_layout.addWidget(self.stop_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._sync_transport_buttons()

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
        self.run_btn.setVisible(index == 0)

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
            self._set_runtime_status("РїР°СѓР·Р°")
            self._set_runtime_progress("РЅР° РїР°СѓР·Рµ")
            self._sync_transport_buttons()

    def _resume_repair(self) -> None:
        if self._run_controller.resume():
            self._set_runtime_status("РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ")
            self._set_runtime_progress("РІРѕР·РѕР±РЅРѕРІР»РµРЅРѕ")
            self._sync_transport_buttons()

    def _stop_repair(self) -> None:
        if self._run_controller.stop():
            self._set_runtime_status("РѕСЃС‚Р°РЅРѕРІРєР°...")
            self._set_runtime_progress("РѕСЃС‚Р°РЅРѕРІРєР°...")
            self._append_log("Р—Р°РїСЂРѕС€РµРЅР° РѕСЃС‚Р°РЅРѕРІРєР° РїСЂРѕС†РµСЃСЃР°.")
            self._sync_transport_buttons()

    def _build_menu(self) -> None:
        gemini_settings_action = QAction("РќР°СЃС‚СЂРѕР№РєРё Gemini", self)
        gemini_settings_action.triggered.connect(self._open_gemini_settings_dialog)

        copy_action = QAction("РЎРєРѕРїРёСЂРѕРІР°С‚СЊ РїСЂРѕРјРїС‚ Gemini", self)
        copy_action.triggered.connect(self._copy_prompt)

        cleanup_help_action = QAction("РљР°Рє СЂР°Р±РѕС‚Р°РµС‚ РѕС‡РёСЃС‚РєР° РўРњ", self)
        cleanup_help_action.triggered.connect(self._show_tm_cleanup_help)

        tools_menu = self.menuBar().addMenu("РРЅСЃС‚СЂСѓРјРµРЅС‚С‹")
        tools_menu.addAction(gemini_settings_action)
        tools_menu.addAction(copy_action)

        help_menu = self.menuBar().addMenu("РЎРїСЂР°РІРєР°")
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
        refresh_btn = QPushButton("РЎР±СЂРѕСЃРёС‚СЊ РїСЂРѕРјРїС‚")
        refresh_btn.clicked.connect(self._refresh_prompt)
        copy_btn = QPushButton("РЎРєРѕРїРёСЂРѕРІР°С‚СЊ РїСЂРѕРјРїС‚")
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
            QMessageBox.information(self, "Р’С‹РїРѕР»РЅСЏРµС‚СЃСЏ", "РџСЂР°РІРєР° СѓР¶Рµ Р·Р°РїСѓС‰РµРЅР°.")
            return

        view_state = self._read_view_state()
        input_paths = view_state.input_paths
        if not input_paths:
            QMessageBox.warning(self, "РќРµС‚ РІС…РѕРґРЅС‹С… С„Р°Р№Р»РѕРІ", "Р”РѕР±Р°РІСЊС‚Рµ С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ TMX-С„Р°Р№Р».")
            return
        missing = [str(p) for p in input_paths if not p.exists()]
        if missing:
            QMessageBox.warning(
                self,
                "Р¤Р°Р№Р»С‹ РЅРµ РЅР°Р№РґРµРЅС‹",
                "Р­С‚Рё С„Р°Р№Р»С‹ РЅРµ СЃСѓС‰РµСЃС‚РІСѓСЋС‚:\n" + "\n".join(missing[:10]),
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
                "РќРµС‚ Р°РєС‚РёРІРЅС‹С… СЌС‚Р°РїРѕРІ",
                "Р’РєР»СЋС‡РёС‚Рµ С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ СЌС‚Р°Рї: СЃРїР»РёС‚ РёР»Рё Р»СЋР±СѓСЋ РѕС‡РёСЃС‚РєСѓ/РґРёР°РіРЅРѕСЃС‚РёРєСѓ.",
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
                    env_hint = "\nР—Р°РіСЂСѓР¶РµРЅРЅС‹Рµ .env:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                QMessageBox.warning(
                    self,
                    "API-РєР»СЋС‡ Gemini РЅРµ Р·Р°РґР°РЅ",
                    "РЈРєР°Р¶РёС‚Рµ API-РєР»СЋС‡ Gemini РІ РїРѕР»Рµ РёР»Рё РїРµСЂРµРјРµРЅРЅРѕР№ РѕРєСЂСѓР¶РµРЅРёСЏ GEMINI_API_KEY." + env_hint,
                )
                return
            gemini_prompt_template = self.prompt_editor.toPlainText()
            if self._loaded_env_files:
                self._append_log(
                    "Р—Р°РіСЂСѓР¶РµРЅРЅС‹Рµ .env:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                )
            self._append_log(f"РСЃС‚РѕС‡РЅРёРє API-РєР»СЋС‡Р° Gemini: {gemini_key_source}")
            self._append_log(
                    "РЁР°Р±Р»РѕРЅ РїСЂРѕРјРїС‚Р° Gemini РІР·СЏС‚ РёР· СЂРµРґР°РєС‚РѕСЂР° UI:\n"
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
            gemini_max_parallel=self._gemini_max_parallel,
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
        self._run_finished_at = 0.0
        self._last_rate_tick_at = self._run_started_at
        self._last_rate_total_tokens = 0
        self._elapsed_timer.start()
        self._tick_elapsed_timer()
        self._render_live_usage()
        self._render_live_rate()
        self._switch_page(2)
        self._sync_transport_buttons()
        self._set_runtime_status(f"РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ ({len(input_paths)} С„Р°Р№Р»РѕРІ)...")
        self._set_runtime_progress("РёРЅРёС†РёР°Р»РёР·Р°С†РёСЏ")
        self._append_log(f"РЎС‚Р°СЂС‚ РїР°РєРµС‚РЅРѕР№ РїСЂР°РІРєРё: С„Р°Р№Р»РѕРІ={len(input_paths)}")
        self._append_log(
            "РќР°СЃС‚СЂРѕР№РєРё: "
            f"verify_gemini={config.verify_with_gemini}, "
            f"split={config.enable_split}, split_short_pair_guard={config.enable_split_short_sentence_pair_guard}, "
            f"cleanup_spaces={config.enable_cleanup_spaces}, "
            f"cleanup_service_markup={config.enable_cleanup_service_markup}, "
            f"cleanup_garbage={config.enable_cleanup_garbage}, "
            f"cleanup_warnings={config.enable_cleanup_warnings}, "
            f"model={config.gemini_model}, input_price={config.gemini_input_price_per_1m}, "
            f"output_price={config.gemini_output_price_per_1m}, gemini_max_parallel={config.gemini_max_parallel}, "
            f"output_dir={config.output_dir or '<same as input>'}, "
            f"html_reports={config.html_report_dir or 'tmx-reports/<file>'}, "
            f"xlsx_reports={config.xlsx_report_dir or 'tmx-reports/<file>'}, "
            f"json_reports={config.report_dir or 'tmx-reports/<file>' if config.verify_with_gemini else 'disabled'}"
        )

        self._run_controller.start_run(config)
        self._sync_transport_buttons()

    def _on_plans_ready(self, plans: object) -> None:
        """Plan phase finished - show review dialog, then launch apply phase."""
        if not isinstance(plans, PlanPhaseResult):
            self._on_worker_failed("Internal error: plan worker returned invalid payload.")
            return
        total_proposals = sum(len(f.plan.proposals) for f in plans.files)
        self._append_log(
            f"Plan done: files={len(plans.files)}, proposals={total_proposals}. Opening review window."
        )

        dialog = ReviewDialog(plans, parent=self)
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

        done_message = "Batch repair completed."
        if batch.files:
            first = batch.files[0]
            done_message = f"{done_message}\nSample HTML report:\n{first.html_report_path}"
            done_message = f"{done_message}\nSample XLSX report:\n{first.xlsx_report_path}"
            if first.report_path is not None:
                done_message = f"{done_message}\nSample JSON report:\n{first.report_path}"
        QMessageBox.information(self, "Done", done_message)

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
            QMessageBox.critical(self, "Repair failed", error_text)

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
            self._set_runtime_progress(f"С„Р°Р№Р» {file_index}/{file_total} ({short_name})")
        elif event == "file_complete" and file_index > 0 and file_total > 0:
            self._set_runtime_progress(f"Р·Р°РІРµСЂС€РµРЅРѕ {file_index}/{file_total} С„Р°Р№Р»РѕРІ")

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
        self._append_log("РџСЂРѕРјРїС‚ Gemini СЃРєРѕРїРёСЂРѕРІР°РЅ РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°.")

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
            "РџСЂР°РІРёР»Рѕ В«РЈРґР°Р»РµРЅРёРµ СЃР»СѓР¶РµР±РЅРѕР№ СЂР°Р·РјРµС‚РєРёВ» РѕР±СЉРµРґРёРЅСЏРµС‚ С‚СЂРё РѕС‡РёСЃС‚РєРё:\n\n"
            "1. РЈРґР°Р»РµРЅРёРµ inline-С‚РµРіРѕРІ РІРЅСѓС‚СЂРё seg (bpt/ept/ph/...)\n"
            "2. РЈРґР°Р»РµРЅРёРµ РёРіСЂРѕРІРѕРіРѕ markup: ^{...}^, $m(...|...), &lt;Color=...&gt;...&lt;/Color&gt;\n"
            "3. РЈРґР°Р»РµРЅРёРµ Р±РµР·РѕРїР°СЃРЅС‹С… С‚РѕРєРµРЅРѕРІ РІРёРґР° %Name% Рё %Name%%\n\n"
            "РџРѕСЃР»Рµ СѓРґР°Р»РµРЅРёСЏ РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ Р°РєРєСѓСЂР°С‚РЅР°СЏ СЃРєР»РµР№РєР° С‚РµРєСЃС‚Р°, С‡С‚РѕР±С‹ РЅРµ Р±С‹Р»Рѕ СЃР»РёРїС€РёС…СЃСЏ СЃР»РѕРІ.\n"
            "РќРѕСЂРјР°Р»РёР·СѓСЋС‚СЃСЏ С‚РѕР»СЊРєРѕ РѕР±С‹С‡РЅС‹Рµ РїСЂРѕР±РµР»С‹ ASCII (NBSP/РїРµСЂРµРЅРѕСЃС‹ РЅРµ РёР·РјРµРЅСЏСЋС‚СЃСЏ)."
        )
        QMessageBox.information(self, "РџРѕРґСЃРєР°Р·РєР°: СЃР»СѓР¶РµР±РЅР°СЏ СЂР°Р·РјРµС‚РєР°", hint_text)

    def _show_tm_cleanup_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("РЎРїСЂР°РІРєР°: РѕС‡РёСЃС‚РєР° РўРњ")
        dialog.setModal(True)
        dialog.resize(980, 700)
        dialog.setMinimumSize(860, 620)

        root_layout = QVBoxLayout(dialog)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        intro_label = QLabel(
            "РќРёР¶Рµ вЂ” РєСЂР°С‚РєР°СЏ СЃС…РµРјР° СЌС‚Р°РїРѕРІ РѕС‡РёСЃС‚РєРё TMX Рё РїСЂРёРјРµСЂС‹ С‚РѕРіРѕ, С‡С‚Рѕ РёРјРµРЅРЅРѕ РјРµРЅСЏРµС‚СЃСЏ."
        )
        intro_label.setWordWrap(True)
        root_layout.addWidget(intro_label)

        help_view = QTextBrowser(dialog)
        help_view.setOpenExternalLinks(False)
        help_view.setHtml(
            """
            <h2>1) РЎРїР»РёС‚ СЃРµРіРјРµРЅС‚РѕРІ РїРѕ РїСЂРµРґР»РѕР¶РµРЅРёСЏРј</h2>
            <p>Р Р°Р·Р±РёРІР°РµС‚ РѕРґРёРЅ TU РЅР° РЅРµСЃРєРѕР»СЊРєРѕ, РµСЃР»Рё source/target РєРѕСЂСЂРµРєС‚РЅРѕ РІС‹СЂР°РІРЅРёРІР°СЋС‚СЃСЏ РїРѕ РїСЂРµРґР»РѕР¶РµРЅРёСЏРј.</p>
            <p><b>Guard:</b> РµСЃР»Рё РїРѕР»СѓС‡Р°СЋС‚СЃСЏ 2 РєРѕСЂРѕС‚РєРёРµ С‡Р°СЃС‚Рё (РѕР±С‹С‡РЅРѕ 2-3 СЃР»РѕРІР° РєР°Р¶РґР°СЏ), СЃРїР»РёС‚ РїСЂРѕРїСѓСЃРєР°РµС‚СЃСЏ.</p>
            <p><b>РџСЂРёРјРµСЂ:</b><br/>
            Р”Рѕ: <code>The battle is almost over. Gather your team and strike now!</code> /
            <code>Р‘РёС‚РІР° РїРѕС‡С‚Рё РѕРєРѕРЅС‡РµРЅР°. РЎРѕР±РµСЂРё РєРѕРјР°РЅРґСѓ Рё Р°С‚Р°РєСѓР№ РїСЂСЏРјРѕ СЃРµР№С‡Р°СЃ!</code><br/>
            РџРѕСЃР»Рµ: 2 РѕС‚РґРµР»СЊРЅС‹С… TU.</p>

            <h2>2) РћС‡РёСЃС‚РєР° РїСЂРѕР±РµР»РѕРІ (AUTO normalize_spaces)</h2>
            <ul>
              <li>РЎС…Р»РѕРїС‹РІР°РµС‚ РїРѕРІС‚РѕСЂСЏСЋС‰РёРµСЃСЏ РѕР±С‹С‡РЅС‹Рµ РїСЂРѕР±РµР»С‹ <code>ASCII ' '</code> РґРѕ РѕРґРЅРѕРіРѕ.</li>
              <li>РЈРґР°Р»СЏРµС‚ РѕР±С‹С‡РЅС‹Рµ РїСЂРѕР±РµР»С‹ РІ РЅР°С‡Р°Р»Рµ Рё РєРѕРЅС†Рµ СЃРµРіРјРµРЅС‚Р°.</li>
              <li><b>РќРµ РјРµРЅСЏРµС‚</b> NBSP/NNBSP, С‚Р°Р±С‹ Рё РїРµСЂРµРЅРѕСЃС‹ СЃС‚СЂРѕРє.</li>
            </ul>
            <p><b>РџСЂРёРјРµСЂ:</b><br/>
            Р”Рѕ: <code>"  Hero   Wars  "</code><br/>
            РџРѕСЃР»Рµ: <code>"Hero Wars"</code></p>

            <h2>3) РЈРґР°Р»РµРЅРёРµ СЃР»СѓР¶РµР±РЅРѕР№ СЂР°Р·РјРµС‚РєРё (AUTO remove_service_markup)</h2>
            <ul>
              <li>РЈРґР°Р»СЏРµС‚ inline-С‚РµРіРё РІРЅСѓС‚СЂРё <code>&lt;seg&gt;</code> (<code>bpt/ept/ph/...</code>).</li>
              <li>РЈРґР°Р»СЏРµС‚ РёРіСЂРѕРІРѕР№ markup: <code>^{...}^</code>, <code>$m(...|...)</code>, <code>&lt;Color=...&gt;...&lt;/Color&gt;</code>.</li>
              <li>РЈРґР°Р»СЏРµС‚ Р±РµР·РѕРїР°СЃРЅС‹Рµ С‚РѕРєРµРЅС‹ РІРёРґР° <code>%Name%</code> Рё <code>%Name%%</code>.</li>
              <li>РЎРѕС…СЂР°РЅСЏРµС‚ РѕР±С‹С‡РЅС‹Рµ РїСЂРѕС†РµРЅС‚С‹ (РЅР°РїСЂРёРјРµСЂ, <code>100%</code>).</li>
              <li>РџРѕСЃР»Рµ СѓРґР°Р»РµРЅРёСЏ РІРѕСЃСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РїСЂРѕР±РµР»С‹, С‡С‚РѕР±С‹ РЅРµ Р±С‹Р»Рѕ СЃР»РёРїС€РёС…СЃСЏ СЃР»РѕРІ.</li>
            </ul>
            <p><b>РџСЂРёРјРµСЂ:</b><br/>
            Р”Рѕ: <code>&lt;bpt/&gt;Hello %param%&lt;ept/&gt;</code><br/>
            РџРѕСЃР»Рµ: <code>Hello</code></p>

            <h2>4) РЈРґР°Р»РµРЅРёРµ РјСѓСЃРѕСЂРЅС‹С… TU (AUTO remove_garbage_segment)</h2>
            <ul>
              <li>РЈРґР°Р»СЏРµС‚ TU, РіРґРµ source Рё target СЃРѕСЃС‚РѕСЏС‚ С‚РѕР»СЊРєРѕ РёР· С‡РёСЃРµР».</li>
              <li>РЈРґР°Р»СЏРµС‚ TU, РіРґРµ source СЃРѕРґРµСЂР¶Р°С‚РµР»СЊРЅС‹Р№, Р° РІ target РЅРµС‚ Р±СѓРєРІ/С†РёС„СЂ.</li>
              <li>РЈРґР°Р»СЏРµС‚ TU, РіРґРµ РѕР±Рµ СЃС‚РѕСЂРѕРЅС‹ СЃРѕСЃС‚РѕСЏС‚ С‚РѕР»СЊРєРѕ РёР· РїСѓРЅРєС‚СѓР°С†РёРё/С‚РµРіРѕРІ/РїСѓСЃС‚С‹С… Р·РЅР°С‡РµРЅРёР№.</li>
            </ul>

            <h2>5) WARN-РґРёР°РіРЅРѕСЃС‚РёРєР° (Р±РµР· СѓРґР°Р»РµРЅРёСЏ TU)</h2>
            <ul>
              <li>РђРЅРѕРјР°Р»РёСЏ РґР»РёРЅС‹ source/target.</li>
              <li>РќРµСЃРѕРѕС‚РІРµС‚СЃС‚РІРёРµ СЃРєСЂРёРїС‚Р° (Р»Р°С‚РёРЅРёС†Р°/РєРёСЂРёР»Р»РёС†Р°/CJK) Р·РЅР°С‡РµРЅРёСЋ <code>xml:lang</code>.</li>
              <li>РџРѕР»РЅРѕСЃС‚СЊСЋ РѕРґРёРЅР°РєРѕРІС‹Рµ source/target РїСЂРё СЂР°Р·РЅС‹С… СЏР·С‹РєР°С….</li>
            </ul>

            <h2>6) РћРїС†РёРѕРЅР°Р»СЊРЅР°СЏ РїСЂРѕРІРµСЂРєР° Gemini</h2>
            <p>Р•СЃР»Рё РІРєР»СЋС‡РµРЅР°, Gemini РїСЂРѕРІРµСЂСЏРµС‚ С‚РѕР»СЊРєРѕ СЂРµС€РµРЅРёСЏ СЃРїР»РёС‚Р° Рё РІС‹СЃС‚Р°РІР»СЏРµС‚ confidence.</p>

            <h2>РћС‚С‡РµС‚С‹</h2>
            <p>HTML Рё XLSX РїРѕРєР°Р·С‹РІР°СЋС‚ РёР·РјРµРЅРµРЅРёСЏ РїРѕ РєР°Р¶РґРѕРјСѓ TU Рё СЃРІРѕРґРєРё РїРѕ РїСЂР°РІРёР»Р°Рј.</p>
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

    def _create_qsettings(self) -> QSettings:
        return QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)

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
        self._save_window_persistence()
        super().closeEvent(event)




