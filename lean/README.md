# Lean support

The runtime environment query is packaged at
`src/lean_exposition/lean/environment.lean` with its Python wrapper. It runs using
the selected project toolchain. No global project toolchain is selected here.

See `docs/loading.md` for the verified Lean 4.28/4.32 mappings and the boundary
between basic declaration extraction and future features.
