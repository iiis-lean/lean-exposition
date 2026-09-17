# Local reader service

Start the hand-authored demo with the optional `reader` dependencies installed:

```sh
PYTHONPATH=src python -m lean_exposition.app --state-dir data/reader-demo --port 8765
```

The server binds only `127.0.0.1`. For a fixed package, supply `--workspace`, `--hierarchy`, and `--content` together. A multilingual library uses `--packages` with one Workspace/Hierarchy/content entry per locale. Only one service process should own a reader state file.

HTTP and streamable HTTP MCP expose the same seven capabilities:

| Capability | Required arguments | Behavior |
| --- | --- | --- |
| `open_reader` | `instance_id` | Create an independent reader and initial immutable view. |
| `get_overview` | `reader_id` | Page visible containers, frontier nodes, projected edges, and external interfaces. |
| `read_text` | `reader_id` | Page rendered Markdown lines and stable anchors. |
| `inspect` | `reader_id`, `ref` | Read summaries, interfaces, members, NL, Lean, sources, edges, or an owned job. Interface inspection accepts `dependency_view: analysis|full`, defaulting to `analysis`. |
| `locate` | `reader_id`, `ref` | Return the visible ancestor and required expansion path without changing state. |
| `recommend` | `reader_id` | Rank legal expansion candidates with compact policy evidence. |
| `apply_action` | `reader_id`, `expected_view`, `action`, `target` | Expand, collapse, reset, cancel a job, switch locale, or set a display budget. |

HTTP uses `POST /api/{tool}`. `GET /api/schema` returns the canonical schemas and `GET /api/instances` lists fixed instances. Tool results include `ok` and `view_id`; errors use stable codes such as `stale_view`, `validation_error`, `not_found`, `not_expandable`, `generation_failed`, and `budget_exceeded`.

## Views and generation jobs

Every saved view binds its own content `instance_id` and manifest. Old owned views remain readable. Cursors bind the exact view and query, so they cannot continue silently against newer content.

Expanding a section with missing children creates a reader-owned job. Its observable states are:

| State | Meaning |
| --- | --- |
| `queued` | The job is registered against an expected view and base content. |
| `drafting` | Missing siblings are being drafted concurrently. |
| `stitching` | Strict adjacent boundary reconciliation is running. |
| `validating` | Final local checks passed and group model validation is running. |
| `published` | The sibling group was committed to a new immutable manifest. |
| `failed` | No group was published; the previous view remains current. |
| `cancelled` | Cancellation won before publication; generated evidence may be retained. |

Repeating the same active expansion returns the same job. Inspection and cancellation enforce reader ownership. Cancellation shares the publication commit control, so a cancelled job cannot publish afterward. A published job advances the requesting reader only if its expected view remains current and the rendered expansion fits its budget; otherwise `applied=false` and the cached content can be opened explicitly later.

Collapse removes the target and all descendants from the current expansion set. Reopening that ancestor reveals only its direct children. Reset clears all expansion state. Terminal entries cannot expand.

## API execution and recommendations

Pass `--runtime-config configs/runtime.api.example.json` to enable on-demand API generation. Select the package-wide writing strategy with `--generation-strategy sequential|concurrent`; the default is `concurrent`. The strategy is part of content identity and each generation job reports the one it uses. The runtime file contains current `ApiConfig` fields and refers to a credential environment-variable name; it never contains the credential. Responses is the default protocol, and Chat Completions must be selected explicitly. Codex and Pi implement an optional separate Agent lifecycle; they are not required to serve or read cached content.

The application defaults to the structural recommendation policy. It binds current Workspace, Hierarchy, and FeatureSet digests and returns cost tier, structural gains, material coverage, and target basis. Use `--recommendation-policy random` for the deterministic seeded baseline, which makes no mathematical-benefit claim. A direct `ReaderService` without an injected policy retains the random fallback for small programmatic fixtures.

Recommendation context contains only current legal candidates, expansion memory, locale, structure identity, current display length, budget, and cached synopsis lengths. It never exposes hidden generated child text. The service rejects hidden targets, duplicate targets, and duplicate ranks.

## Locales, budgets, and inspection

Chinese and English packages with the same fixed Workspace and Hierarchy share `structure_id` but have different content and `instance_id` values. Switching locale chooses an already loaded package, preserves legal expansion memory, and never translates or generates prose. The hand-authored demo may use `locale: null`.

A display budget counts Unicode codepoints in rendered prose plus visible internal titles. It is not a reading-time or understanding estimate. Lowering a budget does not fold text automatically. An over-budget expansion leaves the view unchanged; collapse remains legal.

The overview projects true provider-to-consumer edges onto the current frontier. External repositories remain grouped interfaces outside the internal tree. Explicit inspection pages exact underlying declaration pairs, source lines, provenance, and declaration material; raw details are not injected into ordinary prose.
For a scope or Region, `inspect(detail="interfaces")` uses the filtered
`analysis` dependency view by default. Pass `dependency_view="full"` to recover
the complete stored declaration pairs without changing the reading view.

Focused verification uses the exposition and reading unit suites, including loopback HTTP/MCP parity. The current bilingual AgreeToDisagree smoke package additionally passed package loading, cached expansion without new provider calls, terminal inspection, locale switching, collapse, and an independent-port browser run. These checks do not certify generated mathematics.
