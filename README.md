# Lean Exposition

Lean Exposition explores Lean mathematics through a continuous, progressively expandable exposition and a synchronized dependency graph. It accepts structured Lean Constellation results and native Lean projects.

The pipeline imports a fixed project snapshot, derives a deterministic narrative order from dependencies and explicit source sequences, builds declaration units and nested regions, and constructs an Expandable Exposition Tree (EET). One reading engine serves the web interface and seven HTTP/MCP tools. Each instance displays one repository; other repositories contribute external declaration facts and interface groups.

## Status

The [foundation model](docs/data-model.md), [LC/native adapters](docs/loading.md), [graph queries](docs/graph-foundation.md), and [source, helper and Region construction](docs/structure-construction.md) are implemented. Real input checks cover Uniform, Erdős 946, Sensitivity, and classic AgreeToDisagree, with Lean 4.28/4.32 loading probes. This does not imply support for every Lean version or project layout.

[API-first model execution](docs/runtime.md) provides strict structured calls and bounded tool loops for OpenAI-compatible Responses and Chat Completions endpoints. [Codex and Pi](docs/agents.md) implement one optional stateful Agent lifecycle. Current workflow adapters cover naming, EET drafting and review, Reader tools, feature annotation, and source-order evaluation without selecting a backend by model name. The checked-in DeepSeek API and Pi configurations permit only the Flash tier.

[Content construction](docs/exposition.md) separates Region metadata from fixed exposition blocks and publishes generated children atomically. [The reader service](docs/reader.md) provides view-bound reads, local expansion/collapse, generation jobs, and optional display-length budgets. [The web reader](docs/web-reader.md) uses the same operations as agents.

[Recommendations](docs/recommendation.md) default to a generation-free structural policy bound to the current Workspace, Hierarchy, and feature digests. Its cost tiers are uncalibrated engineering estimates and its gains are structural proxies. A deterministic seeded random policy remains available as an explicit baseline with no estimated mathematical benefit. Local reference-label experiments and source-order evaluations are research evidence, not product guarantees; downstream understanding evaluation remains unfinished. Generated text is not formally verified.

## Run the local demo

```bash
python -m pip install -e '.[reader]'
python -m lean_exposition.app --state-dir data/reader-demo --port 8765
```

Open http://127.0.0.1:8765/. The handwritten fixture needs no model credentials. HTTP tools use `/api/{tool}`; standard streamable HTTP MCP is served at `/mcp`. See [reader configuration](docs/reader.md) for loading content packages and [runtime configuration](docs/runtime.md) for model-backed generation. The server is a local, single-process service.

## Layout

| Path | Responsibility |
| --- | --- |
| `src/lean_exposition/` | Fact models, importers, structure, runtime, content, reading engine, and interfaces |
| `src/lean_exposition/app/static/` | Web reader and vendored browser dependencies |
| `src/lean_exposition/lean/` | Version-aware source extraction and packaged Lean environment query |
| `tests/` | Focused unit, transport, and browser checks and shareable fixtures |
| `experiments/local_features/` | Reproducible exploratory sampling, labeling, and analysis scripts |
| `configs/` | Shareable configuration examples without credentials |
| `docs/` | Shareable English documentation |
| `dev_docs/` | Local Chinese design, research, implementation plans, and handoff |
| `data/` | Local frozen inputs and generated artifacts; ignored by Git |

See [the architecture overview](docs/architecture.md) for module boundaries. Internal working copies may also contain a Git-ignored `dev_docs/` tree for active design records, implementation status, research evidence, and handoff notes; it is not part of the public source distribution.

Lean Constellation and Lean MCP Toolkit are external sources/adapters, not embedded runtime requirements. Sample projects retain their own Lean toolchains and dependency locks.
