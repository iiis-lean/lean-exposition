from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts/project_benchmark.py"
SPEC = importlib.util.spec_from_file_location("project_benchmark", SCRIPT)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class ProjectBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.input = self.root / "input.txt"
        self.input.write_text("fixed input\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_child(
        self,
        source: str,
        *,
        timeout: float = 2,
        max_wall: float = 2,
        max_rss: int = 256 * 1024 * 1024,
        environment: dict[str, str] | None = None,
    ) -> dict:
        child = self.root / "child.py"
        child.write_text(source)
        return benchmark.run_benchmark(
            command=[sys.executable, str(child)],
            cwd=self.root,
            inputs=[self.input],
            cache_state="cold",
            toolkit_commit="not-used-by-fixture",
            timeout_seconds=timeout,
            max_wall_seconds=max_wall,
            max_rss_bytes=max_rss,
            environment_additions=environment,
        )

    def test_success_records_contract_and_process_tree_rss(self) -> None:
        report = self.run_child(
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; value = bytearray(12_000_000); time.sleep(0.2)'])\n"
            "child.wait()\n"
        )
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["outcome"]["exit_code"], 0)
        self.assertFalse(report["outcome"]["timed_out"])
        self.assertGreater(report["outcome"]["peak_rss_bytes"], 12_000_000)
        self.assertEqual(report["command"][0], sys.executable)
        self.assertEqual(report["cwd"], str(self.root.resolve()))
        self.assertEqual(report["cache_state"], "cold")
        self.assertEqual(report["toolkit_commit"], "not-used-by-fixture")
        self.assertEqual(len(report["input_digest"]), 64)
        self.assertEqual(len(report["runner_implementation_digest"]), 64)
        self.assertEqual(
            set(report),
            {
                "command", "cwd", "inputs", "input_digest",
                "environment_additions", "platform", "toolkit_commit",
                "cache_state", "runner_implementation_digest", "outcome",
                "limits", "gate",
            },
        )

    def test_nonzero_child_fails_gate(self) -> None:
        report = self.run_child("raise SystemExit(7)\n")
        self.assertEqual(report["outcome"]["exit_code"], 7)
        self.assertEqual(report["gate"]["violations"], ["nonzero_exit"])

    def test_timeout_uses_term_then_kill(self) -> None:
        original = benchmark.TERM_GRACE_SECONDS
        benchmark.TERM_GRACE_SECONDS = 0.1
        try:
            report = self.run_child(
                "import signal, time\n"
                "signal.signal(signal.SIGTERM, lambda *_: None)\n"
                "time.sleep(30)\n",
                timeout=0.1,
                max_wall=2,
            )
        finally:
            benchmark.TERM_GRACE_SECONDS = original
        self.assertTrue(report["outcome"]["timed_out"])
        self.assertEqual(report["outcome"]["termination_signal"], "SIGKILL")
        self.assertIn("timeout", report["gate"]["violations"])

    def test_frozen_resource_thresholds_fail_without_adjustment(self) -> None:
        report = self.run_child(
            "import time\ntime.sleep(0.1)\n",
            max_wall=0.01,
            max_rss=1,
        )
        self.assertFalse(report["gate"]["passed"])
        self.assertEqual(report["limits"]["max_wall_seconds"], 0.01)
        self.assertEqual(report["limits"]["max_rss_bytes"], 1)
        self.assertIn("wall_seconds", report["gate"]["violations"])
        self.assertIn("peak_rss_bytes", report["gate"]["violations"])

    def test_cli_stdout_and_report_are_strict_json_without_env_values(self) -> None:
        child = self.root / "child.py"
        child.write_text("import os\nassert os.environ['PRIVATE_TOKEN'] == 'secret-value'\n")
        output = self.root / "report.json"
        completed = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--cwd", str(self.root),
                "--input", str(self.input),
                "--cache-state", "warm",
                "--toolkit-commit", "fixture-commit",
                "--timeout-seconds", "2",
                "--max-wall-seconds", "2",
                "--max-rss-bytes", str(256 * 1024 * 1024),
                "--env", "PRIVATE_TOKEN=secret-value",
                "--report", str(output),
                "--", sys.executable, str(child),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        stdout_report = json.loads(completed.stdout)
        file_report = json.loads(output.read_text())
        self.assertEqual(stdout_report, file_report)
        self.assertEqual(stdout_report["environment_additions"], ["PRIVATE_TOKEN"])
        self.assertNotIn("secret-value", completed.stdout)
        self.assertNotIn("secret-value", output.read_text())


if __name__ == "__main__":
    unittest.main()
