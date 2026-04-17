"""Background worker for running the TMX repair pipeline.

The worker owns a :class:`RepairRunConfig` and streams progress to the GUI
through Qt signals. Subsequent stages will extend it with plan/apply phases;
this module is intentionally small and single-purpose so it can evolve.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import traceback

from PySide6.QtCore import QThread, Signal

from core.gemini_client import GeminiVerifier
from core.repair import repair_tmx_file
from ui.logging_utils import configure_logger
from ui.types import BatchRunResult, FileRunResult, RepairRunConfig


class RepairWorker(QThread):
    """Runs a batch TMX repair off the UI thread."""

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
        os.environ["GEMINI_PRICE_INPUT_PER_1M_USD"] = f"{self.config.gemini_input_price_per_1m}"
        os.environ["GEMINI_PRICE_OUTPUT_PER_1M_USD"] = f"{self.config.gemini_output_price_per_1m}"
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
            tb = traceback.format_exc()
            logging.getLogger("tmx_repair").exception("RepairWorker crashed: %s", exc)
            self.log_message.emit(f"Traceback:\n{tb}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
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
