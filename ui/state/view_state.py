"""View-state model for the TMX repair GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.codex_client import DEFAULT_CODEX_MODEL


@dataclass(slots=True)
class ViewState:
    input_paths: list[Path] = field(default_factory=list)
    output_dir: Path | None = None
    dry_run: bool = False
    enable_split: bool = False
    enable_split_short_sentence_pair_guard: bool = False
    enable_split_line_breaks: bool = False
    enable_cleanup_spaces: bool = True
    enable_cleanup_line_breaks: bool = False
    enable_cleanup_service_markup: bool = True
    enable_cleanup_garbage: bool = True
    enable_cleanup_warnings: bool = True
    enable_dedup_tus: bool = True
    verify_splits: bool = False
    codex_model: str = DEFAULT_CODEX_MODEL
    log_file: str | None = "tmx-repair.log"
    report_dir: Path | None = None
    xlsx_report_dir: Path | None = None

    @classmethod
    def defaults(cls) -> "ViewState":
        return cls()
