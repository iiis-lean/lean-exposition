# Paper structure and evaluation plan

Status: discussion draft, updated 2026-09-19. The title and abstract are retained; the final abstract claim awaits experiments. `main.tex` is now the single detailed evaluation-plan text. This file is its navigation and evidence map, not a parallel protocol specification.

## Compact structure

Six main sections replace the previous eight-section outline and many empty subsections:

1. Introduction: the shared verification/understanding problem and contributions.
2. Related Work: formalization, mathematical retrieval/exposition, and reader evaluation.
3. From Formalization to Progressive Exposition: just two subsections, LC and Exposition.
4. LeanComprehendBench: source families, current resource status, and proposed question families, in connected prose.
5. Evaluation Plan: just three subsections, reader methods/settings; scores/costs; mechanism/supporting comparisons.
6. Discussion and Conclusion: evidence boundaries and supported conclusions.

The appendix has one entry for supplementary materials and experimental details. Required AI-use and recommended reproducibility statement slots remain. Formal submission has a nine-page main-text limit; the current short planning PDF is not a pagination estimate for the completed paper.

## Decisions represented in the plan

- Source materials and tools exist; questions, gold answers and a complete evaluation suite are still being developed.
- Three task families: results/assumptions, argument relationships, bounded application of intermediate results.
- Main setting: open-book question answering through a common Reader with different method tools. Full external agent systems require disclosed component adaptation or a separate end-to-end comparison.
- Constructed comparators: Files-Agent, keyword/embedding retrieval, and HDG-only; the latter is our own system control. External shortlist: RAPTOR or ReadAgent first, possibly GraphRAG; Serena, PaperQA, Danus and STORM remain optional, not implemented main-table entries.
- Proposed question scores in [0,1]: deterministic checks for objectively checkable answers and a separate blinded judge for rubric-based explanations. Mixed scores use fixed weights; human auditing checks reliability. Proposed aggregation balances question families and projects. All of these are protocol proposals, not completed scoring infrastructure.
- Report quality under material-access allowances and actual token/time/monetary usage, including helper calls. Separate cold construction, warm reading, amortization, and judge overhead. No API output-token caps. Do not double-count provider usage categories.
- Auxiliary comparisons: same-EET static/dynamic access; recommendations; runtime graph access; paired original/LC inputs across methods; hidden-question pre-reading with retained context versus fixed notes; serial/parallel writing and scalability diagnostics.

## Resource evidence checked for this revision

Read-only inspection on 2026-09-19:

- `/root/code/lean-comprehend-bench/cases/`: four registered cases (Coverage, Erdos1025, Sensitivity, Zeta23).
- `/root/code/lean-comprehend-bench-lc-grok/cases/` and `dev_docs/lc_expansion/RESULTS.md`: expansion report lists 21 resource-accepted cases, 11 LC + 9 native + Zeta23, and 159 passing targeted tests. These are the expansion task's recorded checks, not newly rerun here; parent integration still requires review.
- The expansion report retains Erdos946's provider-proof/assumption limitation, Erdos1091's restricted subproblem, and Sphere Eversion's unused-module failures. Resource acceptance does not remove these mathematical/build boundaries.
- S6 and Euler/Navier--Stokes are additional candidates from separate preparation work, not registered cases in either inspected case list.

The manuscript includes the project names but no unsupported final benchmark size or claim of completed questions/EETs. Source family, mathematical scope, and original/LC correspondence must be checked before final evaluation membership is frozen.

## Open choices

Final external baseline, evaluated project subset, question counts, rubric weights, budgets, model matrix, repeats, statistical procedures and numerical findings. No runner or paid experiments were started by this paper edit. Do not choose held-out questions based on whether our method wins.

## Earlier planning and provenance

- `../dev_docs/discussions/2026-09-17_evaluation_fork/DOWNSTREAM_NOTES.md`: earlier agreed paper and experiment organization.
- `../dev_docs/reference_research/iclr_abstract_positioning.md`: accepted-paper abstract study.
- `TEMPLATE_SOURCE.md`: official ICLR 2027 template and formatting sources.
