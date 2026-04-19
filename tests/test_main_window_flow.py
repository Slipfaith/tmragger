"""GUI flow regression tests for MainWindow/controller integration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from core.plan import Proposal, RepairPlan, make_split_proposal_id
from core.repair import RepairStats
from ui.main_window import MainWindow
from ui.types import FilePlanResult, PlanPhaseResult, RepairRunConfig


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _make_plans() -> PlanPhaseResult:
    plan = RepairPlan(
        input_path="in.tmx",
        total_tus=1,
        proposals=[
            Proposal(
                proposal_id=make_split_proposal_id(0),
                kind="split",
                tu_index=0,
                confidence="HIGH",
                src_parts=["Hello."],
                tgt_parts=["Privet."],
                original_src="Hello.",
                original_tgt="Privet.",
            )
        ],
    )
    return PlanPhaseResult(
        files=[
            FilePlanResult(
                input_path=Path("in.tmx"),
                output_path=Path("out.tmx"),
                report_path=None,
                html_report_path=Path("out.html"),
                xlsx_report_path=Path("out.xlsx"),
                stats=RepairStats(
                    total_tus=1,
                    split_tus=0,
                    created_tus=1,
                    src_lang="en-US",
                    tgt_lang="ru-RU",
                    skipped_tus=0,
                ),
                plan=plan,
            )
        ]
    )


def test_run_repair_delegates_plan_start_to_controller(qapp):
    window = MainWindow()
    input_path = Path("tests") / "_run_controller_input.tmx"
    input_path.write_text("<tmx />", encoding="utf-8")
    captured: dict[str, object] = {"is_running": False}

    class _FakeController:
        def is_running(self) -> bool:
            return bool(captured["is_running"])

        def start_run(self, config) -> bool:  # type: ignore[no-untyped-def]
            captured["config"] = config
            captured["is_running"] = True
            return True

    window._run_controller = _FakeController()
    window.files_panel.set_input_paths([input_path])
    window.stages_panel.enable_gemini_verification_checkbox.setChecked(True)
    window._gemini_api_key_override = "test-api-key"
    window.prompt_editor.setPlainText("CUSTOM_PROMPT_X")

    try:
        window._run_repair()

        config = captured.get("config")
        assert isinstance(config, RepairRunConfig)
        assert config.input_paths == [input_path]
        assert config.enable_split is True
        assert config.verify_with_gemini is True
        assert config.gemini_prompt_template == "CUSTOM_PROMPT_X"
        assert window.run_btn.isEnabled() is False
    finally:
        window.close()
        if input_path.exists():
            input_path.unlink()


def test_on_plans_ready_uses_controller_apply_after_dialog(monkeypatch, qapp):
    window = MainWindow()
    plans = _make_plans()
    captured: dict[str, object] = {}

    class _FakeController:
        def start_apply(self, payload) -> bool:  # type: ignore[no-untyped-def]
            captured["plans"] = payload
            return True

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, _plans: object, parent: object | None = None) -> None:
            captured["dialog_parent"] = parent

        def exec(self) -> int:
            return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr("ui.main_window.ReviewDialog", _FakeDialog)
    window._run_controller = _FakeController()

    window._on_plans_ready(plans)

    assert captured.get("plans") is plans
    assert captured.get("dialog_parent") is window
    window.close()


def test_progress_label_updates_on_file_events(qapp):
    window = MainWindow()

    try:
        window._on_progress_event(
            {
                "event": "file_start",
                "file_index": 1,
                "file_total": 3,
                "input_path": "a.tmx",
            }
        )
        progress_text = window.status_panel.progress_text()
        assert "1/3" in progress_text
        assert "(a.tmx)" in progress_text
        assert "tok:" in window.status_strip_label.text()

        window._on_progress_event(
            {
                "event": "file_complete",
                "file_index": 1,
                "file_total": 3,
                "input_path": "a.tmx",
            }
        )
        complete_text = window.status_panel.progress_text()
        assert "1/3" in complete_text
        assert "tok:" in window.status_strip_label.text()
    finally:
        window.close()


