# Local feature experiment results

50 frozen declarations, two exploratory reference-label sets, 40 stratified-random and 10 separate boundary cases. These are descriptive results, not gold-standard labels or evidence of reading benefit.

| Label | Three-category agreement | Kappa | Definite pairs | Definite agreement | Luna / GLM uncertain |
|---|---:|---:|---:|---:|---:|
| A | 70.0% | 0.392 | 50 | 70.0% | 0.0% / 0.0% |
| B | 68.0% | 0.360 | 50 | 68.0% | 0.0% / 0.0% |
| C | 72.0% | 0.435 | 50 | 72.0% | 0.0% / 0.0% |
| D | 74.0% | 0.335 | 50 | 74.0% | 0.0% / 0.0% |

## Project label counts

| Model / project | A yes | B yes | C yes | D yes |
|---|---:|---:|---:|---:|
| glm / agree | 7 | 6 | 1 | 5 |
| glm / erdos946 | 12 | 9 | 6 | 2 |
| glm / sensitivity | 5 | 2 | 5 | 1 |
| glm / uniform | 9 | 8 | 8 | 0 |
| luna / agree | 2 | 10 | 4 | 6 |
| luna / erdos946 | 10 | 11 | 6 | 7 |
| luna / sensitivity | 6 | 8 | 8 | 2 |
| luna / uniform | 8 | 12 | 6 | 2 |

## Coverage and interpretation

Formal source size and dependency counts cover all 50 declarations. NL size is missing for cards without NL rather than encoded as zero. Complete compiled type/telescope and parsed command Syntax cover 24 native declarations; 26 LC declarations have explicit missing compiled/Syntax metrics because a matching fixed-version artifact was unavailable. No dirty or different-version artifact was substituted.

The 24 native samples contain no direct Exists/Sigma conclusion and no proposition-parameter binder. These candidates have no observed variation here; this cannot establish general uselessness. A real Lean fixture separately validates Exists/Sigma and the distinction between P : Prop and h : P.

Each model has independent per-project and per-stratum feature analyses, using fixed within-project random-sample quartiles. The full cross-tabs, definite-pair medians/ranges and uncertainty coverage are in feature_review.json. Pooled quartile tables aggregate project-relative bins; they do not erase project differences. Small cells and model disagreements preclude predictive claims.

Dependency bodies are absent from local cards. The provided material can therefore leave an application's mathematical role unclear. A model choosing a definite label does not remove this limitation.

Recompute: prepare.py → extract.py → validate_probe.py → tactic_groups.py → label.py → analyze.py → verify.py → summarize.py. label.py reuses valid matching saved batches. See README.md for the environment and protocol.

Artifacts under ignored data/research/feature_selection: sample_manifest.json, cards.json, local_features.json, compiled_evidence.json, labels_luna.json, labels_glm.json, agreement.json, feature_review.json, retained_features.json, validation.json and per-attempt raw outputs in runs/.

See [INTERPRETATION.md](INTERPRETATION.md) for rubric-adherence checks, actual per-candidate evidence, native-only limits and conservative retain/defer recommendations.
