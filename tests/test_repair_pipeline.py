import json
from pathlib import Path
import threading
import time

from openpyxl import load_workbook

from core.gemini_client import GeminiIssue, GeminiVerificationResult
from core.repair import RepairStats, repair_tmx_file


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_sample_tmx(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <prop type="x-Note">n1</prop>
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee predlozhenie!</seg></tuv>
    </tu>
    <tu creationid="u2">
      <tuv xml:lang="en-US"><seg>Single sentence only</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Tolko odno predlozhenie</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )


def _write_multi_split_tmx(path: Path, count: int = 4) -> None:
    tus: list[str] = []
    for idx in range(1, count + 1):
        tus.append(
            f"""
    <tu creationid="u{idx}">
      <tuv xml:lang="en-US"><seg>Alpha {idx}. Beta {idx}!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Alfa {idx}. Beta {idx}!</seg></tuv>
    </tu>"""
        )
    body = "".join(tus)
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>{body}
  </body>
</tmx>
""",
        encoding="utf-8",
    )


def test_repair_tmx_file_splits_aligned_tu():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input.tmx"
    output_path = runtime_dir / "output.tmx"
    html_report_path = runtime_dir / "report.html"
    xlsx_report_path = runtime_dir / "report.xlsx"
    _write_sample_tmx(input_path)

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split_short_sentence_pair_guard=False,
        html_report_path=html_report_path,
        xlsx_report_path=xlsx_report_path,
    )

    assert isinstance(stats, RepairStats)
    assert stats.total_tus == 2
    assert stats.split_tus == 1
    assert stats.created_tus == 3
    assert stats.high_confidence_splits == 1
    assert stats.medium_confidence_splits == 0
    assert stats.gemini_checked == 0
    assert stats.gemini_rejected == 0

    content = _read(output_path)
    assert content.count("<tu ") == 3
    assert "Hello world." in content
    assert "Next sentence!" in content
    assert "Single sentence only" in content
    assert '<prop type="x-TMXRepair-Confidence">HIGH</prop>' in content
    assert html_report_path.exists()
    assert xlsx_report_path.exists()
    html_content = _read(html_report_path)
    assert "TMX Repair Diff Report" in html_content
    assert "Hello world. Next sentence!" in html_content
    assert "Next sentence!" in html_content

    workbook = load_workbook(xlsx_report_path)
    assert workbook.sheetnames == [
        "Summary",
        "Split Changes",
        "Auto Cleanup",
        "Warnings",
        "Gemini Checks",
    ]
    assert workbook["Summary"]["A2"].value == "Input TMX"
    workbook.close()

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    html_report_path.unlink(missing_ok=True)
    xlsx_report_path.unlink(missing_ok=True)


def test_repair_with_gemini_fail_rejects_split():
    class AlwaysFailVerifier:
        def verify_split(self, request):
            return GeminiVerificationResult(
                verdict="FAIL",
                issues=[
                    GeminiIssue(
                        severity="high",
                        issue_type="alignment",
                        message="bad mapping",
                        src_index=0,
                        tgt_index=0,
                        suggestion="keep original tu",
                    )
                ],
                summary="rejected",
            )

    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_fail.tmx"
    output_path = runtime_dir / "output_fail.tmx"
    report_path = runtime_dir / "report_fail.json"
    _write_sample_tmx(input_path)

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        verify_with_gemini=True,
        gemini_verifier=AlwaysFailVerifier(),
        enable_split_short_sentence_pair_guard=False,
        report_path=report_path,
    )

    assert stats.split_tus == 0
    assert stats.created_tus == 2
    assert stats.gemini_checked == 1
    assert stats.gemini_rejected == 1
    assert stats.medium_confidence_splits == 0

    content = _read(output_path)
    assert content.count("<tu ") == 2
    assert '<prop type="x-TMXRepair-Confidence">' not in content

    report = json.loads(_read(report_path))
    assert report["gemini_checked"] == 1
    assert report["gemini_rejected"] == 1
    assert report["items"][0]["verdict"] == "FAIL"

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)


def test_repair_with_gemini_ok_marks_medium_confidence():
    class AlwaysOkVerifier:
        def verify_split(self, request):
            return GeminiVerificationResult(
                verdict="OK",
                issues=[],
                summary="looks good",
            )

    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_ok.tmx"
    output_path = runtime_dir / "output_ok.tmx"
    report_path = runtime_dir / "report_ok.json"
    _write_sample_tmx(input_path)

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        verify_with_gemini=True,
        gemini_verifier=AlwaysOkVerifier(),
        enable_split_short_sentence_pair_guard=False,
        report_path=report_path,
    )

    assert stats.split_tus == 1
    assert stats.created_tus == 3
    assert stats.gemini_checked == 1
    assert stats.gemini_rejected == 0
    assert stats.high_confidence_splits == 0
    assert stats.medium_confidence_splits == 1

    content = _read(output_path)
    assert content.count("<tu ") == 3
    assert '<prop type="x-TMXRepair-Confidence">MEDIUM</prop>' in content

    report = json.loads(_read(report_path))
    assert report["items"][0]["verdict"] == "OK"

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)


def test_repair_passes_custom_prompt_template_to_verifier():
    class CapturePromptVerifier:
        def __init__(self) -> None:
            self.captured_prompt_template = None

        def verify_split(self, request, prompt_template=None):
            self.captured_prompt_template = prompt_template
            return GeminiVerificationResult(
                verdict="OK",
                issues=[],
                summary="ok",
            )

    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_prompt.tmx"
    output_path = runtime_dir / "output_prompt.tmx"
    _write_sample_tmx(input_path)

    verifier = CapturePromptVerifier()
    custom_prompt = "CUSTOM PROMPT TEMPLATE {SRC_LANG} -> {TGT_LANG}"
    repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        verify_with_gemini=True,
        gemini_verifier=verifier,
        enable_split_short_sentence_pair_guard=False,
        gemini_prompt_template=custom_prompt,
    )

    assert verifier.captured_prompt_template == custom_prompt

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_repair_emits_progress_and_token_usage():
    class UsageVerifier:
        def verify_split(self, request):
            return GeminiVerificationResult(
                verdict="OK",
                issues=[],
                summary="ok",
                prompt_tokens=111,
                completion_tokens=22,
                total_tokens=133,
            )

    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_usage.tmx"
    output_path = runtime_dir / "output_usage.tmx"
    report_path = runtime_dir / "report_usage.json"
    _write_sample_tmx(input_path)

    progress_events: list[dict[str, object]] = []
    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        verify_with_gemini=True,
        gemini_verifier=UsageVerifier(),
        enable_split_short_sentence_pair_guard=False,
        report_path=report_path,
        progress_callback=lambda payload: progress_events.append(dict(payload)),
    )

    assert stats.gemini_input_tokens == 111
    assert stats.gemini_output_tokens == 22
    assert stats.gemini_total_tokens == 133
    assert stats.gemini_estimated_cost_usd > 0

    event_names = {str(event.get("event", "")) for event in progress_events}
    assert "file_start" in event_names
    assert "tu_start" in event_names
    assert "gemini_result" in event_names
    assert "file_complete" in event_names

    report = json.loads(_read(report_path))
    assert report["gemini_input_tokens"] == 111
    assert report["gemini_output_tokens"] == 22
    assert report["gemini_total_tokens"] == 133
    assert report["gemini_estimated_cost_usd"] > 0

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)


def test_repair_runs_gemini_verification_in_parallel_when_enabled():
    class SlowVerifier:
        def __init__(self) -> None:
            self.calls = 0
            self.in_flight = 0
            self.max_in_flight = 0
            self._lock = threading.Lock()

        def verify_split(self, request, prompt_template=None):  # noqa: ANN001
            with self._lock:
                self.calls += 1
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                time.sleep(0.06)
                return GeminiVerificationResult(
                    verdict="OK",
                    issues=[],
                    summary="ok",
                )
            finally:
                with self._lock:
                    self.in_flight -= 1

    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_parallel.tmx"
    output_path = runtime_dir / "output_parallel.tmx"
    _write_multi_split_tmx(input_path, count=4)

    verifier = SlowVerifier()
    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        verify_with_gemini=True,
        gemini_verifier=verifier,
        gemini_max_parallel=3,
        enable_split_short_sentence_pair_guard=False,
    )

    assert stats.gemini_checked == 4
    assert verifier.calls == 4
    assert verifier.max_in_flight >= 2
    assert stats.split_tus == 4
    assert stats.created_tus == 8

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_html_report_contains_interactive_tabs_for_cleanup_and_warnings():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_tabs.tmx"
    output_path = runtime_dir / "output_tabs.tmx"
    html_report_path = runtime_dir / "report_tabs.html"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>  Hello\u00A0 world  </seg></tuv>
      <tuv xml:lang="ru-RU"><seg>  Привет\u00A0 мир  </seg></tuv>
    </tu>
    <tu creationid="u2">
      <tuv xml:lang="en-US"><seg>Need translation now.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>!!!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        html_report_path=html_report_path,
    )

    html_content = _read(html_report_path)
    assert "tab-button" in html_content
    assert "Split Changes" in html_content
    assert "Auto Cleanup" in html_content
    assert "Warnings" in html_content
    assert "Gemini Checks" in html_content
    assert "diff-add" in html_content or "diff-del" in html_content
    assert "Legend: changed regular spaces are marked as ·" in html_content
    assert "Whitespace delta:" in html_content

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    html_report_path.unlink(missing_ok=True)


def test_repair_can_disable_split_stage():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_no_split.tmx"
    output_path = runtime_dir / "output_no_split.tmx"
    _write_sample_tmx(input_path)

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split=False,
    )

    assert stats.split_tus == 0
    assert stats.created_tus == 2
    content = _read(output_path)
    assert content.count("<tu ") == 2
    assert "Hello world. Next sentence!" in content

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_repair_matches_srclang_primary_language_tag():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_srclang_primary.tmx"
    output_path = runtime_dir / "output_srclang_primary.tmx"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Привет мир. Следующее предложение!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split_short_sentence_pair_guard=False,
    )

    assert stats.split_tus == 1
    assert stats.created_tus == 2
    content = _read(output_path)
    assert content.count("<tu ") == 2

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_repair_falls_back_to_first_tuv_when_srclang_missing():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_srclang_missing.tmx"
    output_path = runtime_dir / "output_srclang_missing.tmx"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="ru-RU"><seg>Привет мир. Следующее предложение!</seg></tuv>
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split_short_sentence_pair_guard=False,
    )

    assert stats.split_tus == 1
    assert stats.created_tus == 2
    content = _read(output_path)
    assert content.count("<tu ") == 2

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_repair_can_remove_inline_tags_without_gluing_text():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_tags_cleanup.tmx"
    output_path = runtime_dir / "output_tags_cleanup.tmx"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Hello!<ph x="1" type="0"/>World.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Привет!<ph x="1" type="0"/>Мир.</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split=False,
        enable_cleanup_tag_removal=True,
    )

    assert stats.created_tus == 1
    content = _read(output_path)
    assert "<ph " not in content
    assert "Hello! World." in content
    assert "Привет! Мир." in content

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_repair_cleans_service_markup_in_context_content_prop():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_context_cleanup.tmx"
    output_path = runtime_dir / "output_context_cleanup.tmx"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <prop type="x-ContextContent">Increases Health by ^{85 221 85}^%param1%%^{/color}^ and &lt;Color=#51D052FF&gt;%param2%%&lt;/Color&gt; with $m(s|s).</prop>
      <tuv xml:lang="en-US"><seg>Plain text.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Обычный текст.</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    stats = repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split=False,
        enable_cleanup_percent_wrapped=True,
        enable_cleanup_game_markup=True,
    )

    assert stats.created_tus == 1
    content = _read(output_path)
    assert '<prop type="x-ContextContent">' in content
    assert "^{85 221 85}^" not in content
    assert "^{/color}^" not in content
    assert "&lt;Color=" not in content
    assert "&lt;/Color&gt;" not in content
    assert "%param1%" not in content
    assert "%param2%" not in content
    assert "$m(" not in content

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)


def test_html_cleanup_tab_shows_final_diff_and_intermediate_steps():
    runtime_dir = Path("tests") / "fixtures" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    input_path = runtime_dir / "input_cleanup_aggregate.tmx"
    output_path = runtime_dir / "output_cleanup_aggregate.tmx"
    html_report_path = runtime_dir / "report_cleanup_aggregate.html"
    input_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Damage: ^{221 85 85}^%paramFloor%^{/color}^ ^{237 194 154}^(depends on the Totem power)^{/color}^</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Урон: ^{221 85 85}^%paramFloor%^{/color}^ ^{237 194 154}^(зависит от силы тотема)^{/color}^</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    repair_tmx_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=False,
        enable_split=False,
        enable_cleanup_percent_wrapped=True,
        enable_cleanup_game_markup=True,
        html_report_path=html_report_path,
    )

    html_content = _read(html_report_path)
    assert "Final cleanup result for TU (aggregated from 2 steps)." in html_content
    assert "Intermediate steps (2)" in html_content
    assert "depends on the Totem power" in html_content

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    html_report_path.unlink(missing_ok=True)
