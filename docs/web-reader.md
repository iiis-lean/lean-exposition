# Local web reader

The repository contains one current static client at `src/lean_exposition/app/static`. It has no frontend build step. The browser uses the same seven Reader operations as HTTP and MCP; the Python service owns content identities, views, expansion legality, jobs, budgets, and recommendations.

Start the hand-authored demo:

```sh
PYTHONPATH=src python -m lean_exposition.app --state-dir data/reader-demo --port 8765
```

Open `http://127.0.0.1:8765`. Fixed packages use the server's `--workspace`, `--hierarchy`, and `--content` options. Cached content needs no model credentials; missing-child generation requires an API runtime configuration.

## Reading and graph interaction

The center column renders continuous Markdown and LaTeX from the selected immutable view. A section control expands or collapses fixed content; statements and proofs remain distinct. Stable DOM shells and unchanged segments survive view replacement, and breadcrumbs retain the selected location.

The HDG shows only current frontier nodes and external interfaces as circles. Expanded ancestor containers remain in the outline and prose but leave the graph frontier. Circle size encodes text amount; color encodes dependency evidence. Directed edges run from provider to consumer and expose paginated original evidence on inspection. Layout is deterministic, including cycle fallback and collision separation. Nodes support hover, selection, drag/pin, zoom, fit, and reset.

The application requests structural recommendations by default. Each suggestion displays the service's reason and compact cost/gain evidence and invokes the same expansion action as the section control. When the server is explicitly started with `--recommendation-policy random`, the UI labels the result as a random baseline with no estimated mathematical benefit.

The browser preserves old views as read-only snapshots. Stale actions offer a return to the latest view. Missing content shows job progress through `queued`, `drafting`, `stitching`, and `validating`, followed by `published`, `failed`, or `cancelled`. Existing prose stays visible during generation. Cancellation, failure, stale completion, or a budget rejection never replaces the current article.

Locale switching selects an already loaded package with the same `structure_id`; it does not translate content. Display budgets use codepoints in rendered text and titles. Details expose overview, interfaces, members, original NL, Lean, and source pages without executing source text.

## Rendering and security

KaTeX, marked, and DOMPurify are pinned and vendored with their licenses. Markdown is sanitized before insertion; scripts, inline event handlers, images, forms, embedded frames, and styles are rejected. KaTeX trusted commands are disabled. Mathematical titles may use inline TeX in the article, outline, breadcrumbs, details, and graph labels.

Text and graph pagination stay bound to one view. The UI resolves compressed scope references through server-provided aliases and never reconstructs hierarchy or provider logic locally.

## Browser verification

With Playwright Chromium installed, the service-driven check targets an already running demo server:

```sh
PYTHONPATH=src python tests/browser/test_reader.py --url http://127.0.0.1:8765
```

The layout-focused check starts and stops its own server on a free loopback port:

```sh
PYTHONPATH=src python tests/browser/test_reader_ui.py
```

The focused checks cover service pagination, stable prose shells, frontier replacement, deterministic circle layout, collision avoidance, projected edges, hover and selection, keyboard expansion, LaTeX, details, budgets, recommendations, old views, stale recovery, collapse restoration, generation status UI, sanitization, and narrow screens. A separate independent-port acceptance loaded the current bilingual AgreeToDisagree smoke package and verified real cached expansion, outline/frontier synchronization, locale switching with shared structure identity, collapse, and an error-free page. These are interface checks, not evidence of mathematical correctness.
