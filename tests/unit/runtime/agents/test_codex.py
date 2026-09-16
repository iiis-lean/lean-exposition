from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from lean_exposition.runtime.agents import (
    CodexAgentConfig,
    CodexAgentExecutor,
    McpServerConfig,
)


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class _Value(Enum):
    completed = "completed"
    interrupted = "interrupted"


class _Turn:
    def __init__(self):
        self.interrupted = False
        self.steers = []
        self.started = threading.Event()

    def steer(self, message):
        self.steers.append(message)

    def interrupt(self):
        self.interrupted = True

    def run(self):
        self.started.set()
        time.sleep(0.08)
        usage = SimpleNamespace(last=SimpleNamespace(
            input_tokens=11, output_tokens=3, total_tokens=14,
            cached_input_tokens=4, reasoning_output_tokens=1,
        ))
        return SimpleNamespace(
            status=_Value.interrupted if self.interrupted else _Value.completed,
            final_response='{"answer":"ok"}', usage=usage,
        )


class _Thread:
    def __init__(self, thread_id):
        self.id = thread_id
        self.last_turn = None

    def turn(self, *args, **kwargs):
        self.last_turn = _Turn()
        return self.last_turn


class _Codex:
    started = []
    resumed = []
    threads = []

    def __init__(self, config):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def thread_start(self, **options):
        self.started.append(options)
        thread = _Thread("thread-new")
        self.threads.append(thread)
        return thread

    def thread_resume(self, thread_id, **options):
        self.resumed.append((thread_id, options))
        thread = _Thread(thread_id)
        self.threads.append(thread)
        return thread


@dataclass
class _CodexConfig:
    codex_bin: str | None = None
    cwd: str | None = None
    env: dict | None = None


FAKE_SDK = SimpleNamespace(
    Codex=_Codex,
    CodexConfig=_CodexConfig,
    ApprovalMode=SimpleNamespace(deny_all="deny"),
    Sandbox=SimpleNamespace(read_only="read-only"),
)


class CodexAgentExecutorTests(unittest.TestCase):
    def setUp(self):
        _Codex.started.clear()
        _Codex.resumed.clear()
        _Codex.threads.clear()

    def config(self, root):
        return CodexAgentConfig(
            model="gpt-5.6-sol", cwd=root, codex_home=root + "/.codex",
            mcp_servers=(McpServerConfig(
                "writing", "http://127.0.0.1:1/mcp", ("get_step", "submit_draft")
            ),),
            codex_config={"features.shell_tool": True},
        )

    def test_start_is_structured_and_security_defaults_override_input(self):
        with tempfile.TemporaryDirectory() as root:
            executor = CodexAgentExecutor(self.config(root), sdk_loader=lambda: FAKE_SDK)
            result = executor.result(executor.start("answer", SCHEMA), 2)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data, {"answer": "ok"})
        self.assertEqual(result.usage.cached_tokens, 4)
        options = _Codex.started[0]
        self.assertFalse(options["config"]["features.shell_tool"])
        self.assertIn("mcp_servers.writing", options["config"])
        self.assertEqual(
            options["config"]["mcp_servers.writing"]["enabled_tools"],
            ["get_step", "submit_draft"],
        )
        self.assertEqual(options["approval_mode"], "deny")
        self.assertEqual(options["sandbox"], "read-only")

    def test_resume_and_active_controls(self):
        with tempfile.TemporaryDirectory() as root:
            executor = CodexAgentExecutor(self.config(root), sdk_loader=lambda: FAKE_SDK)
            first = executor.result(executor.start("first"), 2)
            handle = executor.resume(first.session, "second")
            control = None
            for _ in range(40):
                control = executor.follow_up(handle, "more")
                if control.accepted:
                    break
                time.sleep(0.005)
            self.assertTrue(control.accepted)
            self.assertTrue(executor.steer(handle, "focus").accepted)
            self.assertTrue(executor.cancel(handle).accepted)
            result = executor.result(handle, 2)
        self.assertEqual(_Codex.resumed[0][0], "thread-new")
        self.assertEqual(result.status, "cancelled")

    def test_executor_deadline_interrupts_the_native_turn(self):
        with tempfile.TemporaryDirectory() as root:
            config = replace(self.config(root), timeout=0.01)
            executor = CodexAgentExecutor(config, sdk_loader=lambda: FAKE_SDK)
            result = executor.result(executor.start("slow"), 2)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.kind, "timeout")

    def test_hidden_mcp_or_skill_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "explicit Agent field"):
                CodexAgentConfig(
                    model="gpt-5.6-sol", cwd=root,
                    codex_config={"mcp_servers.unreviewed": {"url": "http://localhost"}},
                )


if __name__ == "__main__":
    unittest.main()
