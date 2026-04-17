"""GUI flow regression tests for plan/apply transition."""

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


def _make_config() -> RepairRunConfig:
    return RepairRunConfig(
        input_paths=[Path("in.tmx")],
        output_dir=Path("."),
        dry_run=True,
        enable_split=True,
        enable_cleanup_spaces=True,
        enable_cleanup_service_markup=True,
        enable_cleanup_garbage=True,
        enable_cleanup_warnings=True,
        log_file=None,
        verify_with_gemini=False,
        gemini_api_key="",
        gemini_model="gemini-3.1-flash-lite-preview",
        gemini_input_price_per_1m=0.10,
        gemini_output_price_per_1m=0.40,
        gemini_prompt_template=None,
        report_dir=None,
        html_report_dir=Path("."),
        xlsx_report_dir=Path("."),
    )


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
                tgt_parts=["Привет."],
                original_src="Hello.",
                original_tgt="Привет.",
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


def test_on_plans_ready_uses_config_snapshot_before_dialog(monkeypatch, qapp):
    window = MainWindow()
    window._pending_config = _make_config()
    plans = _make_plans()
    captured: dict[str, object] = {}

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, _plans: object, parent: object | None = None) -> None:
            # Simulate the race: pending config gets cleared while review dialog is open.
            if isinstance(parent, MainWindow):
                parent._pending_config = None

        def exec(self) -> int:
            return int(QDialog.DialogCode.Accepted)

    class _FakeSignal:
        def connect(self, _slot):  # type: ignore[no-untyped-def]
            return None

    class _FakeWorker:
        def __init__(self, config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
            captured["config"] = config
            captured["phase"] = phase
            captured["plans"] = plans
            self.log_message = _FakeSignal()
            self.progress_event = _FakeSignal()
            self.apply_completed = _FakeSignal()
            self.failed = _FakeSignal()
            self.finished = _FakeSignal()

        def start(self):  # type: ignore[no-untyped-def]
            captured["started"] = True

    monkeypatch.setattr("ui.main_window.ReviewDialog", _FakeDialog)
    monkeypatch.setattr("ui.main_window.RepairWorker", _FakeWorker)

    expected = window._pending_config
    window._on_plans_ready(plans)

    assert captured.get("phase") == "apply"
    assert captured.get("config") is expected
    assert captured.get("started") is True
    window.close()
