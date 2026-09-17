"""Run the preregistered T10 Grok ordering and declaration-summary experiment.

The input manifest is produced only after the T09 project gates. Failed or
missing gates become explicit missing samples; this script never substitutes a
different project or model. Reports and the local request cache contain
digests, normalized usage, and structured data, but never credentials.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import random
from typing import Any, Iterable

from lean_exposition.runtime import (
    ApiConfig,
    ExecutionResult,
    StructuredExecutor,
    canonical_json,
    prompt_digest,
    request_digest,
    stable_prompt,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/research/source_order/t10/input.json"
DEFAULT_OUTPUT = ROOT / "data/research/source_order/t10/report.json"
DEFAULT_CACHE = ROOT / "data/research/source_order/t10/request-cache.json"
DEFAULT_CREDENTIALS = Path("/root/.config/lean-exposition/model-providers.env")

SEED = 20260917
PROJECTS = ("agree", "erdos1025", "zeta23")
SUMMARY_PROJECTS = ("agree", "zeta23")
USAGE_FIELDS = (
    "input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens"
)

ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["A", "B", "tie", "invalid"]},
        "reason": {"type": "string", "maxLength": 120},
    },
    "required": ["choice", "reason"],
    "additionalProperties": False,
}

RUBRIC_FIELDS = (
    "facts_conditions_symbols",
    "proof_route",
    "non_lean_style",
    "length",
    "standalone_clarity",
)
RUBRIC_VALUE = {"type": "string", "enum": ["pass", "minor", "fail"]}
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "rubric": {
                        "type": "object",
                        "properties": {name: RUBRIC_VALUE for name in RUBRIC_FIELDS},
                        "required": list(RUBRIC_FIELDS),
                        "additionalProperties": False,
                    },
                },
                "required": ["ref", "summary", "rubric"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["records"],
    "additionalProperties": False,
}

ORDER_PREFIX = (
    "Compare two anonymous, dependency-legal orders of the same mathematical items. "
    "Candidate letters carry no rank and reveal no algorithm. Judge mathematical narrative "
    "coherence, prerequisite timing, and conceptual locality using only the supplied cards, "
    "dependencies, and material evidence. Return choice A, B, tie, or invalid and a reason of "
    "at most 120 characters. Return only JSON."
)
SUMMARY_PREFIXES = {
    "en": (
        "Write one concise English mathematical summary for every supplied declaration. Preserve "
        "quantifiers, hypotheses, notation, and conclusion; describe only a source-supported proof "
        "route; avoid Lean syntax, tactics, paths, and internal identifiers. Make each summary "
        "understandable on its own. Then self-check the five supplied rubric dimensions as pass, "
        "minor, or fail. This self-check is descriptive, not ground truth. Return only JSON. "
        "Example style: 'Every continuous map from a compact space has compact image. The proof "
        "uses the open-cover definition and pulls a cover back along the map.'"
    ),
    "zh": (
        "为每个给定声明写一段简洁的中文数学摘要。完整保留量词、假设、符号和结论；只写来源支持的"
        "证明路线；避免 Lean 语法、策略、路径和内部标识符；每段应当能够独立理解。随后将五项 rubric "
        "分别自检为 pass、minor 或 fail；该自检只作描述，不是真值。只返回 JSON。文风示例："
        "“紧空间的连续像仍然紧。证明把像空间上的开覆盖沿映射拉回，再由有限子覆盖推出结论。”"
    ),
}


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_env(path: Path) -> None:
    """Load a local key file without returning or logging its values."""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


def grok_config(*, cache_key: str = "lean-exposition-t10") -> ApiConfig:
    return ApiConfig(
        model="grok-4.6",
        credential_env="BEEAPI_GROK_API_KEY",
        base_url="https://beeapi.ai/v1",
        protocol="responses",
        timeout=600,
        max_output_tokens=None,
        reasoning={"effort": "high"},
        extra_body={},
        prompt_cache_key=cache_key,
        proxy_url=os.environ.get("BEEAPI_PROXY_URL"),
    )


def safe_settings(config: ApiConfig) -> dict[str, Any]:
    return {
        "provider": "BeeAPI",
        "base_url": config.base_url,
        "model": config.model,
        "protocol": config.protocol,
        "reasoning": config.reasoning,
        "timeout_seconds": config.timeout,
        "max_output_tokens": config.max_output_tokens,
        "temperature_omitted": True,
        "seed_omitted": True,
        "prompt_cache_key": config.prompt_cache_key,
        "proxy_configured": config.proxy_url is not None,
    }


def _validate_gate(manifest: dict[str, Any], project: str) -> tuple[bool, str | None]:
    gate = manifest.get("project_gates", {}).get(project)
    if not isinstance(gate, dict):
        return False, "T09 gate missing"
    if gate.get("status") != "passed":
        return False, f"T09 gate status is {gate.get('status', 'missing')}"
    if not isinstance(gate.get("artifact_digest"), str) or len(gate["artifact_digest"]) != 64:
        return False, "T09 gate artifact digest missing"
    return True, None


def _edge_parts(edge: dict[str, Any]) -> tuple[str, str, float]:
    return str(edge["provider"]), str(edge["consumer"]), float(edge.get("weight", 1))


def _legal(order: Iterable[str], edges: Iterable[dict[str, Any]]) -> bool:
    positions = {atom: index for index, atom in enumerate(order)}
    return all(
        provider in positions and consumer in positions and positions[provider] < positions[consumer]
        for provider, consumer, _ in map(_edge_parts, edges)
    )


def _frontier_sum(order: list[str], edges: list[dict[str, Any]]) -> int:
    predecessors = {atom: set() for atom in order}
    for provider, consumer, _ in map(_edge_parts, edges):
        predecessors[consumer].add(provider)
    placed: set[str] = set()
    total = 0
    for chosen in order:
        ready = [atom for atom in order if atom not in placed and predecessors[atom] <= placed]
        if chosen not in ready:
            raise ValueError("order is not dependency-legal")
        total += len(ready)
        placed.add(chosen)
    return total


def structural_metrics(scope: dict[str, Any], order: list[str]) -> dict[str, Any]:
    positions = {atom: index for index, atom in enumerate(order)}
    dependencies = scope.get("hard_dependencies", [])
    protected = scope.get("protected_relations", [])
    source = scope.get("source_order") or scope["dependency_only_order"]
    source_positions = {atom: index for index, atom in enumerate(source)}
    dependency_distance = sum(
        weight * (positions[consumer] - positions[provider])
        for provider, consumer, weight in map(_edge_parts, dependencies)
    )
    preserved = sum(
        positions[str(item["before"])] < positions[str(item["after"])] for item in protected
    )
    return {
        "dependency_distance": dependency_distance,
        "protected_preserved": preserved,
        "protected_total": len(protected),
        "source_displacement": sum(
            abs(index - source_positions[atom]) for atom, index in positions.items()
        ),
        "frontier_sum": _frontier_sum(order, dependencies),
    }


def select_order_cases(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Apply the frozen eligibility rule and stable per-project cap."""
    scopes = manifest.get("order_scopes", [])
    selected: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for project in PROJECTS:
        gate_ok, reason = _validate_gate(manifest, project)
        if not gate_ok:
            missing.append({"project": project, "reason": reason or "gate failed"})
            continue
        eligible = []
        for scope in scopes:
            if scope.get("project") != project:
                continue
            atoms = list(scope.get("direct_atoms", []))
            dependency = list(scope.get("dependency_only_order", []))
            fusion = list(scope.get("fusion_order", []))
            edges = list(scope.get("hard_dependencies", []))
            cards = scope.get("cards")
            if not 4 <= len(atoms) <= 12:
                continue
            if len(set(atoms)) != len(atoms) or set(dependency) != set(atoms):
                continue
            if set(fusion) != set(atoms) or dependency == fusion:
                continue
            if set(scope.get("source_order") or dependency) != set(atoms):
                continue
            if not isinstance(cards, dict) or set(cards) != set(atoms):
                continue
            if any(
                str(item.get(endpoint)) not in set(atoms)
                for item in scope.get("protected_relations", [])
                for endpoint in ("before", "after")
            ):
                continue
            if not _legal(dependency, edges) or not _legal(fusion, edges):
                continue
            eligible.append(scope)
        eligible.sort(key=lambda item: str(item["scope_id"]))
        scope_ids = [str(item["scope_id"]) for item in eligible]
        if len(scope_ids) != len(set(scope_ids)):
            raise ValueError(f"duplicate stable scope ID in {project}")
        selected.extend(eligible[:3])
        if not eligible:
            missing.append({"project": project, "reason": "no eligible scope"})
    return selected, missing


def _initial_mappings(cases: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    rng = random.Random(SEED)
    result = {}
    for case in cases:
        dependency_label = rng.choice(("A", "B"))
        result[f"{case['project']}:{case['scope_id']}"] = {
            "dependency_only": dependency_label,
            "fusion": "B" if dependency_label == "A" else "A",
        }
    return result


def _order_dynamic(scope: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    candidates = []
    for strategy, label in sorted(mapping.items(), key=lambda item: item[1]):
        order = scope[f"{strategy}_order"]
        candidates.append({
            "candidate": label,
            "ordered_items": [scope["cards"][atom] for atom in order],
        })
    return {
        "rubric": {
            "coherence": "A readable mathematical progression.",
            "prerequisite_timing": "Prerequisites occur before and near their uses.",
            "conceptual_locality": "Related definitions and results remain close.",
        },
        "hard_dependencies": scope.get("hard_dependencies", []),
        "material_evidence": scope.get("material_evidence", []),
        "candidates": candidates,
    }


class JsonRequestCache:
    def __init__(self, path: Path):
        self.path = path
        self.state = {"requests": {}}
        if path.exists():
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or set(value) != {"requests"} or not isinstance(value["requests"], dict):
                raise ValueError("invalid T10 request cache")
            self.state = value

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.state["requests"].get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.state["requests"][key] = json.loads(json.dumps(value))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.path)


def _usage(result: ExecutionResult) -> dict[str, int]:
    return {name: int(getattr(result.usage, name)) for name in USAGE_FIELDS}


def _error_record(result: ExecutionResult) -> dict[str, Any] | None:
    return asdict(result.error) if result.error is not None else None


def _execute_cached(
    executor: Any,
    cache: JsonRequestCache,
    config: ApiConfig,
    *,
    prefix: str,
    dynamic: dict[str, Any],
    schema: dict[str, Any],
    trace_label: str,
) -> dict[str, Any]:
    prompt = stable_prompt(prefix, dynamic)
    key = request_digest(prompt, schema, config)
    cached = cache.get(key)
    if cached is not None:
        return {**cached, "cache_hit": True}
    result = executor.execute(prompt, schema, trace_label=trace_label)
    record = {
        "cache_hit": False,
        "request_digest": key,
        "input_digest": digest(dynamic),
        "prompt_digest": prompt_digest(prompt),
        "prefix_digest": prompt_digest(prefix.rstrip()),
        "response_digest": digest(result.data) if result.data is not None else None,
        "status": result.status,
        "data": result.data,
        "error": _error_record(result),
        "usage": _usage(result),
        "requested_model": result.requested_model,
        "response_model": result.response_model,
        "finish_reason": result.finish_reason,
    }
    cache.put(key, record)
    return record


def _valid_order_response(call: dict[str, Any]) -> bool:
    data = call.get("data")
    return (
        call.get("status") == "succeeded"
        and isinstance(data, dict)
        and set(data) == {"choice", "reason"}
        and data["choice"] in {"A", "B", "tie", "invalid"}
        and isinstance(data["reason"], str)
        and len(data["reason"]) <= 120
    )


def _valid_summary_response(call: dict[str, Any], expected: set[str]) -> bool:
    data = call.get("data")
    if call.get("status") != "succeeded" or not isinstance(data, dict) or set(data) != {"records"}:
        return False
    records = data["records"]
    if (not isinstance(records, list) or len(records) != len(expected) or
            {item.get("ref") for item in records if isinstance(item, dict)} != expected):
        return False
    for item in records:
        if set(item) != {"ref", "summary", "rubric"} or not isinstance(item["summary"], str) or not item["summary"]:
            return False
        rubric = item["rubric"]
        if not isinstance(rubric, dict) or set(rubric) != set(RUBRIC_FIELDS):
            return False
        if any(value not in {"pass", "minor", "fail"} for value in rubric.values()):
            return False
    return True


def _representable_batch_count(count: int) -> bool:
    if count == 0:
        return True
    return any(8 * groups <= count <= 12 * groups for groups in range(1, count // 8 + 1))


def _batch_sizes(count: int) -> list[int]:
    if not _representable_batch_count(count):
        raise ValueError(f"cannot partition {count} declarations into batches of 8-12")
    if count == 0:
        return []
    groups = min(groups for groups in range(1, count // 8 + 1) if 8 * groups <= count <= 12 * groups)
    base, extra = divmod(count, groups)
    return [base + (1 if index < extra else 0) for index in range(groups)]


def select_summary_declarations(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    """Select all eligible Agree declarations and the largest feasible Zeta prefix up to 24."""
    missing: list[dict[str, str]] = []
    for project in SUMMARY_PROJECTS:
        ok, reason = _validate_gate(manifest, project)
        if not ok:
            missing.append({"project": project, "reason": reason or "gate failed"})
    available = [item for item in manifest.get("summary_declarations", [])
                 if item.get("project") in SUMMARY_PROJECTS]
    agree = [item for item in available if item.get("project") == "agree"
             and item.get("author_declaration") is True and item.get("missing_summary") is True]
    zeta = [item for item in available if item.get("project") == "zeta23"
            and item.get("missing_summary") is True]
    key = lambda item: (int(item.get("stable_order", 0)), str(item.get("ref", "")))
    agree.sort(key=key)
    zeta.sort(key=key)
    if any(entry["project"] == "agree" for entry in missing):
        agree = []
    if any(entry["project"] == "zeta23" for entry in missing):
        zeta = []

    candidate_zeta = zeta[:24]
    long = [item for item in agree + candidate_zeta if _is_long(item)]
    agree_normal = [item for item in agree if not _is_long(item)]
    zeta_normal = [item for item in candidate_zeta if not _is_long(item)]
    chosen_zeta_count = len(zeta_normal)
    while chosen_zeta_count >= 0 and not _representable_batch_count(
        len(agree_normal) + chosen_zeta_count
    ):
        chosen_zeta_count -= 1
    excluded: list[dict[str, str]] = []
    if chosen_zeta_count < 0:
        raise ValueError("summary sample cannot satisfy the frozen 8-12 batch contract")
    for item in zeta_normal[chosen_zeta_count:]:
        excluded.append({"ref": str(item["ref"]), "reason": "batch-contract cap"})
    selected = agree_normal + zeta_normal[:chosen_zeta_count] + long
    refs = [str(item.get("ref", "")) for item in selected]
    if not all(refs) or len(refs) != len(set(refs)):
        raise ValueError("summary declaration refs must be nonempty and unique")
    return selected, missing, excluded


def _is_long(item: dict[str, Any]) -> bool:
    return item.get("long_proof") is True or int(item.get("proof_chars", 0)) > 10000


def summary_batches(declarations: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    normal = [item for item in declarations if not _is_long(item)]
    long = [item for item in declarations if _is_long(item)]
    sizes = _batch_sizes(len(normal))
    batches, offset = [], 0
    for size in sizes:
        batches.append(normal[offset:offset + size])
        offset += size
    batches.extend([[item] for item in long])
    return batches


def _summary_dynamic(locale: str, batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "locale": locale,
        "rubric": {
            "facts_conditions_symbols": "All mathematical facts, hypotheses, notation, and conclusions are preserved.",
            "proof_route": "Any stated proof route is supported by the supplied source.",
            "non_lean_style": "The prose does not expose Lean implementation details.",
            "length": "The summary is concise relative to the declaration.",
            "standalone_clarity": "The summary is understandable without another generated summary.",
        },
        "declarations": [{"ref": item["ref"], "input": item["input"]} for item in batch],
    }


class _NoCalls:
    calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("cache replay attempted a provider call")


def _stage_executor(executor: Any, stage: str) -> Any:
    if isinstance(executor, dict):
        if set(executor) != {"order", "summary"}:
            raise ValueError("executor mapping must contain exactly order and summary")
        return executor[stage]
    return executor


def _provider_unavailable(call: dict[str, Any]) -> bool:
    error = call.get("error") or {}
    return call.get("status") == "failed" and error.get("kind") in {
        "missing_credential", "provider_error", "provider_failed"
    }


def _finish_report(report: dict[str, Any], output_path: Path) -> dict[str, Any]:
    all_calls = [entry["call"] for pair in report["order"]["pairs"] for entry in pair["calls"]]
    all_calls += [entry["call"] for entry in report["summary"]["calls"]]
    report["usage"] = {
        field: sum(int(call["usage"][field]) for call in all_calls if not call["cache_hit"])
        for field in USAGE_FIELDS
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return report


def run_experiment(
    manifest: dict[str, Any],
    *,
    executor: Any,
    output_path: Path,
    cache_path: Path,
) -> dict[str, Any]:
    """Run the fixed experiment; suitable for both StructuredExecutor and a fake."""
    order_config = grok_config(cache_key="lean-exposition-t10-order")
    summary_config = grok_config(cache_key="lean-exposition-t10-summary")
    settings = {"order": safe_settings(order_config), "summary": safe_settings(summary_config)}
    cache = JsonRequestCache(cache_path)
    cases, missing_order = select_order_cases(manifest)
    mappings = _initial_mappings(cases)
    report: dict[str, Any] = {
        "experiment": "t10-grok-preregistered",
        "seed": SEED,
        "input_digest": digest(manifest),
        "settings": settings,
        "settings_digest": digest(settings),
        "order": {"missing_samples": missing_order, "pairs": []},
        "summary": {"missing_samples": [], "excluded": [], "calls": []},
        "interpretation_boundary": (
            "Structural legality and metrics are primary. Grok choices and summary self-rubrics "
            "are descriptive model evidence, not mathematical correctness or human-understanding truth."
        ),
    }
    replay_requests: list[tuple[ApiConfig, str, dict[str, Any], dict[str, Any], str]] = []

    try:
        from .run_grok_label_swap import aggregate_pair, swap_mapping
    except ImportError:
        from run_grok_label_swap import aggregate_pair, swap_mapping

    for scope in cases:
        scope_id = str(scope["scope_id"])
        mapping = mappings[f"{scope['project']}:{scope_id}"]
        calls = []
        for phase, current_mapping in (("initial", mapping), ("swapped", swap_mapping(mapping))):
            dynamic = _order_dynamic(scope, current_mapping)
            call = _execute_cached(
                _stage_executor(executor, "order"), cache, order_config,
                prefix=ORDER_PREFIX, dynamic=dynamic,
                schema=ORDER_SCHEMA, trace_label=f"t10.order.{scope_id}.{phase}",
            )
            entry = {
                "phase": phase,
                "label_mapping": current_mapping,
                "label_mapping_digest": digest(current_mapping),
                "valid": _valid_order_response(call),
                "call": call,
            }
            calls.append(entry)
            if _provider_unavailable(call):
                report["order"]["pairs"].append({
                    "project": scope["project"], "scope_id": scope_id,
                    "atom_count": len(scope["direct_atoms"]),
                    "case_input_digest": digest(scope), "calls": calls,
                    "structural_metrics": {
                        "dependency_only": structural_metrics(scope, scope["dependency_only_order"]),
                        "fusion": structural_metrics(scope, scope["fusion_order"]),
                    },
                    "paired_outcome": None,
                })
                report["stopped"] = {
                    "stage": "order", "reason": call["error"]["kind"],
                    "model_switched": False,
                }
                report["cache_replay"] = {"skipped": True, "reason": "provider unavailable"}
                return _finish_report(report, output_path)
            replay_requests.append((order_config, ORDER_PREFIX, dynamic, ORDER_SCHEMA,
                                    f"t10.order.{scope_id}.{phase}"))
        report["order"]["pairs"].append({
            "project": scope["project"],
            "scope_id": scope_id,
            "atom_count": len(scope["direct_atoms"]),
            "case_input_digest": digest(scope),
            "structural_metrics": {
                "dependency_only": structural_metrics(scope, scope["dependency_only_order"]),
                "fusion": structural_metrics(scope, scope["fusion_order"]),
            },
            "calls": calls,
            "paired_outcome": aggregate_pair(calls),
        })

    declarations, missing_summary, excluded = select_summary_declarations(manifest)
    report["summary"]["missing_samples"] = missing_summary
    report["summary"]["excluded"] = excluded
    batches = summary_batches(declarations)
    for locale in ("en", "zh"):
        for index, batch in enumerate(batches):
            dynamic = _summary_dynamic(locale, batch)
            trace = f"t10.summary.{locale}.{index:03d}"
            call = _execute_cached(
                _stage_executor(executor, "summary"), cache, summary_config,
                prefix=SUMMARY_PREFIXES[locale], dynamic=dynamic,
                schema=SUMMARY_SCHEMA, trace_label=trace,
            )
            refs = {str(item["ref"]) for item in batch}
            entry = {
                "locale": locale,
                "batch_index": index,
                "refs": sorted(refs),
                "long_proof_singleton": len(batch) == 1 and _is_long(batch[0]),
                "valid": _valid_summary_response(call, refs),
                "call": call,
            }
            report["summary"]["calls"].append(entry)
            if _provider_unavailable(call):
                report["stopped"] = {
                    "stage": "summary", "reason": call["error"]["kind"],
                    "model_switched": False,
                }
                report["cache_replay"] = {"skipped": True, "reason": "provider unavailable"}
                return _finish_report(report, output_path)
            replay_requests.append((summary_config, SUMMARY_PREFIXES[locale], dynamic,
                                    SUMMARY_SCHEMA, trace))

    no_calls = _NoCalls()
    replay_hits = 0
    for config, prefix, dynamic, schema, trace in replay_requests:
        value = _execute_cached(
            no_calls, cache, config, prefix=prefix, dynamic=dynamic, schema=schema,
            trace_label=trace,
        )
        replay_hits += bool(value["cache_hit"])
    report["cache_replay"] = {
        "requests": len(replay_requests),
        "hits": replay_hits,
        "executor_calls": no_calls.calls,
    }
    return _finish_report(report, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--credential-file", type=Path, default=DEFAULT_CREDENTIALS)
    args = parser.parse_args()
    manifest = json.loads(args.input.read_text())
    load_env(args.credential_file)
    result = run_experiment(
        manifest,
        executor={
            "order": StructuredExecutor(grok_config(cache_key="lean-exposition-t10-order")),
            "summary": StructuredExecutor(grok_config(cache_key="lean-exposition-t10-summary")),
        },
        output_path=args.output,
        cache_path=args.cache,
    )
    print(json.dumps({
        "input_digest": result["input_digest"],
        "order_pairs": len(result["order"]["pairs"]),
        "summary_calls": len(result["summary"]["calls"]),
        "usage": result["usage"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
