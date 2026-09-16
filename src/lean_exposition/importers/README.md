# importers

`common.py` assembles validated declaration facts and singleton units. `lc.py`
reads current LC records from selected Git snapshots; `native.py` maps Lean
source and compiled constant evidence into the same model.

Public entry points: `LCRepositoryInput`, `load_lc_workspace`, `load_native`, and
`assemble_workspace`. See [loading APIs](../../../docs/loading.md) for usage,
field mapping, pinned repository/toolchain support, and input limitations. Importers
preserve real Git revisions, source hashes, and dependency locks; they do not add a
manual input version.
