# Declaration features

`extract_features(workspace, hierarchy, compiled=None)` produces a serializable
`FeatureSet`; `save(path)` and `FeatureSet.load(path)` preserve observations and
coverage. It is deterministic and reads no research artifacts or generated text.
The artifact records the fixed workspace digest, hierarchy ID and extractor version.

Each declaration retains formal and original natural-language statement/proof
codepoints, five compiled binder categories, type-expression nodes/depth, exact
rewrite/simplification parser-node counts, and unique internal/external/unresolved
incoming declaration-pair counts. Codepoints are Python string length. LC formal
sizes measure registered segments; they need not be exclusive declaration spans.
Syntax counts describe parser occurrences, not semantic tactic steps or runtime.

Missing values are `null`, never measured zero. Aggregate values are null when any
member is missing; `observed_sum`, counts, coverage and min/max remain available.
Node aggregation deduplicates declaration identities. It does not estimate union
source-span size or generated exposition length.

`collect_native_features(workspace, repo_key, project_path, output_path)` explicitly
runs the bundled Lean probe via `lake env lean`. It verifies the workspace toolchain
and source asset hashes, records query/log evidence, and observes authored declarations
with source ranges. Compiler-only declarations remain missing. The returned envelope
must match the workspace/repository/extractor when passed to `extract_features`.
LC compilation is not part of this production path. Use the benchmark environment.

Unit cost material is its unique members' stored formal statement plus proof.
Section cost material is unique incoming/output and cross-child interface statements;
local public declarations or internal sinks provide a fallback for isolated sections.
Missing required interface statements remain visible in coverage.
