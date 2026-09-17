"""Generated ownership followed by conservative exclusive-support aggregation."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class BuildConfig:
    max_declarations: int = 8
    max_codepoints: int = 12000
    region_k: int = 6
    region_algorithm: str = "ordered_dp"
    region_dp_limit: int = 120
    scope_compression: str = "unary"

    def __post_init__(self):
        if min(self.max_declarations, self.max_codepoints, self.region_dp_limit) < 1 or self.region_k < 2:
            raise ValueError("invalid build capacities")
        if self.scope_compression not in {"none", "unary"}:
            raise ValueError("invalid scope compression policy")
        if self.region_algorithm not in {"balanced", "ordered_dp"}:
            raise ValueError("invalid Region algorithm")

    def to_dict(self):
        return asdict(self)


def aggregate_helpers(graph, repo_key, source, config, unit_aggregation,
                      keep_separate=(), source_priority=None):
    """Aggregate whole top-level seed units according to an explicit policy."""
    source_priority = source_priority or {}
    decls = {d.ref: d for d in graph.workspace.declarations if d.ref.repo_key == repo_key}
    units = {unit.unit_id: unit for unit in graph.workspace.units}
    child_units = {member for unit in units.values() for member in unit.members}
    top_units = [unit for unit in units.values()
                 if unit.unit_id not in child_units and unit.representative.repo_key == repo_key]
    groups = {unit.representative: set(graph.unit_coverages[unit.unit_id]) for unit in top_units}
    owner = {ref: representative for representative, refs in groups.items() for ref in refs}
    diagnostics = []
    if unit_aggregation == "preserve":
        return groups, owner, diagnostics
    if unit_aggregation != "native_helpers":
        raise ValueError("invalid unit aggregation policy")

    # Resolve generated ownership at seed granularity: a seed is merged whole or
    # remains whole.  Existing compound seeds are never split into declarations.
    seed_owner = {representative: representative for representative in groups}
    for representative in tuple(groups):
        decl = decls[representative]
        current, seen = representative, set()
        while current in decls and decls[current].generated_from is not None and current not in seen:
            seen.add(current)
            current = decls[current].generated_from
        target = owner.get(current)
        if (current != representative and target in groups and current not in seen and
                decls[target].native_scope == decl.native_scope):
            seed_owner[representative] = target
        elif decl.generated_from is not None:
            diagnostics.append({"code": "unresolved_generated_owner", "decl": asdict(representative)})
    resolved_groups = {}
    for representative, refs in groups.items():
        root, seen = representative, set()
        while seed_owner[root] != root and root not in seen:
            seen.add(root)
            root = seed_owner[root]
        if root in seen:
            root = representative
            diagnostics.append({"code": "unresolved_generated_owner", "decl": asdict(representative)})
        resolved_groups.setdefault(root, set()).update(refs)
    groups = resolved_groups
    owner = {ref: representative for representative, refs in groups.items() for ref in refs}
    primary = next(r.primary_outcomes for r in graph.workspace.manifest.repositories if r.repo_key == repo_key)
    protected = set(primary) | set(keep_separate)
    protected.update(r for r, d in decls.items()
                     if d.extraction_status.state == "compiler_only" and owner[r] == r)
    for edge in graph.edges:
        if edge.provider in decls and (edge.consumer not in decls or decls[edge.provider].native_scope != decls[edge.consumer].native_scope):
            protected.add(edge.provider)
    rejected = set()
    while True:
        changed = False
        for helper in sorted(groups, key=lambda r: ((0, source_priority[r]) if r in source_priority else (1, source.key(r)), r.local_id)):
            members = groups[helper]
            reason = None
            if members & protected:
                reason = "protected"
            consumers = {owner.get(e.consumer, e.consumer) for r in members for e in graph.outgoing[r] if e.consumer not in members}
            if reason is None and len(consumers) != 1:
                reason = "unused" if not consumers else "shared"
            target = next(iter(consumers)) if len(consumers) == 1 else None
            if reason is None and (target not in groups or decls[target].native_scope != decls[helper].native_scope):
                reason = "scope_boundary"
            if reason is None:
                combined = members | groups[target]
                measurement = source.measure(decls[r] for r in combined)
                if len(combined) > config.max_declarations or measurement["codepoints"] > config.max_codepoints:
                    reason = "capacity"
            if reason is not None:
                rejected.add((helper.local_id, reason))
                continue
            groups[target].update(members)
            for ref in members:
                owner[ref] = target
            del groups[helper]
            changed = True
            break
        if not changed:
            break
    diagnostics.extend({"code": "helper_rejection", "decl": ref, "reason": reason} for ref, reason in sorted(rejected))
    return groups, owner, diagnostics
