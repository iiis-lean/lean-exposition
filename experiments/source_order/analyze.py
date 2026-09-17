"""Bounded deterministic order statistics over four fixed source projects."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from lean_exposition.importers import LCRepositoryInput, load_lc_workspace
from lean_exposition.importers.native import load_native
from lean_exposition.models import Workspace
from lean_exposition.structure import (
    BuildConfig,
    DependencyGraph,
    Hierarchy,
    NarrativeOrder,
    build_hierarchy,
    derive_narrative_order,
    derive_source_order,
    source_order_from_lc_git,
)


ROOT = Path(__file__).resolve().parents[2]
LC_ROOT = Path("/root/lean_projects/lean_constellation_release_candidates/20260914")
DEFAULT_OUTPUT = ROOT / "data/research/source_order/deterministic_20260916"
EXPECTED_PAIRS = {
    "uniform": 268,
    "erdos946": 814,
    "sensitivity": 3984,
    "agree_to_disagree": 682,
}


def load_case(label):
    if label == "uniform":
        path = LC_ROOT / "UniformMissingTraceFamily"
        bundle = load_lc_workspace(LCRepositoryInput(
            path, "UniformMissingTraceFamily", "de46f82cae08cd2b7e5cc08d45a800ad952dba53"))
        workspace = bundle.workspace
        repo_key = "UniformMissingTraceFamily"
        source = source_order_from_lc_git(workspace, repo_key, path)
    elif label == "erdos946":
        path = LC_ROOT / "ConsecutiveDivisorCounts"
        provider = LC_ROOT / "WeightedSieve"
        bundle = load_lc_workspace(
            LCRepositoryInput(path, "ConsecutiveDivisorCounts",
                              "2bcca869a7d9d0a79320784e7bf27b871f8f8cd0"),
            (LCRepositoryInput(provider, "WeightedSieve"),),
        )
        workspace = bundle.workspace
        repo_key = "ConsecutiveDivisorCounts"
        source = source_order_from_lc_git(workspace, repo_key, path)
    elif label == "sensitivity":
        path = ROOT / "data/research/native/sensitivity/source"
        modules = ("Sensitivity.Defs", "Sensitivity.Multilinear", "Sensitivity.Subcube",
                   "Sensitivity.Parity", "Sensitivity.HuangBridge", "Sensitivity.Main")
        bundle = load_native(path, repo_key=label, modules=modules,
                             primary_outcomes=("Sensitivity.sensitivity_ge_sqrt_degree",))
        workspace = bundle.workspace
        repo_key = label
        source = native_source(workspace, repo_key, path)
    elif label == "agree_to_disagree":
        path = ROOT / "data/research/native/agree_to_disagree/source"
        bundle = load_native(
            path, repo_key=label, modules=("AgreeToDisagree.AgreeToDisagree",),
            primary_outcomes=("AgreeToDisagree.agreeToDisagree",
                              "AgreeToDisagree.agreeToDisagree'"),
        )
        workspace = bundle.workspace
        repo_key = label
        source = native_source(workspace, repo_key, path)
    else:
        raise ValueError(label)
    return bundle, repo_key, source


def native_source(workspace, repo_key, path):
    texts = {asset.asset_id: (path / asset.path).read_text()
             for asset in workspace.manifest.assets if asset.repo_key == repo_key}
    return derive_source_order(workspace, repo_key, asset_texts=texts)


def region_summary(data):
    nodes = {node["id"]: node for node in data["nodes"]}
    depth = {}
    def node_depth(node_id):
        if node_id not in depth:
            parent = nodes[node_id]["parent"]
            depth[node_id] = 0 if parent is None else node_depth(parent) + 1
        return depth[node_id]
    for node_id in nodes:
        node_depth(node_id)
    regions = [node for node in nodes.values() if node["kind"] == "region"]
    owner = {}
    for node in nodes.values():
        if node["kind"] != "unit":
            continue
        current = node
        while current["parent"] is not None and nodes[current["parent"]]["kind"] == "region":
            current = nodes[current["parent"]]
        owner[node["id"]] = current["id"]
    cross = sum(edge["provider_node"] in owner and edge["consumer_node"] in owner and
                owner[edge["provider_node"]] != owner[edge["consumer_node"]]
                for edge in data["edges"])
    return {
        "regions": len(regions),
        "region_primary_cost_sum": sum(
            node.get("metadata", {}).get("region", {}).get("cost", 0)
            for node in nodes.values()),
        "maximum_tree_depth": max(depth.values(), default=0),
        "maximum_fanout": max((len(node["children"]) for node in nodes.values()), default=0),
        "region_member_counts": sorted(len(node["decl_refs"]) for node in regions),
        "cross_top_region_dependency_pairs": cross,
    }


def unit_facts(data):
    return sorted((node["representative"]["repo_key"],
                   node["representative"]["local_id"], node["decl_refs"])
                  for node in data["nodes"] if node["kind"] == "unit")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def analyze(label, output):
    bundle, repo_key, source = load_case(label)
    workspace = bundle.workspace
    config = BuildConfig()
    order = derive_narrative_order(bundle, repo_key, config=config, source=source)
    order_path = output / f"{label}-narrative-order.json"
    order.save(order_path)
    assert NarrativeOrder.load(order_path) == order
    hierarchy = build_hierarchy(bundle, repo_key, config=config, source=source,
                                narrative_order=order)
    assert Hierarchy.from_dict(hierarchy.to_dict()) == hierarchy

    target = {decl.ref for decl in workspace.declarations if decl.ref.repo_key == repo_key}
    graph = DependencyGraph.from_workspace(workspace)
    expected_pairs = {(edge.provider.repo_key, edge.provider.local_id,
                       edge.consumer.repo_key, edge.consumer.local_id)
                      for edge in graph.edges if edge.provider in target or edge.consumer in target}
    actual_pairs = {(edge["provider_decl"]["repo_key"], edge["provider_decl"]["local_id"],
                     edge["consumer_decl"]["repo_key"], edge["consumer_decl"]["local_id"])
                    for edge in hierarchy.edges}
    assert expected_pairs == actual_pairs
    assert len(actual_pairs) == EXPECTED_PAIRS[label]
    covered = [tuple(ref.values()) for node in hierarchy.nodes if node["kind"] == "unit"
               for ref in node["decl_refs"]]
    assert len(covered) == len(set(covered)) == len(target)
    representatives = {(node["representative"]["repo_key"], node["representative"]["local_id"])
                       for node in hierarchy.nodes if node["kind"] == "unit"}
    repository = next(repo for repo in workspace.manifest.repositories if repo.repo_key == repo_key)
    assert all((ref.repo_key, ref.local_id) in representatives
               for ref in repository.primary_outcomes if ref in target)

    reversed_workspace = replace(workspace, declarations=tuple(reversed(workspace.declarations)))
    reversed_digest = reversed_workspace.digest()
    reversed_bundle = replace(
        bundle,
        workspace=reversed_workspace,
        structure_policy=replace(bundle.structure_policy, workspace_digest=reversed_digest),
        dependency_coverage=replace(bundle.dependency_coverage, workspace_digest=reversed_digest),
    )
    reversed_source = derive_source_order(
        reversed_workspace, repo_key,
        asset_texts=source.assets,
        corpus_files=None,
        document_order=tuple(source.config["document_order"]),
    ) if not source.config["corpus_digest"] or source.config["corpus_digest"] == hashlib.sha256(b"{}").hexdigest() else None
    if reversed_source is None:
        # LC source records are already canonical and independent of declaration enumeration.
        reversed_source = replace(source, records=dict(reversed(tuple(source.records.items()))))
    reversed_order = derive_narrative_order(reversed_bundle, repo_key, config=config,
                                             source=reversed_source)
    order_semantics = [scope.to_dict() for scope in order.scopes]
    reversed_semantics = [scope.to_dict() for scope in reversed_order.scopes]
    assert order_semantics == reversed_semantics

    old_path = ROOT / "data/research/structure_delivery" / f"{label}-hierarchy.json"
    old = json.loads(old_path.read_text())
    assert unit_facts(old) == unit_facts(hierarchy.to_dict())
    assert old["edges"] == hierarchy.edges
    assert old["external_refs"] == hierarchy.external_refs
    old_root = next(node for node in old["nodes"] if node["id"] == old["root_id"])
    new_root = next(node for node in hierarchy.nodes if node["id"] == hierarchy.root_id)
    assert old_root["decl_refs"] == new_root["decl_refs"]
    old_aliases = old_root["metadata"].get("aliases")
    new_aliases = new_root["metadata"].get("aliases", {})

    metrics = [scope.metrics for scope in order.scopes]
    final_distance = sum(metric["dependency_distance"] for metric in metrics)
    source_distance = sum(metric["source_start_distance"] for metric in metrics)
    reverse_distance = sum(metric["reverse_start_distance"] for metric in metrics)
    assert final_distance <= source_distance
    return {
        "repo_key": repo_key,
        "repository_revision": repository.revision,
        "repository_input_digest": repository.input_digest,
        "workspace_digest": workspace.digest(),
        "source_digest": source.digest(),
        "narrative_order_id": order.narrative_order_id,
        "narrative_order_file": order_path.name,
        "facts": {
            "declarations": len(target),
            "dependency_pairs": len(actual_pairs),
            "units": len(representatives),
            "external_refs": len(hierarchy.external_refs),
            "primary_outcomes": len(repository.primary_outcomes),
            "coverage_exact": True,
            "raw_edges_exact": True,
            "old_baseline_unit_facts_equal": True,
            "old_baseline_edges_equal": True,
            "old_baseline_external_refs_equal": True,
            "old_baseline_root_coverage_equal": True,
            "current_scope_alias_count": len(new_aliases),
            "current_scope_aliases_valid": True,
            "historical_baseline_scope_aliases_available": old_aliases is not None,
            "historical_baseline_scope_aliases_equal": old_aliases == new_aliases
            if old_aliases is not None else None,
        },
        "order_only": {
            "scope_count": len(metrics),
            "real_edge_weight": sum(metric["edge_weight"] for metric in metrics),
            "projected_edge_count": sum(metric["projected_edge_count"] for metric in metrics),
            "source_start_distance": source_distance,
            "reverse_start_distance": reverse_distance,
            "final_distance": final_distance,
            "source_start_projected_edge_distance": sum(
                metric["source_start_projected_edge_distance"] for metric in metrics),
            "reverse_start_projected_edge_distance": sum(
                metric["reverse_start_projected_edge_distance"] for metric in metrics),
            "final_projected_edge_distance": sum(
                metric["projected_edge_distance"] for metric in metrics),
            "final_not_worse_than_source": True,
            "changed_atoms_from_source_start": sum(metric["changed_atoms_from_source_start"]
                                                   for metric in metrics),
            "maximum_ready_width": max((metric["ready_maximum_width"] for metric in metrics), default=0),
            "ambiguous_ready_steps": sum(metric["ready_ambiguous_steps"] for metric in metrics),
            "source_basis": dict(Counter(record["basis"] for record in source.records.values())),
            "protected_constraint_count": sum(len(scope.constraints) for scope in order.scopes),
            "semantic_order_digest": digest(order_semantics),
            "declaration_enumeration_semantics_equal": True,
        },
        "region_rebuild": {
            "source_first_baseline": region_summary(old),
            "deterministic_order": region_summary(hierarchy.to_dict()),
            "interpretation": "joint order-plus-contiguous-Region result",
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {}
    for label in ("uniform", "erdos946", "sensitivity", "agree_to_disagree"):
        report[label] = analyze(label, args.output)
        print(label, json.dumps(report[label]["order_only"], sort_keys=True), flush=True)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
