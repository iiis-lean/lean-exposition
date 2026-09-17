# Loading projects

`load_project` combines catalog metadata, Lean source, compiled environments,
and published materials into the same `RepositoryBuildBundle`. Every bundle can
enter the same HDG, content, EET, and reader pipeline. Missing fields and approximate
edges carry provenance, coverage, and diagnostics; they do not select a different
reader or prevent structure construction. Structural corruption still raises an
error.

## Unified entry point

```python
from lean_exposition.importers import load_project
from lean_exposition.structure import build_hierarchy

bundle = load_project(
    '/path/to/project', repo_key='project',
    compiled_modules=(),  # Explicit source-only policy, including profile inputs.
    cache_dir='/path/to/cache',
)
hierarchy = build_hierarchy(bundle, 'project')
```

The equivalent CLI writes `bundle.json` and `hierarchy.json`, without generating
text:

```sh
python scripts/load_project.py --root /path/to/project --repo-key project \
  --source-only --cache-dir /path/to/cache --output-dir /path/to/output
```

Install Toolkit in the extraction environment (`pip install -e '.[native]'`, or
an editable local `lean-mcp-toolkit` checkout). LC catalog loading alone does not
require Toolkit or Lean.

### Acquisition choices

| Input | Selection | What is recovered |
| --- | --- | --- |
| LC | Auto-detected catalog, or `lc_catalog` profile contributor | Registered declarations, NL/FL, dependencies, scopes, summaries, resources |
| Source | `modules` or `source_roots`; otherwise discovered `.lean` files | Approximate declaration inventory, exact recognized slices, docstrings, context, explicit references |
| Prebuilt Lean | `compiled_modules=('M', ...)` | Canonical names, kernel kinds, type/value constants, generated ownership, elaborated types and docstrings |
| Compiled-first default | `compiled_modules=None`, `build=None` | Query all selected modules; reuse existing artifacts and build missing ones |
| Explicit build | `build=True` | Build selected semantic modules before extraction |
| Exported semantics | Compiled contributor `config.semantic_manifest` | Existing `extract_modules` JSON, checked against source/config/toolchain hashes |
| Published materials | Profile assets and selected declarations | Available published statements, NL and published dependencies |

`build=None` is the unified runner default: build missing selected-module artifacts,
reuse existing ones. `build=True` requests incremental builds even when artifacts
exist; `build=False` (CLI `--no-build`) forbids builds but still requests semantic
extraction. `compiled_modules=()` (CLI `--source-only`) explicitly chooses text-only
loading for expensive projects. LC continues its dedicated catalog path.
Reading `.olean` uses a small Lean program under the
project's toolchain, importing the existing environment; Python does not decode
Lean's binary format. No LeanInteract or REPL is required. Each module runs in a
fresh single-thread Lean query with a timeout and optional `memory_limit_mb`
(Lean's allocation limit, not a whole-process RSS guarantee). Imports may still
consume substantial memory. Failure of a requested module preserves source and
other successful observations, but `compiled_acquisition:incomplete` explicitly
lists incomplete modules. This is an acquisition report, not a downstream gate. The loader does not clone projects; an explicitly or automatically requested Lake
build may resolve dependencies using the project configuration.

Source parsing reuses Toolkit's existing `text_ast`. Reference resolution masks
comments/strings, respects loaded imports, namespaces, simple opens and local
binders, and reports ambiguity. It does not recover all macro, notation,
typeclass, or tactic-generated dependencies. These edges are `text_reference`,
never compiler expression dependencies. Source-only IDs remain source-stable;
unique module/name/range matches connect them to compiled identities in a mixed
bundle. Unmatched source declarations remain loaded.

A theorem's type/value dependencies belong to statement/proof respectively.
Definitions keep body and type/value dependencies in statement. Complete compiled
coverage supersedes approximate edges for that part, including confirmed empty
sets. Partial coverage preserves fallback evidence. Unloaded providers remain
references. Cycles remain graph facts; cyclic ordering constraints are relaxed
with a diagnostic so a reading tree can still be built.

## LC inputs

LC uses fixed Git objects, not uncommitted working-tree edits. One active catalog
declaration produces one singleton unit; the `preserve` policy keeps this unit
boundary. Main exports become primary outcomes. Provider repositories supplied
through `providers` are resolved using the consumer's Lake lock. The loader does
not run LC runtime, recovery, or compilation.

Catalog statement/proof NL and FL, fine kinds, dependencies, origins, and summaries
are retained. Readable resource manifests under `.lean_constellation/resources/items`
are checked for byte size and SHA-256. Exact origin ranges attach to declarations;
otherwise material remains available at repository level. Invalid resources are
reported locally. Images and vector drawing source are not treated as prose.

The narrower `load_lc_workspace` and `load_native` APIs remain available; prefer
`load_project` when mixing acquisition routes or needing source fallback.

## Profiles and project-specific materials

Pass a `RepositoryProfile` or a profile JSON path, optionally with `target_slice`.
Profiles fix inputs, select contributors/material assets, and provide primary
outcomes, scope/unit hints, and order hints. Their stage labels describe acquisition
intent; they do not gate downstream access. A source archive without Git records
its declared revision as unverified and fixes loaded bytes with asset hashes.
A profiled Git checkout must match its fixed revision without tracked edits.

Built-in material readers cover formalization YAML, PROOF-PATH, published HTML
and site JavaScript data, TeX, plain/Markdown text, and optional PDF page text
(requires PyMuPDF; no OCR). Published-site readers use data parsing, not JavaScript
execution. PDF page numbers are retained without inventing source line positions.
TeX resolves static includes; explicit `\lean{...}` or label-to-declaration maps
bind complete statement/proof environments locally. Unsupported TeX stays
reported; there is no general TeX interpreter or fuzzy mathematical alignment.

Materials retain their own assets, records, bindings, parser/config identities,
and provenance in the bundle. Exact `states` / `proof_route` relations can fill
core NL fields. General explanations stay additional materials, visible in full
declaration cards and summary input. Ambiguous/unbound materials remain available
on a scope or repository. Published edges retain `published` evidence labels.

For repository-specific logic use a narrow contributor:

```python
# Return a RepositoryAdapterResult, normally via dataclasses.replace.
def enrich(adapter, context):
    # context provides root, repo_key, profile, plan, and a fixed-input read(path).
    # Add field contributions, SourceTextContribution summaries, material bundles,
    # or explicit scope/unit seeds. Use attach_materials for text projection.
    return adapter

bundle = load_project('/path/to/project', contributors=(enrich,))
```

Custom contributions may fill core NL/FL fields, dependencies, summaries, or
structure seeds. Region construction and helper aggregation remain shared.
Equal-authority field conflicts select deterministically and record alternatives
in diagnostics; no unmarked replacement is performed.

## Summaries and downstream use

LC catalog summaries feed the existing declaration text store. Extra generation
remains opt-in. The existing writing preparation can collect missing texts in
batches; the generation input now includes declaration context, elaborated type,
and attached materials. Batches are bounded by declaration count and characters.
Oversized single requests remain failed/missing and retain full-source access.
Accepted records are cached and pinned for fixed EET content.

Mathematical writing views use summaries for nonfocus declarations in coarse
scope/region contexts. Focused unit cards retain full statement/proof fields and
materials. A source-only bundle uses exactly these same views.

## Caching and limits

Source caches include source and Toolkit implementation bytes. Compiled caches
include source/config/toolchain, exporter implementation, and local artifact
state. The unified runner shares artifact checks across modules and caches the
normalized project bundle; custom contributors disable that whole-bundle cache.
Partial compiler failures are not saved as final project-cache successes.
Artifact stamps use paths, sizes, and mtimes; they are local cache guards, not a
proof that supplied binaries were built from supplied sources. Trusted artifacts
must come from a matching fixed checkout/toolchain.

The current runner supports conventional project-root module paths. Nonstandard
Lake `srcDir` layouts need a project adapter. Very large single modules are not
split during Lean environment loading. Source parsing, graph construction, and
material aggregation still run in memory; source-only access avoids proof
elaboration but is not a claim that every whole-repository HDG is cheap. Select
modules/roots/slices and cache results for large experiments. No full builds or
model-generation quality claims follow from a successful inventory.
