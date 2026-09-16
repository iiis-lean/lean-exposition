from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Callable

from .types import ApiError, ExecutionResult, JobHandle


@dataclass
class _Job:
    result: ExecutionResult
    done: threading.Event
    cancelled: threading.Event
    stop: Callable[[], object] | None = None


class Runtime:
    """A process-local job registry with immutable terminal publication."""

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        worker: Callable[[threading.Event, Callable[[Callable[[], object]], bool]], ExecutionResult],
        *,
        timeout: float,
    ) -> JobHandle:
        handle = JobHandle(uuid.uuid4().hex)
        job = _Job(ExecutionResult(status="queued"), threading.Event(), threading.Event())
        with self._lock:
            self._jobs[handle.job_id] = job
        threading.Thread(
            target=self._run, args=(handle, worker, timeout), daemon=True
        ).start()
        return handle

    def status(self, handle: JobHandle) -> ExecutionResult:
        with self._lock:
            return copy.deepcopy(self._jobs[handle.job_id].result)

    def result(self, handle: JobHandle, timeout: float | None = None) -> ExecutionResult:
        job = self._jobs[handle.job_id]
        if not job.done.wait(timeout):
            raise TimeoutError("job is still active")
        return self.status(handle)

    def cancel(self, handle: JobHandle) -> bool:
        return self._stop(
            handle,
            ExecutionResult(status="cancelled", error=ApiError(kind="cancelled")),
        )

    def _run(self, handle: JobHandle, worker, timeout: float) -> None:
        with self._lock:
            job = self._jobs[handle.job_id]
            if job.done.is_set():
                return
            job.result = replace(job.result, status="running")
        timer = threading.Timer(
            timeout,
            lambda: self._stop(
                handle,
                ExecutionResult(status="failed", error=ApiError(kind="timeout")),
            ),
        )
        timer.daemon = True
        timer.start()
        try:
            result = worker(job.cancelled, lambda stop: self._register_stop(handle, stop))
            self._finish(handle, result)
        except Exception as exc:
            self._finish(
                handle,
                ExecutionResult(
                    status="failed",
                    error=ApiError(
                        kind="internal_error",
                        exception_type=type(exc).__name__,
                        status_code=getattr(exc, "status_code", None),
                    ),
                ),
            )
        finally:
            timer.cancel()

    def _register_stop(self, handle: JobHandle, stop: Callable[[], object]) -> bool:
        with self._lock:
            job = self._jobs[handle.job_id]
            job.stop = stop
            stopped = job.done.is_set()
        if stopped:
            self._safe_stop(stop)
        return not stopped

    def _finish(self, handle: JobHandle, result: ExecutionResult) -> bool:
        with self._lock:
            job = self._jobs[handle.job_id]
            if job.done.is_set():
                return False
            job.result = copy.deepcopy(result)
            job.done.set()
            return True

    def _stop(self, handle: JobHandle, result: ExecutionResult) -> bool:
        with self._lock:
            job = self._jobs[handle.job_id]
            if job.done.is_set():
                return False
            job.cancelled.set()
            job.result = result
            job.done.set()
            stop = job.stop
        if stop is not None:
            threading.Thread(target=self._safe_stop, args=(stop,), daemon=True).start()
        return True

    @staticmethod
    def _safe_stop(stop: Callable[[], object]) -> None:
        try:
            stop()
        except Exception:
            pass
