"""Typed progress events emitted by the TMX repair pipeline.

The pipeline historically emits plain `dict` payloads through a
`progress_callback`. Those payloads are still used by existing tests and by
the UI, so we keep them intact. In parallel, `repair_tmx_file` now also emits
strongly typed events to an optional `event_callback`. Subsequent stages
(Review UI, external tools) should prefer the typed events — they are easier
to introspect, serialize, and refactor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Union


@dataclass
class FileStartEvent:
    input_path: str
    total_tus: int
    src_lang: str
    kind: Literal["file_start"] = "file_start"


@dataclass
class FileCompleteEvent:
    input_path: str
    output_path: str
    total_tus: int
    split_tus: int
    created_tus: int
    skipped_tus: int
    kind: Literal["file_complete"] = "file_complete"


@dataclass
class TuStartEvent:
    tu_index: int  # 0-based
    total_tus: int
    kind: Literal["tu_start"] = "tu_start"


@dataclass
class TuSkippedEvent:
    tu_index: int
    total_tus: int
    reason: str
    kind: Literal["tu_skipped"] = "tu_skipped"


@dataclass
class SplitProposedEvent:
    tu_index: int
    src_parts: list[str]
    tgt_parts: list[str]
    confidence: Literal["HIGH", "MEDIUM"]
    original_src: str
    original_tgt: str
    kind: Literal["split_proposed"] = "split_proposed"


@dataclass
class CleanupProposedEvent:
    tu_index: int
    rule: str
    message: str
    before_src: str
    after_src: str
    before_tgt: str
    after_tgt: str
    kind: Literal["cleanup_proposed"] = "cleanup_proposed"


@dataclass
class WarningEvent:
    tu_index: int
    rule: str
    severity: str
    message: str
    kind: Literal["warning"] = "warning"


@dataclass
class GeminiUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class GeminiResultEvent:
    tu_index: int
    check_kind: Literal["split", "cleanup"]
    verdict: str
    summary: str
    issues_count: int
    usage: GeminiUsage = field(default_factory=GeminiUsage)
    kind: Literal["gemini_result"] = "gemini_result"


RepairEvent = Union[
    FileStartEvent,
    FileCompleteEvent,
    TuStartEvent,
    TuSkippedEvent,
    SplitProposedEvent,
    CleanupProposedEvent,
    WarningEvent,
    GeminiResultEvent,
]


def event_to_dict(event: RepairEvent) -> dict:
    """Serialize a typed event to a plain dict (useful for JSON/UI bridges)."""
    return asdict(event)
