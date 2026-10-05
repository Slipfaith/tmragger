"""Shared UI-layer dataclasses for the TMX repair app."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.codex_client import DEFAULT_CODEX_REASONING_EFFORT
from core.plan import RepairPlan
from core.repair import RepairStats


@dataclass
class RepairRunConfig:
    input_paths: list[Path]
    output_dir: Path | None
    dry_run: bool
    enable_split: bool
    enable_split_short_sentence_pair_guard: bool
    enable_cleanup_spaces: bool
    enable_cleanup_line_breaks: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool
    enable_dedup_tus: bool
    log_file: str | None
    verify_splits: bool
    codex_model: str
    verification_max_parallel: int
    max_verification_checks: int | None
    verification_prompt_template: str | None
    report_dir: Path | None
    xlsx_report_dir: Path | None
    codex_reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT
    enable_split_line_breaks: bool = False


@dataclass
class FileRunResult:
    input_path: Path
    output_path: Path
    report_path: Path | None
    xlsx_report_path: Path
    stats: RepairStats


@dataclass
class FilePlanResult:
    """Outcome of the plan phase for one file.

    Carries everything needed by the apply phase: pre-resolved output/report
    paths (so we don't recompute them on the second pass) and the mutable
    ``plan`` that the UI will show to the user and later hand back with
    accepted flags toggled.
    """

    input_path: Path
    output_path: Path
    report_path: Path | None
    xlsx_report_path: Path
    stats: RepairStats
    plan: RepairPlan


@dataclass
class PlanPhaseResult:
    """Plan-phase payload emitted to the UI — a list of per-file plans."""

    files: list[FilePlanResult] = field(default_factory=list)


@dataclass
class BatchRunResult:
    files: list[FileRunResult]
    total_tu: int
    split_tu: int
    skipped_tu: int
    output_tu: int
    high_conf: int
    medium_conf: int
    verification_checked: int
    verification_rejected: int
    verification_input_tokens: int
    verification_output_tokens: int
    verification_total_tokens: int
