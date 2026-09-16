# Feature interpretation and limits

The 50-pair agreement is 70%/68%/72%/74% for A/B/C/D, with kappa 0.392/0.360/0.435/0.335. There are 58 disagreements among 200 label pairs. Neither model used `uncertain`; that is a model behavior, not evidence of certainty or complete local context.

Some disagreements reflect rubric adherence or different thresholds for a **dominant** role, rather than unavoidable mathematical ambiguity:

- For `agreeToDisagree'`, GLM's A=yes evidence cites sophisticated concepts in the statement. That does not itself establish that this declaration performs a substantive construction or choice; Luna's A=no focuses on the equality proved.
- For `localSensitivity_le_sensitivity`, GLM treats one `Finset.le_sup` application as insufficient for B, while Luna treats the local-to-global connection as explanatory. All 16 B disagreements have Luna=yes and GLM=no, indicating a systematic annotator threshold difference.
- For `sevenAffineFamilyEval`, Luna's D=yes cites `Nat.cast_mul`. This establishes that an adaptation occurs, but does not by itself establish that adaptation dominates the declaration. D should especially not become a benefit penalty.

The original labels remain unchanged. No relabeling was selected to improve feature associations. Missing dependency bodies and these adherence issues limit what the comparisons establish.

## Conservative shortlist

- Retain `formal_chars` and observed `dependency_count` as material/context descriptors. In Erdős 946's random sample, B=yes/no dependency medians are 12/3 for GLM (n=7/3) and 11.5/3.5 for Luna (n=8/2). Do not treat length as a role rule: C=yes/no length medians in the same project reverse from GLM 20985.5/942.5 to Luna 1230/2238. NL size is optional context, available in 40/50 cards, with absence represented by null.
- Retain separate object, proof-premise, instance and type-parameter interface observations where genuine compiled types are available. For Agree's random sample, B=yes/no proof-premise medians are GLM 2/1 (n=5/5) and Luna 2/0 (n=8/2). This evidence is **native-only** (24 observations). Proposition-parameter count is zero throughout those 24 and is not an empirically selected discriminator. Instance counts are not automatic noise penalties.
- Use type Expr node count as a primary structural-size observation; keep depth for audit rather than giving both independent weight. In Sensitivity, C=yes/no node medians are GLM 28/22 and Luna 31/19, but project/model coverage is sparse. Within-project random-sample node/depth Spearman correlations are 0.959 in Sensitivity and 0.994 in Agree (n=10 each).
- Retain the narrow exact-kind rewrite/simplify Syntax count for follow-up. Sensitivity C=yes/no medians are GLM 1.5/0 (n=4/6) and Luna 1/0 (n=7/3). This is one native project, not a general tactic-role classifier. Custom macros and term-level `calc` outside the fixed map are not counted.

Defer selection of direct Exists/Sigma flags: all 24 observed native conclusions are false, despite separate real Lean fixture validation. Defer case/induction and construction/application Syntax groups as semantic selectors: the former is sparse, and the latter misses term-level definitions. In Sensitivity, every A=yes sample has zero construction/application nodes under both models, so the group is not a construction detector.

Avoid stacking all size measures. Within-project random formal-character/line Spearman correlations range from 0.809 to 1.000; formal-character/Syntax-node correlations are 0.774 and 0.964 in the two native projects. Raw formal size includes comments, and total tactic Syntax nodes include wrappers. Keep these as audit observations instead of redundant score components.

These decisions are provisional descriptions, with no learned weights, threshold search, importance score, helper/Region rule, or measured understanding benefit. All cross-tabs and medians are in `feature_review.json`; the fixed-map evidence is in `syntax_kind_groups.json`; redundancy checks are in `feature_redundancy.json`; selected counterexamples retain both model explanations in `counterexamples.json`.
