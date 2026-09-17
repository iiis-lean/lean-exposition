"""Offline integrity verification for the preregistered T10 Grok report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .run_grok_evaluation import (
        DEFAULT_CACHE,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT,
        PROJECTS,
        RUBRIC_FIELDS,
        USAGE_FIELDS,
        _is_long,
        digest,
        select_order_cases,
        select_summary_declarations,
    )
    from .run_grok_label_swap import aggregate_pair, swap_mapping
except ImportError:
    from run_grok_evaluation import (
        DEFAULT_CACHE,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT,
        PROJECTS,
        RUBRIC_FIELDS,
        USAGE_FIELDS,
        _is_long,
        digest,
        select_order_cases,
        select_summary_declarations,
    )
    from run_grok_label_swap import aggregate_pair, swap_mapping


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def verify(manifest_path: Path, report_path: Path, cache_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    report = json.loads(report_path.read_text())
    cache = json.loads(cache_path.read_text())
    assert report["experiment"] == "t10-grok-preregistered"
    assert report["seed"] == 20260917
    assert report["input_digest"] == digest(manifest)
    assert set(report["settings"]) == {"order", "summary"}
    for settings in report["settings"].values():
        assert settings["provider"] == "BeeAPI"
        assert settings["base_url"] == "https://beeapi.ai/v1"
        assert settings["model"] == "grok-4.6"
        assert settings["protocol"] == "responses"
        assert settings["reasoning"] == {"effort": "high"}
        assert settings["timeout_seconds"] == 600
        assert settings["max_output_tokens"] is None
        assert settings["temperature_omitted"] is True
        assert settings["seed_omitted"] is True
    assert report["settings_digest"] == digest(report["settings"])
    forbidden = {"api_key", "authorization", "credential", "credential_env"}
    assert not (forbidden & set(_walk_keys(report)))
    assert not (forbidden & set(_walk_keys(cache)))

    selected, expected_missing = select_order_cases(manifest)
    assert report["order"]["missing_samples"] == expected_missing
    assert [pair["scope_id"] for pair in report["order"]["pairs"]] == [
        str(scope["scope_id"]) for scope in selected
    ]
    assert len(report["order"]["pairs"]) <= 3 * len(PROJECTS)
    for pair in report["order"]["pairs"]:
        assert 4 <= pair["atom_count"] <= 12
        assert len(pair["calls"]) == 2
        initial, swapped = pair["calls"]
        assert initial["phase"] == "initial" and swapped["phase"] == "swapped"
        assert swapped["label_mapping"] == swap_mapping(initial["label_mapping"])
        assert pair["paired_outcome"] == aggregate_pair(pair["calls"])
        assert set(pair["structural_metrics"]) == {"dependency_only", "fusion"}
        for entry in pair["calls"]:
            call = entry["call"]
            assert call["request_digest"] in cache["requests"]
            assert call["prompt_digest"] and call["prefix_digest"] and call["input_digest"]
            if call["status"] == "succeeded":
                assert call["response_digest"] == digest(call["data"])

    declarations, expected_summary_missing, expected_excluded = select_summary_declarations(manifest)
    assert report["summary"]["missing_samples"] == expected_summary_missing
    assert report["summary"]["excluded"] == expected_excluded
    selected_by_ref = {str(item["ref"]): item for item in declarations}
    assert set(call["locale"] for call in report["summary"]["calls"]) <= {"en", "zh"}
    for locale in ("en", "zh"):
        locale_calls = [call for call in report["summary"]["calls"] if call["locale"] == locale]
        seen: set[str] = set()
        for entry in locale_calls:
            refs = set(entry["refs"])
            assert not (seen & refs)
            seen |= refs
            long_refs = {ref for ref in refs if _is_long(selected_by_ref[ref])}
            if long_refs:
                assert len(refs) == 1 and entry["long_proof_singleton"] is True
            else:
                assert 8 <= len(refs) <= 12
                assert entry["long_proof_singleton"] is False
            call = entry["call"]
            assert call["request_digest"] in cache["requests"]
            if entry["valid"]:
                output_refs = {item["ref"] for item in call["data"]["records"]}
                assert output_refs == refs
                for item in call["data"]["records"]:
                    assert set(item["rubric"]) == set(RUBRIC_FIELDS)
        assert seen == set(selected_by_ref)
    assert report["cache_replay"] == {
        "requests": 2 * len(report["order"]["pairs"]) + len(report["summary"]["calls"]),
        "hits": 2 * len(report["order"]["pairs"]) + len(report["summary"]["calls"]),
        "executor_calls": 0,
    }
    all_calls = [entry["call"] for pair in report["order"]["pairs"] for entry in pair["calls"]]
    all_calls += [entry["call"] for entry in report["summary"]["calls"]]
    for field in USAGE_FIELDS:
        assert report["usage"][field] == sum(
            call["usage"][field] for call in all_calls if not call["cache_hit"]
        )
    return {
        "input_digest_matches": True,
        "fixed_api_contract": True,
        "no_credentials_persisted": True,
        "stable_eligible_selection": True,
        "paired_label_swap_contract": True,
        "summary_batch_contract": True,
        "bilingual_prompts": True,
        "cache_replay_zero_calls": True,
        "missing_gates_reported": True,
        "all_checks_pass": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.input, args.report, args.cache)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
