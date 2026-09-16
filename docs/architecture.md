# Architecture

Lean Exposition separates fixed mathematical inputs, reading structure, immutable exposition content, and reader state. Each layer has a digest-bound identity, so stale structure, features, content, and views are rejected.

| Module | Responsibility |
| --- | --- |
| `models` | Repository snapshots, RawDecl facts, provenance, scopes, units, and the Workspace digest |
| `importers` | Shared fact assembly with narrow LC Git and native Lean adapters |
| `lean` | Version-aware source and environment extraction |
| `structure` | Dependency queries, explicit source sequences, deterministic narrative order, helper grouping, scope compression, and Regions |
| `features` | Recomputable declaration and hierarchy observations bound to Workspace and Hierarchy digests |
| `recommendation` | Structural refinement ranking and a deterministic random comparison baseline |
| `runtime` | API-first strict structured calls and bounded tool loops; optional Codex/Pi Agent executors |
| `workflows` | Provider-independent naming, EET, Reader-tool, annotation, and source-order calls |
| `exposition` | Digest-bound content stores, concurrent sibling generation, validation, immutable manifests, and rendering |
| `reading` | Persistent readers, immutable views, legal actions, pagination, generation jobs, and display budgets |
| `interfaces` | One seven-tool contract shared by HTTP and MCP |
| `app` | Loopback server, structural policy binding, hand-authored demo, and the single static web reader |

The identity chain is explicit. `Workspace.digest()` covers current source facts. A Hierarchy binds that digest plus source and construction digests. `structure_id` binds the Workspace and complete Hierarchy. `content_digest` binds locale, prompts, schemas, EET stitching and validation instructions, and writing configuration. `instance_id` binds structure, locale, and content digest. Content files with a mismatched current digest fail loading.

RawDecl facts remain unchanged when helpers are grouped, scopes are compressed, or Regions are formed. Each declaration appears once in the internal reading tree. Underlying provider-to-consumer pairs remain available, while external repositories are represented as interfaces outside the selected repository tree. Source order and structural metrics are deterministic proxies, not evidence of reader comprehension.

API-backed EET generation freezes a sibling group against one base manifest. It drafts missing siblings concurrently, stitches only adjacent boundaries, runs local validation for every final draft, performs one group-level model validation, and publishes the whole group with a compare-and-swap check. A stale base, rejected validation, failure, or cancellation leaves the prior manifest unchanged. Cancellation and the single commit decision share a lock, so cancellation that wins before commit prevents publication.

Reader views are immutable snapshots. Expansion replaces only a section synopsis with its ordered children; collapse restores the exact fixed synopsis. Missing content starts a reader-owned job whose states are `queued`, `drafting`, `stitching`, `validating`, and then `published`, `failed`, or `cancelled`. A published result advances the requesting reader only when its expected view is still current and its display budget permits the expansion.

Human and Agent clients use the same Reader operations. The browser renders returned prose, frontier nodes, projected edges, and recommendation evidence; it does not rebuild structure or choose a provider. The application defaults to structural recommendations. The seeded random policy is available as an explicit baseline with no estimated mathematical benefit.

The service uses local JSON persistence and process-local locks. One service process must own each content and reader state file. Schema validation, source provenance, structural gains, model validation, and successful publication do not formally verify generated mathematical prose.

See [structure construction](structure-construction.md), [content construction](exposition.md), [reader tools](reader.md), [web reader](web-reader.md), and [runtime](runtime.md) for the concrete contracts.
