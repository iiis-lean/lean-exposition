# Immutable exposition content

`ContentStore(workspace, hierarchy, path, executor=..., locale=..., generation_strategy=...)` binds a validated Workspace and complete Hierarchy snapshot. `generation_strategy` is `concurrent` by default and may be set to `sequential`. The API-first production executor is `StructuredExecutor(ApiConfig(...))`; credentials remain outside the content file. Writer operations are construction APIs and are not exposed through Reader tools.

## Identity and manifests

The store derives four related identities:

- `workspace_digest` is the canonical identity of current source facts.
- `structure_id` hashes the Workspace digest and complete Hierarchy digest.
- `content_digest` hashes the locale, generation strategy, current prompts and JSON Schemas, EET stitch and validation instructions, and `max_input_characters`.
- `instance_id` hashes the fixed structure, locale, and content digest.

Saved content must match all current identities. A mismatched content digest fails with an instruction to regenerate the package.

Each manifest contains immutable validated blocks, display metadata, locale, the structure/content identities, and a `complete` coverage flag. Its `manifest_id` is derived from the complete manifest payload. `publish()` may append an already validated group, but cannot rewrite an existing block with different content.

## Blocks and source context

Sections contain fixed `lead_in`, `synopsis`, and `lead_out` segments. Theorems contain `statement` and `proof`; other terminal entries contain `content`. Locale-specific mathematical sections require all three Section segments to be nonempty. Terminal titles are returned with their block and published atomically.

Anchors identify one segment and explicit declaration, node, or edge targets. Targets must belong to that entry's bound writing context. `decl_card`, `scope_view`, and `writing_view` expose compact source material while preserving the ability to query complete declarations, relations, and source pages. Missing external bodies and compiler-only source gaps remain explicit; the writer must not invent a proof.

Canonical writing context includes stable ancestor introductions and preceding sibling outcomes. Every sibling also receives one shared writing convention containing the fixed child order, parent setting and goal, shared source interfaces, and the superseded parent synopsis. This convention is placed in a byte-stable group prompt prefix so sibling calls can share the same cacheable prefix. The synopsis may preserve terminology, notation, and intended coverage only; its conclusions are never premises. A parent ending is a goal for its children, not an already established result. The prompt is checked against the configured character budget before any model call and is never silently truncated.

## API sibling workflow

`generate_root()` handles the one-root group. `generate_children(parent_id)` freezes the ordered direct children and base manifest, then runs the strategy bound to the content package. The strategy is part of content identity, so one file never mixes sequential and concurrent cache entries.

In `concurrent` mode:

1. Draft every model-backed sibling concurrently; fixed source-missing entries are prepared without a model call.
2. For groups with more than one child, make one stitching call. It must return every adjacent pair exactly once, preserve sibling order, and may change only the left `lead_out` and right `lead_in` when those fields exist.
3. Validate every final draft locally against its bound content schema, anchors, nonempty Section rule, and hard prose diagnostics.
4. If local checks pass, make one model validation call for the complete ordered group.
5. Publish all siblings once against the frozen `base_manifest_id` using compare-and-swap.

In `sequential` mode the same source preparation, local validation, group validation, cancellation, evidence, and atomic publication contract applies. Siblings are drafted in reading order; each later request receives the compact outcomes of earlier siblings. The separate stitching call is skipped because transitions are written with the accepted prefix already available.

Draft, stitch when applicable, local-check, validation, usage, cache, and actual strategy evidence are retained. Any failed draft, illegal stitch, local rejection, model rejection, stale base, or executor error prevents group publication. The previously published manifest remains current.

Generation reports `queued`, `drafting`, `stitching`, and `validating` progress. `PublicationControl` serializes cancellation with the single manifest commit decision. If cancellation wins before commit, no generated block is published; if commit has already completed, cancellation cannot relabel that publication.

Codex and Pi are optional Agent backends for the separate interactive writing-job interface. Their MCP endpoint exposes only the bound job's read/query/draft/accept operations. Agent completion is not publication evidence; callers confirm the persisted manifest after the Agent finishes.

## Rendering

`render(hierarchy, manifest, expanded)` is pure. Expanding a Section replaces only its synopsis with ordered children while preserving its lead-in and lead-out. Collapsing restores the exact original synopsis. It returns fixed Markdown lines, stable segment and section anchors, visible nodes, and the current reading frontier.

The store uses one atomic JSON file and process-local locks. Run one owning process per content path. Local schema checks and the single model review constrain format and consistency but do not formally verify mathematical correctness.

Focused verification:

```sh
PYTHONPATH=src python -m unittest discover -s tests/unit/exposition -v
```

The suite covers digest binding, immutable manifests, strict adjacent stitching, local and model rejection, base-manifest races, cancellation at the commit boundary, retained safe evidence, and exact expansion/collapse rendering. The current AgreeToDisagree smoke package was also generated through the API workflow in Chinese and English: both locales published all five nodes, shared one `structure_id`, retained distinct locale instances, and passed cached Reader expansion, locale switching, collapse, and independent-port browser checks. This is integration evidence, not a proof of the generated prose.
