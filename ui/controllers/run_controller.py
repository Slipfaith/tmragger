"""Run orchestration controller for the TMX repair GUI."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from ui.types import PlanPhaseResult, RepairRunConfig
from ui.worker import RepairWorker


WorkerFactory = Callable[..., RepairWorker]


class RunController(QObject):
    """Owns plan/apply worker orchestration and forwards worker signals."""

    log_message = Signal(str)
    progress_event = Signal(object)
    plans_ready = Signal(object)
    apply_completed = Signal(object)
    failed = Signal(str)
    phase_started = Signal(str)
    phase_finished = Signal(str)
    run_finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: WorkerFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory or RepairWorker
        self._worker: RepairWorker | object | None = None
        self._pending_config: RepairRunConfig | None = None

    def is_running(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        is_running = getattr(worker, "isRunning", None)
        return bool(is_running()) if callable(is_running) else False

    def start_run(self, config: RepairRunConfig) -> bool:
        if self.is_running():
            return False
        self._pending_config = config
        self._start_worker(config=config, phase="plan")
        return True

    def pause(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        request_pause = getattr(worker, "request_pause", None)
        if callable(request_pause):
            request_pause()
            return True
        return False

    def resume(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        request_resume = getattr(worker, "request_resume", None)
        if callable(request_resume):
            request_resume()
            return True
        return False

    def stop(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        request_stop = getattr(worker, "request_stop", None)
        if callable(request_stop):
            request_stop()
            return True
        return False

    def is_paused(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        is_paused = getattr(worker, "is_paused", None)
        return bool(is_paused()) if callable(is_paused) else False

    def start_apply(
        self,
        plans: PlanPhaseResult,
        config: RepairRunConfig | None = None,
    ) -> bool:
        active_config = config or self._pending_config
        if active_config is None:
            self.failed.emit("Internal error: apply config is missing.")
            return False
        self._start_worker(config=active_config, phase="apply", plans=plans)
        return True

    def _start_worker(
        self,
        config: RepairRunConfig,
        phase: str,
        plans: PlanPhaseResult | None = None,
    ) -> None:
        worker = self._worker_factory(config, phase=phase, plans=plans)
        self._worker = worker
        worker.log_message.connect(self.log_message.emit)
        worker.progress_event.connect(self.progress_event.emit)
        worker.plans_ready.connect(self.plans_ready.emit)
        worker.apply_completed.connect(self.apply_completed.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(lambda: self._on_worker_finished(worker, phase))
        self.phase_started.emit(phase)
        worker.start()

    def _on_worker_finished(self, worker: object, phase: str) -> None:
        self.phase_finished.emit(phase)
        if self._worker is not worker:
            return
        self._worker = None
        # Keep pending config after plan phase so apply can be started from
        # review dialog even if the plan worker has already fully finished.
        if phase == "apply":
            self._pending_config = None
        self.run_finished.emit()
