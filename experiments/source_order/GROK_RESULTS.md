# Grok experiments

## Current T10 preregistration (2026-09-17)

The current experiment contract and final input manifest are implemented. Two
initial attempts omitted the explicit proxy required by the shared executor and
returned `APIConnectionError`. With the proxy configured, authentication and
minimal unstructured Responses/Chat probes succeeded, but the frozen batch
returned HTTP 502 without output or usage. Minimal strict JSON-schema canaries
returned 404 on both protocol routes. The experiment remains
`blocked/unverified`; it produced no Grok quality result.

The fixed request contract is BeeAPI Responses with `grok-4.6`, high reasoning,
a 600-second timeout, and no output-token, temperature, or provider-seed field.
The experiment stores model/settings/input/prompt/response digests and normalized
usage but no credential. Sorting selects at most three eligible scopes per
project in stable scope-ID order, uses seed `20260917` for the first anonymous
labels, and preregisters the swapped-label call for every pair. A preference is
counted only when both valid calls select the same underlying strategy; a tie,
invalid response, or label-sensitive disagreement is retained as tie/invalid.

The summary arm includes all eligible Agree author declarations without source
summary and at most 24 declarations from the verified Zeta slice. English and
Chinese have distinct prompts and examples. Ordinary batches contain 8–12
declarations, long proofs run alone, and a cache replay must complete with zero
executor calls. The five model self-rubrics are descriptive checks rather than
correctness labels.

Fake-executor contract tests cover gate-aware stable selection, label swapping,
pair aggregation, bilingual batching, output/config digests, credential
redaction, and zero-call cache replay.

The final current manifest found no eligible ordering pairs: Agree's only order
scope has 18 atoms, every 4--12 atom Erdos1025 scope had identical
dependency-only and fused order, and Zeta23 failed its 16 GiB verified-slice
gate. Agree supplied 19 public author declarations, planned as batches 10 and
9 for each locale. The first batch failed twice; no remaining batch was sent,
no model/protocol/setting was changed, and no substitute project was used.
The stopped-run verifier confirms the current input digest, cached failure,
missing-sample set, zero provider usage, and absence of credentials. The full
experiment verifier fails as expected because 0 of 4 summary batches completed.
Detailed sanitized evidence is in the T10 task-package directory.

The T09 handoff is one JSON manifest with three top-level collections:

```json
{
  "project_gates": {
    "agree": {"status": "passed", "artifact_digest": "..."},
    "erdos1025": {"status": "passed", "artifact_digest": "..."},
    "zeta23": {"status": "passed", "artifact_digest": "..."}
  },
  "order_scopes": [
    {
      "project": "agree",
      "scope_id": "...",
      "direct_atoms": ["..."],
      "dependency_only_order": ["..."],
      "fusion_order": ["..."],
      "source_order": ["..."],
      "cards": {"atom": {"opaque_id": "...", "text": "..."}},
      "hard_dependencies": [],
      "protected_relations": [],
      "material_evidence": []
    }
  ],
  "summary_declarations": [
    {
      "project": "agree",
      "ref": "repo:decl",
      "stable_order": 0,
      "author_declaration": true,
      "missing_summary": true,
      "proof_chars": 0,
      "input": {}
    }
  ]
}
```

The script independently checks gate status, atom-set equality, actual order
difference, and dependency legality. It does not trust a precomputed
`eligible=true` flag.

## Historical source-order retest (2026-09-16)

Date: 2026-09-16. This is a new run that preserves the earlier DeepSeek
evidence. The old blind calls used a 4,096-token output limit and ended before
structured output. This retest sends no output-limit field and explicitly asks
for high thinking.

## Inputs and request contract

The current worktree passed the 67-test source-order suite and regenerated all
four deterministic projects before any Grok call. Workspace, source,
NarrativeOrder, semantic-order digests, metrics, and the selected sample were
unchanged. The regenerated `selection.json` was byte-identical to the accepted
file (SHA-256
`3d03fd0003e7c8173a9f457183ed2400eaf61470c1589f61209ced5a90eebfc5`).

Calls used the shared workflow and executor with BeeAPI `grok-4.6`, Responses,
`reasoning={"effort":"high"}`, an explicit controlled proxy, and
`max_output_tokens=None`. The executor omitted the output-limit field. There
was no Agent, retry, model switch, reasoning rewrite, or fallback. A separate
strict-JSON canary succeeded in 8.82 seconds before the evaluation.

## Primary blind review

All three calls returned valid structured reviews.

| Project | Preferred order | Duration | Output | Reasoning | Cached |
| --- | --- | ---: | ---: | ---: | ---: |
| Erdős 946 | deterministic | 60.22 s | 2,995 | 2,861 | 640 |
| Sensitivity | source | 198.22 s | 9,139 | 9,004 | 640 |
| Uniform | deterministic | 85.14 s | 4,176 | 3,977 | 2,688 |

Grok's explanations favored keeping each local mathematical cluster together.
For Sensitivity, it preferred the source order because it introduced `embed`
and `restrictTo` before the source-unavailable simplification lemmas and kept
those lemmas close to the sensitivity results. This is a concrete local
exception to any claim that the deterministic proxy always gives the better
narrative.

The primary count is deterministic 2, source 1. All three primary calls happened
to select anonymous label `B`, so this count was not accepted without a label
diagnostic.

## Frozen label-swap diagnostic

After observing the three `B` selections, a separate diagnostic swapped A/B for
the same cases and changed nothing else. It was frozen before those three calls
and is not pooled with the primary count.

| Project | Primary label | Swapped label | Strategy retained |
| --- | --- | --- | --- |
| Erdős 946 | B | A | deterministic |
| Sensitivity | B | A | source |
| Uniform | B | A | deterministic |

All three swapped calls succeeded and retained the same strategy while changing
the selected letter. The primary result is therefore stable under this one
paired label reversal; the observed choices are not explained by a fixed
preference for `B`. The diagnostic used 31,546 total tokens over 289.71 seconds.

## Reader comparison

Both Uniform conditions used the same five cards and three questions and
returned three valid answers citing all five opaque references.

| Order | Duration | Output | Reasoning | Cached | Confidence |
| --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic | 51.40 s | 2,612 | 2,359 | 2,176 | 0.88 |
| Source | 72.72 s | 3,363 | 3,136 | 1,024 | 0.80 |

In this single run the deterministic condition was faster, used fewer output
and reasoning tokens, and reported higher confidence. There is no independent
answer oracle, and confidence is model self-report, so these differences remain
descriptive rather than evidence of human or general model understanding.

## Usage and conclusion

The five primary calls used 28,585 input, 22,285 output, 21,337 reasoning,
7,168 cached, and 50,870 total tokens over 467.71 seconds. The canary and three
label-swap diagnostics add 34,514 total tokens. All nine live calls succeeded;
none was retried.

The corrected API contract is technically viable, and the three blind choices
were stable under one label reversal. The sample is still only three local
windows judged by one model. It supports retaining the deterministic product
as a useful default while documenting Sensitivity as a real counterexample; it
does not justify adding API ordering to the product path or claiming reader
understanding gains.
