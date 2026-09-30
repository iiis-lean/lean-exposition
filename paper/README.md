# Proofs to Build On

Working manuscript for ICLR 2027. The title and abstract reflect the current discussion; the body contains a compact section structure and a provisional evaluation plan. The abstract's final result claim is provisional and must be supported or revised before submission. A visible working-draft note makes this status explicit in the PDF.

- `main.tex`: the single manuscript entry point.
- `OUTLINE.md`: proposed narrative, space allocation and evidence requirements.
- `TEMPLATE_SOURCE.md`: official template provenance and formatting constraints.
- `official-template/`: unmodified upstream example sources for reference only.

Build from this directory:

```sh
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

The output is `main.pdf`. No author identities, affiliations, numerical results or unverified citations have been inserted. The official style remains in anonymous submission mode. The current preview is an outline, so its page count does not predict final manuscript length.

Validation (2026-09-19): `latexmk` completed successfully, producing a four-page planning draft with no overfull boxes after the evaluation-plan revision. The long title uses phrase-level line breaks; the initial title/abstract layout was visually inspected.
