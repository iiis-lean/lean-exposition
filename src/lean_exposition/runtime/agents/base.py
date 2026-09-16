from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from .types import (
    AgentControlResult,
    AgentHandle,
    AgentResult,
    AgentSession,
)


class AgentExecutor(ABC):
    """Common lifecycle for stateful Codex and Pi turns."""

    @abstractmethod
    def start(
        self, prompt: str, output_schema: Mapping[str, Any] | None = None
    ) -> AgentHandle:
        raise NotImplementedError

    @abstractmethod
    def resume(
        self,
        session: AgentSession,
        prompt: str,
        output_schema: Mapping[str, Any] | None = None,
    ) -> AgentHandle:
        raise NotImplementedError

    @abstractmethod
    def follow_up(self, handle: AgentHandle, message: str) -> AgentControlResult:
        raise NotImplementedError

    @abstractmethod
    def steer(self, handle: AgentHandle, message: str) -> AgentControlResult:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, handle: AgentHandle) -> AgentControlResult:
        raise NotImplementedError

    @abstractmethod
    def status(self, handle: AgentHandle) -> AgentResult:
        raise NotImplementedError

    @abstractmethod
    def result(self, handle: AgentHandle, timeout: float | None = None) -> AgentResult:
        raise NotImplementedError
