# Graph foundation

`DependencyGraph.from_workspace(workspace)` validates the immutable facts and
builds provider-to-consumer edges. By default it selects `lc_declared`,
`lean_type`, and `lean_value`; pass an explicit iterable of evidence kinds to
change that view. Text references are excluded by default. Each unique declaration
pair retains every selected occurrence, including statement/proof part, evidence
kind, and original provenance. Duplicate occurrences are not discarded.

The graph exposes immutable edge tuples, reverse indexes, loaded declarations,
unloaded dependency references, and unloaded primary outcomes. Missing references
never become loaded facts. `representative_owner` identifies each declaration's
own unit; `top_level_owner` identifies its enclosing top-level reading unit.
`unit_coverages` includes recursive members, `native_scope_coverages` follows the
original Scope tree, and `reading_scope_coverages` assigns complete top-level units
to their representative's native Scope. Repository coverage and `loaded_decls`
provide repository and workspace ranges. An empty unit forest remains facts-only.

`boundary(refs)` accepts any subset of loaded declarations and returns incoming,
outgoing, and internal edges. Its outputs include contained repository primary
outcomes even without consumers. Pair counts and distinct provider counts remain
separate. Usage is only observed within the loaded workspace: it cannot establish
absence of consumers elsewhere, and therefore alone cannot justify future helper
absorption as globally exclusive usage.

`project({name: refs})` accepts disjoint groups without requiring a workspace
partition. It reports uncovered declarations, the selected range boundary,
internal edges, and cross-group edges retaining their base evidence. Empty groups
are allowed. Unknown declarations and overlapping groups are rejected.
`scope_children(scope_id)` builds an exact cover from direct child Scopes and
direct top-level units. It rejects conflicts between native and reading coverage,
including units crossing Scope boundaries, without changing either fact model.
Use explicit `project` groups for facts-only workspaces.

`order(source_priority=None, refs=None)` orders loaded declarations (or a selected
loaded subset). `Projection.order(source_priority=None)` orders groups. Dependency
constraints take precedence; a caller's explicitly trusted priority then breaks
ready-node ties, followed by source path/position and stable identity. Asset
manifest insertion order is not trusted narrative order. Unloaded or unselected
prerequisites remain boundary references. Results contain an ordered prefix,
actual cyclic SCCs with closed directed witnesses, and separately blocked
noncyclic descendants. Internal projected edges do not become projected self-loops;
the original graph still diagnoses declaration self-loops. Graph traversal is
iterative. Coverage storage can be quadratic for very deeply nested unit forests.

This module does not merge helpers, construct Regions, implement features, generate
EET content, or verify mathematical correctness. Run its focused synthetic tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests/unit/structure -v
```
