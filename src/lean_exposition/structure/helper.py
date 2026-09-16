"""Generated ownership followed by conservative exclusive-support aggregation."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class BuildConfig:
    native_helper: bool = True
    lc_cross_file_helper: bool = False
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


def aggregate_helpers(graph, repo_key, source, config, keep_separate=(), source_priority=None):
    source_priority = source_priority or {}
    decls = {d.ref: d for d in graph.workspace.declarations if d.ref.repo_key == repo_key}
    groups = {ref: {ref} for ref in decls}
    owner = {ref: ref for ref in decls}
    diagnostics = []
    # Resolve whole chains before changing groups; known ownership is mandatory.
    for ref, decl in decls.items():
        current, seen = ref, set()
        while current in decls and decls[current].generated_from is not None and current not in seen:
            seen.add(current)
            current = decls[current].generated_from
        if current != ref and current in decls and current not in seen and decls[current].native_scope == decl.native_scope:
            owner[ref] = current
        elif decl.generated_from is not None:
            diagnostics.append({"code": "unresolved_generated_owner", "decl": asdict(ref)})
    groups = {}
    for ref, root in owner.items():
        groups.setdefault(root, set()).add(ref)
    primary = next(r.primary_outcomes for r in graph.workspace.manifest.repositories if r.repo_key == repo_key)
    protected = set(primary) | set(keep_separate)
    protected.update(r for r, d in decls.items() if d.extraction_status.state == "compiler_only" and owner[r] == r)
    for edge in graph.edges:
        if edge.provider in decls and (edge.consumer not in decls or decls[edge.provider].native_scope != decls[edge.consumer].native_scope):
            protected.add(edge.provider)
    lc = any(p.method.startswith("lc_") for d in decls.values() for p in d.provenance)
    rejected = set()
    while True:
        changed = False
        for helper in sorted(groups, key=lambda r: ((0, source_priority[r]) if r in source_priority else (1, source.key(r)), r.local_id)):
            members = groups[helper]
            reason = None
            if members & protected:
                reason = "protected"
            elif not config.native_helper and not lc:
                reason = "helper_disabled"
            consumers = {owner.get(e.consumer, e.consumer) for r in members for e in graph.outgoing[r] if e.consumer not in members}
            if reason is None and len(consumers) != 1:
                reason = "unused" if not consumers else "shared"
            target = next(iter(consumers)) if len(consumers) == 1 else None
            if reason is None and (target not in groups or decls[target].native_scope != decls[helper].native_scope):
                reason = "scope_boundary"
            if reason is None and lc and not config.lc_cross_file_helper and len({decls[r].module for r in members | groups[target]}) > 1:
                reason = "lc_registered_file_boundary"
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
