from __future__ import annotations

import importlib
import os
import shutil
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from ._jobs import ThreadedAgentJobs
from ._schema import parse_output
from .base import AgentExecutor
from .config import CodexAgentConfig
from .types import (
    AgentControlResult,
    AgentError,
    AgentHandle,
    AgentResult,
    AgentSession,
    AgentUsage,
)


class CodexSdkUnavailable(RuntimeError):
    pass


class _CodexController:
    def __init__(self, turn: object) -> None:
        self.turn = turn

    def steer(self, message: str) -> None:
        self.turn.steer(message)

    def follow_up(self, message: str) -> None:
        # Codex calls additional input to the active turn "steer". The common
        # follow_up control intentionally has the same active-turn semantics.
        self.turn.steer(message)

    def cancel(self) -> None:
        self.turn.interrupt()


class CodexAgentExecutor(AgentExecutor):
    """Thin stateful adapter around the OpenAI Codex Python SDK."""

    def __init__(
        self,
        config: CodexAgentConfig,
        *,
        sdk_loader: Callable[[], object] | None = None,
    ) -> None:
        self.config = config
        self._sdk_loader = sdk_loader or self._load_sdk
        self._jobs = ThreadedAgentJobs()

    def start(self, prompt: str, output_schema: Mapping[str, Any] | None = None) -> AgentHandle:
        return self._submit(prompt, output_schema, None)

    def resume(
        self,
        session: AgentSession,
        prompt: str,
        output_schema: Mapping[str, Any] | None = None,
    ) -> AgentHandle:
        if session.backend != "codex":
            raise ValueError("Codex can only resume a Codex session")
        return self._submit(prompt, output_schema, session.session_id)

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

    def _submit(self, prompt, output_schema, thread_id):
        schema = dict(output_schema) if output_schema is not None else None
        return self._jobs.submit(
            lambda register: self._run(prompt, schema, thread_id, register)
        )

    def _run(self, prompt, schema, thread_id, register) -> AgentResult:
        sdk = self._sdk_loader()
        environment = dict(os.environ)
        if self.config.codex_home:
            self._prepare_codex_home()
            environment["CODEX_HOME"] = self.config.codex_home
        codex_config = sdk.CodexConfig(
            codex_bin=self.config.codex_bin or shutil.which("codex"),
            cwd=self.config.cwd,
            env=environment,
        )
        with sdk.Codex(config=codex_config) as codex:
            options = {
                "cwd": self.config.cwd,
                "model": self.config.model,
                "developer_instructions": self.config.developer_instructions,
                "config": self._thread_config(),
                "approval_mode": sdk.ApprovalMode.deny_all,
                "sandbox": sdk.Sandbox.read_only,
            }
            thread = (
                codex.thread_resume(thread_id, **options)
                if thread_id is not None
                else codex.thread_start(**options)
            )
            session = AgentSession(
                "codex", str(thread.id), {"cwd": self.config.cwd}
            )
            turn = thread.turn(
                prompt,
                cwd=self.config.cwd,
                effort=self.config.reasoning,
                model=self.config.model,
                output_schema=schema,
                approval_mode=sdk.ApprovalMode.deny_all,
                sandbox=sdk.Sandbox.read_only,
            )
            register(_CodexController(turn))
            deadline_reached = threading.Event()
            def expire() -> None:
                deadline_reached.set()
                try:
                    turn.interrupt()
                except Exception:
                    pass
            timer = threading.Timer(self.config.timeout, expire)
            timer.daemon = True
            timer.start()
            try:
                native = turn.run()
            finally:
                timer.cancel()
        native_status = _enum_value(getattr(native, "status", None))
        usage = _codex_usage(getattr(native, "usage", None))
        text = getattr(native, "final_response", None) or ""
        if deadline_reached.is_set():
            return AgentResult(
                status="failed", raw_text=text or None,
                error=AgentError(kind="timeout", native_source="codex_turn"),
                session=session, usage=usage, native_stop_reason=native_status,
            )
        if native_status == "interrupted":
            return AgentResult(
                status="cancelled", raw_text=text or None, session=session,
                usage=usage, native_stop_reason=native_status,
            )
        if native_status != "completed":
            return AgentResult(
                status="failed", raw_text=text or None,
                error=AgentError(kind="native_error", native_source="codex_turn"),
                session=session, usage=usage, native_stop_reason=native_status,
            )
        try:
            data = parse_output(text, schema)
        except Exception as exc:
            return AgentResult(
                status="failed", raw_text=text,
                error=AgentError(kind="invalid_structured_output", exception_type=type(exc).__name__),
                session=session, usage=usage, native_stop_reason=native_status,
            )
        return AgentResult(
            status="succeeded", data=data, raw_text=text, session=session,
            usage=usage, native_stop_reason=native_status,
        )

    def _thread_config(self) -> dict[str, Any]:
        config = dict(self.config.codex_config)
        config.update(
            {
                "features.shell_tool": False,
                "features.view_image": False,
                "features.apply_patch_freeform": False,
                "features.js_repl": False,
                "features.multi_agent": False,
                "features.multi_agent_v2": False,
                "features.apps": False,
                "features.plugins": False,
                "features.skill_search": False,
                "features.skip_host_skill_discovery": True,
            }
        )
        for server in self.config.mcp_servers:
            config[f"mcp_servers.{server.name}"] = {
                "url": server.url,
                "default_tools_approval_mode": "approve",
                "enabled_tools": list(server.enabled_tools),
            }
        return config

    def _load_sdk(self) -> object:
        if self.config.sdk_python_root:
            source = Path(self.config.sdk_python_root) / "src"
            if str(source) not in sys.path:
                sys.path.insert(0, str(source))
        try:
            return importlib.import_module("openai_codex")
        except ImportError as exc:
            raise CodexSdkUnavailable(
                "OpenAI Codex Python SDK is unavailable; install openai-codex or configure sdk_python_root"
            ) from exc

    def _prepare_codex_home(self) -> None:
        home = Path(self.config.codex_home or "")
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.config.auth_path:
            return
        target = home / "auth.json"
        if target.exists():
            return
        shutil.copyfile(self.config.auth_path, target)
        target.chmod(0o600)


def _enum_value(value: object) -> str | None:
    native = getattr(value, "value", value)
    return str(native) if native is not None else None


def _codex_usage(usage: object) -> AgentUsage:
    last = getattr(usage, "last", None)
    if last is None:
        return AgentUsage()
    return AgentUsage(
        scope="turn",
        input_tokens=_int_attr(last, "input_tokens"),
        output_tokens=_int_attr(last, "output_tokens"),
        total_tokens=_int_attr(last, "total_tokens"),
        cached_tokens=_int_attr(last, "cached_input_tokens"),
        reasoning_tokens=_int_attr(last, "reasoning_output_tokens"),
    )


def _int_attr(value: object, name: str) -> int | None:
    item = getattr(value, name, None)
    return int(item) if isinstance(item, int) else None
