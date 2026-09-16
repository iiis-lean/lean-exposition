# Loading declaration facts

LC and native Lean adapters produce the same `Workspace` model. Shared assembly
registers unresolved dependency repositories, creates one singleton `DeclUnit` per
loaded declaration, and validates the result. This is the foundation for later
graph construction; helper merging, regions, features, and exposition generation
are separate work.

## LC Git inputs

```python
from lean_exposition.importers import LCRepositoryInput, load_lc_workspace

workspace = load_lc_workspace(
    LCRepositoryInput('/path/to/ConsecutiveDivisorCounts', 'ConsecutiveDivisorCounts'),
    providers=(LCRepositoryInput('/path/to/WeightedSieve', 'WeightedSieve'),),
)
serialized = workspace.to_json()
```

Use repository keys matching the source's dependency references and Lake package
names. The adapter reads Git objects, so uncommitted working-tree edits are not
included. The main input defaults to local `HEAD`; fetching newer GitHub commits
is the caller's responsibility. Each declaration contributes only its current
revision. A supplied provider repository is an object store from which the
consumer's Lake lock selects the dependency commit. This does not maintain a
history of declaration revisions in the resulting model.

`Main` is represented by the repository root scope, named after the repository.
Its children and directly owned declarations can be presented immediately below
the repository. All of the active Main contract's `exports` become
`primary_outcomes`; `interfaces[].bound_decl` is not an exhaustive substitute.
`local_public` remains a separate declaration property.

The adapter preserves statement/proof NL, formal code, declared dependencies,
node ownership, fine declaration kinds, and source provenance. It does not load
LC runtime state, build objects, or node dependency graphs. Dependency edges are
labelled `lc_declared`; they are not claimed to enumerate kernel expression
constants. Missing proof content stays missing.

## Native Lean inputs

Install the optional native tools in the environment used for extraction:

```sh
pip install -e '.[native]'
```

```python
from lean_exposition.importers import load_native

workspace = load_native(
    '/path/to/AgreeToDisagree',
    repo_key='agree_to_disagree',
    modules=('AgreeToDisagree.AgreeToDisagree',),
    primary_outcomes=('AgreeToDisagree.agreeToDisagree',),
    evidence_dir='/path/to/local/extraction-evidence',
)
```

Select the modules to include explicitly. Include their local import closure when
you want those declarations loaded rather than retained as references. Independent
alternative formalizations that define the same names should be loaded into
separate workspaces.

Native extraction uses the project's own `lean-toolchain`. LeanInteract provides
source declarations, positions, docstrings, and section context. A narrow Lean
query obtains compiled names, kinds, type/value constant dependencies, and known
generated-declaration ownership. Syntax identifier lists are not used as semantic
edges. Source text is sliced from the original file rather than reconstructed
from pretty-printed terms.

A theorem's type dependencies belong to its statement and value dependencies to
its proof. A definition retains its body and value dependencies in the statement.
An indivisible docstring is kept whole in statement NL; proof NL records that no
separate text was supplied. A later writing context should provide both parts.
Compiler-only generated constants can have missing original source text; their
status records that distinction.

Raw extraction evidence is an audit artifact, not a validated reusable cache.
Later features may extend the same source/environment passes, but no feature
selection or extraction is performed by these adapters.

## Presentation scope

A loaded Workspace may contain multiple repositories for dependency resolution.
Each HDG/EET presents one explicitly chosen repository. Other loaded repositories
supply external declaration facts rather than additional trees in that document.
Use the [repository graph view](graph-foundation.md) to enforce this boundary.

## Field correspondence

| Model field | LC input | Native Lean input |
| --- | --- | --- |
| Workspace repositories | Main repository and supplied provider object stores | One selected project and dependency metadata |
| Repository version | Selected Git commit | Project Git commit when applicable, plus input asset digest |
| Repository toolchain | Selected `lean-toolchain` | Exact project toolchain; never upgraded by the adapter |
| `primary_outcomes` | Active Main `exports` | Explicit selected declaration names |
| Scope | Active node tree with implicit Main root | Selected module paths and their containing scopes |
| Declaration identity | Repository + node path + registered name | Repository + canonical compiled Lean name |
| Fine kind / kernel kind | LC registered kind / unavailable | Source command kind / compiler constant kind |
| Statement NL | Current statement NL | Complete declaration docstring when available |
| Proof NL | Current proof NL | Missing unless independent text is available |
| Formal content | Current statement/proof code | Exact source slices, with theorem value separated |
| Dependencies | LC statement/proof declaration references | Compiler `Expr` constants in type/value |
| Source context | LC projection source, including possible helpers | Lean namespace/section context returned by the parser |
| Generated owner | No extra helper enumeration | Confirmed constructor, recursor, or projection owner |
| DeclUnit | One per imported declaration | One per imported declaration, including compiler-only facts |

The source adapters do not infer native primary outcomes from every public
constant. Unknown generated ownership is retained as unknown rather than guessed
from a name suffix.

## Version and input boundaries

The default verified mappings are Lean 4.28.0 with REPL v1.3.14 and Lean 4.32.0
with REPL v1.3.18. Other toolchains require an explicit `repl_rev`; that override
is not a claim of tested compatibility. `local_repl_path` can select an existing
REPL project of the matching Lean version when automatic fetching is unavailable.

The native adapter currently expects explicit module selections and conventional
project-root module paths. Automatic entrypoint discovery and general Lake
`srcDir`/custom package layout resolution are not implemented. Dependencies outside
the selected modules remain references; installed package source paths help
resolve their repository ownership. A full dependency source audit is not performed.

Selected-module incremental builds precede extraction. Source, project
configuration, and toolchain digests guard against changes during extraction.
Compiler-only facts retain semantic dependencies even when no original author
command can be mapped. This supports basic loading, not a claim of exhaustive
generated-owner classification or independent mathematical verification.
