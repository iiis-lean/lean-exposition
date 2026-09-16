# Structure construction

`lean_exposition.structure.build_hierarchy` builds one repository reading tree
from immutable Workspace facts:

```python
from lean_exposition.structure import BuildConfig, build_hierarchy

hierarchy = build_hierarchy(workspace, repo_key, config=BuildConfig())
hierarchy.save("hierarchy.json")
```

The constructor resolves generated ownership, optionally aggregates exclusive
helpers, compresses unary display scopes, derives a deterministic sibling order,
and partitions continuous intervals of that order into Regions. It does not
mutate declaration, scope, dependency, provenance, or source facts.

## Source sequences

`derive_source_order` derives anchors from native source ranges and LC origins.
`source_order_from_lc_git` reads committed source-corpus and formal files at the
Workspace repository revision without changing the external checkout.

An optional `SourceSequenceSpec` marks ordered materials explicitly. It is a
strict JSON object bound to `repo_key` and optionally to `revision` or
`input_digest`. Each sequence has:

- `kind`: `tex_document`, `ordered_files`, or `lean_modules`;
- `role`: `primary`, `supporting`, or `reference`;
- `strength`: `protected` or `tie_breaker`;
- all three arrays `roots`, `paths`, and `modules`, with only the array selected
  by `kind` populated.

See [`configs/source-order.example.json`](../configs/source-order.example.json).
Unknown fields and unknown corpus or module members fail validation. Only an
explicit `primary` plus `protected` sequence creates narrative constraints.
Other sequences choose between dependency-legal alternatives.

```python
from lean_exposition.structure import SourceSequenceSpec, derive_source_order

spec = SourceSequenceSpec.load("source-order.json")
source = derive_source_order(
    workspace,
    repo_key,
    corpus_files=corpus_files,
    corpus_prefix=".lean_constellation/source_corpus/files",
    source_spec=spec,
)
```

For LC material, the static TeX scanner recursively expands literal
`input`/`include` commands in document order. It retains repeated occurrences and
reports cycles, missing inputs, dynamic paths, conditionals, and ambiguous lines.
It does not run TeX or evaluate macros. All origins and ranges remain in the
source record; ordering only selects one derived anchor. Selection prefers
explicit primary material, explicit supporting material, document occurrence,
author source position, then module and stable identity fallbacks. A declaration
with a known `generated_from` owner can inherit the owner's anchor.

For native projects, real declaration dependencies remain hard constraints. An
explicit module sequence supplies a tie-break or protected sequence. Without one,
module/path/stable identity only provide deterministic fallback and do not claim
an author-defined module narrative.

## Narrative order artifact

Ordering happens after helper aggregation and unary scope compression, and before
Region construction. Since Regions are continuous intervals, changing the order
requires rebuilding Regions.

Use `derive_narrative_order` to create the fixed pre-Region artifact separately:

```python
from lean_exposition.structure import derive_narrative_order

order = derive_narrative_order(
    workspace,
    repo_key,
    config=BuildConfig(),
    source=source,
    source_spec=spec,
)
order.save("narrative-order.json")

hierarchy = build_hierarchy(
    workspace,
    repo_key,
    config=BuildConfig(),
    source=source,
    source_spec=spec,
    narrative_order=order,
)
```

Every non-Unit parent contributes one `ScopeOrder`. It binds the exact atom set,
projected real edges, source basis, accepted protected constraints, repository
identity, Workspace digest, source digest, helper/compression settings, and order
implementation digest. Loading rejects duplicate JSON keys, unknown fields,
noncanonical scope ordering, stale inputs, incomplete scope coverage, illegal
orders, changed constraints, and changed metrics. Region-only settings are not
part of the pre-Region order identity, so one validated order can be compared
under different Region configurations.

The deterministic solver uses two legal starts:

1. source-prioritized forward Kahn order;
2. reverse-frontier construction, choosing among atoms whose consumers are
   already in the suffix by `outgoing_weight - incoming_weight`.

Real projected edge weights count distinct underlying declaration dependency
pairs. The solver selects the start with lower weighted dependency distance,
then repeatedly applies the best legal single-atom insertion that strictly lowers

```text
sum(edge_weight * (consumer_position - provider_position)).
```

Protected narrative edges constrain legality but contribute zero to this
objective. If a protected source relation conflicts with a real dependency, the
real dependency wins and the artifact records
`dependency_overrides_source_order`. Ties use source displacement and stable
identities; there is no randomness.

Per-scope metrics include dependency distance, weighted span quantiles, frontier
area and peak, provider-to-last-use distance, source displacement, source-role
inversions, source-basis coverage, and ready-frontier width. These are structural
proxies and do not establish reader comprehension.

## Scope normalization and aliases

`BuildConfig(scope_compression="unary")` removes a non-root Scope wrapper with
one display child. The repository root keeps its identity and absorbs its only
Scope child. A sole Unit remains a `repo -> Unit` edge, and multi-child author
scopes remain visible.

The surviving node records removed Scope facts in
`metadata.collapsed_scopes`. Root `metadata.aliases` maps raw scope IDs and
removed scope-node IDs directly to surviving node IDs.
`Hierarchy.resolve_node_id` resolves either a current ID or one of these aliases.

## Helper aggregation and Regions

Known `generated_from` chains resolve first within one repository and native
scope. Optional helper absorption requires one consumer, the same scope,
primary-outcome/interface/`keep_separate` protection, and configured declaration
and codepoint capacity. Shared, unused, cross-boundary, and unknown technical
declarations remain separate. All raw dependency pairs remain exported even when
helper aggregation internalizes a pair.

Regions default to fanout 6 and ordered interval dynamic programming. Larger
inputs use the configured balanced fallback. `edge_weights` may override the
Region association weight of an existing internal declaration pair; it does not
change dependency legality, narrative order, helper ownership, or exported raw
edges. Invalid and external pairs fail validation.

`region_burden_features` enables the formal-material tie-break only when the fixed
features match the Workspace and all required members are present. It affects
Region split ties, not narrative order.

## Hierarchy JSON

The hierarchy top level contains repository and artifact digests, configuration,
diagnostics, nodes, raw edges, and external references. Nodes contain stable IDs,
kind, title, parent, ordered children, declaration coverage, source scope, and
metadata. Only Units contain a representative. No EET prose is stored here.

`Hierarchy.from_dict` checks identity, tree ownership, exact disjoint coverage,
aliases, external separation, and edge endpoint ownership. To prove that a
hierarchy matches current facts, rebuild it against the fixed Workspace and a
validated `NarrativeOrder`.

## Focused verification

```sh
PYTHONPATH=src python -m pytest \
  tests/unit/structure/test_order.py \
  tests/unit/structure/test_construction.py \
  tests/unit/structure/test_scope_compression.py \
  tests/unit/structure/test_graph.py \
  tests/unit/recommendation -q
```

The tests cover dependency graph shapes, protected source conflicts, multi-origin
selection, native module order, enumeration stability, strict artifact loading,
stale rejection, Region rebuilding invariants, helper/scoping behavior, and the
recommendation integration boundary.
