from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .types import AgentControlResult, AgentError, AgentHandle, AgentResult


class Controller(Protocol):
    def steer(self, message: str) -> None: ...
    def follow_up(self, message: str) -> None: ...
    def cancel(self) -> None: ...


@dataclass
class _Job:
    result: AgentResult = field(default_factory=lambda: AgentResult(status="queued"))
    done: threading.Event = field(default_factory=threading.Event)
    controller: Controller | None = None


class ThreadedAgentJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def submit(self, worker: Callable[[Callable[[Controller], None]], AgentResult]) -> AgentHandle:
        handle = AgentHandle(uuid.uuid4().hex)
        with self._lock:
            self._jobs[handle.run_id] = _Job()
        thread = threading.Thread(target=self._run, args=(handle, worker), daemon=True)
        thread.start()
        return handle

    def status(self, handle: AgentHandle) -> AgentResult:
        with self._lock:
            return copy.deepcopy(self._job(handle).result)

    def result(self, handle: AgentHandle, timeout: float | None = None) -> AgentResult:
        job = self._job(handle)
        if not job.done.wait(timeout):
            raise TimeoutError("agent run is still active")
        return self.status(handle)

    def control(self, handle: AgentHandle, action: str, message: str | None = None) -> AgentControlResult:
        with self._lock:
            job = self._job(handle)
            if job.done.is_set():
                return AgentControlResult(False, True, "agent run is already terminal")
            controller = job.controller
        if controller is None:
            return AgentControlResult(False, False, "agent run has not reached a controllable turn")
        try:
            operation = getattr(controller, action)
            operation(message) if message is not None else operation()
        except Exception as exc:
            return AgentControlResult(False, job.done.is_set(), type(exc).__name__)
        return AgentControlResult(True, job.done.is_set())

    def _run(self, handle: AgentHandle, worker) -> None:
        with self._lock:
            self._job(handle).result = AgentResult(status="running")
        try:
            result = worker(lambda controller: self._register(handle, controller))
        except Exception as exc:
            result = AgentResult(
                status="failed",
                error=AgentError(kind="agent_error", exception_type=type(exc).__name__),
            )
        with self._lock:
            job = self._job(handle)
            job.result = copy.deepcopy(result)
            job.controller = None
            job.done.set()

    def _register(self, handle: AgentHandle, controller: Controller) -> None:
        with self._lock:
            self._job(handle).controller = controller

    def _job(self, handle: AgentHandle) -> _Job:
        try:
            return self._jobs[handle.run_id]
        except KeyError:
            raise KeyError(f"unknown agent run: {handle.run_id}") from None
