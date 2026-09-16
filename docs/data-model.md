# Current fact model

`lean_exposition.models` provides immutable standard-library dataclasses, explicit
`Workspace.validate()`, and validated `Workspace.to_json()` / `Workspace.from_json()`.
There are no runtime dependencies. Construction itself does not validate; validate
at the package boundary. Python records use tuples; JSON uses arrays. Unknown JSON
fields, duplicate keys, missing required fields, and incorrect primitive types fail
with `ValidationError`. JSON accepts exactly the current dataclass fields.

## Input and identity

`WorkspaceManifest` holds `Repository` records, `SourceAsset` records, and
`DependencyLock` relationships. It contains no hand-maintained schema or input
version fields. `Workspace.digest()` hashes the canonical validated JSON for the
complete current fact package, so any fact or ordering change changes its identity.
A fixed repository requires a full lowercase Git object ID and/or a SHA256 input
digest, plus its exact toolchain identifier. These record the SHA/digest of the input read on this occasion; they do not
maintain repository history or implement publication/version audits. `DependencyLock` connects repository keys already bound
in the manifest and retains the lock's provenance; it does not resolve dependencies.

An external repository whose version cannot be recovered has
`version_status="unresolved"` and a nonempty `unresolved_reason`, without a fabricated
revision/digest. Its toolchain may be unknown. It cannot own loaded scopes or facts.
A fixed external repository may also omit its root scope when only referenced.

`DeclRef(repo_key, local_id)` identifies a declaration. Neither field is generated
from features or unit membership. External dependency refs and primary outcomes need not have loaded
`RawDecl` records, but their repository keys must be registered. Original LC
repo/node/name/revision identifiers can be retained in `Provenance.source_ref`;
importers must choose a lossless encoding and must not infer Lean names from them.
An LC declaration revision is source provenance, not an entry in a core revision
database.

## Loading boundary

The thin LC/native adapters share this fact model and load natural-language
and formal content, declaration dependencies, scope membership, and necessary source
provenance. The main repository defaults to its latest current state. For each LC
declaration, read only its current effective revision; no multiple-revision or
historical-state management is provided. Provider repositories should be read at
their specified versions when available; unresolved providers retain a reason.
Provider selection and recording the input SHA/digest do not introduce a publication
or consistency audit. Build-process objects, node dependency graphs, and runtime
state are outside the shared loading boundary. See [loading](loading.md) for the
implemented adapters and their verified layouts.

## Facts and sources

`RawDecl` retains its full Lean name, module, native scope ID, original fine `kind`,
optional `kernel_kind`, statement, optional proof, source ranges/context, provenance,
extraction status, optional completion status (default `None`), local-public flag,
and optional `generated_from` ref. Completion status is validated only when supplied;
adapters need not manufacture or transfer build-process status.
`Status` preserves a source-specific state and provenance; a status value alone is
not proof that the package was independently checked.

Each `DeclContent` has separate natural-language and formal `TextContent` plus its
own dependencies. A non-theorem's statement formal content holds the complete
definition, including its body or constructors. The schema stores this verbatim;
it cannot check completeness or Lean correctness. `TextContent` distinguishes
present empty text (`text=""`) from missing or not-applicable text (`text=null`,
with a reason). Every content record has provenance and may carry a source check
status. Generated/private declarations retain full names and origin evidence;
no extraction or ownership inference is performed.

`Dependency` records a provider `DeclRef`, a nonempty `evidence_kind`, and provenance.
Examples are `lc_declared`, `lean_type`, `lean_value`, and `text_reference`. The string
is open to preserve future source labels. Evidence records are not merged and the
model does not interpret all evidence as verified semantic edges. Consumers must
select evidence explicitly before building a graph. Statement and proof dependencies
remain separate. A graph would orient the provider toward the containing declaration.

Every `SourceAsset` has an ID, owning repository key, original path, and SHA256.
`asset.verify(bytes)` checks supplied bytes without opening files. JSON validation
checks digest syntax and references, not source availability, range bounds against
actual bytes, or filesystem paths. A path is an opaque location, not an instruction
to read it.

`SourceRange` uses one-based lines and Unicode code-point columns with an exclusive
end position. Importers must convert byte/UTF-16 offsets to this convention. When
columns are unknown, both are null and the line interval is inclusive; a single-line
range is valid. Multiple and overlapping ranges are retained. No source-size
aggregation or overlap deduplication is implemented. `source_context` preserves
namespace/section/import/notation/header/docstring text and provenance without
claiming that a fragment is independently compilable. Provenance can reference
multiple ranges, including material assets separate from Lean source.

## Native structure and ownership

`Scope` retains its original container kind, name, repository, parent, and
provenance. IDs are explicit: paths, modules, and namespaces are not inferred from
one another. Each loaded scope belongs to a rooted, acyclic repository tree.
Repository `primary_outcomes` remain separate from declaration `local_public` flags.

`DeclUnit` references one loaded representative and zero or more member unit IDs.
`Workspace.coverage(unit_id)` returns the recursive set of declaration refs. When
units are supplied, their forest must cover every loaded declaration exactly once.
Duplicate representatives, duplicate member ownership, self membership, unknown
members, and cycles are rejected. An empty unit tuple is allowed for a fact package
before structure construction. Unit roots are inferred as units without a parent;
the model performs no LC-per-file or native-per-declaration grouping. Raw facts stay
unchanged when units change. Scope/Region graph aggregation and source deduplication
are outside this implementation.

## Verification

From the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests/unit/models -v
```

`tests/fixtures/models/workspace.json` is synthetic, with matching `Example.lean`
source bytes. It demonstrates definition-body preservation, missing versus empty
NL, separate evidence, an unresolved external repository, a main outcome, and a
nested unit. Its evidence/status labels are illustrative, not real extraction or
Lean verification. Tests exercise round trips, asset hashes, invalid references,
repository identity constraints, digest changes, source positions, and ownership
failures. No Lean process, real input importer, feature computation, EET, or Region
implementation is included in the model test.

`multi_repository.json` contains two different synthetic repositories, `example`
and `consumer`, with the same declaration local ID, independent scope roots/assets,
a dependency lock, and an illustrative cross-repository text-reference edge. It
checks identity and preservation without performing a real import or Lean check.
