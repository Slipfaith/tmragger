"""Shared UI-layer dataclasses for the TMX repair app."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.plan import RepairPlan
from core.repair import RepairStats


@dataclass
class RepairRunConfig:
    input_paths: list[Path]
    output_dir: Path | None
    dry_run: bool
    enable_split: bool
    enable_cleanup_spaces: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool
    log_file: str | None
    verify_with_gemini: bool
    gemini_api_key: str
    gemini_model: str
    gemini_input_price_per_1m: float
    gemini_output_price_per_1m: float
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
    html_report_path: Path
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
    gemini_checked: int
    gemini_rejected: int
    gemini_input_tokens: int
    gemini_output_tokens: int
    gemini_total_tokens: int
    gemini_estimated_cost_usd: float
