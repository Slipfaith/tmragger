"""Entry point for TMX repair CLI and PySide6 app."""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
from pathlib import Path
import sys
import traceback

from app_meta import APP_ICON_SVG_PATH, APP_NAME, APP_USER_MODEL_ID, APP_VERSION
from core.env_utils import load_project_env
from core.codex_client import (
    CODEX_REASONING_EFFORTS,
    DEFAULT_CODEX_BATCH_SIZE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    CodexVerifier,
)
from core.output_paths import sibling_output_dir
from core.repair import RepairStats, repair_tmx_file
from ui.logging_utils import configure_logger

INTER_FONT_PATH = (
    Path(__file__).resolve().parent / "asset" / "Inter-VariableFont_opsz,wght.ttf"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TMX repair tool (rule-based first pass).")
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        help="One or multiple input TMX file paths.",
    )
    parser.add_argument("--output", type=Path, help="Output TMX file path (single input only).")
    parser.add_argument("--output-dir", type=Path, help="Output directory for repaired files.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and split without writing output.")
    parser.add_argument("--log-file", type=str, default="tmx-repair.log", help="Log file path.")
    parser.add_argument("--no-split", action="store_true", help="Disable sentence split stage.")
    parser.add_argument(
        "--no-split-short-pair-guard",
        action="store_true",
        help="Allow split for tiny two-part pairs (default guard keeps them unsplit).",
    )
    parser.add_argument(
        "--split-line-breaks",
        action="store_true",
        help="Also split at single line breaks that end a line (list items, steps, headings).",
    )
    parser.add_argument(
        "--no-cleanup-spaces",
        action="store_true",
        help="Disable ASCII space cleanup (double spaces + edge trim).",
    )
    parser.add_argument(
        "--cleanup-tags",
        action="store_true",
        help="Enable inline tag removal (bpt/ept/ph) with boundary spacing fix.",
    )
    parser.add_argument(
        "--no-cleanup-garbage",
        action="store_true",
        help="Disable garbage TU removal rules.",
    )
    parser.add_argument(
        "--no-cleanup-warnings",
        action="store_true",
        help="Disable WARN diagnostics (length/script/identical checks).",
    )
    parser.add_argument(
        "--verify-codex",
        "--verify-gemini",
        dest="verify_splits",
        action="store_true",
        help="Enable Codex CLI verification for split proposals.",
    )
    parser.add_argument(
        "--codex-model",
        "--gemini-model",
        dest="codex_model",
        type=str,
        default=os.getenv("CODEX_MODEL", DEFAULT_CODEX_MODEL),
        help=f"Codex model name (or use CODEX_MODEL env; default {DEFAULT_CODEX_MODEL}).",
    )
    parser.add_argument(
        "--codex-effort",
        choices=CODEX_REASONING_EFFORTS,
        default=os.getenv("CODEX_REASONING_EFFORT", DEFAULT_CODEX_REASONING_EFFORT),
        help=f"Codex reasoning effort (or CODEX_REASONING_EFFORT env; default {DEFAULT_CODEX_REASONING_EFFORT}).",
    )
    parser.add_argument(
        "--codex-batch-size",
        type=int,
        default=int(os.getenv("CODEX_BATCH_SIZE", str(DEFAULT_CODEX_BATCH_SIZE))),
        help=(
            "Split candidates per Codex call; candidates are queued and verified after the TU pass "
            f"(default: CODEX_BATCH_SIZE or {DEFAULT_CODEX_BATCH_SIZE}; 0 = one call per TU)."
        ),
    )
    parser.add_argument(
        "--codex-max-parallel",
        "--gemini-max-parallel",
        dest="verification_max_parallel",
        type=int,
        default=int(os.getenv("CODEX_MAX_PARALLEL", "4")),
        help="Max parallel Codex split verifications (default: CODEX_MAX_PARALLEL or 4).",
    )
    parser.add_argument(
        "--max-codex-checks",
        "--max-gemini-checks",
        dest="max_verification_checks",
        type=int,
        default=int(os.getenv("CODEX_MAX_CHECKS", "1200")),
        help=(
            "Cap Codex split verifications per file "
            "(default: CODEX_MAX_CHECKS or 1200; <=0 means unlimited)."
        ),
    )
    parser.add_argument(
        "--resume-state-file",
        type=Path,
        help="Optional checkpoint file path for resume support.",
    )
    parser.add_argument(
        "--codex-cache-file",
        "--gemini-cache-file",
        dest="verification_cache_file",
        type=Path,
        help="Optional persistent verification cache file path.",
    )
    parser.add_argument(
        "--checkpoint-every-tus",
        type=int,
        default=int(os.getenv("CHECKPOINT_EVERY_TUS", "50")),
        help="Checkpoint interval in processed TUs (default: CHECKPOINT_EVERY_TUS or 50).",
    )
    parser.add_argument("--report-file", type=Path, help="Optional JSON report path (single input only).")
    parser.add_argument("--report-dir", type=Path, help="JSON report directory for batch mode.")
    parser.add_argument(
        "--xlsx-report-file",
        type=Path,
        help="Optional XLSX multi-sheet report path (single input only).",
    )
    parser.add_argument("--xlsx-report-dir", type=Path, help="XLSX report directory for batch mode.")
    parser.add_argument(
        "--codex-prompt-file",
        "--gemini-prompt-file",
        dest="verification_prompt_file",
        type=Path,
        help="Optional UTF-8 text file with custom batch prompt template (must contain {ITEMS_JSON}).",
    )
    parser.add_argument("--cli", action="store_true", help="Force CLI mode.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    input_paths: list[Path] = args.input or []
    if not input_paths:
        print("Error: --input is required in CLI mode.")
        return 2

    for path in input_paths:
        if not path.exists():
            print(f"Error: input file does not exist: {path}")
            return 2

    batch_mode = len(input_paths) > 1
    if batch_mode and args.output is not None:
        print("Error: --output can be used only with a single --input.")
        return 2
    if batch_mode and args.report_file is not None:
        print("Error: --report-file can be used only with a single --input. Use --report-dir.")
        return 2
    if batch_mode and args.xlsx_report_file is not None:
        print("Error: --xlsx-report-file can be used only with a single --input. Use --xlsx-report-dir.")
        return 2
    if batch_mode and args.resume_state_file is not None:
        print("Error: --resume-state-file can be used only with a single --input.")
        return 2

    verifier = None
    verification_prompt_template = None
    enable_split = not args.no_split
    enable_split_short_sentence_pair_guard = not args.no_split_short_pair_guard
    enable_split_line_breaks = bool(args.split_line_breaks)
    enable_cleanup_spaces = not args.no_cleanup_spaces
    enable_cleanup_tags = bool(args.cleanup_tags)
    enable_cleanup_garbage = not args.no_cleanup_garbage
    enable_cleanup_warnings = not args.no_cleanup_warnings
    verification_max_parallel = max(1, int(getattr(args, "verification_max_parallel", 1) or 1))
    if not any(
        (
            enable_split,
            enable_cleanup_spaces,
            enable_cleanup_tags,
            enable_cleanup_garbage,
            enable_cleanup_warnings,
        )
    ):
        print("Error: all processing stages are disabled. Enable at least one stage.")
        return 2

    if args.verify_splits:
        if args.verification_prompt_file is not None:
            if not args.verification_prompt_file.exists():
                print(f"Error: prompt file does not exist: {args.verification_prompt_file}")
                return 2
            verification_prompt_template = args.verification_prompt_file.read_text(encoding="utf-8-sig")
        try:
            verifier = CodexVerifier(
                model=args.codex_model,
                reasoning_effort=args.codex_effort,
                batch_size=args.codex_batch_size,
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 2

    logger = configure_logger(log_file=args.log_file)

    total_in = 0
    total_out = 0
    total_split = 0
    total_skipped = 0
    total_high = 0
    total_medium = 0
    total_g_checked = 0
    total_g_rejected = 0

    for input_path in input_paths:
        output_path = _resolve_output_path(
            input_path=input_path,
            output_override=args.output if not batch_mode else None,
            output_dir=args.output_dir,
        )
        report_path = _resolve_report_path(
            input_path=input_path,
            output_path=output_path,
            verify_splits=args.verify_splits,
            report_file=args.report_file if not batch_mode else None,
            report_dir=args.report_dir,
        )
        xlsx_report_path = _resolve_xlsx_report_path(
            input_path=input_path,
            output_path=output_path,
            xlsx_report_file=args.xlsx_report_file if not batch_mode else None,
            xlsx_report_dir=args.xlsx_report_dir,
        )

        stats = repair_tmx_file(
            input_path=input_path,
            output_path=output_path,
            dry_run=args.dry_run,
            logger=logger,
            verify_splits=args.verify_splits,
            verifier=verifier,
            max_verification_checks=(
                int(args.max_verification_checks)
                if int(args.max_verification_checks) > 0
                else None
            ),
            verification_max_parallel=verification_max_parallel,
            resume_state_path=(
                args.resume_state_file
                if args.resume_state_file is not None
                else (
                    (report_path.parent / f"{input_path.stem}.resume.json")
                    if report_path is not None
                    else output_path.with_suffix(output_path.suffix + ".resume.json")
                )
            ),
            verification_cache_path=(
                args.verification_cache_file
                if args.verification_cache_file is not None
                else (
                    (report_path.parent.parent / "verification-cache.json")
                    if report_path is not None
                    else output_path.parent / "verification-cache.json"
                )
            ),
            checkpoint_every_tus=max(1, int(args.checkpoint_every_tus or 50)),
            report_path=report_path,
            verification_prompt_template=verification_prompt_template,
            xlsx_report_path=xlsx_report_path,
            enable_split=enable_split,
            enable_split_short_sentence_pair_guard=enable_split_short_sentence_pair_guard,
            enable_split_line_breaks=enable_split_line_breaks,
            enable_cleanup_spaces=enable_cleanup_spaces,
            enable_cleanup_tag_removal=enable_cleanup_tags,
            enable_cleanup_garbage_removal=enable_cleanup_garbage,
            enable_cleanup_warnings=enable_cleanup_warnings,
        )

        print(
            (
                f"[{input_path.name}] total={stats.total_tus}, split={stats.split_tus}, skipped={stats.skipped_tus}, "
                f"output_tu={stats.created_tus}, high={stats.high_confidence_splits}, "
                f"medium={stats.medium_confidence_splits}, verification_checked={stats.verification_checked}, "
                f"verification_rejected={stats.verification_rejected}"
            )
        )
        if args.dry_run:
            print(f"[{input_path.name}] Dry run mode: output file was not written.")
        else:
            print(f"[{input_path.name}] Saved: {output_path}")
        if report_path is not None:
            print(f"[{input_path.name}] Report: {report_path}")
        if xlsx_report_path is not None:
            print(f"[{input_path.name}] XLSX multi-sheet report: {xlsx_report_path}")

        total_in += stats.total_tus
        total_out += stats.created_tus
        total_split += stats.split_tus
        total_skipped += stats.skipped_tus
        total_high += stats.high_confidence_splits
        total_medium += stats.medium_confidence_splits
        total_g_checked += stats.verification_checked
        total_g_rejected += stats.verification_rejected

    if batch_mode:
        print(
            (
                f"[BATCH] files={len(input_paths)}, total_tu={total_in}, split={total_split}, "
                f"skipped={total_skipped}, output_tu={total_out}, high={total_high}, medium={total_medium}, "
                f"verification_checked={total_g_checked}, verification_rejected={total_g_rejected}"
            )
        )
    return 0


def _resolve_output_path(
    input_path: Path,
    output_override: Path | None,
    output_dir: Path | None,
) -> Path:
    if output_override is not None:
        return output_override
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{input_path.stem}_repaired{input_path.suffix}"
    output_dir = sibling_output_dir(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{input_path.stem}_repaired{input_path.suffix}"


def _resolve_report_path(
    input_path: Path,
    output_path: Path,
    verify_splits: bool,
    report_file: Path | None,
    report_dir: Path | None,
) -> Path | None:
    if not verify_splits:
        return None
    if report_file is not None:
        return report_file
    report_base_dir = _resolve_report_base_dir(
        input_path=input_path,
        report_dir=report_dir,
    )
    report_base_dir.mkdir(parents=True, exist_ok=True)
    return report_base_dir / f"{input_path.stem}.verification.json"


def _resolve_xlsx_report_path(
    input_path: Path,
    output_path: Path,
    xlsx_report_file: Path | None,
    xlsx_report_dir: Path | None,
) -> Path:
    if xlsx_report_file is not None:
        return xlsx_report_file
    report_base_dir = _resolve_report_base_dir(
        input_path=input_path,
        report_dir=xlsx_report_dir,
    )
    report_base_dir.mkdir(parents=True, exist_ok=True)
    return report_base_dir / f"{input_path.stem}.diff-report.xlsx"


def _resolve_report_base_dir(input_path: Path, report_dir: Path | None) -> Path:
    if report_dir is None:
        return sibling_output_dir(input_path)
    elif report_dir.is_absolute():
        reports_root = report_dir
    else:
        reports_root = input_path.parent / report_dir
    return reports_root / input_path.stem


def _install_global_excepthook() -> None:
    """Log uncaught exceptions and show a message box in GUI mode."""
    log = logging.getLogger("tmx_repair")
    prev_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            prev_hook(exc_type, exc_value, exc_tb)
            return
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("Uncaught exception: %s", tb_text)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Необработанная ошибка",
                    f"{exc_type.__name__}: {exc_value}\n\n{tb_text}",
                )
        except Exception:
            pass
        prev_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def run_gui() -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except Exception:
        print("PySide6 is not installed. Install it and retry, or run with --cli.")
        return 2

    from ui.main_window import MainWindow

    _install_global_excepthook()
    _set_windows_appusermodelid(APP_USER_MODEL_ID)
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    if APP_ICON_SVG_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_SVG_PATH)))
    _apply_custom_app_font(app)
    window = MainWindow()
    window.show()
    return app.exec()


def _apply_custom_app_font(app: "QApplication") -> None:
    """Load Inter variable font from local assets and apply it app-wide."""
    from PySide6.QtGui import QFontDatabase

    log = logging.getLogger("tmx_repair")
    if not INTER_FONT_PATH.exists():
        log.warning("Custom font file not found: %s", INTER_FONT_PATH)
        return

    font_id = QFontDatabase.addApplicationFont(str(INTER_FONT_PATH))
    if font_id < 0:
        log.warning("Failed to load custom font: %s", INTER_FONT_PATH)
        return

    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        log.warning("Custom font loaded but no font families found: %s", INTER_FONT_PATH)
        return

    current = app.font()
    current.setFamily(families[0])
    app.setFont(current)
    log.info("Applied app font: %s (%s)", families[0], INTER_FONT_PATH)


def _set_windows_appusermodelid(app_id: str) -> None:
    """Set explicit AppUserModelID so taskbar/start-menu use the app identity/icon."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        logging.getLogger("tmx_repair").debug("Failed to set AppUserModelID", exc_info=True)


def main() -> int:
    load_project_env()
    parser = build_parser()
    args = parser.parse_args()

    should_use_cli = args.cli or args.input is not None
    if should_use_cli:
        return run_cli(args)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
