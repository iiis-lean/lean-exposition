from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.runtime import ApiError, ApiUsage, ExecutionResult

from run_grok_evaluation import (
    RUBRIC_FIELDS,
    grok_config,
    run_experiment,
    safe_settings,
    select_order_cases,
)
from run_grok_label_swap import aggregate_pair, swap_mapping
from verify_grok_evaluation import verify


HEX = "a" * 64


def scope(project: str, name: str, *, valid: bool = True):
    atoms = ["a", "b", "c", "d"]
    fusion = ["b", "a", "c", "d"] if valid else ["a", "b", "c", "d"]
    return {
        "project": project,
        "scope_id": name,
        "direct_atoms": atoms,
        "dependency_only_order": ["a", "b", "c", "d"],
        "fusion_order": fusion,
        "source_order": ["a", "b", "c", "d"],
        "hard_dependencies": [{"provider": "a", "consumer": "c", "weight": 2}],
        "protected_relations": [{"before": "b", "after": "d"}],
        "cards": {atom: {"opaque_id": atom, "text": f"card {atom}"} for atom in atoms},
        "material_evidence": [{"before": "b", "after": "d", "basis": "paper"}],
    }


def declaration(project: str, index: int, *, long: bool = False):
    return {
        "project": project,
        "ref": f"{project}:{index:02d}",
        "stable_order": index,
        "author_declaration": True,
        "missing_summary": True,
        "proof_chars": 10001 if long else 100,
        "input": {
            "name": f"theorem_{index}",
            "statement": f"Statement {index}",
            "proof": f"Proof outline {index}",
        },
    }


def manifest():
    return {
        "project_gates": {
            "agree": {"status": "passed", "artifact_digest": HEX},
            "erdos1025": {"status": "failed", "artifact_digest": HEX},
            "zeta23": {"status": "passed", "artifact_digest": HEX},
        },
        "order_scopes": [
            scope("agree", "s4"), scope("agree", "s2"), scope("agree", "s1"),
            scope("agree", "s3"), scope("agree", "same", valid=False),
            scope("erdos1025", "blocked"), scope("zeta23", "z1"),
        ],
        "summary_declarations": (
            [declaration("agree", index, long=index == 7) for index in range(8)]
            + [declaration("zeta23", index) for index in range(24)]
        ),
    }


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, prompt, schema, *, trace_label=None):
        self.calls.append((prompt, schema, trace_label))
        dynamic = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
        if trace_label.startswith("t10.order"):
            data = {"choice": "A", "reason": "The prerequisites appear close to their uses."}
        else:
            data = {"records": [{
                "ref": item["ref"],
                "summary": f"Summary of {item['ref']}",
                "rubric": {field: "pass" for field in RUBRIC_FIELDS},
            } for item in dynamic["declarations"]]}
        return ExecutionResult(
            status="succeeded",
            data=data,
            raw_text=json.dumps(data),
            usage=ApiUsage(10, 4, 14, 2, 1),
            requested_model="grok-4.6",
            response_model="grok-4.6",
            protocol="responses",
            finish_reason="stop",
            input_digest="f" * 64,
        )


class UnavailableExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, _prompt, _schema, *, trace_label=None):
        self.calls += 1
        return ExecutionResult(
            status="failed",
            error=ApiError(kind="missing_credential"),
            requested_model="grok-4.6",
            protocol="responses",
            trace_label=trace_label,
        )


class GrokContractTests(unittest.TestCase):
    def test_fixed_api_settings_omit_optional_sampling_and_output_limit(self):
        config = grok_config()
        self.assertEqual(config.base_url, "https://beeapi.ai/v1")
        self.assertEqual(config.model, "grok-4.6")
        self.assertEqual(config.protocol, "responses")
        self.assertEqual(config.reasoning, {"effort": "high"})
        self.assertEqual(config.timeout, 600)
        self.assertIsNone(config.max_output_tokens)
        self.assertEqual(config.extra_body, {})

    def test_proxy_is_explicit_and_kept_out_of_public_settings(self):
        with patch.dict("os.environ", {"BEEAPI_PROXY_URL": "http://127.0.0.1:7890"}):
            config = grok_config()
        self.assertEqual(config.proxy_url, "http://127.0.0.1:7890")
        settings = safe_settings(config)
        self.assertTrue(settings["proxy_configured"])
        self.assertNotIn("proxy_url", settings)

    def test_selection_is_stable_capped_and_gate_aware(self):
        selected, missing = select_order_cases(manifest())
        self.assertEqual([item["scope_id"] for item in selected], ["s1", "s2", "s3", "z1"])
        self.assertEqual(missing, [{"project": "erdos1025", "reason": "T09 gate status is failed"}])

    def test_pair_rule_requires_label_invariant_strategy(self):
        initial = {
            "phase": "initial", "valid": True,
            "label_mapping": {"dependency_only": "A", "fusion": "B"},
            "call": {"data": {"choice": "A", "reason": "x"}},
        }
        swapped = {
            "phase": "swapped", "valid": True,
            "label_mapping": swap_mapping(initial["label_mapping"]),
            "call": {"data": {"choice": "B", "reason": "x"}},
        }
        self.assertEqual(aggregate_pair([initial, swapped])["preferred_strategy"], "dependency_only")
        swapped["call"]["data"]["choice"] = "A"
        self.assertEqual(aggregate_pair([initial, swapped])["outcome"], "tie")
        swapped["call"]["data"]["choice"] = "invalid"
        self.assertEqual(aggregate_pair([initial, swapped])["outcome"], "invalid")

    def test_fake_run_covers_bilingual_batches_and_zero_call_cache_replay(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            input_path = directory / "input.json"
            report_path = directory / "report.json"
            cache_path = directory / "cache.json"
            value = manifest()
            input_path.write_text(json.dumps(value))
            executor = FakeExecutor()
            report = run_experiment(
                value, executor=executor, output_path=report_path, cache_path=cache_path
            )
            self.assertEqual(len(report["order"]["pairs"]), 4)
            self.assertEqual(len(report["summary"]["calls"]), 8)
            # Identical blind inputs may share one request-cache entry across scopes.
            self.assertGreater(len(executor.calls), 0)
            self.assertLessEqual(len(executor.calls), 16)
            self.assertEqual(report["cache_replay"], {
                "requests": 16, "hits": 16, "executor_calls": 0,
            })
            sizes = [len(call["refs"]) for call in report["summary"]["calls"] if
                     not call["long_proof_singleton"]]
            self.assertTrue(all(8 <= size <= 12 for size in sizes))
            self.assertEqual(sum(call["long_proof_singleton"] for call in report["summary"]["calls"]), 2)
            self.assertTrue(verify(input_path, report_path, cache_path)["all_checks_pass"])

            second = FakeExecutor()
            rerun = run_experiment(
                value, executor=second, output_path=report_path, cache_path=cache_path
            )
            self.assertEqual(second.calls, [])
            self.assertTrue(all(
                entry["call"]["cache_hit"]
                for pair in rerun["order"]["pairs"] for entry in pair["calls"]
            ))

    def test_unavailable_provider_stops_without_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            executor = UnavailableExecutor()
            report = run_experiment(
                manifest(), executor=executor, output_path=directory / "report.json",
                cache_path=directory / "cache.json",
            )
            self.assertEqual(executor.calls, 1)
            self.assertEqual(report["stopped"], {
                "stage": "order", "reason": "missing_credential", "model_switched": False,
            })
            self.assertTrue(report["cache_replay"]["skipped"])


if __name__ == "__main__":
    unittest.main()
