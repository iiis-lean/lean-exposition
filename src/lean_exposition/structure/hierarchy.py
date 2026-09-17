"""Build a single-repository hierarchy without mutating declaration facts."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from lean_exposition.construction import (
    DependencyCoverage, RepositoryBuildBundle, StructurePolicy,
)
from lean_exposition.construction.materials import MaterialBundle
from lean_exposition.models import Workspace
from .graph import DependencyGraph, _order
from .helper import BuildConfig, aggregate_helpers
from .order import (
    NarrativeOrder, OrderEdge, OrderEvidenceBundle, OrderProblem, OrderSubject,
    ProjectedOrderEvidence, ProjectedOrderRelation, SubjectProjection,
    material_subject_projections, project_order_evidence, solve_order,
    validate_scope_order,
)
from .regions import partition_regions
from .source import SourceSequenceSpec, derive_source_order


def stable_id(kind, value):
    return kind + ":" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class HierarchyCycleError(ValueError):
    def __init__(self, scope_id, ordering):
        self.scope_id = scope_id
        self.cycles = ordering.cycles
        self.witnesses = [list(c.witness) for c in ordering.cycles]
        super().__init__(f"cyclic hierarchy quotient at {scope_id}: {self.witnesses}")


@dataclass(frozen=True)
class Hierarchy:
    repo_key: str
    root_id: str
    hierarchy_id: str
    workspace_digest: str
    source_digest: str
    config_digest: str
    config: dict
    nodes: list
    edges: list
    external_refs: list
    diagnostics: list

    def to_dict(self):
        return asdict(self)

    def resolve_node_id(self, reference):
        """Resolve a current node, raw scope ID or removed scope-node ID."""
        nodes = {node["id"]: node for node in self.nodes}
        if reference in nodes:
            return reference
        aliases = nodes[self.root_id].get("metadata", {}).get("aliases", {})
        if reference not in aliases or aliases[reference] not in nodes:
            raise KeyError(reference)
        return aliases[reference]

    @classmethod
    def from_dict(cls, data):
        value = cls(**json.loads(json.dumps(data)))
        if value.config_digest != _digest(value.config):
            raise ValueError("hierarchy config digest mismatch")
        for name in ("workspace_digest", "source_digest", "config_digest"):
            digest_value = getattr(value, name)
            if len(digest_value) != 64 or any(character not in "0123456789abcdef" for character in digest_value):
                raise ValueError(f"invalid hierarchy {name}")
        nodes = {n["id"]: n for n in value.nodes}
        if len(nodes) != len(value.nodes) or value.root_id not in nodes:
            raise ValueError("duplicate nodes or absent root")
        visited = set()
        def walk(node_id, parent):
            if node_id in visited or node_id not in nodes:
                raise ValueError("cyclic, duplicated or missing child")
            visited.add(node_id)
            node = nodes[node_id]
            if node["parent"] != parent:
                raise ValueError("inconsistent parent")
            refs = {(r["repo_key"], r["local_id"]) for r in node["decl_refs"]}
            if len(refs) != len(node["decl_refs"]) or any(r[0] != value.repo_key for r in refs):
                raise ValueError("invalid declaration coverage")
            if value.config.get("scope_compression") == "unary" and len(node["children"]) == 1:
                if node["kind"] in {"scope", "region"}:
                    raise ValueError("normalized hierarchy contains a unary scope or Region")
                if node["kind"] == "repo" and nodes.get(node["children"][0], {}).get("kind") == "scope":
                    raise ValueError("normalized repository retains a unary scope wrapper")
            if node["kind"] == "unit":
                if node["children"] or tuple(node["representative"][k] for k in ("repo_key", "local_id")) not in refs:
                    raise ValueError("invalid unit")
            else:
                combined = set()
                for child in node["children"]:
                    child_refs = walk(child, node_id)
                    if combined & child_refs:
                        raise ValueError("overlapping child coverage")
                    combined.update(child_refs)
                if combined != refs:
                    raise ValueError("incomplete child coverage")
            return refs
        walk(value.root_id, None)
        if visited != set(nodes):
            raise ValueError("orphan node")
        aliases = nodes[value.root_id].get("metadata", {}).get("aliases", {})
        if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(target, str) or target not in nodes
                                               for key, target in aliases.items()):
            raise ValueError("scope aliases must directly resolve to surviving nodes")
        if any(key in nodes and key != target for key, target in aliases.items()):
            raise ValueError("scope alias shadows a current node identity")
        externals = {e["id"]: e for e in value.external_refs}
        if len(externals) != len(value.external_refs) or set(externals) & set(nodes):
            raise ValueError("duplicate or internal external identity")
        if any(e["ref"]["repo_key"] == value.repo_key and e["loaded"] for e in externals.values()):
            raise ValueError("loaded local reference must belong to the tree")
        endpoints = set(nodes) | set(externals)
        if len({e["id"] for e in value.edges}) != len(value.edges):
            raise ValueError("duplicate edge identity")
        if any(e[side] not in endpoints for e in value.edges for side in ("provider_node", "consumer_node")):
            raise ValueError("unresolved edge endpoint")
        for edge in value.edges:
            if edge["provider_decl"]["repo_key"] != value.repo_key and edge["consumer_decl"]["repo_key"] != value.repo_key:
                raise ValueError("edge does not touch target repository")
            for role in ("provider", "consumer"):
                endpoint = edge[role + "_node"]
                ref = edge[role + "_decl"]
                if endpoint in nodes:
                    if nodes[endpoint]["kind"] != "unit" or ref not in nodes[endpoint]["decl_refs"]:
                        raise ValueError("edge endpoint does not own declaration")
                elif ref != externals[endpoint]["ref"]:
                    raise ValueError("external endpoint reference mismatch")
        if value.hierarchy_id != _hierarchy_id(value):
            raise ValueError("hierarchy identity mismatch")
        return value

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))


def build_hierarchy(build_input, repo_key, *, structure_policy=None, dependency_coverage=None,
                    config=None, source=None, source_spec=None,
                    narrative_order=None, materials=None, order_evidence=None,
                    keep_separate=(), edge_weights=None,
                    region_burden_features=None):
    workspace, policy, coverage = _resolve_build_input(
        build_input, structure_policy, dependency_coverage)
    if not policy.production_structure:
        raise ValueError(
            "provisional source-only bundles support diagnostic graph/order inspection, not production Regions"
        )
    hierarchy, _ = _construct(workspace, repo_key, policy, coverage,
                              config=config, source=source, source_spec=source_spec,
                              narrative_order=narrative_order, materials=materials,
                              order_evidence=order_evidence, keep_separate=keep_separate,
                              edge_weights=edge_weights, region_burden_features=region_burden_features)
    return hierarchy


def derive_narrative_order(build_input, repo_key, *, structure_policy=None, dependency_coverage=None,
                           config=None, source=None, source_spec=None,
                           materials=None, order_evidence=None, keep_separate=()):
    workspace, policy, coverage = _resolve_build_input(
        build_input, structure_policy, dependency_coverage)
    _, order = _construct(workspace, repo_key, policy, coverage,
                          config=config, source=source, source_spec=source_spec,
                          materials=materials, order_evidence=order_evidence,
                          keep_separate=keep_separate, order_only=True)
    return order


def _resolve_build_input(build_input, structure_policy, dependency_coverage):
    if isinstance(build_input, RepositoryBuildBundle):
        if structure_policy is not None or dependency_coverage is not None:
            raise ValueError("bundle input cannot be combined with explicit construction sidecars")
        build_input.validate()
        return (build_input.workspace, build_input.structure_policy,
                build_input.dependency_coverage)
    if not isinstance(build_input, Workspace):
        raise TypeError("structure input must be RepositoryBuildBundle or Workspace")
    if not isinstance(structure_policy, StructurePolicy) or not isinstance(
            dependency_coverage, DependencyCoverage):
        raise ValueError("Workspace input requires explicit StructurePolicy and DependencyCoverage")
    build_input.validate()
    structure_policy.validate()
    if structure_policy.workspace_digest != build_input.digest():
        raise ValueError("stale StructurePolicy workspace digest")
    dependency_coverage.validate(build_input)
    return build_input, structure_policy, dependency_coverage


def _construct(workspace, repo_key, structure_policy, dependency_coverage, *,
               config=None, source=None, source_spec=None,
               narrative_order=None, materials=None, order_evidence=None,
               keep_separate=(), edge_weights=None,
               region_burden_features=None, order_only=False):
    config = config or BuildConfig()
    graph = DependencyGraph.from_workspace(workspace)
    repo = next(r for r in workspace.manifest.repositories if r.repo_key == repo_key)
    if repo.root_scope is None:
        raise ValueError("cannot construct an unloaded repository")
    if source_spec is not None and not isinstance(source_spec, SourceSequenceSpec):
        source_spec = SourceSequenceSpec.from_dict(source_spec)
    if (materials is None) != (order_evidence is None):
        raise ValueError("materials and order evidence must be supplied together")
    if materials is not None:
        if not isinstance(materials, MaterialBundle):
            materials = MaterialBundle.from_dict(materials)
        if not isinstance(order_evidence, OrderEvidenceBundle):
            order_evidence = OrderEvidenceBundle.from_dict(order_evidence)
        materials.validate()
        if materials.repo_key != repo_key:
            raise ValueError("material bundle belongs to another repository")
        if (order_evidence.material_digest != materials.material_digest() or
                order_evidence.binding_digest != materials.binding_digest()):
            raise ValueError("order evidence does not match the fixed material bundle")
    source = source or derive_source_order(workspace, repo_key, source_spec=source_spec)
    if source_spec is not None and (source.sequence_spec is None or
                                    source.sequence_spec.digest() != source_spec.digest()):
        raise ValueError("source order does not match source sequence spec")
    decls = {d.ref: d for d in workspace.declarations if d.ref.repo_key == repo_key}
    overrides = dict(edge_weights or {})
    internal_pairs = {(edge.provider, edge.consumer) for edge in graph.edges
                      if edge.provider in decls and edge.consumer in decls}
    for pair, weight in overrides.items():
        if pair not in internal_pairs:
            raise ValueError("Region weight override must name an existing internal declaration pair")
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0:
            raise ValueError("Region weights must be finite nonnegative numbers")
    burden_values, burden_settings = None, None
    if region_burden_features is not None:
        # Extract only the formal-material projection.  Unrelated FeatureSet
        # metrics, hierarchy identity and feature configuration do not belong to
        # Region identity.
        from lean_exposition.features.core import ref_key
        feature_data = region_burden_features.to_dict() if hasattr(region_burden_features, "to_dict") else region_burden_features
        if (feature_data["workspace_digest"] != workspace.digest() or
                feature_data["repo_key"] != repo_key or
                set(feature_data["declarations"]) != {ref_key(ref) for ref in decls}):
            raise ValueError("Region burden features do not match fixed declaration facts")
        burden_values = {ref: feature_data["declarations"][ref_key(ref)]["metrics"]["formal_material_codepoints"]["value"] for ref in decls}
        if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0) for v in burden_values.values()):
            raise ValueError("Region material burdens must be finite and nonnegative or missing")
        projection = {
            "workspace_digest": workspace.digest(),
            "repo_key": repo_key,
            "values": {json.dumps(asdict(ref), sort_keys=True, separators=(",", ":")): burden_values[ref]
                       for ref in sorted(burden_values, key=lambda value: (value.repo_key, value.local_id))},
        }
        burden_settings = {"policy": "strict_equal_material", "workspace_digest": feature_data["workspace_digest"],
            "projection_digest": _digest(projection),
            "objective": "equal primary DP cost only; minimize sum of binary split absolute material imbalance",
            "missing_rule": "disable burden tie-break for a scope with any missing member material"}
    class HelperSource:
        def key(self, ref):
            return source.records[ref].get("base_key", source.key(ref))
        def measure(self, declarations):
            return source.measure(declarations)
    groups, owners, diagnostics = aggregate_helpers(
        graph, repo_key, HelperSource(), config, structure_policy.unit_aggregation, keep_separate)
    diagnostics = list(source.diagnostics) + diagnostics
    nodes, coverage = {}, {}
    def refs_json(refs):
        return [asdict(r) for r in sorted(refs, key=lambda r: (r.repo_key, r.local_id))]
    def make(kind, identity, title, refs, scope, representative=None, metadata=None):
        node_id = stable_id(kind, identity)
        nodes[node_id] = {"id": node_id, "kind": kind, "title": title, "parent": None, "children": [],
                          "decl_refs": refs_json(refs), "source_scope": scope,
                          "metadata": {"source": source.summary(refs), **(metadata or {})}}
        if representative is not None:
            nodes[node_id]["representative"] = asdict(representative)
        coverage[node_id] = set(refs)
        return node_id
    unit_ids = {}
    for representative, refs in sorted(groups.items(), key=lambda p: p[0].local_id):
        d = decls[representative]
        unit_ids[representative] = make("unit", asdict(representative), d.lean_name, refs, d.native_scope, representative,
            {"material": source.measure(decls[r] for r in refs), "raw_kind": d.kind,
             "extraction_status": d.extraction_status.state, "technical": d.extraction_status.state == "compiler_only",
             "source_missing": not bool(d.source_refs)})
    scopes = {s.scope_id: s for s in workspace.scopes if s.repo_key == repo_key}
    def node_key(node_id):
        refs = coverage[node_id]
        return min((source.key(r) for r in refs), default=(3, node_id)), node_id
    def attach(parent, children):
        nodes[parent]["children"] = list(children)
        for child in children:
            nodes[child]["parent"] = parent
    aliases = {}
    def build_scope(scope_id):
        scope = scopes[scope_id]
        children = [build_scope(s.scope_id) for s in sorted(scopes.values(), key=lambda s: s.scope_id) if s.parent == scope_id]
        children.extend(unit_ids[r] for r in unit_ids if decls[r].native_scope == scope_id)
        refs = set().union(*(coverage[c] for c in children))
        kind = "repo" if scope_id == repo.root_scope else "scope"
        current = make(kind, [repo_key, scope_id], repo_key if kind == "repo" else scope.name, refs, scope_id)
        aliases[scope_id] = current
        attach(current, children)
        return current

    root = build_scope(repo.root_scope)

    def remove_scope(removed, survivor):
        """Move only the removed wrapper's identity/provenance; facts stay intact."""
        record = asdict(scopes[nodes[removed]["source_scope"]])
        prior = nodes[removed]["metadata"].get("collapsed_scopes", [])
        inherited = nodes[survivor]["metadata"].get("collapsed_scopes", [])
        nodes[survivor]["metadata"]["collapsed_scopes"] = sorted(
            inherited + prior + [record], key=lambda value: value["scope_id"])
        for alias, target in list(aliases.items()):
            if target == removed:
                aliases[alias] = survivor
        aliases[removed] = survivor
        del nodes[removed]
        del coverage[removed]

    def compress(current):
        children = [compress(child) for child in nodes[current]["children"]]
        attach(current, children)
        if nodes[current]["kind"] == "scope" and len(children) == 1:
            survivor = children[0]
            remove_scope(current, survivor)
            return survivor
        if nodes[current]["kind"] == "repo":
            while len(children) == 1 and nodes[children[0]]["kind"] == "scope":
                child = children[0]
                children = list(nodes[child]["children"])
                remove_scope(child, current)
                attach(current, children)
        return current

    if config.scope_compression == "unary":
        compress(root)
    nodes[root]["metadata"]["aliases"] = dict(sorted(aliases.items()))

    def atom_source(child):
        refs = coverage[child]
        if not refs:
            return (3, nodes[child]["source_scope"], child), "empty_scope", None, None
        selected = min(refs, key=lambda ref: (source.key(ref), ref.repo_key, ref.local_id))
        record = source.records[selected]
        evidence = {
            "decl_ref": asdict(selected),
            "selected_anchor": record["selected_anchor"],
            "selected_sequence": record["selected_sequence"],
            "selected_strength": record["selected_strength"],
            "sequence_matches": record["sequence_matches"],
            "ambiguous_occurrence": record["ambiguous_occurrence"],
        }
        return source.key(selected), record["basis"], record["selected_role"], evidence

    scope_children_index = {}
    for scope_id in scopes:
        current = scope_id
        ancestors = {current}
        while scopes[current].parent is not None:
            current = scopes[current].parent
            ancestors.add(current)
        scope_children_index[scope_id] = ancestors

    def target_refs(target):
        if target.repo_key != repo_key:
            return set()
        if target.kind == "declaration":
            return {target.ref} if target.ref in decls else set()
        if target.kind == "declaration_locator":
            return {ref for ref, declaration in decls.items()
                    if declaration.lean_name == target.identifier}
        if target.kind == "repository":
            return set(decls) if target.identifier in {repo_key, repo.root_scope} else set()
        matches = [scope_id for scope_id, scope in scopes.items()
                   if scope_id == target.identifier or scope.name == target.identifier]
        if len(matches) != 1:
            return set()
        selected = matches[0]
        return {ref for ref, declaration in decls.items()
                if selected in scope_children_index[declaration.native_scope]}

    def subject_projections(children):
        child_for_ref = {ref: child for child in children for ref in coverage[child]}
        result = {}
        if materials is not None:
            target_atoms = {}
            for binding in materials.bindings:
                refs = target_refs(binding.target)
                target_atoms[binding.target.canonical_key()] = tuple(sorted(
                    {child_for_ref[ref] for ref in refs if ref in child_for_ref}))
            result.update(material_subject_projections(materials, target_atoms))
        if order_evidence is None:
            return result
        subjects = {subject for relation in order_evidence.expanded_relations()
                    for subject in (relation.before, relation.after)}
        for subject in subjects:
            if subject.kind == "material_record":
                continue
            refs = set()
            if subject.kind == "declaration":
                refs = {subject.ref} if subject.ref in decls else set()
            elif subject.kind == "module":
                refs = {ref for ref, declaration in decls.items()
                        if declaration.module == subject.identifier}
            elif subject.kind == "scope":
                matches = [scope_id for scope_id, scope in scopes.items()
                           if scope_id == subject.identifier or scope.name == subject.identifier]
                if len(matches) == 1:
                    refs = {ref for ref, declaration in decls.items()
                            if matches[0] in scope_children_index[declaration.native_scope]}
            elif subject.kind == "source_range":
                refs = {ref for ref, declaration in decls.items()
                        if subject.source_range in declaration.source_refs}
            atoms = tuple(sorted({child_for_ref[ref] for ref in refs if ref in child_for_ref}))
            result[subject.canonical_key()] = SubjectProjection(
                "exact" if refs and all(ref in child_for_ref for ref in refs) else "unresolved",
                atoms,
            )
        return result

    def projected_evidence(current, children):
        if order_evidence is not None:
            return project_order_evidence(order_evidence, subject_projections(children))
        relations = []
        if source.sequence_spec is not None:
            producer = {"tex_document": "lc_tex_document",
                        "ordered_files": "ordered_files", "lean_modules": "lean_modules"}
            for sequence in source.sequence_spec.sequences:
                if sequence.role != "primary" or sequence.strength != "protected":
                    continue
                ranked = []
                for child in children:
                    matching = [source.key(ref) for ref in coverage[child]
                                if source.records[ref].get("selected_sequence") == sequence.id]
                    if matching:
                        ranked.append((min(matching), child))
                atoms = tuple(child for _, child in sorted(ranked, key=lambda item: (item[0], item[1])))
                relations.extend(ProjectedOrderRelation(
                    f"source-sequence:{sequence.id}:{index}", before, after,
                    "protected", f"{sequence.role} {sequence.kind} sequence",
                    producer[sequence.kind], index,
                ) for index, (before, after) in enumerate(zip(atoms, atoms[1:])))
        return ProjectedOrderEvidence(tuple(relations), (), (), source.digest())

    problems = []
    def collect_order_problems(current):
        children = list(nodes[current]["children"])
        if nodes[current]["kind"] == "unit":
            return
        for child in children:
            collect_order_problems(child)
        projection = graph.project({child: coverage[child] for child in children})
        baseline = _order(children, ((edge.provider, edge.consumer) for edge in projection.edges), node_key, {})
        if not baseline.is_acyclic:
            raise HierarchyCycleError(nodes[current]["source_scope"], baseline)
        source_keys, source_basis, source_roles, source_anchors = {}, {}, {}, {}
        for child in children:
            (source_keys[child], source_basis[child], source_roles[child],
             source_anchors[child]) = atom_source(child)
        projected = projected_evidence(current, children)
        internalized = tuple(sorted(item["relation_id"] for item in projected.diagnostics
                                    if item["code"] == "order_relation_internalized"))
        unprojected = tuple(sorted(item["relation_id"] for item in projected.diagnostics
                                   if item["code"] == "order_relation_not_projected"))
        problems.append(OrderProblem(
            nodes[current]["source_scope"], current, tuple(sorted(children)),
            tuple(OrderEdge(edge.provider, edge.consumer, len(edge.base_edges))
                  for edge in sorted(projection.edges, key=lambda edge: (edge.provider, edge.consumer))),
            source_keys, source_basis, source_roles, source_anchors,
            protected_relations=projected.protected,
            tie_breaker_relations=projected.tie_breakers,
            internalized_relation_ids=internalized,
            unprojected_relation_ids=unprojected,
            projection_diagnostics=projected.diagnostics))
    collect_order_problems(root)

    order_config = {
        "helper": {
            "unit_aggregation": structure_policy.unit_aggregation,
            "max_declarations": config.max_declarations,
            "max_codepoints": config.max_codepoints,
        },
        "structure_policy_digest": structure_policy.digest(),
        "dependency_coverage_digest": dependency_coverage.digest(),
        "scope_compression": config.scope_compression,
        "keep_separate": refs_json(set(keep_separate)),
        **({
            "material_digest": materials.material_digest(),
            "binding_digest": materials.binding_digest(),
            "order_evidence_digest": order_evidence.digest(),
        } if materials is not None else {}),
    }
    order_config_digest = _digest(order_config)
    if narrative_order is None:
        scope_orders = tuple(solve_order(problem) for problem in problems)
        narrative_order = NarrativeOrder.create(
            repo_key=repo_key, repository_revision=repo.revision,
            repository_input_digest=repo.input_digest, workspace_digest=workspace.digest(),
            source_digest=source.digest(), config_digest=order_config_digest, scopes=scope_orders,
            material_digest=materials.material_digest() if materials is not None else None,
            binding_digest=materials.binding_digest() if materials is not None else None,
            order_evidence_digest=order_evidence.digest() if order_evidence is not None else None)
    else:
        narrative_order = NarrativeOrder.from_dict(
            json.loads(json.dumps(narrative_order.to_dict()))
            if isinstance(narrative_order, NarrativeOrder) else narrative_order)
        if (narrative_order.repo_key != repo_key or narrative_order.repository_revision != repo.revision or
                narrative_order.repository_input_digest != repo.input_digest or
                narrative_order.workspace_digest != workspace.digest() or
                narrative_order.source_digest != source.digest() or
                narrative_order.config_digest != order_config_digest or
                narrative_order.material_digest != (materials.material_digest() if materials is not None else None) or
                narrative_order.binding_digest != (materials.binding_digest() if materials is not None else None) or
                narrative_order.order_evidence_digest != (order_evidence.digest() if order_evidence is not None else None)):
            raise ValueError("narrative order does not match hierarchy inputs")
    scope_order_index = {(scope.scope_id, scope.parent_id): scope for scope in narrative_order.scopes}
    problem_index = {(problem.scope_id, problem.parent_id): problem for problem in problems}
    if set(scope_order_index) != set(problem_index):
        raise ValueError("narrative order scope coverage mismatch")
    for identity, problem in problem_index.items():
        validate_scope_order(problem, scope_order_index[identity])
    expected_order_diagnostics = tuple(diagnostic for scope in narrative_order.scopes
                                       for diagnostic in scope.diagnostics)
    if narrative_order.diagnostics != expected_order_diagnostics:
        raise ValueError("narrative order diagnostics mismatch")
    diagnostics.extend(narrative_order.diagnostics)
    if order_only:
        return None, narrative_order

    def build_regions(current):
        children = list(nodes[current]["children"])
        if nodes[current]["kind"] == "unit":
            return
        for child in children:
            build_regions(child)
        scope_id = nodes[current]["source_scope"]
        projection = graph.project({c: coverage[c] for c in children})
        scope_order = scope_order_index[(scope_id, current)]
        ordered = validate_scope_order(problem_index[(scope_id, current)], scope_order)
        nodes[current]["metadata"]["narrative_order"] = {
            "narrative_order_id": narrative_order.narrative_order_id,
            "metrics": scope_order.metrics,
            "constraints": [list(pair) for pair in scope_order.constraints],
            "accepted_relation_ids": list(scope_order.accepted_relation_ids),
            "rejected_relation_ids": list(scope_order.rejected_relation_ids),
        }
        indices = {c: i for i, c in enumerate(ordered)}
        weights = {(indices[e.provider], indices[e.consumer]):
                   sum(overrides.get((edge.provider, edge.consumer), 1) for edge in e.base_edges)
                   for e in projection.edges}
        burdens = None
        if burden_values is not None and all(burden_values[r] is not None for child in ordered for r in coverage[child]):
            burdens = [sum(burden_values[r] for r in coverage[child]) for child in ordered]
        tree, cost, actual = partition_regions(ordered, weights, k=config.region_k,
                                               algorithm=config.region_algorithm, dp_limit=config.region_dp_limit, burdens=burdens)
        nodes[current]["metadata"]["region"] = {"algorithm": actual, "cost": cost, "atom_order": list(ordered)}
        if burden_values is not None:
            nodes[current]["metadata"]["region"]["burden_status"] = "missing_disabled" if burdens is None else "active" if actual == "ordered_dp" else "balanced_fallback_unchanged"
        def materialize(tree, parent):
            result = []
            for item in tree:
                if isinstance(item, str):
                    result.append(item)
                else:
                    def leaves(t):
                        return [x for value in t for x in ([value] if isinstance(value, str) else leaves(value))]
                    atoms = leaves(item)
                    if len(atoms) == 1:
                        result.append(atoms[0])
                        continue
                    region_refs = set().union(*(coverage[a] for a in atoms))
                    region = make("region", [current, atoms], "Region", region_refs, scope_id)
                    materialize(item, region)
                    result.append(region)
            attach(parent, result)
        materialize(tree, current)
        return current
    build_regions(root)
    external, edges = {}, []
    def endpoint(ref):
        if ref in owners:
            return unit_ids[owners[ref]]
        identity = stable_id("external", asdict(ref))
        external[identity] = {"id": identity, "ref": asdict(ref), "loaded": ref in graph.loaded_decls}
        if ref.repo_key == repo_key:
            diagnostic = {"code": "unresolved_local_reference", "ref": asdict(ref)}
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
        return identity
    for edge in graph.edges:
        if edge.provider not in decls and edge.consumer not in decls:
            continue
        pair = [asdict(edge.provider), asdict(edge.consumer)]
        edges.append({"id": stable_id("edge", pair), "provider_node": endpoint(edge.provider), "consumer_node": endpoint(edge.consumer),
                      "provider_decl": pair[0], "consumer_decl": pair[1]})
    for ref in repo.primary_outcomes:
        if ref not in decls:
            endpoint(ref)
    settings = {**config.to_dict(), "source": source.config,
                "structure_policy_digest": structure_policy.digest(),
                "dependency_coverage_digest": dependency_coverage.digest(),
                "unit_aggregation": structure_policy.unit_aggregation,
                "narrative_order_id": narrative_order.narrative_order_id,
                "narrative_order_implementation_digest": narrative_order.implementation_digest,
                **({"material_digest": narrative_order.material_digest,
                    "binding_digest": narrative_order.binding_digest,
                    "order_evidence_digest": narrative_order.order_evidence_digest}
                   if narrative_order.material_digest is not None else {}),
                "keep_separate": refs_json(set(keep_separate))}
    if burden_settings is not None:
        settings["region_burden"] = burden_settings
    if overrides:
        settings["region_edge_weights"] = {
            "semantics": "Region association weights only; raw pairs, helper aggregation and dependency order are unchanged",
            "default_weight": 1,
            "overrides": [{"provider_decl": asdict(pair[0]), "consumer_decl": asdict(pair[1]), "weight": weight}
                          for pair, weight in sorted(overrides.items(), key=lambda item:
                              (item[0][0].repo_key, item[0][0].local_id, item[0][1].repo_key, item[0][1].local_id))],
        }
    result = Hierarchy(repo_key, root, "", workspace.digest(), source.digest(), _digest(settings), settings,
                       [nodes[k] for k in sorted(nodes)], edges, [external[k] for k in sorted(external)], diagnostics)
    result = Hierarchy(**{**result.to_dict(), "hierarchy_id": _hierarchy_id(result)})
    return Hierarchy.from_dict(result.to_dict()), narrative_order


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _hierarchy_id(value):
    payload = value.to_dict() if hasattr(value, "to_dict") else dict(value)
    payload.pop("hierarchy_id", None)
    return stable_id("hierarchy", payload)
