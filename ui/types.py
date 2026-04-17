"""Shared UI-layer dataclasses for the TMX repair app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.repair import RepairStats


@dataclass
class RepairRunConfig:
    input_paths: list[Path]
    output_dir: Path | None
    dry_run: bool
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
