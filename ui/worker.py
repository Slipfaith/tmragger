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
import traceback

from PySide6.QtCore import QThread, Signal

from core.gemini_client import GeminiVerifier
from core.plan import RepairPlan
from core.repair import repair_tmx_file
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

    # ------------------------------------------------------------------ run
    def run(self) -> None:  # type: ignore[override]
        try:
            if self.phase == "plan":
                self._run_plan_phase()
            else:
                self._run_apply_phase()
        except Exception as exc:
            tb = traceback.format_exc()
            logging.getLogger("tmx_repair").exception("RepairWorker crashed: %s", exc)
            self.log_message.emit(f"Traceback:\n{tb}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    # --------------------------------------------------------------- phases
    def _run_plan_phase(self) -> None:
        logger = self._configure_env_and_logger()
        verifier = self._maybe_build_verifier()

        plans: list[FilePlanResult] = []
        total = len(self.config.input_paths)
        for idx, input_path in enumerate(self.config.input_paths, start=1):
            self.log_message.emit(f"[план {idx}/{total}] Анализ: {input_path.name}")
            paths = self._resolve_paths(input_path)
            progress_cb = self._make_progress_cb(idx, total, input_path, 0, 0, 0, 0.0)

            stats = repair_tmx_file(
                input_path=input_path,
                output_path=paths["output"],
                mode="plan",
                logger=logger,
                verify_with_gemini=self.config.verify_with_gemini,
                gemini_verifier=verifier,
                gemini_prompt_template=self.config.gemini_prompt_template,
                progress_callback=progress_cb,
                gemini_input_price_per_1m=self.config.gemini_input_price_per_1m,
                gemini_output_price_per_1m=self.config.gemini_output_price_per_1m,
                enable_split=self.config.enable_split,
                enable_cleanup_spaces=self.config.enable_cleanup_spaces,
                enable_cleanup_tag_removal=self.config.enable_cleanup_tags,
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
            self.log_message.emit(f"[apply {idx}/{total}] Start: {item.input_path.name}")
            accepted_split_ids = item.plan.accepted_split_ids()
            accepted_cleanup_ids = item.plan.accepted_cleanup_ids()
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
                verify_with_gemini=self.config.verify_with_gemini,
                gemini_verifier=verifier,
                max_gemini_checks=None,
                report_path=item.report_path,
                gemini_prompt_template=self.config.gemini_prompt_template,
                html_report_path=item.html_report_path,
                xlsx_report_path=item.xlsx_report_path,
                progress_callback=progress_cb,
                accepted_split_ids=accepted_split_ids,
                accepted_cleanup_ids=accepted_cleanup_ids,
                gemini_input_price_per_1m=self.config.gemini_input_price_per_1m,
                gemini_output_price_per_1m=self.config.gemini_output_price_per_1m,
                enable_split=self.config.enable_split,
                enable_cleanup_spaces=self.config.enable_cleanup_spaces,
                enable_cleanup_tag_removal=self.config.enable_cleanup_tags,
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
        }

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
