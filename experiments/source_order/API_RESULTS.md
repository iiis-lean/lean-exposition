# Bounded API blind review and Reader comparison

> Historical run: its 4,096-token output limit prevented every blind review
> from returning structured output. The corrected Grok run preserves this
> evidence and is reported separately in
> [`GROK_RESULTS.md`](GROK_RESULTS.md).

Date: 2026-09-16. The frozen selection and complete redacted call evidence are
in [`selection.json`](../../data/research/source_order/api_eval_20260916/selection.json)
and [`report.json`](../../data/research/source_order/api_eval_20260916/report.json).

## Frozen sample

The pre-call rule found three qualifying local windows. AgreeToDisagree had no
window satisfying all requirements and was omitted without relaxing the rule.

| Project | Atoms | Source distance | Deterministic distance |
| --- | ---: | ---: | ---: |
| Uniform | 5 | 105 | 71 |
| Sensitivity | 6 | 42 | 29 |
| Erdős 946 | 5 | 8 | 5 |

Uniform, the largest pre-call structural improvement, was fixed as the sole
Reader case. Strategy names were absent from blind-review inputs; A/B placement
was fixed by the recorded hash rule.

## Blind review

All three one-attempt calls failed with normalized error `length` and provider
incomplete reason `max_output_tokens`. Each used the full fixed 4,096 output
tokens as reasoning and returned no structured review.

| Project | Input | Output | Reasoning | Cached | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Erdős 946 | 2,434 | 4,096 | 4,096 | 0 | length failure |
| Sensitivity | 2,160 | 4,096 | 4,096 | 128 | length failure |
| Uniform | 5,500 | 4,096 | 4,096 | 128 | length failure |

There is no API preference to aggregate. This is an endpoint/model/budget
observation, not a tie and not evidence about either order. The protocol did not
retry, raise the budget, alter reasoning, or substitute a model.

## Reader comparison

Both Uniform conditions used the same five cards, three questions, 2,726 input
tokens, and 4,096 maximum output tokens. The condition labels were opaque during
the calls.

| Order | Status | Output | Reasoning | Cached | Confidence | Evidence refs |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic | succeeded | 3,676 | 3,235 | 0 | 0.87 | 5 |
| Source baseline | succeeded | 2,933 | 2,577 | 0 | 0.86 | 5 |

Both returned three answers and cited every displayed item. The deterministic
answer described a tools → criterion → construction → cancellation → main-result
progression; the source answer described criterion → construction → tools →
cancellation → main result. The 0.01 self-reported confidence difference is too
small and too weakly grounded to support a choice. The deterministic condition
also consumed more output and reasoning tokens in this single run.

## Aggregate evidence and limits

The five calls used 15,546 input, 18,897 output, 18,100 reasoning, and 256 cached
tokens. Total wall time was 95.9 seconds; per-call latency was not captured in the
accepted evidence and must not be reconstructed from console polling.

This was one model, three blind windows, and one two-condition Reader task. The
Reader questions had no independent correctness oracle. The evidence does not
measure human comprehension, general model understanding, or expected behavior
on other scopes. It provides no basis to replace the deterministic default or to
introduce API ordering into the product algorithm.

`verify_api_evaluation.py` passed all offline checks for the frozen selection
digest, official Flash configuration, fixed reasoning/budget, anonymous blind
inputs, matched Reader conditions, and usage totals. A separate secret-value
check confirmed that the credential value is absent from `report.json`.
