# Local feature selection experiment

This temporary experiment compares two sets of exploratory A/B/C/D reference labels on the same 50 local declarations. It does not train a classifier, implement production features, estimate reading benefit, or establish a gold standard.

Sampling is frozen by `prepare.py` (seed 20260914): 10 random declarations per project after removing prespecified boundary declarations, plus shortest/longest theorem per project and shortest non-theorem in each LC project. This gives Uniform 13, Erdős 946 consumer 13, Sensitivity 12, Agree 12. Provider mirror declarations and compiler-only facts are excluded. Random and boundary strata remain separate in statistical outputs. The random sample represents the eligible non-boundary pool, not an unadjusted population prevalence estimate.

The label definitions and JSON schema are in `protocol.py`. Inputs contain local formal code (nested comments removed lexically), available NL, names/kinds, and a compiled full type when available. Both models receive byte-identical cards and protocol per batch; no feature values or other labels are included. Lexical comment removal is not represented as a Syntax AST. Raw source provenance remains in the manifest.

Prespecified candidates:

| Candidate | Definition |
|---|---|
| Type binder counts | Complete elaborated outer telescope, including section variables. Classify in order: instance-implicit; binder domain is a Sort (type parameter or proposition parameter); binder domain is a proposition (proof premise); otherwise object parameter. Record binder explicitness separately. `P : Prop` is a proposition parameter, `h : P` a proof premise. |
| Expr nodes/depth | Structural tree count/depth of the original compiled type; repeated subexpressions count repeatedly, metadata nodes included. No body unfolding for size. |
| Conclusion existence | Head constant after opening the outer telescope and removing metadata: direct `Exists`, `Sigma`/`PSigma`, otherwise false. No unlimited unfolding; not a semantic construction label. |
| Formal/NL size | Unicode codepoints and formal line count, nested comments removed for formal code; raw formal size separately retained. LC uses actual proof file when present, otherwise statement file; native concatenates source statement and proof segments. |
| Dependency baseline | Distinct explicit provider refs across statement/proof from frozen RawDecl facts. |
| Syntax nodes/depth | Real `Parser.runParserCategory` command AST on the frozen local formal card using the imported native project grammar; all Syntax constructors count. 24/24 native parsed, LC missing. |
| Tactic Syntax nodes | Count Syntax nodes whose actual kind begins `Lean.Parser.Tactic.`; includes organizational/container nodes, so it is not the number of semantic proof steps. Exact kind arrays retained. |

Native compiled type extraction uses the fixed source digests and existing Lean 4.28 artifacts. LC snapshot declarations use Lean 4.32; release-candidate sources have no matching local compiled artifacts discovered, so compiled candidates may be missing rather than borrowing types from dirty or mismatched workspaces. Coverage and project confounding must be explicit.

Model runs use exact `glm-5.3-flash` and `gpt-5.6-luna` (max), five cards per batch, independent contexts, bounded concurrency, and at most two retry attempts for execution/format failures. The first batch is the format pilot and counts toward 50 if the protocol is unchanged. Codex uses a fresh repository-external empty cwd/home with tools disabled, checks actual item types, and deletes copied authentication after each batch.

Analysis reports each label's three-category agreement/Cohen kappa, definite-pair agreement and uncertainty rates. Constant-distribution kappa is null with a reason. Each model is analyzed separately by project and stratum using fixed within-project random-sample quartile cuts, cross-tabs, and yes/no feature medians/ranges; uncertain labels are excluded from binary associations and coverage is reported. There is no threshold search.

Dependency bodies are not expanded into the labeling cards. A short application can remain uncertain when its mathematical role cannot be established locally; neither model is instructed to guess missing context.

Pilot configuration: the first GLM attempt returned empty content under an 8192-token budget; its automatic second attempt succeeded. After this format diagnostic, the final GLM configuration explicitly requests `reasoning_effort=max` with 16384 output tokens and repeats its pilot batch. Earlier attempts remain recorded but the final valid fixed-configuration result alone enters analysis. Luna remains max; its unchanged successful pilot counts toward 50. Model identity and label rules never change. Both providers use a bounded 480-second run timeout for subsequent batches.

`tactic_groups.py` fixes three exact parser-kind groups (case/induction/decomposition, construction/application, rewriting/simplification). It excludes pattern/config/location wrappers and never calls these counts semantic proof steps. A zero means no mapped parser node; custom macros and term-level calc outside the declared kind set are not counted. The map is fixed before inspecting model-label associations, and its raw kind names are saved in `syntax_kind_groups.json`.

The exact GLM thinking levels/default are documented in the [official model card](https://huggingface.co/zai-org/GLM-5.3-Flash). Final runs request max explicitly. The initial empty response occurred under the smaller budget; its cause cannot be established conclusively because that initial attempt predates finish-reason telemetry. Later attempts record finish reason, token usage and reasoning length.

Example reproduction from the repository root (real label calls require explicit authorization and existing external credentials):

```bash
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/prepare.py
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/extract.py
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/validate_probe.py
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/tactic_groups.py
PYTHONPATH=src:/root/code/codex/sdk/python/src /root/miniconda3/envs/benchmark/bin/python experiments/local_features/label.py --workers 3
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/analyze.py
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/verify.py
/root/miniconda3/envs/benchmark/bin/python experiments/local_features/summarize.py
```

Final descriptive results: [RESULTS.md](RESULTS.md). Candidate decisions and rubric-adherence limitations: [INTERPRETATION.md](INTERPRETATION.md). Run `redundancy.py` to recompute the prespecified within-project rank correlations between size measures; these do not use labels.

After successful paired verification, `cleanup_probe_homes.py` removes this lane's completed temporary probe homes and verifies no labeling home or runtime auth copy remains; it never reads credential contents. Cleanup evidence is saved in `credential_cleanup.json`.
