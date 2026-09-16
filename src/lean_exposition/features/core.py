"""Declaration observations and deduplicated structural summaries."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from lean_exposition.models import DeclRef
from lean_exposition.structure import DependencyGraph, Hierarchy

REWRITE_KINDS = frozenset({"Lean.Parser.Tactic.rwSeq", "Lean.Parser.Tactic.tacticRwa__",
    "Lean.Parser.Tactic.simp", "Lean.Parser.Tactic.simpAll", "Lean.Parser.Tactic.simpa", "Lean.Parser.Tactic.calc"})


def ref_key(ref):
    return json.dumps(asdict(ref) if isinstance(ref, DeclRef) else ref, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


FEATURE_CONFIG = {
    "formal_counting": "unicode_codepoints_in_stored_statement_and_proof_segments",
    "dependency_counting": "deduplicated_declaration_pairs",
    "rewrite_kinds": sorted(REWRITE_KINDS),
    "rewrite_measurement": "exact_parser_kind_occurrences",
}
FEATURE_CONFIG_DIGEST = digest(FEATURE_CONFIG)


def observation(value, scope, *, reason=None, status=None):
    return {"value": value, "status": status or ("present" if value is not None else "missing"),
            "measurement_scope": scope, "reason": reason}


def text_observation(part, field, scope):
    if part is None:
        return observation(0, scope, status="not_applicable")
    text = getattr(part, field)
    return observation(len(text.text) if text.text is not None else (0 if text.status == "not_applicable" else None),
                       scope, status=text.status, reason=text.reason)


def aggregate_observations(observations, scope):
    values = [o["value"] for o in observations if o["value"] is not None]
    complete = len(values) == len(observations)
    return {"value": sum(values) if complete else None, "observed_sum": sum(values),
            "status": "present" if complete else "partial" if values else "missing",
            "observed_count": len(values), "total_count": len(observations),
            "coverage": len(values) / len(observations) if observations else 1,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None,
            "measurement_scope": scope}


@dataclass(frozen=True)
class FeatureSet:
    repo_key: str
    hierarchy_id: str
    workspace_digest: str
    config_digest: str
    declarations: dict
    nodes: dict
    metadata: dict

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    @classmethod
    def from_dict(cls, data):
        value = cls(**json.loads(json.dumps(data)))
        if value.config_digest != FEATURE_CONFIG_DIGEST:
            raise ValueError("feature config digest does not match the current extractor")
        return value

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))


def extract_features(workspace, hierarchy, *, compiled=None):
    """Pure production extraction. Compiled evidence is explicit, never auto-loaded."""
    hierarchy = Hierarchy.from_dict(hierarchy.to_dict() if hasattr(hierarchy, "to_dict") else hierarchy).to_dict()
    repo_key = hierarchy["repo_key"]
    workspace_digest = workspace.digest()
    if hierarchy.get("workspace_digest") != workspace_digest:
        raise ValueError("hierarchy does not match the fixed workspace")
    compiled_rows = {}
    if compiled is not None:
        if compiled.get("workspace_digest") != workspace_digest or compiled.get("repo_key") != repo_key:
            raise ValueError("compiled evidence does not match the fixed workspace/repository")
        if compiled.get("config_digest") != FEATURE_CONFIG_DIGEST:
            raise ValueError("compiled feature config digest mismatch")
        compiled_rows = {row["lean_name"]: row for row in compiled["rows"]}
    graph = DependencyGraph.from_workspace(workspace)
    all_decls = {d.ref: d for d in workspace.declarations}
    target = {r: d for r, d in all_decls.items() if r.repo_key == repo_key}
    records = {}
    for ref, decl in sorted(target.items(), key=lambda item: item[0].local_id):
        lc = any(p.method.startswith("lc_") for p in decl.provenance)
        scope = "lc_registered_formal_segment" if lc else "native_declaration_source_segment"
        metrics = {}
        for field, prefix in (("formal", "formal"), ("nl", "original_nl")):
            for name in ("statement", "proof"):
                metrics[f"{prefix}_{name}_codepoints"] = text_observation(getattr(decl, name), field,
                    scope if field == "formal" else "original_natural_language_segment")
            metrics[f"{prefix}_material_codepoints"] = aggregate_observations(
                [metrics[f"{prefix}_{name}_codepoints"] for name in ("statement", "proof")],
                "sum_of_stored_segments_not_source_range_union")
        row = compiled_rows.get(decl.lean_name)
        reason = "No matched compiled observation was supplied for this declaration."
        for category in ("object_parameter", "proof_premise", "instance", "type_parameter", "proposition_parameter"):
            value = sum(b["category"] == category for b in row["binders"]) if row else None
            metrics["binder_" + category] = observation(value, "full_elaborated_telescope", reason=None if row else reason)
        for name in ("type_expr_nodes", "type_expr_depth"):
            metrics[name] = observation(row[name] if row else None, "full_elaborated_type_expr", reason=None if row else reason)
        syntax = row.get("syntax") if row else None
        parsed = syntax and syntax["status"] == "parsed"
        metrics["rewrite_simplify_syntax_nodes"] = observation(
            sum(k in REWRITE_KINDS for k in syntax["tactic_kinds"]) if parsed else None,
            "exact_parser_kind_occurrences_not_executed_tactics", reason=None if parsed else (syntax or {}).get("reason", reason))
        incoming = graph.incoming[ref]
        metrics["internal_dependency_pairs"] = observation(sum(e.provider in target for e in incoming), "deduplicated_decl_pairs")
        metrics["external_dependency_pairs"] = observation(sum(e.provider.repo_key != repo_key for e in incoming), "deduplicated_decl_pairs")
        metrics["unresolved_local_dependency_pairs"] = observation(sum(e.provider.repo_key == repo_key and e.provider not in target for e in incoming), "deduplicated_decl_pairs")
        records[ref_key(ref)] = {"ref": asdict(ref), "kind": decl.kind, "technical": decl.extraction_status.state == "compiler_only",
            "source_digest": digest(asdict(decl)), "metrics": metrics,
            "type_metadata": {"binders": row["binders"], "full_type": row["full_type"]} if row else None}
    nodes = {}
    index = {n["id"]: n for n in hierarchy["nodes"]}
    def statement_size(refs):
        return aggregate_observations([text_observation(all_decls.get(ref).statement if ref in all_decls else None,
            "formal", "interface_statement") if ref in all_decls else observation(None, "interface_statement", reason="Unloaded interface declaration")
            for ref in sorted(refs, key=lambda r: (r.repo_key, r.local_id))], "deduplicated_interface_statements")
    for node in hierarchy["nodes"]:
        refs = {DeclRef(**r) for r in node["decl_refs"]}
        if not refs <= set(target):
            raise ValueError("hierarchy feature coverage is outside loaded target declarations")
        rows = [records[ref_key(ref)] for ref in sorted(refs, key=lambda r: r.local_id)]
        aggregated = {name: aggregate_observations([row["metrics"][name] for row in rows], "deduplicated_member_declarations")
                      for name in next(iter(records.values()))["metrics"]} if records else {}
        boundary = graph.boundary(refs)
        children = {DeclRef(**r): child for child in node["children"] for r in index[child]["decl_refs"]}
        cross = [e for e in boundary.internal if children.get(e.provider) != children.get(e.consumer)]
        interface_refs = set(boundary.input_decls) | set(boundary.output_decls)
        interface_refs.update(ref for e in cross for ref in (e.provider, e.consumer))
        # An isolated region still exposes local terminal interfaces, not zero material.
        if not interface_refs:
            interface_refs.update(ref for ref in refs if target[ref].local_public)
        if not interface_refs and refs:
            internal_consumed = {e.provider for e in boundary.internal}
            interface_refs.update(refs - internal_consumed or refs)
        nodes[node["id"]] = {"decl_count": len(refs), "metrics": aggregated,
            "interface": {"input_refs": [asdict(r) for r in sorted(boundary.input_decls, key=ref_key)],
                          "output_refs": [asdict(r) for r in sorted(boundary.output_decls, key=ref_key)],
                          "internal_pairs": len(boundary.internal), "incoming_pairs": len(boundary.incoming),
                          "outgoing_pairs": len(boundary.outgoing), "cross_child_pairs": len(cross),
                          "statement_material": statement_size(interface_refs)},
            "cost_material": aggregated.get("formal_material_codepoints") if node["kind"] == "unit" else statement_size(interface_refs)}
    return FeatureSet(repo_key, hierarchy["hierarchy_id"], workspace_digest,
        FEATURE_CONFIG_DIGEST, records, nodes, {"compiled_declarations": len(compiled_rows),
        "rewrite_kinds": sorted(REWRITE_KINDS),
        "formal_counting": "Unicode codepoints in original stored statement/proof segments; not source union or generated EET",
        "lc_scope": "Registered formal segments may cover a registered file, not solely the representative declaration"})
