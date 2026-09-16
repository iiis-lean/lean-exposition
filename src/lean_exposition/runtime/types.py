from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ExecutionStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class ApiError:
    """A provider-safe failure record that never includes exception text."""

    kind: str
    exception_type: str | None = None
    status_code: int | None = None
    provider_code: str | None = None


@dataclass(frozen=True)
class ApiUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reports: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ToolEvent:
    step: int
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    data: Any = None
    raw_text: str | None = None
    error: ApiError | None = None
    provider_status: str | None = None
    finish_reason: str | None = None
    incomplete_details: dict[str, Any] | None = None
    usage: ApiUsage = field(default_factory=ApiUsage)
    tool_events: tuple[ToolEvent, ...] = ()
    requested_model: str | None = None
    response_model: str | None = None
    protocol: str | None = None
    response_id: str | None = None
    trace_label: str | None = None
    input_digest: str | None = None


@dataclass(frozen=True)
class JobHandle:
    job_id: str


class RuntimeFailure(RuntimeError):
    def __init__(self, result: ExecutionResult):
        self.result = result
        kind = result.error.kind if result.error else result.status
        super().__init__(kind)
