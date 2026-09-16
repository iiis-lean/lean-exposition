# Deterministic source-order evaluation

`analyze.py` rebuilds current Workspace facts from the four fixed source
projects, derives one `NarrativeOrder` per target repository, rebuilds Regions,
and writes an independent report. It does not load or migrate historical
Workspace JSON. Historical hierarchy JSON is read only as the source-first
Region baseline.

Run from the repository root in the benchmark environment:

```sh
PYTHONPATH=src /root/miniconda3/envs/benchmark/bin/python \
  experiments/source_order/analyze.py
```

The default output is `data/research/source_order/deterministic_20260916/`.
The script verifies fixed raw dependency-pair counts, Unit coverage, exported
edges, external references, primary outcomes, final distance versus the source
start, artifact round trips, and semantic order stability under declaration
enumeration reversal. It reports order-only metrics separately from Region
rebuild metrics.

No model, EET generator, Reader, server, or external checkout mutation is used.

## Bounded API evaluation protocol

The API stage is frozen before any live call:

1. Rebuild the same four fixed current Workspaces and use the historical
   source-first atom order plus the accepted deterministic `NarrativeOrder`.
2. For each project, enumerate source-order contiguous windows of 3–6 atoms.
   Keep a window only when the two induced orders differ, both obey the same
   projected hard dependencies, at least one internal real edge exists, and the
   deterministic order has strictly smaller declaration-pair-weighted distance.
3. Select at most one window per project by largest distance improvement, then
   smaller window, scope identity, and stable atom IDs. No model output affects
   sample selection.
4. Make one `SourceOrderEvaluationWorkflow.blind_review` call per selected
   window. Candidate labels contain no strategy name or metric. Label placement
   is deterministically balanced across the selected cases.
5. Before seeing blind-review output, choose the window with the largest
   structural improvement for the sole Reader comparison. Call
   `reading_comparison` once for each order with the same cards, questions, and
   output budget.

All live calls use the official `deepseek-flash` Responses endpoint through the
shared `StructuredExecutor`, with `reasoning=null`, no Agent, no Pro model, no
retry presented as the same attempt, and no automatic parameter rewrite. The
report records exact redacted inputs, structured outputs, request/prompt/response
digests, normalized usage including cache tokens, and failures. The results are
descriptive API-review evidence, not training data or a claim about human or
model understanding.

The accepted bounded run is summarized in [`API_RESULTS.md`](API_RESULTS.md).
All three blind reviews exhausted the fixed 4,096-token output budget in
reasoning and produced no structured preference. Both preselected Uniform Reader
conditions succeeded, but their self-reported confidence differed by only 0.01
and has no independent correctness oracle. The deterministic product order
therefore remains unchanged.
