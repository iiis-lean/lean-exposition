"""Freeze bounded ambiguous local order cases before any live API call."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from analyze import ROOT, load_case
from lean_exposition.structure import BuildConfig, DependencyGraph, NarrativeOrder, build_hierarchy


DEFAULT_ORDERS = ROOT / "data/research/source_order/deterministic_20260916"
DEFAULT_OUTPUT = ROOT / "data/research/source_order/api_eval_20260916/selection.json"
LABELS = ("uniform", "erdos946", "sensitivity", "agree_to_disagree")


def ref_tuple(value):
    return value["repo_key"], value["local_id"]


def normalized_text(value, limit=360):
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def atom_card(atom_id, opaque_id, node, declarations, source_record):
    refs = [ref_tuple(value) for value in node["decl_refs"]]
    members = [declarations[ref] for ref in refs]
    members.sort(key=lambda decl: (decl.module, decl.lean_name, decl.ref.local_id))
    items = []
    for declaration in members[:3]:
        nl = declaration.statement.nl.text if declaration.statement.nl.status == "present" else None
        formal = declaration.statement.formal.text if declaration.statement.formal.status == "present" else None
        items.append({
            "name": declaration.lean_name,
            "kind": declaration.kind,
            "module": declaration.module,
            "statement": normalized_text(nl or formal or "Source statement unavailable."),
        })
    return {
        "opaque_id": opaque_id,
        "title": node["title"],
        "kind": node["kind"],
        "member_count": len(refs),
        "items": items,
        "source_basis": source_record["basis"],
        "source_role": source_record["role"],
    }


def legal(order, edges):
    positions = {atom: index for index, atom in enumerate(order)}
    return all(positions[provider] < positions[consumer] for provider, consumer in edges)


def distance(order, weights):
    positions = {atom: index for index, atom in enumerate(order)}
    return sum(weight * (positions[consumer] - positions[provider])
               for (provider, consumer), weight in weights.items())


def project_weights(workspace, repo_key, atoms, nodes):
    owner = {}
    for atom in atoms:
        for value in nodes[atom]["decl_refs"]:
            key = ref_tuple(value)
            if key in owner:
                raise ValueError(f"overlapping atom coverage: {key}")
            owner[key] = atom
    weights = Counter()
    for edge in DependencyGraph.from_workspace(workspace).edges:
        provider = (edge.provider.repo_key, edge.provider.local_id)
        consumer = (edge.consumer.repo_key, edge.consumer.local_id)
        if provider in owner and consumer in owner and owner[provider] != owner[consumer]:
            weights[owner[provider], owner[consumer]] += 1
    return dict(weights)


def select_case(label, order_dir):
    bundle, repo_key, source = load_case(label)
    workspace = bundle.workspace
    artifact = NarrativeOrder.load(order_dir / f"{label}-narrative-order.json")
    hierarchy = build_hierarchy(bundle, repo_key, config=BuildConfig(), source=source,
                                narrative_order=artifact)
    nodes = {node["id"]: node for node in hierarchy.nodes}
    declarations = {(decl.ref.repo_key, decl.ref.local_id): decl for decl in workspace.declarations}
    historical = json.loads((ROOT / "data/research/structure_delivery" /
                             f"{label}-hierarchy.json").read_text())
    historical_nodes = {node["id"]: node for node in historical["nodes"]}
    eligible = []
    for scope in artifact.scopes:
        old_parent = historical_nodes.get(scope.parent_id)
        if old_parent is None:
            continue
        baseline = tuple(old_parent.get("metadata", {}).get("region", {}).get("atom_order", ()))
        if set(baseline) != set(scope.atoms) or baseline == scope.order:
            continue
        weights = project_weights(workspace, repo_key, scope.atoms, nodes)
        hard_edges = set(weights)
        if not legal(baseline, hard_edges) or not legal(scope.order, hard_edges):
            raise AssertionError(f"stored order is not legal: {label}:{scope.scope_id}")
        final_positions = {atom: index for index, atom in enumerate(scope.order)}
        for size in range(3, min(6, len(baseline)) + 1):
            for start in range(len(baseline) - size + 1):
                selected = baseline[start:start + size]
                selected_set = set(selected)
                final = tuple(atom for atom in scope.order if atom in selected_set)
                final_indices = sorted(final_positions[atom] for atom in selected)
                if final_indices != list(range(final_indices[0], final_indices[0] + size)):
                    continue
                local_weights = {pair: weight for pair, weight in weights.items()
                                 if pair[0] in selected_set and pair[1] in selected_set}
                if not local_weights or selected == final:
                    continue
                source_distance = distance(selected, local_weights)
                final_distance = distance(final, local_weights)
                if final_distance >= source_distance:
                    continue
                if not legal(selected, local_weights) or not legal(final, local_weights):
                    continue
                eligible.append({
                    "scope_id": scope.scope_id,
                    "parent_id": scope.parent_id,
                    "atoms": selected,
                    "source_order": selected,
                    "deterministic_order": final,
                    "weights": local_weights,
                    "source_distance": source_distance,
                    "deterministic_distance": final_distance,
                    "improvement": source_distance - final_distance,
                })
    if not eligible:
        return None
    chosen = min(eligible, key=lambda item: (-item["improvement"], len(item["atoms"]),
                                             item["scope_id"], item["atoms"]))
    opaque = {atom: f"N{index + 1}" for index, atom in enumerate(sorted(chosen["atoms"]))}
    scope = next(scope for scope in artifact.scopes
                 if (scope.scope_id, scope.parent_id) == (chosen["scope_id"], chosen["parent_id"]))
    cards = {}
    for atom in chosen["atoms"]:
        cards[opaque[atom]] = atom_card(
            atom, opaque[atom], nodes[atom], declarations,
            {"basis": scope.source_basis[atom], "role": scope.source_roles[atom]},
        )
    dependencies = [{"provider": opaque[provider], "consumer": opaque[consumer], "weight": weight}
                    for (provider, consumer), weight in sorted(chosen["weights"].items())]
    return {
        "case_id": label + ":" + hashlib.sha256(
            json.dumps([chosen["scope_id"], chosen["atoms"]], sort_keys=True).encode()).hexdigest()[:12],
        "project": label,
        "repo_key": repo_key,
        "workspace_digest": workspace.digest(),
        "narrative_order_id": artifact.narrative_order_id,
        "scope_id": chosen["scope_id"],
        "parent_id": chosen["parent_id"],
        "atom_count": len(chosen["atoms"]),
        "atom_ids": {opaque[atom]: atom for atom in sorted(chosen["atoms"])},
        "cards": cards,
        "hard_dependencies": dependencies,
        "source_order": [opaque[atom] for atom in chosen["source_order"]],
        "deterministic_order": [opaque[atom] for atom in chosen["deterministic_order"]],
        "selection_metrics": {
            "source_distance": chosen["source_distance"],
            "deterministic_distance": chosen["deterministic_distance"],
            "improvement": chosen["improvement"],
            "eligible_windows_in_project": len(eligible),
        },
    }


def prepare(order_dir, output):
    cases = [case for label in LABELS if (case := select_case(label, order_dir)) is not None]
    hash_order = sorted(cases, key=lambda case: hashlib.sha256(case["case_id"].encode()).hexdigest())
    for index, case in enumerate(hash_order):
        source_label = "A" if index % 2 == 0 else "B"
        deterministic_label = "B" if source_label == "A" else "A"
        case["anonymous_labels"] = {
            "source": source_label,
            "deterministic": deterministic_label,
        }
    reader_case = min(cases, key=lambda case: (-case["selection_metrics"]["improvement"],
                                               case["case_id"])) if cases else None
    result = {
        "protocol": {
            "projects": list(LABELS),
            "window_sizes": [3, 4, 5, 6],
            "requires_contiguous_in_both_orders": True,
            "requires_different_legal_orders": True,
            "requires_internal_real_edge": True,
            "requires_strict_pair_weighted_improvement": True,
            "selection": "one per project: improvement desc, size asc, scope and atoms asc",
            "labeling": "sha256(case_id) order, alternating source=A/source=B",
            "reader_selection": "largest structural improvement before blind output",
        },
        "cases": sorted(cases, key=lambda case: case["case_id"]),
        "reader_case_id": reader_case["case_id"] if reader_case else None,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=DEFAULT_ORDERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    selection = prepare(args.orders, args.output)
    print(json.dumps({
        "cases": [{"case_id": case["case_id"], "project": case["project"],
                   "atoms": case["atom_count"], **case["selection_metrics"]}
                  for case in selection["cases"]],
        "reader_case_id": selection["reader_case_id"],
    }, ensure_ascii=False, sort_keys=True))
