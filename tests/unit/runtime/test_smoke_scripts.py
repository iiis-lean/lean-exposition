from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.runtime import AgentResult, ExecutionResult


ROOT = Path(__file__).resolve().parents[3]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runtime_smoke = load_script("runtime_smoke")
reader_smoke = load_script("reader_agent_smoke")


class FakeStructuredExecutor:
    def __init__(self, config):
        self.config = config

    def execute(self, prompt, schema, *, trace_label=None):
        return ExecutionResult(
            status="succeeded",
            data={"title": "Addition", "description": "Basic laws of addition."},
            requested_model=self.config.model,
            response_model="fake-api",
            protocol=self.config.protocol,
            trace_label=trace_label,
        )


class FakeAgentExecutor:
    def __init__(self, config):
        self.config = config

    def start(self, prompt, output_schema=None):
        return (prompt, output_schema)

    def result(self, handle, timeout=None):
        return AgentResult(
            status="succeeded",
            data={"title": "Addition", "description": "Basic laws of addition."},
            raw_text='{"title":"Addition","description":"Basic laws of addition."}',
        )


class FakeReaderExecutor:
    last_tool_names = ()

    def __init__(self, config):
        self.config = config

    def execute(
        self,
        prompt,
        schema,
        *,
        tools,
        handlers,
        max_steps=8,
        trace_label=None,
    ):
        self.__class__.last_tool_names = tuple(tool.name for tool in tools)
        task = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
        opened = handlers["open_reader"](instance_id=task["instance_id"])
        reader_id = opened["reader_id"]
        initial = opened["view_id"]
        root = opened["root_id"]
        before = handlers["read_text"](reader_id=reader_id, view_id=initial, limit=50)
        handlers["get_overview"](reader_id=reader_id, view_id=initial, limit=50)
        handlers["inspect"](
            reader_id=reader_id, view_id=initial, ref=root, detail="summary", limit=50
        )
        handlers["locate"](reader_id=reader_id, view_id=initial, ref=root)
        handlers["recommend"](reader_id=reader_id, limit=5)
        action = handlers["apply_action"](
            reader_id=reader_id,
            expected_view=initial,
            action="expand",
            target=root,
        )
        expanded = action["view_id"]
        after = handlers["read_text"](reader_id=reader_id, view_id=expanded, limit=50)
        handlers["get_overview"](reader_id=reader_id, view_id=expanded, limit=50)
        return ExecutionResult(
            status="succeeded",
            data={
                "reader_id": reader_id,
                "initial_view": initial,
                "expanded_view": expanded,
                "root_id": root,
                "before_text": before["text"],
                "after_text": after["text"],
                "summary": "The successor construction strictly increases natural numbers.",
            },
            requested_model=self.config.model,
            response_model="fake-reader",
            protocol=self.config.protocol,
            trace_label=trace_label,
        )


class RuntimeSmokeTests(unittest.TestCase):
    def test_current_defaults_and_deepseek_flash_guard(self):
        api_args = runtime_smoke.build_parser().parse_args(["api"])
        api_config = runtime_smoke.api_config_from_args(api_args)
        self.assertEqual(api_config.model, "deepseek-flash")
        self.assertEqual(api_config.base_url, "https://api.deepseek.com")
        codex_args = runtime_smoke.build_parser().parse_args(["codex"])
        self.assertEqual(
            runtime_smoke.agent_config_from_args(codex_args).model,
            "gpt-5.6-sol",
        )
        with self.assertRaisesRegex(ValueError, "deepseek-flash"):
            runtime_smoke.api_config_from_args(
                runtime_smoke.build_parser().parse_args(
                    ["api", "--model", "deepseek-pro"]
                )
            )
        with self.assertRaisesRegex(ValueError, "deepseek-flash"):
            runtime_smoke.agent_config_from_args(
                runtime_smoke.build_parser().parse_args(
                    ["pi", "--model", "deepseek-pro"]
                )
            )

    def test_api_and_agents_write_redacted_current_records(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "out"
            env_file = Path(root) / "providers.env"
            env_file.write_text("SMOKE_API_KEY=never-serialize-this\n")
            api_args = runtime_smoke.build_parser().parse_args(
                [
                    "api",
                    "--credential-env",
                    "SMOKE_API_KEY",
                    "--env-file",
                    str(env_file),
                    "--output",
                    str(output),
                ]
            )
            with patch.dict(os.environ, {}, clear=False):
                runtime_smoke.run_api(api_args, executor_factory=FakeStructuredExecutor)
            api_text = (output / "api.json").read_text()
            self.assertNotIn("never-serialize-this", api_text)
            self.assertIn('"credential_env": "SMOKE_API_KEY"', api_text)

            codex_args = runtime_smoke.build_parser().parse_args(
                ["codex", "--cwd", root, "--output", str(output)]
            )
            runtime_smoke.run_agent(codex_args, codex_factory=FakeAgentExecutor)
            pi_args = runtime_smoke.build_parser().parse_args(
                [
                    "pi",
                    "--cwd",
                    root,
                    "--session-dir",
                    str(Path(root) / "sessions"),
                    "--output",
                    str(output),
                ]
            )
            runtime_smoke.run_agent(pi_args, pi_factory=FakeAgentExecutor)
            self.assertEqual(
                json.loads((output / "codex.json").read_text())["backend"], "codex"
            )
            self.assertEqual(
                json.loads((output / "pi.json").read_text())["backend"], "pi"
            )

    def test_reader_smoke_uses_only_the_explicit_workflow_tools(self):
        with tempfile.TemporaryDirectory() as root:
            args = reader_smoke.build_parser().parse_args(
                ["--output", str(Path(root) / "reader")]
            )
            with patch("builtins.print"):
                report = reader_smoke.run(
                    args.output,
                    None,
                    reader_smoke.api_config_from_args(args),
                    executor_factory=FakeReaderExecutor,
                )
            evidence = json.loads((args.output / "run.json").read_text())
        self.assertTrue(report["passed"])
        self.assertEqual(FakeReaderExecutor.last_tool_names, reader_smoke.ALLOWED_TOOLS)
        self.assertEqual(evidence["allowed_tools"], list(reader_smoke.ALLOWED_TOOLS))


if __name__ == "__main__":
    unittest.main()
