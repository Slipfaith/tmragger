"""Plan/apply data model for controlled TMX repair.

A `RepairPlan` is the pipeline output in `mode="plan"`: nothing is written, no
TUs are replaced. Instead the caller gets a list of `Proposal` objects — each
one a candidate edit (split or cleanup) with enough info to render a preview
UI. The caller may flip `accepted=False` on any proposal and then re-run the
pipeline in `mode="apply"` with the modified plan; rejected proposals are
skipped, accepted ones are applied as usual.

This keeps the user in control and matches the original product requirement:
> "процесс должен быть контролируемым, чтоб пользователь мог отменять
> некоторые правки по ТМХ".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal
import json


ProposalKind = Literal["split", "cleanup"]


@dataclass
class Proposal:
    """A single candidate edit for one TU."""

    proposal_id: str
    kind: ProposalKind
    tu_index: int  # 0-based index within the file
    accepted: bool = True
    # Human-readable confidence ("HIGH" / "MEDIUM") for splits; empty for cleanup.
    confidence: str = ""
    # Split verification verdict from Gemini ("OK"/"WARN"/"FAIL"), if available.
    gemini_verdict: str = ""
    # For splits only.
    src_parts: list[str] = field(default_factory=list)
    tgt_parts: list[str] = field(default_factory=list)
    # For cleanup only.
    rule: str = ""
    message: str = ""
    before_src: str = ""
    after_src: str = ""
    before_tgt: str = ""
    after_tgt: str = ""
    # Original segments (common to both kinds, used by UI preview).
    original_src: str = ""
    original_tgt: str = ""


@dataclass
class RepairPlan:
    """Full plan for one input TMX file."""

    input_path: str
    total_tus: int = 0
    proposals: list[Proposal] = field(default_factory=list)

    def accepted_split_ids(self) -> set[str]:
        return {p.proposal_id for p in self.proposals if p.kind == "split" and p.accepted}

    def accepted_cleanup_ids(self) -> set[str]:
        return {p.proposal_id for p in self.proposals if p.kind == "cleanup" and p.accepted}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "RepairPlan":
        raw = json.loads(payload)
        proposals = [Proposal(**p) for p in raw.pop("proposals", [])]
        return cls(proposals=proposals, **raw)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RepairPlan":
        return cls.from_json(path.read_text(encoding="utf-8"))


def make_split_proposal_id(tu_index: int) -> str:
    return f"split:{tu_index}"


def make_cleanup_proposal_id(tu_index: int, rule: str, ordinal: int) -> str:
    # Multiple cleanup actions per TU are possible; ordinal disambiguates.
    return f"cleanup:{tu_index}:{rule}:{ordinal}"
