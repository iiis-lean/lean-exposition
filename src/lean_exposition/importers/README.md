# importers

`common.py` provides lossless adapter migration helpers. `lc.py` reads current LC
catalog records from selected Git snapshots; `native.py` maps Lean source and
compiled constant evidence into the same construction contracts. Both public
loaders return a validated `RepositoryBuildBundle`.

`source.py` is the source-only native path. It consumes the current
`lean-mcp-toolkit declarations.extract` JSON/JSONL contract, including chunk
identity, rather than copying the Toolkit parser. It records declarations,
docstrings, exact source slices, command-coverage diagnostics, and unknown
dependency coverage. An inventory is not a production Workspace. Callers may
explicitly create a provisional bundle for inspection; Region, recommendation,
EET, and Reader still require a verified bundle.

Public entry points: `LCRepositoryInput`, `load_lc_workspace`, `load_native`, and
`assemble_workspace`, plus the source-inventory readers. See
[loading APIs](../../../docs/loading.md) for usage,
field mapping, pinned repository/toolchain support, and input limitations. Importers
preserve real Git revisions, source hashes, and dependency locks; they do not add a
manual input version.

LC never uses the source-only path: one active current-catalog declaration becomes
one singleton DeclUnit, and the default `preserve` policy does not merge it.
