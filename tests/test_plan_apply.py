"""Stage 1 tests: plan/apply modes, typed events, proposal filtering."""

from __future__ import annotations

from pathlib import Path

from core.gemini_client import GeminiVerificationResult
from core.events import (
    CleanupProposedEvent,
    FileCompleteEvent,
    FileStartEvent,
    SplitProposedEvent,
    TuStartEvent,
)
from core.plan import RepairPlan, make_split_proposal_id
from core.repair import repair_tmx_file


RUNTIME_DIR = Path("tests") / "fixtures" / "runtime"


def _prepare() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def _write_two_splittable_tus(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee predlozhenie!</seg></tuv>
    </tu>
    <tu creationid="u2">
      <tuv xml:lang="en-US"><seg>Alpha one. Beta two.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Alfa raz. Beta dva.</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )


class _CountingVerifier:
    def __init__(self, verdict: str = "OK") -> None:
        self.calls = 0
        self._verdict = verdict

    def verify_split(self, _verify_request, prompt_template=None):  # noqa: ANN001
        self.calls += 1
        return GeminiVerificationResult(
            verdict=self._verdict,
            issues=[],
            summary="stub",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_plan_mode_does_not_write_output_or_reports():
    runtime = _prepare()
    inp = runtime / "plan_in.tmx"
    out = runtime / "plan_out.tmx"
    html = runtime / "plan_out.html"
    xlsx = runtime / "plan_out.xlsx"
    _write_two_splittable_tus(inp)

    stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        html_report_path=html,
        xlsx_report_path=xlsx,
        mode="plan",
    )

    assert not out.exists(), "plan mode must not create output TMX"
    assert not html.exists(), "plan mode must not create HTML report"
    assert not xlsx.exists(), "plan mode must not create XLSX report"

    assert stats.plan is not None
    plan = stats.plan
    assert plan.total_tus == 2
    split_proposals = [p for p in plan.proposals if p.kind == "split"]
    assert len(split_proposals) == 2
    assert {p.proposal_id for p in split_proposals} == {
        make_split_proposal_id(0),
        make_split_proposal_id(1),
    }
    for proposal in split_proposals:
        assert proposal.confidence == "HIGH"
        assert proposal.accepted is True
        assert len(proposal.src_parts) == 2
        assert len(proposal.tgt_parts) == 2

    inp.unlink(missing_ok=True)


def test_apply_mode_with_accepted_subset_splits_only_accepted_tus():
    runtime = _prepare()
    inp = runtime / "subset_in.tmx"
    out = runtime / "subset_out.tmx"
    _write_two_splittable_tus(inp)

    # Accept only the second TU (index=1). First TU must remain unsplit.
    accepted_ids = {make_split_proposal_id(1)}
    stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        dry_run=False,
        accepted_split_ids=accepted_ids,
    )

    assert stats.split_tus == 1
    assert stats.created_tus == 3  # one unsplit TU + two parts from the second
    content = out.read_text(encoding="utf-8")
    # First TU kept as one segment (contains the full original text).
    assert "Hello world. Next sentence!" in content
    # Second TU was split — its two halves appear separately.
    assert "Alpha one." in content
    assert "Beta two." in content

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


def test_typed_event_callback_fires_alongside_dict_callback():
    runtime = _prepare()
    inp = runtime / "events_in.tmx"
    out = runtime / "events_out.tmx"
    _write_two_splittable_tus(inp)

    typed: list[object] = []
    dict_events: list[dict] = []

    repair_tmx_file(
        input_path=inp,
        output_path=out,
        dry_run=True,
        event_callback=lambda e: typed.append(e),
        progress_callback=lambda p: dict_events.append(dict(p)),
    )

    kinds = [type(e).__name__ for e in typed]
    assert "FileStartEvent" in kinds
    assert "TuStartEvent" in kinds
    assert "SplitProposedEvent" in kinds
    assert "FileCompleteEvent" in kinds
    # Typed events must not starve the legacy dict stream.
    assert any(d.get("event") == "tu_start" for d in dict_events)
    assert any(d.get("event") == "file_complete" for d in dict_events)

    # Sanity-check payload of the first split event.
    splits = [e for e in typed if isinstance(e, SplitProposedEvent)]
    assert len(splits) == 2
    assert splits[0].confidence == "HIGH"
    assert splits[0].src_parts and splits[0].tgt_parts

    inp.unlink(missing_ok=True)


def test_repair_plan_json_roundtrip():
    runtime = _prepare()
    inp = runtime / "roundtrip_in.tmx"
    out = runtime / "roundtrip_out.tmx"
    _write_two_splittable_tus(inp)

    stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="plan",
    )
    assert stats.plan is not None
    original = stats.plan
    restored = RepairPlan.from_json(original.to_json())

    assert restored.input_path == original.input_path
    assert restored.total_tus == original.total_tus
    assert len(restored.proposals) == len(original.proposals)
    for a, b in zip(original.proposals, restored.proposals):
        assert a.proposal_id == b.proposal_id
        assert a.kind == b.kind
        assert a.src_parts == b.src_parts
        assert a.tgt_parts == b.tgt_parts

    inp.unlink(missing_ok=True)


def test_apply_with_empty_accepted_set_skips_all_splits():
    runtime = _prepare()
    inp = runtime / "empty_accept_in.tmx"
    out = runtime / "empty_accept_out.tmx"
    _write_two_splittable_tus(inp)

    stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        dry_run=False,
        accepted_split_ids=set(),
    )
    assert stats.split_tus == 0
    assert stats.created_tus == 2
    content = out.read_text(encoding="utf-8")
    assert "Hello world. Next sentence!" in content
    assert "Alpha one. Beta two." in content

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


def test_apply_reuses_plan_phase_gemini_verdict_and_does_not_recheck():
    runtime = _prepare()
    inp = runtime / "reuse_gemini_in.tmx"
    out = runtime / "reuse_gemini_out.tmx"
    _write_two_splittable_tus(inp)

    verifier = _CountingVerifier(verdict="OK")
    plan_stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="plan",
        verify_with_gemini=True,
        gemini_verifier=verifier,
        enable_split_short_sentence_pair_guard=False,
    )
    assert verifier.calls > 0
    assert plan_stats.plan is not None

    accepted_split_ids = plan_stats.plan.accepted_split_ids()
    confidence_by_id = {
        p.proposal_id: p.confidence
        for p in plan_stats.plan.proposals
        if p.kind == "split" and p.accepted and p.confidence
    }
    verdict_by_id = {
        p.proposal_id: p.gemini_verdict
        for p in plan_stats.plan.proposals
        if p.kind == "split" and p.accepted and p.gemini_verdict
    }

    apply_stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="apply",
        verify_with_gemini=False,
        gemini_verifier=verifier,
        accepted_split_ids=accepted_split_ids,
        preverified_split_confidence_by_id=confidence_by_id,
        preverified_split_verdict_by_id=verdict_by_id,
        enable_split_short_sentence_pair_guard=False,
    )

    # No second Gemini pass in apply.
    assert verifier.calls == plan_stats.gemini_checked
    assert apply_stats.gemini_checked == 0

    content = out.read_text(encoding="utf-8")
    assert "x-TMXRepair-Confidence\">MEDIUM<" in content
    assert "x-TMXRepair-GeminiVerdict\">OK<" in content

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
