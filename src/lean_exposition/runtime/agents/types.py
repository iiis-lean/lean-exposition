from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


AgentStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class AgentHandle:
    run_id: str


@dataclass(frozen=True)
class AgentSession:
    backend: Literal["codex", "pi"]
    session_id: str
    locator: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentError:
    kind: str
    exception_type: str | None = None
    native_source: str | None = None


@dataclass(frozen=True)
class AgentUsage:
    scope: Literal["turn", "session", "unknown"] = "unknown"
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: float | None = None


@dataclass(frozen=True)
class AgentToolEvent:
    name: str
    call_id: str | None = None
    phase: Literal["started", "finished"] = "started"


@dataclass(frozen=True)
class AgentResult:
    status: AgentStatus
    data: Any = None
    raw_text: str | None = None
    error: AgentError | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
    tool_events: tuple[AgentToolEvent, ...] = ()
    session: AgentSession | None = None
    native_stop_reason: str | None = None


@dataclass(frozen=True)
class AgentControlResult:
    accepted: bool
    terminal_confirmed: bool = False
    reason: str | None = None
