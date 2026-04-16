"""PySide6 desktop app for TMX repair."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.env_utils import load_project_env
from core.gemini_client import GeminiVerifier
from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT
from core.repair import RepairStats, repair_tmx_file
from ui.logging_utils import configure_logger


@dataclass
class RepairRunConfig:
    input_paths: list[Path]
    output_dir: Path | None
    dry_run: bool
    log_file: str | None
    verify_with_gemini: bool
    gemini_api_key: str
    gemini_model: str
    gemini_prompt_template: str | None
    report_dir: Path | None
    html_report_dir: Path | None
    xlsx_report_dir: Path | None


@dataclass
class FileRunResult:
    input_path: Path
    output_path: Path
    report_path: Path | None
    html_report_path: Path
    xlsx_report_path: Path
    stats: RepairStats


@dataclass
class BatchRunResult:
    files: list[FileRunResult]
    total_tu: int
    split_tu: int
    skipped_tu: int
    output_tu: int
    high_conf: int
    medium_conf: int
    gemini_checked: int
    gemini_rejected: int
    gemini_input_tokens: int
    gemini_output_tokens: int
    gemini_total_tokens: int
    gemini_estimated_cost_usd: float


class RepairWorker(QThread):
    log_message = Signal(str)
    progress_event = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, config: RepairRunConfig):
        super().__init__()
        self.config = config

    def run(self) -> None:  # type: ignore[override]
        # Keep detailed logs in console/file only. GUI receives concise batch progress messages.
        logger = configure_logger(log_file=self.config.log_file, ui_callback=None)
        gemini_verifier = None
        if self.config.verify_with_gemini:
            gemini_verifier = GeminiVerifier(
                api_key=self.config.gemini_api_key,
                model=self.config.gemini_model,
            )

        results: list[FileRunResult] = []
        total = len(self.config.input_paths)
        batch_tokens_in = 0
        batch_tokens_out = 0
        batch_tokens_total = 0
        batch_cost = 0.0

        try:
            for idx, input_path in enumerate(self.config.input_paths, start=1):
                self.log_message.emit(f"[{idx}/{total}] Start: {input_path.name}")
                output_dir = self.config.output_dir or input_path.parent
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"{input_path.stem}_repaired{input_path.suffix}"

                report_path = None
                if self.config.verify_with_gemini:
                    report_dir = self._resolve_report_base_dir(
                        input_path=input_path,
                        report_dir=self.config.report_dir,
                    )
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / f"{input_path.stem}.verification.json"

                html_report_dir = self._resolve_report_base_dir(
                    input_path=input_path,
                    report_dir=self.config.html_report_dir,
                )
                html_report_dir.mkdir(parents=True, exist_ok=True)
                html_report_path = html_report_dir / f"{input_path.stem}.diff-report.html"

                xlsx_report_dir = self._resolve_report_base_dir(
                    input_path=input_path,
                    report_dir=self.config.xlsx_report_dir,
                )
                xlsx_report_dir.mkdir(parents=True, exist_ok=True)
                xlsx_report_path = xlsx_report_dir / f"{input_path.stem}.diff-report.xlsx"

                file_live_tokens_in = 0
                file_live_tokens_out = 0
                file_live_tokens_total = 0
                file_live_cost = 0.0

                def progress_cb(
                    event: dict[str, object],
                    file_index: int = idx,
                    file_total: int = total,
                    file_path: str = str(input_path),
                ) -> None:
                    nonlocal file_live_tokens_in, file_live_tokens_out, file_live_tokens_total, file_live_cost
                    file_live_tokens_in = int(event.get("gemini_input_tokens", file_live_tokens_in) or 0)
                    file_live_tokens_out = int(event.get("gemini_output_tokens", file_live_tokens_out) or 0)
                    file_live_tokens_total = int(event.get("gemini_total_tokens", file_live_tokens_total) or 0)
                    file_live_cost = float(event.get("gemini_estimated_cost_usd", file_live_cost) or 0.0)
                    payload = dict(event)
                    payload["file_index"] = file_index
                    payload["file_total"] = file_total
                    payload["input_path"] = payload.get("input_path", file_path)
                    payload["batch_gemini_input_tokens"] = batch_tokens_in + file_live_tokens_in
                    payload["batch_gemini_output_tokens"] = batch_tokens_out + file_live_tokens_out
                    payload["batch_gemini_total_tokens"] = batch_tokens_total + file_live_tokens_total
                    payload["batch_gemini_estimated_cost_usd"] = batch_cost + file_live_cost
                    self.progress_event.emit(payload)

                stats = repair_tmx_file(
                    input_path=input_path,
                    output_path=output_path,
                    dry_run=self.config.dry_run,
                    logger=logger,
                    verify_with_gemini=self.config.verify_with_gemini,
                    gemini_verifier=gemini_verifier,
                    max_gemini_checks=None,
                    report_path=report_path,
                    gemini_prompt_template=self.config.gemini_prompt_template,
                    html_report_path=html_report_path,
                    xlsx_report_path=xlsx_report_path,
                    progress_callback=progress_cb,
                )
                batch_tokens_in += stats.gemini_input_tokens
                batch_tokens_out += stats.gemini_output_tokens
                batch_tokens_total += stats.gemini_total_tokens
                batch_cost += stats.gemini_estimated_cost_usd
                self.log_message.emit(
                    (
                        f"[{idx}/{total}] Done: {input_path.name} | split={stats.split_tus}, "
                        f"skipped={stats.skipped_tus}, output_tu={stats.created_tus}"
                    )
                )
                results.append(
                    FileRunResult(
                        input_path=input_path,
                        output_path=output_path,
                        report_path=report_path,
                        html_report_path=html_report_path,
                        xlsx_report_path=xlsx_report_path,
                        stats=stats,
                    )
                )
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        batch = BatchRunResult(
            files=results,
            total_tu=sum(r.stats.total_tus for r in results),
            split_tu=sum(r.stats.split_tus for r in results),
            skipped_tu=sum(r.stats.skipped_tus for r in results),
            output_tu=sum(r.stats.created_tus for r in results),
            high_conf=sum(r.stats.high_confidence_splits for r in results),
            medium_conf=sum(r.stats.medium_confidence_splits for r in results),
            gemini_checked=sum(r.stats.gemini_checked for r in results),
            gemini_rejected=sum(r.stats.gemini_rejected for r in results),
            gemini_input_tokens=sum(r.stats.gemini_input_tokens for r in results),
            gemini_output_tokens=sum(r.stats.gemini_output_tokens for r in results),
            gemini_total_tokens=sum(r.stats.gemini_total_tokens for r in results),
            gemini_estimated_cost_usd=sum(r.stats.gemini_estimated_cost_usd for r in results),
        )
        self.completed.emit(batch)

    @staticmethod
    def _resolve_report_base_dir(input_path: Path, report_dir: Path | None) -> Path:
        if report_dir is None:
            reports_root = input_path.parent / "tmx-reports"
        elif report_dir.is_absolute():
            reports_root = report_dir
        else:
            reports_root = input_path.parent / report_dir
        return reports_root / input_path.stem


class DropZone(QFrame):
    """Drop target for TMX files."""

    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            "#dropZone { border: 2px dashed #2d6a4f; border-radius: 8px; background: #f4fbf6; }"
        )
        layout = QVBoxLayout(self)
        label = QLabel("Drop one or multiple TMX files here")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._extract_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = self._extract_paths(event)
        if not paths:
            event.ignore()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    @staticmethod
    def _extract_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        found: list[str] = []
        for url in mime.urls():
            local_path = url.toLocalFile()
            if local_path.lower().endswith(".tmx"):
                found.append(local_path)
        return found


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._loaded_env_files = load_project_env()
        self.setWindowTitle("TMX Repair - Batch + Gemini Verification")
        self.resize(1140, 820)

        self._last_stats: BatchRunResult | None = None
        self._worker: RepairWorker | None = None
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

        self.tabs.addTab(self.repair_tab, "Repair")
        self.tabs.addTab(self.prompt_tab, "Gemini Prompt")
        self._build_menu()

    def _build_menu(self) -> None:
        copy_action = QAction("Copy Gemini Prompt", self)
        copy_action.triggered.connect(self._copy_prompt)
        self.menuBar().addAction(copy_action)

    def _build_repair_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self.drop_zone)

        form = QFormLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("One TMX path per line")
        self.input_edit.setFixedHeight(110)
        add_files_btn = QPushButton("Add Files")
        add_files_btn.clicked.connect(self._browse_inputs)
        clear_files_btn = QPushButton("Clear")
        clear_files_btn.clicked.connect(self._clear_inputs)
        input_buttons = QHBoxLayout()
        input_buttons.addWidget(add_files_btn)
        input_buttons.addWidget(clear_files_btn)
        input_buttons.addStretch(1)
        input_wrap = QWidget()
        input_wrap_layout = QVBoxLayout(input_wrap)
        input_wrap_layout.setContentsMargins(0, 0, 0, 0)
        input_wrap_layout.addWidget(self.input_edit)
        input_wrap_layout.addLayout(input_buttons)
        form.addRow("Input TMX files:", input_wrap)

        self.output_edit = QLineEdit()
        browse_output_dir = QPushButton("Browse")
        browse_output_dir.clicked.connect(self._browse_output_dir)
        row_out = QHBoxLayout()
        row_out.addWidget(self.output_edit)
        row_out.addWidget(browse_output_dir)
        form.addRow("Output folder:", self._wrap_layout(row_out))

        self.log_file_edit = QLineEdit("tmx-repair.log")
        form.addRow("Log file:", self.log_file_edit)

        self.html_report_edit = QLineEdit("tmx-reports")
        form.addRow("HTML report root folder:", self.html_report_edit)

        self.dry_run_checkbox = QCheckBox("Dry run (do not write repaired TMX files)")
        form.addRow("", self.dry_run_checkbox)

        self.verify_gemini_checkbox = QCheckBox("Enable Gemini verification")
        self.verify_gemini_checkbox.toggled.connect(self._toggle_gemini_controls)
        form.addRow("", self.verify_gemini_checkbox)

        self.gemini_api_key_edit = QLineEdit()
        self.gemini_api_key_edit.setPlaceholderText("Optional if GEMINI_API_KEY env is set")
        self.gemini_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Gemini API key:", self.gemini_api_key_edit)

        self.gemini_model_edit = QLineEdit(os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"))
        form.addRow("Gemini model:", self.gemini_model_edit)

        self.report_file_edit = QLineEdit("tmx-reports")
        form.addRow("Verification report root folder:", self.report_file_edit)

        self.xlsx_report_edit = QLineEdit("tmx-reports")
        form.addRow("XLSX report root folder:", self.xlsx_report_edit)
        layout.addLayout(form)
        self._toggle_gemini_controls(False)

        controls = QHBoxLayout()
        self.run_btn = QPushButton("Run Repair")
        self.run_btn.clicked.connect(self._run_repair)
        controls.addWidget(self.run_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.stats_label = QLabel("Status: waiting")
        layout.addWidget(self.stats_label)
        self.progress_label = QLabel("Progress: waiting")
        layout.addWidget(self.progress_label)
        self.token_usage_label = QLabel("Gemini usage: in=0 | out=0 | total=0 | est cost~$0.000000")
        layout.addWidget(self.token_usage_label)
        self.token_rate_label = QLabel(
            "Gemini speed: now~0.0 tok/s | avg~0.0 tok/s | current file forecast~$0.000000"
        )
        layout.addWidget(self.token_rate_label)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output, stretch=1)

        return widget

    def _build_prompt_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        tip = QLabel(
            "Edit this prompt. The exact edited text will be used in Gemini verification runs."
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.prompt_editor = QTextEdit()
        self.prompt_editor.setPlainText(self._render_prompt())
        layout.addWidget(self.prompt_editor, stretch=1)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("Reset Prompt")
        refresh_btn.clicked.connect(self._refresh_prompt)
        copy_btn = QPushButton("Copy Prompt")
        copy_btn.clicked.connect(self._copy_prompt)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return widget

    @staticmethod
    def _wrap_layout(inner_layout: QHBoxLayout) -> QWidget:
        wrap = QWidget()
        wrap.setLayout(inner_layout)
        return wrap

    def _toggle_gemini_controls(self, enabled: bool) -> None:
        self.gemini_api_key_edit.setEnabled(enabled)
        self.gemini_model_edit.setEnabled(enabled)
        self.report_file_edit.setEnabled(enabled)

    def _on_files_dropped(self, paths: list[str]) -> None:
        current = self._collect_input_paths()
        merged = current + [Path(p) for p in paths]
        self._set_input_paths(merged)
        self._append_log(f"Files dropped: {len(paths)}")

    def _browse_inputs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select TMX files",
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
        selected = QFileDialog.getExistingDirectory(self, "Select output folder")
        if selected:
            self.output_edit.setText(selected)

    def _collect_input_paths(self) -> list[Path]:
        raw_lines = [line.strip() for line in self.input_edit.toPlainText().splitlines()]
        paths: list[Path] = []
        seen: set[str] = set()
        for line in raw_lines:
            if not line:
                continue
            normalized = str(Path(line))
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(Path(normalized))
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
            QMessageBox.information(self, "In progress", "Repair is already running.")
            return

        input_paths = self._collect_input_paths()
        if not input_paths:
            QMessageBox.warning(self, "Missing input", "Add at least one TMX file.")
            return
        missing = [str(p) for p in input_paths if not p.exists()]
        if missing:
            QMessageBox.warning(
                self,
                "Input not found",
                "These files do not exist:\n" + "\n".join(missing[:10]),
            )
            return

        verify_with_gemini = self.verify_gemini_checkbox.isChecked()
        gemini_prompt_template = None
        gemini_api_key = ""
        gemini_key_source = ""
        gemini_model = self.gemini_model_edit.text().strip() or "gemini-3.1-flash-lite-preview"
        report_dir = None
        if verify_with_gemini:
            api_key_from_ui = self.gemini_api_key_edit.text().strip()
            if api_key_from_ui:
                gemini_api_key = api_key_from_ui
                gemini_key_source = "UI field"
            else:
                gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
                gemini_key_source = "GEMINI_API_KEY env"
            if not gemini_api_key:
                env_hint = ""
                if self._loaded_env_files:
                    env_hint = "\nLoaded .env files:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                QMessageBox.warning(
                    self,
                    "Gemini API key missing",
                    "Set Gemini API key in field or GEMINI_API_KEY environment variable." + env_hint,
                )
                return
            gemini_prompt_template = self.prompt_editor.toPlainText()
            if self._loaded_env_files:
                self._append_log(
                    "Loaded .env files:\n" + "\n".join(str(path) for path in self._loaded_env_files)
                )
            self._append_log(f"Gemini API key source: {gemini_key_source}")
            self._append_log(
                "Gemini prompt template selected from UI editor:\n"
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
            log_file=self.log_file_edit.text().strip() or None,
            verify_with_gemini=verify_with_gemini,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
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
        self.stats_label.setText(f"Status: running ({len(input_paths)} files)...")
        self.progress_label.setText("Progress: initialization")
        self._append_log(f"Starting batch repair: files={len(input_paths)}")

        self._worker = RepairWorker(config)
        self._worker.log_message.connect(self._append_log)
        self._worker.progress_event.connect(self._on_progress_event)
        self._worker.completed.connect(self._on_worker_completed)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_completed(self, batch: object) -> None:
        if not isinstance(batch, BatchRunResult):
            self._on_worker_failed("Internal error: worker returned invalid batch result.")
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
        self.progress_label.setText("Progress: finished")

        dry_run = self.dry_run_checkbox.isChecked()
        done_message = (
            "Dry run complete. Repaired TMX files were not written."
            if dry_run
            else "Batch repair complete."
        )
        if batch.files:
            first = batch.files[0]
            done_message = (
                f"{done_message}\nExample HTML report:\n{first.html_report_path}"
            )
            done_message = f"{done_message}\nExample XLSX report:\n{first.xlsx_report_path}"
            if first.report_path is not None:
                done_message = f"{done_message}\nExample JSON report:\n{first.report_path}"
        QMessageBox.information(self, "Done", done_message)

    def _on_worker_failed(self, error_text: str) -> None:
        self._append_log(f"Error: {error_text}")
        self.stats_label.setText("Status: failed")
        self.progress_label.setText("Progress: failed")
        QMessageBox.critical(self, "Repair failed", error_text)

    def _on_worker_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self._worker = None

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
            self.progress_label.setText(f"Progress: file {file_index}/{file_total} ({short_name})")
        elif event == "file_complete" and file_index > 0 and file_total > 0:
            self.progress_label.setText(f"Progress: completed {file_index}/{file_total} files")

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
                f"Gemini usage: in={self._live_tokens_in:,} | out={self._live_tokens_out:,} | "
                f"total={self._live_tokens_total:,} | est cost~${self._live_cost:.6f}"
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
        self._append_log("Gemini prompt copied to clipboard.")

    def _render_prompt(self) -> str:
        return GEMINI_VERIFICATION_PROMPT

    def _append_log(self, message: str) -> None:
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()
