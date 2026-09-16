from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ._jobs import ThreadedAgentJobs
from ._schema import parse_output
from .base import AgentExecutor
from .config import PiAgentConfig
from .pi_rpc import PiRpcProcess
from .types import (
    AgentControlResult,
    AgentError,
    AgentHandle,
    AgentResult,
    AgentSession,
    AgentToolEvent,
    AgentUsage,
)


class _PiController:
    def __init__(self, rpc: PiRpcProcess) -> None:
        self.rpc = rpc

    def steer(self, message: str) -> None:
        self.rpc.command("steer", {"message": message})

    def follow_up(self, message: str) -> None:
        self.rpc.command("follow_up", {"message": message})

    def cancel(self) -> None:
        self.rpc.command("abort")


class PiAgentExecutor(AgentExecutor):
    """Direct Pi RPC adapter with no dependency on harness-delegation."""

    def __init__(self, config: PiAgentConfig, *, rpc_factory=PiRpcProcess) -> None:
        self.config = config
        self._rpc_factory = rpc_factory
        self._jobs = ThreadedAgentJobs()

    def start(self, prompt: str, output_schema: Mapping[str, Any] | None = None) -> AgentHandle:
        return self._submit(prompt, output_schema, None)

    def resume(
        self,
        session: AgentSession,
        prompt: str,
        output_schema: Mapping[str, Any] | None = None,
    ) -> AgentHandle:
        if session.backend != "pi":
            raise ValueError("Pi can only resume a Pi session")
        session_file = session.locator.get("session_file")
        if not session_file:
            raise ValueError("Pi session is missing session_file")
        resolved = Path(session_file).resolve()
        session_root = Path(self.config.session_dir).resolve()
        if not resolved.is_relative_to(session_root):
            raise ValueError("Pi session_file is outside the configured session directory")
        return self._submit(prompt, output_schema, resolved)

    def follow_up(self, handle: AgentHandle, message: str) -> AgentControlResult:
        return self._jobs.control(handle, "follow_up", message)

    def steer(self, handle: AgentHandle, message: str) -> AgentControlResult:
        return self._jobs.control(handle, "steer", message)

    def cancel(self, handle: AgentHandle) -> AgentControlResult:
        return self._jobs.control(handle, "cancel")

    def status(self, handle: AgentHandle) -> AgentResult:
        return self._jobs.status(handle)

    def result(self, handle: AgentHandle, timeout: float | None = None) -> AgentResult:
        return self._jobs.result(handle, timeout)

    def _submit(self, prompt, output_schema, session_file):
        schema = dict(output_schema) if output_schema is not None else None
        return self._jobs.submit(
            lambda register: self._run(prompt, schema, session_file, register)
        )

    def _run(self, prompt, schema, session_file, register) -> AgentResult:
        rpc: PiRpcProcess | None = None
        tool_events: list[AgentToolEvent] = []
        try:
            command = self._command(session_file)
            environment = self._environment()
            rpc = self._rpc_factory(command, cwd=Path(self.config.cwd), env=environment)
            initial_state = rpc.command("get_state")
            initial_messages = rpc.command("get_messages").get("messages") or []
            session = _pi_session(initial_state, self.config.cwd)
            register(_PiController(rpc))
            rpc.command("prompt", {"message": prompt})

            current_assistant: dict[str, Any] | None = None

            def observe(record: dict[str, object]) -> None:
                nonlocal current_assistant
                kind = record.get("type")
                if kind == "message_end" and isinstance(record.get("message"), dict):
                    message = record["message"]
                    if message.get("role") == "assistant":
                        current_assistant = message
                elif kind == "auto_retry_start":
                    rpc.command("abort_retry")
                elif kind == "tool_execution_start":
                    tool_events.append(
                        AgentToolEvent(
                            str(record.get("toolName") or "unknown"),
                            str(record.get("toolCallId")) if record.get("toolCallId") else None,
                        )
                    )
                elif kind == "tool_execution_end":
                    tool_events.append(
                        AgentToolEvent(
                            str(record.get("toolName") or "unknown"),
                            str(record.get("toolCallId")) if record.get("toolCallId") else None,
                            "finished",
                        )
                    )

            try:
                rpc.wait_for(
                    lambda item: item.get("type") == "agent_settled",
                    timeout=self.config.timeout,
                    observe=observe,
                )
            except TimeoutError:
                try:
                    rpc.command("abort", timeout=5)
                finally:
                    return AgentResult(
                        status="failed",
                        error=AgentError(kind="timeout", native_source="pi_rpc"),
                        session=session,
                        tool_events=tuple(tool_events),
                    )
            final_state = rpc.command("get_state")
            if final_state.get("isStreaming") or final_state.get("isCompacting"):
                raise RuntimeError("Pi settled while still busy")
            session = _pi_session(final_state, self.config.cwd)
            messages = (rpc.command("get_messages").get("messages") or [])[len(initial_messages):]
            assistant = current_assistant or next(
                (item for item in reversed(messages) if item.get("role") == "assistant"), None
            )
            if assistant is None:
                raise RuntimeError("Pi settled without a current assistant response")
            stop = assistant.get("stopReason")
            stats = rpc.command("get_session_stats")
            usage = _pi_usage(stats)
            if stop == "aborted":
                return AgentResult(
                    status="cancelled", session=session, usage=usage,
                    tool_events=tuple(tool_events), native_stop_reason="aborted"
                )
            if stop == "error" or assistant.get("errorMessage"):
                return AgentResult(
                    status="failed",
                    error=AgentError(kind="native_error", native_source="pi_assistant"),
                    session=session,
                    usage=usage,
                    tool_events=tuple(tool_events),
                    native_stop_reason=str(stop) if stop else None,
                )
            text = _assistant_text(assistant)
            try:
                data = parse_output(text, schema)
            except Exception as exc:
                return AgentResult(
                    status="failed", raw_text=text,
                    error=AgentError(kind="invalid_structured_output", exception_type=type(exc).__name__),
                    session=session, usage=usage, tool_events=tuple(tool_events),
                    native_stop_reason=str(stop) if stop else None,
                )
            return AgentResult(
                status="succeeded", data=data, raw_text=text, session=session,
                usage=usage, tool_events=tuple(tool_events),
                native_stop_reason=str(stop) if stop else None,
            )
        finally:
            if rpc is not None:
                rpc.close()

    def _command(self, session_file: Path | None) -> list[str]:
        executable = shutil.which(self.config.executable) or self.config.executable
        Path(self.config.session_dir).mkdir(parents=True, exist_ok=True)
        command = [
            executable,
            "--mode", "rpc",
            "--provider", self.config.provider,
            "--model", self.config.model,
            "--thinking", self.config.reasoning,
            "--session-dir", self.config.session_dir,
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-context-files", "--no-approve",
        ]
        if self.config.tools:
            command.extend(["--tools", ",".join(self.config.tools)])
        else:
            command.append("--no-tools")
        if self.config.system_prompt:
            command.extend(["--system-prompt", self.config.system_prompt])
        if session_file is not None:
            command.extend(["--session", str(session_file)])
        else:
            command.extend(["--session-id", str(uuid.uuid4())])
        return command

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.config.credential_env:
            value = os.environ.get(self.config.credential_env)
            if not value:
                raise RuntimeError("configured Pi credential is unavailable")
            assert self.config.provider_credential_env is not None
            environment[self.config.provider_credential_env] = value
        return environment


def _pi_session(state: Mapping[str, Any], cwd: str) -> AgentSession:
    session_id = state.get("sessionId")
    session_file = state.get("sessionFile")
    if not session_id or not session_file:
        raise RuntimeError("Pi did not report a recoverable session")
    return AgentSession(
        "pi", str(session_id), {"session_file": str(session_file), "cwd": cwd}
    )


def _assistant_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    )


def _pi_usage(stats: Mapping[str, Any]) -> AgentUsage:
    tokens = stats.get("tokens") if isinstance(stats.get("tokens"), dict) else {}
    input_tokens = _optional_int(tokens.get("input"))
    output_tokens = _optional_int(tokens.get("output"))
    total_tokens = None
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    cost = stats.get("cost")
    return AgentUsage(
        scope="session", input_tokens=input_tokens, output_tokens=output_tokens,
        total_tokens=total_tokens, cost=float(cost) if isinstance(cost, (int, float)) else None,
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
