# importers

`load_project` is the unified acquisition entry point. It composes LC catalog,
Toolkit source, prebuilt Lean environment, exported semantic JSON, and published
material observations into one validated `RepositoryBuildBundle`. All bundles
use the same downstream hierarchy and EET APIs, including source-only bundles.

- `lc.py`: immutable Git catalog, singleton units, summaries and resources.
- `toolkit.py` / `source.py`: cached Toolkit parsing and strict inventory contract.
- `references.py`: approximate explicit references with scope/import diagnostics.
- `native.py`: compiler facts and original source slices, without LeanInteract.
- `merge.py`: unique identity matches and partial source/semantic composition.
- `materials.py`: local/core NL binding and LC resources/blueprint handling.
- `project.py`: acquisition policy, profiles, caching and custom contributors.

Missing and approximate facts remain explicit. Complete semantic coverage can
replace corresponding source reference guesses. Acquisition failures are local;
malformed final structures remain validation errors. LC retains its direct
catalog route rather than being forced through native declaration parsing.

See [loading projects](../../../docs/loading.md) for APIs, profiles, resource
limits, summary projection, and cache boundaries.
