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
from core.gemini_client import GeminiVerifier
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
    parser.add_argument("--verify-gemini", action="store_true", help="Enable Gemini verification for split proposals.")
    parser.add_argument("--gemini-api-key", type=str, help="Gemini API key (or use GEMINI_API_KEY env).")
    parser.add_argument(
        "--gemini-model",
        type=str,
        default=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"),
        help="Gemini model name (or use GEMINI_MODEL env).",
    )
    parser.add_argument(
        "--gemini-max-parallel",
        type=int,
        default=int(os.getenv("GEMINI_MAX_PARALLEL", "4")),
        help="Max parallel Gemini split verifications (default: GEMINI_MAX_PARALLEL or 4).",
    )
    parser.add_argument("--report-file", type=Path, help="Optional JSON report path (single input only).")
    parser.add_argument("--report-dir", type=Path, help="JSON report directory for batch mode.")
    parser.add_argument("--html-report-file", type=Path, help="Optional HTML diff report path (single input only).")
    parser.add_argument("--html-report-dir", type=Path, help="HTML report directory for batch mode.")
    parser.add_argument(
        "--xlsx-report-file",
        type=Path,
        help="Optional XLSX multi-sheet report path (single input only).",
    )
    parser.add_argument("--xlsx-report-dir", type=Path, help="XLSX report directory for batch mode.")
    parser.add_argument(
        "--gemini-prompt-file",
        type=Path,
        help="Optional UTF-8 text file with custom Gemini prompt template.",
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
    if batch_mode and args.html_report_file is not None:
        print("Error: --html-report-file can be used only with a single --input. Use --html-report-dir.")
        return 2
    if batch_mode and args.xlsx_report_file is not None:
        print("Error: --xlsx-report-file can be used only with a single --input. Use --xlsx-report-dir.")
        return 2

    gemini_verifier = None
    gemini_prompt_template = None
    enable_split = not args.no_split
    enable_split_short_sentence_pair_guard = not args.no_split_short_pair_guard
    enable_cleanup_spaces = not args.no_cleanup_spaces
    enable_cleanup_tags = bool(args.cleanup_tags)
    enable_cleanup_garbage = not args.no_cleanup_garbage
    enable_cleanup_warnings = not args.no_cleanup_warnings
    gemini_max_parallel = max(1, int(getattr(args, "gemini_max_parallel", 1) or 1))
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

    if args.verify_gemini:
        api_key = (args.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not api_key:
            print("Error: Gemini verification enabled but no API key provided.")
            print("Set --gemini-api-key or GEMINI_API_KEY environment variable.")
            return 2
        if args.gemini_prompt_file is not None:
            if not args.gemini_prompt_file.exists():
                print(f"Error: prompt file does not exist: {args.gemini_prompt_file}")
                return 2
            gemini_prompt_template = args.gemini_prompt_file.read_text(encoding="utf-8-sig")
        gemini_verifier = GeminiVerifier(api_key=api_key, model=args.gemini_model)

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
            verify_with_gemini=args.verify_gemini,
            report_file=args.report_file if not batch_mode else None,
            report_dir=args.report_dir,
        )
        html_report_path = _resolve_html_report_path(
            input_path=input_path,
            output_path=output_path,
            html_report_file=args.html_report_file if not batch_mode else None,
            html_report_dir=args.html_report_dir,
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
            verify_with_gemini=args.verify_gemini,
            gemini_verifier=gemini_verifier,
            max_gemini_checks=None,
            gemini_max_parallel=gemini_max_parallel,
            report_path=report_path,
            gemini_prompt_template=gemini_prompt_template,
            html_report_path=html_report_path,
            xlsx_report_path=xlsx_report_path,
            enable_split=enable_split,
            enable_split_short_sentence_pair_guard=enable_split_short_sentence_pair_guard,
            enable_cleanup_spaces=enable_cleanup_spaces,
            enable_cleanup_tag_removal=enable_cleanup_tags,
            enable_cleanup_garbage_removal=enable_cleanup_garbage,
            enable_cleanup_warnings=enable_cleanup_warnings,
        )

        print(
            (
                f"[{input_path.name}] total={stats.total_tus}, split={stats.split_tus}, skipped={stats.skipped_tus}, "
                f"output_tu={stats.created_tus}, high={stats.high_confidence_splits}, "
                f"medium={stats.medium_confidence_splits}, gemini_checked={stats.gemini_checked}, "
                f"gemini_rejected={stats.gemini_rejected}"
            )
        )
        if args.dry_run:
            print(f"[{input_path.name}] Dry run mode: output file was not written.")
        else:
            print(f"[{input_path.name}] Saved: {output_path}")
        if report_path is not None:
            print(f"[{input_path.name}] Report: {report_path}")
        if html_report_path is not None:
            print(f"[{input_path.name}] HTML diff report: {html_report_path}")
        if xlsx_report_path is not None:
            print(f"[{input_path.name}] XLSX multi-sheet report: {xlsx_report_path}")

        total_in += stats.total_tus
        total_out += stats.created_tus
        total_split += stats.split_tus
        total_skipped += stats.skipped_tus
        total_high += stats.high_confidence_splits
        total_medium += stats.medium_confidence_splits
        total_g_checked += stats.gemini_checked
        total_g_rejected += stats.gemini_rejected

    if batch_mode:
        print(
            (
                f"[BATCH] files={len(input_paths)}, total_tu={total_in}, split={total_split}, "
                f"skipped={total_skipped}, output_tu={total_out}, high={total_high}, medium={total_medium}, "
                f"gemini_checked={total_g_checked}, gemini_rejected={total_g_rejected}"
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
    return input_path.with_name(f"{input_path.stem}_repaired{input_path.suffix}")


def _resolve_report_path(
    input_path: Path,
    output_path: Path,
    verify_with_gemini: bool,
    report_file: Path | None,
    report_dir: Path | None,
) -> Path | None:
    if not verify_with_gemini:
        return None
    if report_file is not None:
        return report_file
    report_base_dir = _resolve_report_base_dir(
        input_path=input_path,
        report_dir=report_dir,
    )
    report_base_dir.mkdir(parents=True, exist_ok=True)
    return report_base_dir / f"{input_path.stem}.verification.json"


def _resolve_html_report_path(
    input_path: Path,
    output_path: Path,
    html_report_file: Path | None,
    html_report_dir: Path | None,
) -> Path:
    if html_report_file is not None:
        return html_report_file
    report_base_dir = _resolve_report_base_dir(
        input_path=input_path,
        report_dir=html_report_dir,
    )
    report_base_dir.mkdir(parents=True, exist_ok=True)
    return report_base_dir / f"{input_path.stem}.diff-report.html"


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
        reports_root = input_path.parent / "tmx-reports"
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
