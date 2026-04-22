"""View-state model for the TMX repair GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ViewState:
    input_paths: list[Path] = field(default_factory=list)
    output_dir: Path | None = None
    dry_run: bool = False
    enable_split: bool = False
    enable_split_short_sentence_pair_guard: bool = False
    enable_cleanup_spaces: bool = True
    enable_cleanup_service_markup: bool = True
    enable_cleanup_garbage: bool = True
    enable_cleanup_warnings: bool = True
    enable_dedup_tus: bool = True
    verify_with_gemini: bool = False
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite-preview"
    gemini_input_price_per_1m: str = "0.10"
    gemini_output_price_per_1m: str = "0.40"
    log_file: str | None = "tmx-repair.log"
    report_dir: Path | None = Path("tmx-reports")
    html_report_dir: Path | None = Path("tmx-reports")
    xlsx_report_dir: Path | None = Path("tmx-reports")

    @classmethod
    def defaults(cls) -> "ViewState":
        return cls()
