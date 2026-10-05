"""Batch split verification: queue, one call per chunk, verbatim-only fixes."""

from __future__ import annotations

import json
from pathlib import Path

from core.codex_client import parse_codex_batch_jsonl, parts_preserve_text
from core.verification import VerificationRequest, VerificationResult
from core.repair import repair_tmx_file


def _write_tmx(path: Path) -> None:
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
      <tuv xml:lang="en-US"><seg>Alpha one. Beta two. Gamma three.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Alfa raz. Beta dva. Gamma tri.</seg></tuv>
    </tu>
    <tu creationid="u3">
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee predlozhenie!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )


class _BatchVerifier:
    """Approves u1, re-cuts u2 into two parts; never called per TU."""

    batch_size = 10

    def __init__(self) -> None:
        self.batches: list[list[VerificationRequest]] = []

    def verify_split(self, _request, prompt_template=None):  # noqa: ANN001
        raise AssertionError("batch mode must not verify per TU")

    def verify_batch(self, requests):  # noqa: ANN001
        self.batches.append(list(requests))
        results = []
        for req in requests:
            if req.original_src.startswith("Alpha"):
                results.append(
                    VerificationResult(
                        verdict="WARN",
                        issues=[],
                        summary="Split fixed by Codex.",
                        prompt_tokens=10,
                        completion_tokens=1,
                        total_tokens=11,
                        # Separator whitespace inside parts must not reach the segments.
                        fixed_src_parts=["Alpha one. Beta two. ", "Gamma three."],
                        fixed_tgt_parts=["Alfa raz. Beta dva.", " Gamma tri."],
                    )
                )
            else:
                results.append(
                    VerificationResult(
                        verdict="OK", issues=[], summary="ok", prompt_tokens=10, completion_tokens=1, total_tokens=11
                    )
                )
        return results


def test_parts_preserve_text_accepts_only_verbatim_recuts():
    original = "Rufen Sie Dr. Smith an. Er wartet."
    assert parts_preserve_text(original, ["Rufen Sie Dr. Smith an.", "Er wartet."])
    # Rewritten target text is refused.
    assert not parts_preserve_text(original, ["Rufen Sie Dr. Smith an.", "Er wartet jetzt."])
    # Dropped text is refused.
    assert not parts_preserve_text(original, ["Rufen Sie Dr. Smith an."])
    # Reordered parts are refused.
    assert not parts_preserve_text(original, ["Er wartet.", "Rufen Sie Dr. Smith an."])
    # A cut inside a tag is refused.
    tagged = 'Click <ph x="1"/> now. Then wait.'
    assert parts_preserve_text(tagged, ['Click <ph x="1"/> now.', "Then wait."])
    assert not parts_preserve_text(tagged, ["Click <ph", 'x="1"/> now. Then wait.'])


def test_parse_batch_keeps_split_when_codex_only_touched_punctuation():
    req = VerificationRequest(
        "en",
        "de",
        "Shape the future! Create designs.",
        "Gestalte die Zukunft. Entwirf Designs.",
        ["Shape the future!", "Create designs."],
        ["Gestalte die Zukunft.", "Entwirf Designs."],
    )
    answer = {
        "items": [
            {
                "id": 0,
                "verdict": "FIX",
                "src_parts": ["Shape the future!", "Create designs."],
                "tgt_parts": ["Gestalte die Zukunft!", "Entwirf Designs."],
                "reason": "punctuation",
            }
        ]
    }
    stdout = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}})

    [result] = parse_codex_batch_jsonl(stdout, [req])

    assert result.verdict == "OK"
    # The "!" edit is discarded: no fixed parts, the original target text is used.
    assert result.fixed_tgt_parts is None


def test_splitter_keeps_ordinal_dates_together():
    from core.splitter import split_inner_xml_into_sentences

    assert split_inner_xml_into_sentences("Ab dem 9. Juni 2026 gilt das. Danke.") == [
        "Ab dem 9. Juni 2026 gilt das.",
        "Danke.",
    ]
    assert split_inner_xml_into_sentences("Level 9. Next step.") == ["Level 9.", "Next step."]


def test_parse_batch_rejects_fix_that_rewrites_translation():
    requests = [
        VerificationRequest("en", "de", "A b. C d.", "E f. G h.", ["A b.", "C d."], ["E f.", "G h."]),
        VerificationRequest("en", "de", "A b. C d.", "E f. G h.", ["A b.", "C d."], ["E f.", "G h."]),
    ]
    answer = {
        "items": [
            {"id": 0, "verdict": "FIX", "src_parts": ["A b.", "C d."], "tgt_parts": ["E f.", "G hh."], "reason": "x"},
        ]
    }
    stdout = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
            {"type": "turn.completed", "usage": {"input_tokens": 101, "output_tokens": 9}},
        )
    )

    results = parse_codex_batch_jsonl(stdout, requests)

    assert results[0].verdict == "FAIL"
    assert results[0].fixed_tgt_parts is None
    # Missing item is "unavailable", not a rejection.
    assert "request failed" in results[1].issues[0].message
    assert sum(r.prompt_tokens for r in results) == 101
    assert sum(r.completion_tokens for r in results) == 9


def test_plan_queues_candidates_and_apply_uses_fixed_cut_points(tmp_path):
    inp = tmp_path / "in.tmx"
    out = tmp_path / "out.tmx"
    _write_tmx(inp)
    verifier = _BatchVerifier()

    plan_stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="plan",
        verify_splits=True,
        verifier=verifier,
        verification_max_parallel=4,
        # GUI passes a resume path in plan mode; batching must still be used.
        resume_state_path=tmp_path / "in.resume.json",
        enable_split_short_sentence_pair_guard=False,
    )

    assert len(verifier.batches) == 1
    # u1 and u3 are identical, so only two unique candidates are sent.
    assert len(verifier.batches[0]) == 2
    assert plan_stats.verification_checked == 3
    assert plan_stats.verification_input_tokens == 20
    plan = plan_stats.plan
    assert plan is not None
    splits = [p for p in plan.proposals if p.kind == "split"]
    assert [p.tu_index for p in splits] == [0, 1, 2]
    fixed = splits[1]
    assert fixed.src_parts == ["Alpha one. Beta two.", "Gamma three."]
    assert fixed.fixed_tgt_parts == ["Alfa raz. Beta dva.", "Gamma tri."]

    repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="apply",
        verify_splits=False,
        verifier=verifier,
        accepted_split_ids=plan.accepted_split_ids(),
        preverified_split_verdict_by_id={p.proposal_id: p.verification_verdict for p in splits},
        preverified_split_parts_by_id={
            p.proposal_id: (p.fixed_src_parts, p.fixed_tgt_parts) for p in splits if p.fixed_src_parts
        },
        enable_split_short_sentence_pair_guard=False,
    )

    content = out.read_text(encoding="utf-8")
    assert "<seg>Alfa raz. Beta dva.</seg>" in content
    assert "<seg>Gamma tri.</seg>" in content
    assert "<seg>Privet mir.</seg>" in content
    assert len(verifier.batches) == 1, "apply must not call the verifier again"


def test_tmrepair_package_round_trip_keeps_codex_fixed_cut_points(tmp_path):
    import zipfile

    from core.offline_package import export_tmrepair_package, import_tmrepair_package

    inp = tmp_path / "in.tmx"
    out = tmp_path / "out.tmx"
    _write_tmx(inp)
    plan = repair_tmx_file(
        input_path=inp,
        output_path=out,
        mode="plan",
        verify_splits=True,
        verifier=_BatchVerifier(),
        enable_split_short_sentence_pair_guard=False,
    ).plan

    package = tmp_path / "in.tmrepair"
    export_tmrepair_package(package_path=package, input_tmx_path=inp, plan=plan, settings={"enable_split": True})
    decisions = [{"id": p.proposal_id, "decision": "accept"} for p in plan.proposals if p.kind == "split"]
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("decisions.json", json.dumps({"decisions": decisions}))

    # Same call the GUI makes after importing a reviewed package.
    result = import_tmrepair_package(package_path=package)
    repair_tmx_file(
        input_path=result.source_tmx_path,
        output_path=out,
        mode="apply",
        verify_splits=False,
        accepted_split_ids=result.plan.accepted_split_ids(),
        accepted_cleanup_ids=result.plan.accepted_cleanup_ids(),
        enable_split_short_sentence_pair_guard=False,
        **result.plan.preverified_split_kwargs(),
    )
    result.source_tmx_path.unlink(missing_ok=True)

    content = out.read_text(encoding="utf-8")
    assert "<seg>Alfa raz. Beta dva.</seg>" in content
    assert "<seg>Gamma tri.</seg>" in content
    assert 'x-TMXRepair-VerificationVerdict">WARN<' in content
