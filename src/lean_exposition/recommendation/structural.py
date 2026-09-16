"""Generation-free structural refinement ranking with versioned cost estimates."""
from dataclasses import asdict, dataclass

from lean_exposition.features.core import FEATURE_CONFIG_DIGEST, FeatureSet, digest, ref_key
from lean_exposition.structure import Hierarchy


@dataclass(frozen=True)
class RecommendationConfig:
    material_small: int = 800
    material_medium: int = 3000
    planned_small: int = 240
    planned_medium: int = 600
    planned_large: int = 1200
    minimum_increment: int = 120
    tier_low_max: int = 600
    tier_medium_max: int = 1600

    def __post_init__(self):
        if not (0 < self.material_small < self.material_medium and
                0 < self.planned_small < self.planned_medium < self.planned_large and
                0 < self.minimum_increment <= self.tier_low_max < self.tier_medium_max):
            raise ValueError("invalid structural recommendation thresholds")

    def to_dict(self):
        value = {**asdict(self), "policy": "structural",
            "ordering": ["cost_tier", "G_decl_descending", "G_dep_descending", "source_order", "node_id"],
            "calibration": "Uncalibrated engineering defaults, identical in zh/en; not fitted to reference labels",
            "G_dep_denominator": "mean over target consumer Units of newly separated internal raw pairs / max(1, fixed cross-Unit internal incoming pair count)",
            "missing_cost_rule": "Only missing required cost material forces conservative large budget and high cost tier",
            "restored_section_rule": "Charge whole-section planned budget for every newly visible section, including already-expanded sections; conservative I/O approximation"}
        return {**value, "config_digest": digest(value)}


def make_structural_policy(workspace, hierarchy, feature_set, *, config=None, targets=()):
    """Bind immutable facts; call policy(context, candidate_ids) without content."""
    hierarchy = Hierarchy.from_dict(hierarchy.to_dict() if hasattr(hierarchy, "to_dict") else hierarchy).to_dict()
    features = FeatureSet.from_dict(feature_set.to_dict() if hasattr(feature_set, "to_dict") else feature_set).to_dict()
    config = config or RecommendationConfig()
    if (features["hierarchy_id"] != hierarchy["hierarchy_id"] or
            features["workspace_digest"] != workspace.digest() or
            features["config_digest"] != FEATURE_CONFIG_DIGEST):
        raise ValueError("recommendation features do not match fixed facts and structure")
    nodes = {n["id"]: n for n in hierarchy["nodes"]}
    root = hierarchy["root_id"]
    units = {id for id, node in nodes.items() if node["kind"] == "unit"}
    owner = {ref_key(ref): id for id in units for ref in nodes[id]["decl_refs"]}
    covered = {}
    source_rank = {}
    def visit(id):
        source_rank[id] = len(source_rank)
        covered[id] = {id} if id in units else set().union(*(visit(child) for child in nodes[id]["children"]))
        return covered[id]
    visit(root)
    # Original pairs, not projected-edge multiplicity or occurrence counts.
    pairs = {}
    for edge in hierarchy["edges"]:
        provider, consumer = edge["provider_node"], edge["consumer_node"]
        if provider in units and consumer in units and provider != consumer:
            pairs[ref_key(edge["provider_decl"]), ref_key(edge["consumer_decl"])] = (provider, consumer)
    incoming = {unit: set() for unit in units}
    denominators = {unit: 0 for unit in units}
    for provider, consumer in pairs.values():
        incoming[consumer].add(provider)
        denominators[consumer] += 1
    requested = tuple(targets)
    outcomes = next(r.primary_outcomes for r in workspace.manifest.repositories if r.repo_key == hierarchy["repo_key"])
    references = requested or outcomes
    target_units, unresolved = set(), []
    for target in references:
        if isinstance(target, str):
            node_id = target if target in nodes else nodes[root].get("metadata", {}).get("aliases", {}).get(target)
            if node_id not in nodes:
                raise ValueError("unknown explicit target node")
            target_units.update(covered[node_id])
        else:
            key = ref_key(target)
            if key in owner:
                target_units.add(owner[key])
            else:
                unresolved.append(json.loads(key))
    target_basis = "explicit_target_provider_closure" if requested else "root_outcome_provider_closure" if outcomes else "all_internal_units_no_outcomes"
    if not references:
        target_units = set(units)
    pending = list(target_units)
    while pending:
        unit = pending.pop()
        for provider in incoming[unit] - target_units:
            target_units.add(provider)
            pending.append(provider)
    target_info = {"basis": target_basis, "unit_ids": sorted(target_units), "count": len(target_units), "unresolved_refs": unresolved}

    def state_frontier(expanded):
        visible, wrappers = set(), {}
        def walk(id):
            visible.add(id)
            if id in expanded and nodes[id]["children"]:
                for child in nodes[id]["children"]:
                    walk(child)
            else:
                wrappers.update((unit, id) for unit in covered[id])
        walk(root)
        return visible, wrappers

    def policy(context, candidates):
        if context.get("hierarchy_id", hierarchy["hierarchy_id"]) != hierarchy["hierarchy_id"]:
            raise ValueError("recommendation context belongs to another structure")
        if "expanded" not in context:
            raise ValueError("structural recommendation requires expanded node IDs")
        expanded = set(context["expanded"])
        if not expanded <= set(nodes):
            raise ValueError("expanded state contains unknown nodes")
        before_visible, before = state_frontier(expanded)
        results = []
        for candidate in dict.fromkeys(candidates):
            if candidate not in before_visible or candidate in expanded or not nodes[candidate]["children"]:
                raise ValueError("illegal structural recommendation candidate")
            after_visible, after = state_frontier(expanded | {candidate})
            target_count = len(target_units)
            gain_decl = sum(1 / len(covered[after[u]]) - 1 / len(covered[before[u]]) for u in target_units) / target_count if target_count else 0.0
            exposed = {unit: 0 for unit in target_units}
            for provider, consumer in pairs.values():
                if consumer in exposed and before[provider] == before[consumer] and after[provider] != after[consumer]:
                    exposed[consumer] += 1
            gain_dep = sum(count / max(1, denominators[consumer]) for consumer, count in exposed.items()) / target_count if target_count else 0.0
            new_nodes = sorted(after_visible - before_visible, key=lambda id: source_rank[id])
            costs = []
            for id in new_nodes:
                material = features["nodes"][id]["cost_material"]
                value = material.get("value") if material else None
                complete = value is not None
                plan = config.planned_large if not complete or value > config.material_medium else config.planned_medium if value > config.material_small else config.planned_small
                costs.append({"node_id": id, "planned_codepoints": plan, "material_codepoints": value,
                              "material_coverage": material.get("coverage", 0) if material else 0,
                              "scope": "unit_own_formal_material" if id in units else "section_interface_statements",
                              "expanded_section_io_approximation": id in expanded})
            known_synopsis = context.get("synopsis_codepoints", {}).get(candidate)
            if known_synopsis is not None and (type(known_synopsis) is not int or known_synopsis < 0):
                raise ValueError("known synopsis size must be a nonnegative integer")
            planned_total = sum(item["planned_codepoints"] for item in costs)
            raw_increment = planned_total - (known_synopsis or 0)
            estimate = max(config.minimum_increment, raw_increment)
            missing_material = any(item["material_codepoints"] is None for item in costs)
            tier = 2 if missing_material else 0 if estimate <= config.tier_low_max else 1 if estimate <= config.tier_medium_max else 2
            label = ("low", "medium", "high")[tier]
            components = {"structural_gain": {"G_decl": gain_decl, "G_dep": gain_dep,
                "new_decl_pairs": sum(exposed.values()), "consumer_contributions": [
                    {"consumer_unit": unit, "new_pairs": count, "fixed_denominator": max(1, denominators[unit])}
                    for unit, count in sorted(exposed.items()) if count]},
                "estimated_cost": {"codepoints": estimate, "tier": label, "tier_index": tier,
                    "planned_new_codepoints": planned_total, "known_synopsis_codepoints": known_synopsis,
                    "floor_applied": raw_increment < config.minimum_increment},
                "cost_basis": {"config_digest": config.to_dict()["config_digest"], "children": costs,
                    "synopsis_status": "known" if known_synopsis is not None else "missing_not_subtracted",
                    "uses_generated_after_text": False, "section_budget": "Whole section budget conservatively approximates restored expanded I/O"},
                "feature_coverage": {"cost_material": sum(c["material_coverage"] for c in costs) / len(costs) if costs else 1,
                                     "missing_cost_material": missing_material},
                "target": target_info, "config": config.to_dict(), "locale": (context.get("locale") or "en")}
            reason = (f"{label.capitalize()} estimated cost tier; target-unit refinement {gain_decl:.3f}, "
                      f"consumer-normalized dependency refinement {gain_dep:.3f}. Structural proxies, not measured understanding.")
            if (context.get("locale") or "en").startswith("zh"):
                reason = (f"预计成本{('低', '中', '高')[tier]}档；目标声明细化收益 {gain_decl:.3f}，"
                          f"按消费者归一化的依赖细化收益 {gain_dep:.3f}。这些是结构指标，并非实测理解收益。")
            results.append((tier, -gain_decl, -gain_dep, source_rank[candidate], candidate, components, reason))
        results.sort(key=lambda item: item[:5])
        return {"policy_id": "structural", "recommendations": [{"target_id": row[4], "rank": i + 1,
            "reason": row[6], "components": row[5]} for i, row in enumerate(results)]}
    return policy
