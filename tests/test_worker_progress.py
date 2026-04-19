"""Progress aggregation tests for RepairWorker."""

from __future__ import annotations

from pathlib import Path

from core.plan import RepairPlan
from core.repair import RepairStats
from ui.types import RepairRunConfig
from ui.worker import RepairWorker


def _make_config(input_paths: list[Path]) -> RepairRunConfig:
    return RepairRunConfig(
        input_paths=input_paths,
        output_dir=None,
        dry_run=True,
        enable_split=True,
        enable_split_short_sentence_pair_guard=True,
        enable_cleanup_spaces=True,
        enable_cleanup_service_markup=True,
        enable_cleanup_garbage=True,
        enable_cleanup_warnings=True,
        log_file=None,
        verify_with_gemini=True,
        gemini_api_key="stub",
        gemini_model="gemini-3.1-flash-lite-preview",
        gemini_max_parallel=3,
        gemini_input_price_per_1m=0.10,
        gemini_output_price_per_1m=0.40,
        gemini_prompt_template=None,
        report_dir=None,
        html_report_dir=None,
        xlsx_report_dir=None,
    )


def test_plan_phase_progress_uses_batch_token_totals(monkeypatch):
    input_paths = [Path("file_a.tmx"), Path("file_b.tmx")]
    config = _make_config(input_paths)
    worker = RepairWorker(config=config, phase="plan")

    emitted: list[dict[str, object]] = []
    worker.progress_event.connect(lambda payload: emitted.append(dict(payload)))

    per_file_tokens = [100, 200]
    call_idx = {"value": 0}

    def fake_repair_tmx_file(*, input_path, progress_callback=None, **kwargs):  # noqa: ANN001
        idx = call_idx["value"]
        call_idx["value"] += 1
        tokens = per_file_tokens[idx]
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "file_start",
                    "input_path": str(input_path),
                    "gemini_input_tokens": 0,
                    "gemini_output_tokens": 0,
                    "gemini_total_tokens": 0,
                    "gemini_estimated_cost_usd": 0.0,
                }
            )
            progress_callback(
                {
                    "event": "file_complete",
                    "input_path": str(input_path),
                    "gemini_input_tokens": tokens,
                    "gemini_output_tokens": 0,
                    "gemini_total_tokens": tokens,
                    "gemini_estimated_cost_usd": float(tokens) / 1_000_000.0,
                }
            )
        return RepairStats(
            total_tus=1,
            split_tus=0,
            created_tus=1,
            src_lang="en-US",
            tgt_lang="ru-RU",
            skipped_tus=1,
            gemini_checked=1,
            gemini_rejected=0,
            gemini_input_tokens=tokens,
            gemini_output_tokens=0,
            gemini_total_tokens=tokens,
            gemini_estimated_cost_usd=float(tokens) / 1_000_000.0,
            plan=RepairPlan(input_path=str(input_path), total_tus=1),
        )

    monkeypatch.setattr("ui.worker.repair_tmx_file", fake_repair_tmx_file)
    monkeypatch.setattr("ui.worker.configure_logger", lambda log_file, ui_callback=None: None)
    monkeypatch.setattr("ui.worker.RepairWorker._maybe_build_verifier", lambda self: object())

    worker._run_plan_phase()

    file_complete_events = [e for e in emitted if str(e.get("event", "")) == "file_complete"]
    assert len(file_complete_events) == 2
    assert int(file_complete_events[0]["batch_gemini_total_tokens"]) == 100
    assert int(file_complete_events[1]["batch_gemini_total_tokens"]) == 300
