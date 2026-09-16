# Graph foundation

`lean_exposition.structure` derives graph queries from a validated `Workspace`.
LC and native inputs use the same implementation. The fact model is unchanged;
this layer does not merge declarations, create regions, or generate exposition.

```python
from lean_exposition.structure import DependencyGraph

graph = DependencyGraph.from_workspace(workspace)
view = graph.repository_view("ConsecutiveDivisorCounts")
boundary = view.boundary
children = view.root_projection()
ordering = children.order()

if ordering.is_acyclic:
    ordered_child_ids = ordering.ordered
else:
    # Each SCC includes a real closed edge witness. The ordered prefix may
    # still be usable, but it is not a complete topological ordering.
    cycles, blocked_downstream = ordering.cycles, ordering.blocked
```

## One repository per HDG or EET

Use `graph.repository_view(repo_key)` as the presentation entry point. A Workspace
is dependency-resolution context, not a document containing every loaded repo.
The view exposes only the target repository's scopes, coverage, and declaration
order. `view.scope_children(scope_id)` rejects scopes from other repositories.

`view.external_refs` contains cross-repository providers referenced by the target.
`view.external_declarations` maps those references to an already loaded `RawDecl`
or `None`; `view.external_decl(ref)` queries that map. Loaded provider facts
remain external and do not become target tree nodes. There is no automatic fetch
or recursive rendering of their repository. Missing same-repository declarations
are separately reported through `unloaded_local_refs`.

The raw graph's workspace-wide queries remain available for diagnostics. Choose
a different repository explicitly to view it as a separate HDG/EET.

## Declaration edges and evidence

Edges point **provider → consumer**. `edges` contains each declaration pair once;
its `occurrences` retain statement/proof placement, evidence kind, and original
provenance. `incoming` and `outgoing` index those same edges by declaration.

The default evidence set is `lc_declared`, `lean_type`, and `lean_value`. A caller
can pass an explicit iterable of evidence kinds to choose a different view.
Text-reference evidence is not silently included in the default mathematical
view. The underlying source facts remain available in `workspace`.

`loaded_decls` identifies actual loaded records. `unloaded_refs` contains selected
edge endpoints with no loaded declaration; a known external reference need not
be an unresolved repository version. `unloaded_primary_outcomes` separately
reports main outcomes whose declaration bodies have not been loaded.

## Coverage and ownership

| Query/index | Meaning |
| --- | --- |
| `unit_coverages[unit_id]` | Representative plus recursively owned members |
| `representative_owner[decl_ref]` | Unit directly representing the declaration |
| `top_level_owner[decl_ref]` | Root unit owning it in the reading forest |
| `native_scope_coverages[scope_id]` | Declarations originally inside that scope and descendants |
| `reading_scope_coverages[scope_id]` | Complete top-level units placed under their representative's scope |
| `repository_coverages[repo_key]` | Loaded declarations with that repository identity |
| `loaded_decls` | Entire loaded workspace coverage |

A facts-only workspace without units still supports declaration/native coverage
queries. No units are invented. Existing nested units are interpreted, not
created or rearranged. Shared dependencies do not acquire multiple owners.

## Boundaries and weights

`boundary(refs)` accepts any set of loaded declarations. It returns the incoming,
outgoing, and internal original edges, input provider declarations, and output
declarations. Outputs include providers used outside the range **and** contained
repository primary outcomes, even when those outcomes have no consumers.

`incoming_pair_count` and `outgoing_pair_count` count different declaration pairs.
`incoming_provider_count` and `outgoing_provider_count` count different providers
on those crossing edges. Thus one provider used by ten consumers contributes ten
pairs and one provider. The full output interface is `output_decls`; it can be
larger than the outgoing providers because it also includes primary outcomes.

All use boundaries are relative to the observed workspace. Unknown external
consumers cannot be inferred, so these queries do not establish global exclusive
use of a declaration.

## Projection

`project({group_name: declaration_refs, ...})` accepts disjoint groups of loaded
declarations; full workspace coverage is not required. It returns:

- Cross-group `edges`, each retaining its original `base_edges` and both weights.
- `internal_edges` per group, including any original internal self-loops.
- The union's `boundary`, including relationships crossing outside that union.
- `uncovered`, meaning loaded workspace declarations outside the chosen groups.

Unknown declarations and overlapping groups are rejected. Group-internal edges
are not turned into projected self-loops. Empty groups remain explicit.

`scope_children(scope_id)` supplies groups for direct child scopes and directly
placed top-level units. It checks exact coverage throughout that subtree.
Cross-scope unit membership that changes native versus reading coverage is an
explicit conflict for this convenience projection, not a reason to discard facts
or duplicate declarations. Explicit coverage queries remain available.

## Ordering and cycles

`graph.order()` orders loaded declarations; `graph.order(refs=subset)` orders only
that loaded subset. Outside prerequisites remain boundary references.
`projection.order()` orders the selected groups.

Both accept `source_priority={node: integer, ...}`: lower values win among nodes
whose dependencies are already satisfied. Otherwise source path/position and
stable identity break ties. Manifest insertion order is not treated as author
narrative order; path order is only a reproducible fallback.

An `Ordering` contains a topological `ordered` prefix, actual cyclic SCCs with
closed edge witnesses, and `blocked` downstream nodes outside those SCCs. A
nonempty cyclic result is never presented as a complete topological order.
Declaration-level recursion and cycles created by a scope projection can be
inspected separately; neither is labelled a Lean mathematical correctness error.

Results use frozen records, immutable sets/tuples, and read-only index mappings.
Canonical query ordering is independent of the input record order. The original
Workspace retains its original records and order.
