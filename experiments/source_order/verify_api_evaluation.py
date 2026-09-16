"""Offline integrity checks for the accepted bounded API evaluation evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from analyze import ROOT
from lean_exposition.runtime import canonical_json


BASE = ROOT / "data/research/source_order/api_eval_20260916"


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def verify(selection_path, report_path):
    selection = json.loads(selection_path.read_text())
    report = json.loads(report_path.read_text())
    assert report["selection_digest"] == digest(selection)
    assert report["model_config"]["model"] == "deepseek-flash"
    assert report["model_config"]["base_url"] == "https://api.deepseek.com"
    assert report["model_config"]["reasoning"] is None
    assert report["model_config"]["max_output_tokens"] == 4096
    assert not report["agent_used"] and not report["pro_model_used"]
    assert not report["reasoning_rewrite"] and not report["automatic_retry"]
    assert len(report["blind_review"]) == len(selection["cases"])
    for item in report["blind_review"]:
        candidates = item["input"]["candidates"]
        assert {candidate["candidate"] for candidate in candidates} == {"A", "B"}
        assert {card["opaque_id"] for candidate in candidates
                for card in candidate["ordered_items"]} == set(
                    next(case for case in selection["cases"]
                         if case["case_id"] == item["case_id"])["cards"])
        assert item["call"]["prompt_digest"] and item["call"]["request_digest"]
    reader = report["reader_comparison"]
    assert reader["case_id"] == selection["reader_case_id"]
    assert len(reader["conditions"]) == 2
    first, second = reader["conditions"]
    assert first["input"]["questions"] == second["input"]["questions"]
    assert {card["opaque_id"] for card in first["input"]["condition"]["ordered_items"]} == {
        card["opaque_id"] for card in second["input"]["condition"]["ordered_items"]}
    calls = [item["call"] for item in report["blind_review"]] + [
        item["call"] for item in reader["conditions"]]
    for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens"):
        assert report["summary"]["usage"][key] == sum(call["usage"][key] for call in calls)
    return {
        "selection_file_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        "selection_digest_matches": True,
        "official_flash_config": True,
        "fixed_budget_and_reasoning": True,
        "blind_inputs_anonymous": True,
        "reader_conditions_matched": True,
        "usage_totals_match": True,
        "all_checks_pass": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=BASE / "selection.json")
    parser.add_argument("--report", type=Path, default=BASE / "report.json")
    parser.add_argument("--output", type=Path, default=BASE / "verification.json")
    args = parser.parse_args()
    result = verify(args.selection, args.report)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
