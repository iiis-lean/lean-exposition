from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from lean_exposition.runtime.agents import PiAgentConfig, PiAgentExecutor


FIXTURE = Path(__file__).parent / "fixtures" / "fake_pi.py"
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class PiAgentExecutorTests(unittest.TestCase):
    def config(self, root: str, **changes):
        values = dict(
            model="deepseek-flash",
            provider="deepseek",
            cwd=root,
            session_dir=str(Path(root) / "sessions"),
            executable=str(FIXTURE),
            timeout=2,
        )
        values.update(changes)
        return PiAgentConfig(**values)

    def test_structured_turn_records_session_usage_and_tools(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"FAKE_PI_MODE": "tool"}):
            executor = PiAgentExecutor(self.config(root, tools=("read",)))
            result = executor.result(executor.start("answer", SCHEMA), 3)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data, {"answer": "ok"})
        self.assertEqual(result.session.backend, "pi")
        self.assertEqual(result.usage.scope, "session")
        self.assertEqual(result.usage.total_tokens, 12)
        self.assertEqual([event.phase for event in result.tool_events], ["started", "finished"])

    def test_active_controls_and_cancel_have_native_confirmation(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"FAKE_PI_MODE": "cancel"}):
            executor = PiAgentExecutor(self.config(root))
            handle = executor.start("wait")
            accepted = None
            for _ in range(40):
                accepted = executor.steer(handle, "focus")
                if accepted.accepted:
                    break
                time.sleep(0.01)
            self.assertTrue(accepted.accepted)
            self.assertTrue(executor.follow_up(handle, "then finish").accepted)
            self.assertTrue(executor.cancel(handle).accepted)
            result = executor.result(handle, 3)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.native_stop_reason, "aborted")

    def test_native_retry_is_suppressed_without_prompt_replay(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"FAKE_PI_MODE": "retry"}):
            executor = PiAgentExecutor(self.config(root))
            result = executor.result(executor.start("once"), 3)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.native_source, "pi_assistant")

    def test_resume_uses_reported_session_file(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"FAKE_PI_MODE": "normal"}):
            executor = PiAgentExecutor(self.config(root))
            first = executor.result(executor.start("first"), 3)
            second = executor.result(executor.resume(first.session, "second", SCHEMA), 3)
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.session.session_id, first.session.session_id)

    def test_unsafe_tools_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "not permitted"):
                self.config(root, tools=("bash",))


if __name__ == "__main__":
    unittest.main()
