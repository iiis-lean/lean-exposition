"""Run the frozen bounded source-order blind review and Reader comparison."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from analyze import ROOT
from lean_exposition.runtime import ApiConfig, StructuredExecutor, canonical_json
from lean_exposition.workflows import SourceOrderEvaluationWorkflow


DEFAULT_SELECTION = ROOT / "data/research/source_order/api_eval_20260916/selection.json"
DEFAULT_OUTPUT = ROOT / "data/research/source_order/api_eval_20260916/report.json"
DEFAULT_CREDENTIALS = Path("/root/.config/lean-exposition/model-providers.env")
QUESTIONS = [
    "Summarize the conceptual progression across the displayed items in at most two sentences.",
    "Which displayed items serve as prerequisites for later items, and what role does each play?",
    "Where is the largest conceptual transition in this order, and why?",
]
RUBRIC = {
    "coherence": "The order forms a readable mathematical progression without abrupt unexplained shifts.",
    "prerequisite_timing": "Prerequisite ideas appear shortly before the items that need them.",
    "locality": "Closely related definitions and results remain near one another.",
    "constraints": "Both candidates already satisfy the supplied hard dependencies; do not reward a label or infer its origin.",
}


def load_env(path):
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def call_record(call):
    result = call.execution
    return {
        "stage": call.stage,
        "status": result.status,
        "data": result.data,
        "error": asdict(result.error) if result.error else None,
        "usage": asdict(result.usage),
        "requested_model": result.requested_model,
        "response_model": result.response_model,
        "protocol": result.protocol,
        "provider_status": result.provider_status,
        "finish_reason": result.finish_reason,
        "incomplete_details": result.incomplete_details,
        "response_id": result.response_id,
        "trace_label": result.trace_label,
        "request_digest": result.input_digest,
        "prefix_digest": call.prefix_digest,
        "prompt_digest": call.prompt_digest,
        "structured_output_digest": digest(result.data) if result.data is not None else None,
        "raw_text_digest": hashlib.sha256(result.raw_text.encode()).hexdigest()
        if result.raw_text is not None else None,
    }


def blind_input(case):
    by_strategy = {
        "source": case["source_order"],
        "deterministic": case["deterministic_order"],
    }
    candidates = []
    for strategy, label in sorted(case["anonymous_labels"].items(), key=lambda item: item[1]):
        candidates.append({
            "candidate": label,
            "ordered_items": [case["cards"][item] for item in by_strategy[strategy]],
            "hard_dependencies": case["hard_dependencies"],
        })
    return candidates


def blind_semantics(case, record):
    data = record.get("data")
    if record["status"] != "succeeded" or not isinstance(data, dict):
        return {"valid": False, "preferred_strategy": None, "reason": "execution_failed"}
    scores = data.get("scores")
    labels = [item.get("candidate") for item in scores] if isinstance(scores, list) and all(
        isinstance(item, dict) for item in scores) else []
    preferred = data.get("preferred_candidate")
    valid = sorted(labels) == ["A", "B"] and len(set(labels)) == 2 and preferred in {"A", "B"}
    inverse = {label: strategy for strategy, label in case["anonymous_labels"].items()}
    return {
        "valid": valid,
        "preferred_strategy": inverse.get(preferred) if valid else None,
        "reason": None if valid else "candidate labels are missing, duplicated, or unknown",
    }


def reader_condition(case, strategy, label):
    order = case["source_order"] if strategy == "source" else case["deterministic_order"]
    return {
        "condition_label": label,
        "ordered_items": [case["cards"][item] for item in order],
        "instruction": "Read in the displayed order. Use only the visible cards and cite opaque_id values.",
    }


def reader_semantics(case, record):
    data = record.get("data")
    valid_refs = set(case["cards"])
    if record["status"] != "succeeded" or not isinstance(data, dict):
        return {"valid": False, "reason": "execution_failed"}
    answers = data.get("answers")
    refs = data.get("evidence_refs")
    valid = (isinstance(answers, list) and len(answers) == len(QUESTIONS) and
             isinstance(refs, list) and all(ref in valid_refs for ref in refs))
    return {
        "valid": valid,
        "answer_count": len(answers) if isinstance(answers, list) else None,
        "evidence_ref_count": len(refs) if isinstance(refs, list) else None,
        "confidence": data.get("confidence"),
        "reason": None if valid else "answer count or evidence references do not match the fixed task",
    }


def run(selection_path, output, credential_file):
    selection = json.loads(selection_path.read_text())
    load_env(credential_file)
    config = ApiConfig(
        model="deepseek-flash",
        credential_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        protocol="responses",
        timeout=180,
        max_output_tokens=4096,
        reasoning=None,
        extra_body={},
        prompt_cache_key="lean-exposition-source-order-eval-20260916",
    )
    workflow = SourceOrderEvaluationWorkflow(StructuredExecutor(config))
    config_record = asdict(config)
    report = {
        "selection_digest": digest(selection),
        "selection_file": selection_path.name,
        "model_config": config_record,
        "model_config_digest": digest(config_record),
        "official_endpoint": True,
        "agent_used": False,
        "pro_model_used": False,
        "reasoning_rewrite": False,
        "automatic_retry": False,
        "blind_review": [],
        "reader_comparison": None,
        "interpretation_boundary": (
            "Descriptive API review only. The sample is structurally selected, small, and not evidence "
            "of human or general model understanding. Reader outputs have no independent correctness oracle."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    checkpoint()
    started = time.monotonic()
    for case in selection["cases"]:
        candidates = blind_input(case)
        call = workflow.blind_review(candidates=candidates, rubric=RUBRIC)
        record = call_record(call)
        report["blind_review"].append({
            "case_id": case["case_id"],
            "project": case["project"],
            "input": {"candidates": candidates, "rubric": RUBRIC},
            "call": record,
            "semantic_validation": blind_semantics(case, record),
            "label_mapping_digest": digest(case["anonymous_labels"]),
        })
        checkpoint()
        print(json.dumps({"stage": "blind", "case_id": case["case_id"],
                          "status": record["status"], "usage": record["usage"]}, sort_keys=True), flush=True)

    reader_case = next((case for case in selection["cases"]
                        if case["case_id"] == selection["reader_case_id"]), None)
    if reader_case is not None:
        reader_records = []
        reader_hash = hashlib.sha256(reader_case["case_id"].encode()).hexdigest()
        first = "source" if int(reader_hash[0], 16) % 2 == 0 else "deterministic"
        strategies = (first, "deterministic" if first == "source" else "source")
        for index, strategy in enumerate(strategies, 1):
            condition = reader_condition(reader_case, strategy, f"R{index}")
            call = workflow.reading_comparison(condition=condition, questions=QUESTIONS)
            record = call_record(call)
            reader_records.append({
                "condition_label": f"R{index}",
                "strategy": strategy,
                "input": {"condition": condition, "questions": QUESTIONS},
                "call": record,
                "semantic_validation": reader_semantics(reader_case, record),
            })
            report["reader_comparison"] = {
                "case_id": reader_case["case_id"],
                "project": reader_case["project"],
                "selection_rule": "largest structural improvement, fixed before blind output",
                "fixed_questions": QUESTIONS,
                "conditions": reader_records,
            }
            checkpoint()
            print(json.dumps({"stage": "reader", "case_id": reader_case["case_id"],
                              "condition": f"R{index}", "status": record["status"],
                              "usage": record["usage"]}, sort_keys=True), flush=True)

    valid_blind = [item for item in report["blind_review"]
                   if item["semantic_validation"]["valid"]]
    preferences = {"source": 0, "deterministic": 0}
    for item in valid_blind:
        preferences[item["semantic_validation"]["preferred_strategy"]] += 1
    report["summary"] = {
        "seconds": time.monotonic() - started,
        "planned_blind_calls": len(selection["cases"]),
        "successful_blind_calls": sum(item["call"]["status"] == "succeeded"
                                      for item in report["blind_review"]),
        "semantically_valid_blind_calls": len(valid_blind),
        "blind_preferences": preferences,
        "reader_calls": len(report["reader_comparison"]["conditions"])
        if report["reader_comparison"] else 0,
        "successful_reader_calls": sum(item["call"]["status"] == "succeeded"
                                       for item in report["reader_comparison"]["conditions"])
        if report["reader_comparison"] else 0,
        "usage": {
            key: sum(item["call"]["usage"][key] for item in report["blind_review"]) +
                 sum(item["call"]["usage"][key]
                     for item in (report["reader_comparison"]["conditions"]
                                  if report["reader_comparison"] else []))
            for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")
        },
    }
    checkpoint()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--credential-file", type=Path, default=DEFAULT_CREDENTIALS)
    args = parser.parse_args()
    result = run(args.selection, args.output, args.credential_file)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
