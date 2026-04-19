"""Background worker for running the TMX repair pipeline.

The worker runs in one of two phases:

* ``phase="plan"``: runs :func:`repair_tmx_file` in plan mode for every input
  file, collects per-file plans into a :class:`PlanPhaseResult` and emits
  ``plans_ready``. Nothing is written to disk. The GUI uses the plan to drive
  a review UI.
* ``phase="apply"``: runs :func:`repair_tmx_file` in apply mode for every
  input file, filtering split proposals by the accepted flags in the plan
  that the user approved. Writes output TMX + reports and emits
  ``apply_completed``.

The two-phase design is what makes the process controllable — the user must
approve edits before any output file is written.
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from core.gemini_client import GeminiVerifier
from core.plan import RepairPlan
from core.repair import RepairControlInterrupt, repair_tmx_file
from ui.logging_utils import configure_logger
from ui.types import (
    BatchRunResult,
    FilePlanResult,
    FileRunResult,
    PlanPhaseResult,
    RepairRunConfig,
)


class RepairWorker(QThread):
    """Runs a batch TMX repair off the UI thread.

    Instantiate with ``phase="plan"`` first. After the user reviews the
    emitted plans, instantiate a fresh worker with ``phase="apply"`` and the
    (possibly filtered) :class:`PlanPhaseResult`.
    """

    log_message = Signal(str)
    progress_event = Signal(object)
    plans_ready = Signal(object)      # PlanPhaseResult
    apply_completed = Signal(object)  # BatchRunResult
    failed = Signal(str)

    def __init__(
        self,
        config: RepairRunConfig,
        phase: str = "plan",
        plans: PlanPhaseResult | None = None,
    ) -> None:
        super().__init__()
        if config is None:
            raise ValueError("config must not be None")
        if phase not in {"plan", "apply"}:
            raise ValueError(f"phase must be 'plan' or 'apply', got {phase!r}")
        if phase == "apply" and plans is None:
            raise ValueError("phase='apply' requires a PlanPhaseResult via plans=...")
        self.config = config
        self.phase = phase
        self.plans = plans
        self._control_lock = threading.Lock()
        self._pause_requested = False
        self._stop_requested = False

    # ------------------------------------------------------------------ run
    def run(self) -> None:  # type: ignore[override]
        try:
            if self.phase == "plan":
                self._run_plan_phase()
            else:
                self._run_apply_phase()
        except RepairControlInterrupt:
            self.log_message.emit("Остановлено пользователем.")
            self.failed.emit("STOPPED_BY_USER")
        except Exception as exc:
            tb = traceback.format_exc()
            logging.getLogger("tmx_repair").exception("RepairWorker crashed: %s", exc)
            self.log_message.emit(f"Traceback:\n{tb}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def request_pause(self) -> None:
        with self._control_lock:
            self._pause_requested = True

    def request_resume(self) -> None:
        with self._control_lock:
            self._pause_requested = False

    def request_stop(self) -> None:
        with self._control_lock:
            self._stop_requested = True
            self._pause_requested = False

    def is_paused(self) -> bool:
        with self._control_lock:
            return self._pause_requested

    def _wait_if_paused_or_stopped(self) -> None:
        while True:
            with self._control_lock:
                if self._stop_requested:
                    raise RepairControlInterrupt("STOPPED_BY_USER")
                paused = self._pause_requested
            if not paused:
                return
            time.sleep(0.1)

    # --------------------------------------------------------------- phases
    def _run_plan_phase(self) -> None:
        logger = self._configure_env_and_logger()
        verifier = self._maybe_build_verifier()

        plans: list[FilePlanResult] = []
        total = len(self.config.input_paths)
        batch_tokens_in = 0
        batch_tokens_out = 0
        batch_tokens_total = 0
        batch_cost = 0.0
        for idx, input_path in enumerate(self.config.input_paths, start=1):
            self._wait_if_paused_or_stopped()
            self.log_message.emit(f"[план {idx}/{total}] Анализ: {input_path.name}")
            paths = self._resolve_paths(input_path)
            progress_cb = self._make_progress_cb(
                idx, total, input_path,
                batch_tokens_in, batch_tokens_out, batch_tokens_total, batch_cost,
            )

            stats = repair_tmx_file(
                input_path=input_path,
                output_path=paths["output"],
                mode="plan",
                logger=logger,
                verify_with_gemini=self.config.verify_with_gemini,
                gemini_verifier=verifier,
                gemini_max_parallel=self.config.gemini_max_parallel,
                resume_state_path=paths["resume"],
                gemini_cache_path=paths["cache"],
                checkpoint_every_tus=50,
                gemini_prompt_template=self.config.gemini_prompt_template,
                progress_callback=progress_cb,
                gemini_input_price_per_1m=self.config.gemini_input_price_per_1m,
                gemini_output_price_per_1m=self.config.gemini_output_price_per_1m,
                enable_split=self.config.enable_split,
                enable_split_short_sentence_pair_guard=self.config.enable_split_short_sentence_pair_guard,
                enable_cleanup_spaces=self.config.enable_cleanup_spaces,
                enable_cleanup_percent_wrapped=self.config.enable_cleanup_service_markup,
                enable_cleanup_game_markup=self.config.enable_cleanup_service_markup,
                enable_cleanup_tag_removal=self.config.enable_cleanup_service_markup,
                enable_cleanup_garbage_removal=self.config.enable_cleanup_garbage,
                enable_cleanup_warnings=self.config.enable_cleanup_warnings,
            )
            assert stats.plan is not None, "plan mode must populate RepairStats.plan"
            plans.append(
                FilePlanResult(
                    input_path=input_path,
                    output_path=paths["output"],
                    report_path=paths["report"],
                    html_report_path=paths["html"],
                    xlsx_report_path=paths["xlsx"],
                    stats=stats,
                    plan=stats.plan,
                )
            )
            batch_tokens_in += stats.gemini_input_tokens
            batch_tokens_out += stats.gemini_output_tokens
            batch_tokens_total += stats.gemini_total_tokens
            batch_cost += stats.gemini_estimated_cost_usd
            self.log_message.emit(
                f"[план {idx}/{total}] {input_path.name}: {len(stats.plan.proposals)} кандидатов на правки"
            )

        self.plans_ready.emit(PlanPhaseResult(files=plans))

    def _run_apply_phase(self) -> None:
        assert self.plans is not None
        logger = self._configure_env_and_logger()
        verifier = self._maybe_build_verifier()

        results: list[FileRunResult] = []
        total = len(self.plans.files)
        batch_tokens_in = 0
        batch_tokens_out = 0
        batch_tokens_total = 0
        batch_cost = 0.0

        for idx, item in enumerate(self.plans.files, start=1):
            self._wait_if_paused_or_stopped()
            self.log_message.emit(f"[apply {idx}/{total}] Start: {item.input_path.name}")
            accepted_split_ids = item.plan.accepted_split_ids()
            accepted_cleanup_ids = item.plan.accepted_cleanup_ids()
            preverified_split_confidence_by_id = {
                p.proposal_id: p.confidence
                for p in item.plan.proposals
                if p.kind == "split" and p.accepted and p.confidence
            }
            preverified_split_verdict_by_id = {
                p.proposal_id: p.gemini_verdict
                for p in item.plan.proposals
                if p.kind == "split" and p.accepted and p.gemini_verdict
            }
            self.log_message.emit(
                f"[apply {idx}/{total}] Принято: splits={len(accepted_split_ids)}, "
                f"cleanup={len(accepted_cleanup_ids)}"
            )

            # Ensure report directories exist (plan phase did not write anything).
            for p in (item.html_report_path, item.xlsx_report_path):
                p.parent.mkdir(parents=True, exist_ok=True)
            if item.report_path is not None:
                item.report_path.parent.mkdir(parents=True, exist_ok=True)
            item.output_path.parent.mkdir(parents=True, exist_ok=True)

            progress_cb = self._make_progress_cb(
                idx, total, item.input_path,
                batch_tokens_in, batch_tokens_out, batch_tokens_total, batch_cost,
            )
            stats = repair_tmx_file(
                input_path=item.input_path,
                output_path=item.output_path,
                dry_run=self.config.dry_run,
                mode="apply",
                logger=logger,
                # Apply phase reuses plan-phase Gemini verdicts and must not re-call Gemini.
                verify_with_gemini=False,
                gemini_verifier=verifier,
                max_gemini_checks=None,
                gemini_max_parallel=1,
                resume_state_path=self._resolve_resume_state_path(item.input_path, item.report_path),
                gemini_cache_path=self._resolve_gemini_cache_path(item.input_path, item.report_path),
                checkpoint_every_tus=50,
                report_path=item.report_path,
                gemini_prompt_template=self.config.gemini_prompt_template,
                html_report_path=item.html_report_path,
                xlsx_report_path=item.xlsx_report_path,
                progress_callback=progress_cb,
                accepted_split_ids=accepted_split_ids,
                accepted_cleanup_ids=accepted_cleanup_ids,
                preverified_split_confidence_by_id=preverified_split_confidence_by_id,
                preverified_split_verdict_by_id=preverified_split_verdict_by_id,
                gemini_input_price_per_1m=self.config.gemini_input_price_per_1m,
                gemini_output_price_per_1m=self.config.gemini_output_price_per_1m,
                enable_split=self.config.enable_split,
                enable_split_short_sentence_pair_guard=self.config.enable_split_short_sentence_pair_guard,
                enable_cleanup_spaces=self.config.enable_cleanup_spaces,
                enable_cleanup_percent_wrapped=self.config.enable_cleanup_service_markup,
                enable_cleanup_game_markup=self.config.enable_cleanup_service_markup,
                enable_cleanup_tag_removal=self.config.enable_cleanup_service_markup,
                enable_cleanup_garbage_removal=self.config.enable_cleanup_garbage,
                enable_cleanup_warnings=self.config.enable_cleanup_warnings,
            )
            batch_tokens_in += stats.gemini_input_tokens
            batch_tokens_out += stats.gemini_output_tokens
            batch_tokens_total += stats.gemini_total_tokens
            batch_cost += stats.gemini_estimated_cost_usd
            self.log_message.emit(
                f"[apply {idx}/{total}] Готово: {item.input_path.name} | "
                f"split={stats.split_tus}, skipped={stats.skipped_tus}, output_tu={stats.created_tus}"
            )
            results.append(
                FileRunResult(
                    input_path=item.input_path,
                    output_path=item.output_path,
                    report_path=item.report_path,
                    html_report_path=item.html_report_path,
                    xlsx_report_path=item.xlsx_report_path,
                    stats=stats,
                )
            )

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
        self.apply_completed.emit(batch)

    # ---------------------------------------------------------------- utils
    def _configure_env_and_logger(self) -> logging.Logger:
        # Prices flow to repair_tmx_file() explicitly via kwargs, so we no
        # longer need to mutate os.environ from a worker thread.
        return configure_logger(log_file=self.config.log_file, ui_callback=None)

    def _maybe_build_verifier(self) -> GeminiVerifier | None:
        if not self.config.verify_with_gemini:
            return None
        return GeminiVerifier(
            api_key=self.config.gemini_api_key,
            model=self.config.gemini_model,
        )

    def _resolve_paths(self, input_path: Path) -> dict[str, Path | None]:
        output_dir = self.config.output_dir or input_path.parent
        output_path = output_dir / f"{input_path.stem}_repaired{input_path.suffix}"

        report_path: Path | None = None
        if self.config.verify_with_gemini:
            report_dir = self._resolve_report_base_dir(
                input_path=input_path, report_dir=self.config.report_dir,
            )
            report_path = report_dir / f"{input_path.stem}.verification.json"

        html_dir = self._resolve_report_base_dir(
            input_path=input_path, report_dir=self.config.html_report_dir,
        )
        xlsx_dir = self._resolve_report_base_dir(
            input_path=input_path, report_dir=self.config.xlsx_report_dir,
        )
        return {
            "output": output_path,
            "report": report_path,
            "html": html_dir / f"{input_path.stem}.diff-report.html",
            "xlsx": xlsx_dir / f"{input_path.stem}.diff-report.xlsx",
            "resume": self._resolve_resume_state_path(input_path, report_path),
            "cache": self._resolve_gemini_cache_path(input_path, report_path),
        }

    @staticmethod
    def _resolve_resume_state_path(input_path: Path, report_path: Path | None) -> Path:
        if report_path is not None:
            return report_path.parent / f"{input_path.stem}.resume.json"
        return input_path.parent / f"{input_path.stem}.resume.json"

    @staticmethod
    def _resolve_gemini_cache_path(input_path: Path, report_path: Path | None) -> Path:
        if report_path is not None:
            return report_path.parent.parent / "gemini-cache.json"
        return input_path.parent / "gemini-cache.json"

    def _make_progress_cb(
        self,
        file_index: int,
        file_total: int,
        input_path: Path,
        batch_tokens_in: int,
        batch_tokens_out: int,
        batch_tokens_total: int,
        batch_cost: float,
    ):
        state = {"in": 0, "out": 0, "total": 0, "cost": 0.0}

        def cb(event: dict[str, object]) -> None:
            self._wait_if_paused_or_stopped()
            state["in"] = int(event.get("gemini_input_tokens", state["in"]) or 0)
            state["out"] = int(event.get("gemini_output_tokens", state["out"]) or 0)
            state["total"] = int(event.get("gemini_total_tokens", state["total"]) or 0)
            state["cost"] = float(event.get("gemini_estimated_cost_usd", state["cost"]) or 0.0)
            payload = dict(event)
            payload["file_index"] = file_index
            payload["file_total"] = file_total
            payload["input_path"] = payload.get("input_path", str(input_path))
            payload["batch_gemini_input_tokens"] = batch_tokens_in + state["in"]
            payload["batch_gemini_output_tokens"] = batch_tokens_out + state["out"]
            payload["batch_gemini_total_tokens"] = batch_tokens_total + state["total"]
            payload["batch_gemini_estimated_cost_usd"] = batch_cost + state["cost"]
            self.progress_event.emit(payload)

        return cb

    @staticmethod
    def _resolve_report_base_dir(input_path: Path, report_dir: Path | None) -> Path:
        if report_dir is None:
            reports_root = input_path.parent / "tmx-reports"
        elif report_dir.is_absolute():
            reports_root = report_dir
        else:
            reports_root = input_path.parent / report_dir
        return reports_root / input_path.stem
