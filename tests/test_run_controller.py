"""RunController unit tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.plan import Proposal, RepairPlan, make_split_proposal_id
from core.repair import RepairStats
from ui.controllers.run_controller import RunController
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
        enable_split_short_sentence_pair_guard=True,
        enable_cleanup_spaces=True,
        enable_cleanup_service_markup=True,
        enable_cleanup_garbage=True,
        enable_cleanup_warnings=True,
        enable_dedup_tus=False,
        log_file=None,
        verify_with_gemini=False,
        gemini_api_key="",
        gemini_model="gemini-3.1-flash-lite-preview",
        gemini_max_parallel=4,
        max_gemini_checks=1200,
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


class _FakeSignal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot):  # type: ignore[no-untyped-def]
        self._slots.append(slot)

    def emit(self, *args):  # type: ignore[no-untyped-def]
        for slot in list(self._slots):
            slot(*args)


class _FakeWorker:
    def __init__(self, config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
        self.config = config
        self.phase = phase
        self.plans = plans
        self.started = False
        self.running = False
        self.log_message = _FakeSignal()
        self.progress_event = _FakeSignal()
        self.plans_ready = _FakeSignal()
        self.apply_completed = _FakeSignal()
        self.failed = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self) -> None:
        self.started = True
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


def test_start_run_creates_plan_worker_and_emits_phase_started(qapp):
    workers: list[_FakeWorker] = []

    def make_worker(config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
        worker = _FakeWorker(config, phase=phase, plans=plans)
        workers.append(worker)
        return worker

    controller = RunController(worker_factory=make_worker)
    started_phases: list[str] = []
    controller.phase_started.connect(started_phases.append)

    config = _make_config()
    assert controller.start_run(config) is True

    assert len(workers) == 1
    assert workers[0].config is config
    assert workers[0].phase == "plan"
    assert workers[0].started is True
    assert started_phases == ["plan"]
    assert controller.is_running() is True


def test_start_apply_reuses_pending_config_until_apply_worker_finishes(qapp):
    workers: list[_FakeWorker] = []

    def make_worker(config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
        worker = _FakeWorker(config, phase=phase, plans=plans)
        workers.append(worker)
        return worker

    controller = RunController(worker_factory=make_worker)
    run_finished_calls: list[str] = []
    controller.run_finished.connect(lambda: run_finished_calls.append("finished"))

    config = _make_config()
    plans = _make_plans()

    assert controller.start_run(config) is True
    plan_worker = workers[0]

    assert controller.start_apply(plans) is True

    assert len(workers) == 2
    assert workers[1].phase == "apply"
    assert workers[1].config is config
    assert workers[1].plans is plans

    plan_worker.finish()
    assert run_finished_calls == []
    assert controller.is_running() is True

    workers[1].finish()
    assert run_finished_calls == ["finished"]
    assert controller.is_running() is False


def test_start_apply_without_plan_config_emits_failed(qapp):
    controller = RunController(worker_factory=_FakeWorker)
    failures: list[str] = []
    controller.failed.connect(failures.append)

    assert controller.start_apply(_make_plans()) is False

    assert failures == ["Internal error: apply config is missing."]


def test_start_apply_allows_explicit_config_without_plan_phase(qapp):
    workers: list[_FakeWorker] = []

    def make_worker(config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
        worker = _FakeWorker(config, phase=phase, plans=plans)
        workers.append(worker)
        return worker

    controller = RunController(worker_factory=make_worker)
    config = _make_config()
    plans = _make_plans()

    assert controller.start_apply(plans, config=config) is True
    assert len(workers) == 1
    assert workers[0].phase == "apply"
    assert workers[0].config is config
    assert workers[0].plans is plans


def test_start_apply_still_works_after_plan_worker_finished(qapp):
    workers: list[_FakeWorker] = []

    def make_worker(config, phase="plan", plans=None):  # type: ignore[no-untyped-def]
        worker = _FakeWorker(config, phase=phase, plans=plans)
        workers.append(worker)
        return worker

    controller = RunController(worker_factory=make_worker)
    run_finished_calls: list[str] = []
    controller.run_finished.connect(lambda: run_finished_calls.append("finished"))

    config = _make_config()
    plans = _make_plans()

    assert controller.start_run(config) is True
    assert len(workers) == 1

    # Simulate real GUI timing: plan worker already finished before Apply click.
    workers[0].finish()
    assert run_finished_calls == ["finished"]
    assert controller.is_running() is False

    assert controller.start_apply(plans) is True
    assert len(workers) == 2
    assert workers[1].phase == "apply"
    assert workers[1].config is config
    assert workers[1].plans is plans

    workers[1].finish()
    assert run_finished_calls == ["finished", "finished"]
