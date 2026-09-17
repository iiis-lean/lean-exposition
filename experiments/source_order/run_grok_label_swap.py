"""Aggregate the preregistered T10 initial/swapped-label order judgments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def swap_mapping(mapping: dict[str, str]) -> dict[str, str]:
    if set(mapping) != {"dependency_only", "fusion"} or set(mapping.values()) != {"A", "B"}:
        raise ValueError("label mapping must biject dependency_only/fusion to A/B")
    return {strategy: "B" if label == "A" else "A" for strategy, label in mapping.items()}


def _strategy(entry: dict[str, Any]) -> tuple[str, str | None]:
    if entry.get("valid") is not True:
        return "invalid", None
    data = entry.get("call", {}).get("data")
    if not isinstance(data, dict):
        return "invalid", None
    choice = data.get("choice")
    if choice == "invalid":
        return "invalid", None
    if choice == "tie":
        return "tie", None
    inverse = {label: strategy for strategy, label in entry["label_mapping"].items()}
    strategy = inverse.get(choice)
    return ("preference", strategy) if strategy is not None else ("invalid", None)


def aggregate_pair(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the frozen paired rule without converting disagreement into a vote."""
    if len(calls) != 2 or [item.get("phase") for item in calls] != ["initial", "swapped"]:
        raise ValueError("paired calls must be initial then swapped")
    initial_mapping = calls[0].get("label_mapping")
    if calls[1].get("label_mapping") != swap_mapping(initial_mapping):
        raise ValueError("second call must swap both anonymous labels")
    first_kind, first_strategy = _strategy(calls[0])
    second_kind, second_strategy = _strategy(calls[1])
    if "invalid" in {first_kind, second_kind}:
        outcome, preferred = "invalid", None
    elif "tie" in {first_kind, second_kind}:
        outcome, preferred = "tie", None
    elif first_strategy == second_strategy:
        outcome, preferred = "preference", first_strategy
    else:
        outcome, preferred = "tie", None
    return {
        "outcome": outcome,
        "preferred_strategy": preferred,
        "initial_semantics": {"kind": first_kind, "strategy": first_strategy},
        "swapped_semantics": {"kind": second_kind, "strategy": second_strategy},
    }


def refresh_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("experiment") != "t10-grok-preregistered":
        raise ValueError("not a T10 Grok report")
    for pair in report.get("order", {}).get("pairs", []):
        pair["paired_outcome"] = aggregate_pair(pair["calls"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = refresh_report(json.loads(args.report.read_text()))
    output = args.output or args.report
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for pair in report["order"]["pairs"]:
        outcome = pair["paired_outcome"]["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
