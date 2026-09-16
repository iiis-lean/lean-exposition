# Models

The current fact schema implements workspace manifests, revision-bound declaration
references, provenance and source assets, raw declaration content, native scopes,
and declaration-unit ownership. A workspace is identified by the digest of its
validated canonical JSON; the manifest has no manual schema or input batch label.
Import from `lean_exposition.models`; see
[`docs/data-model.md`](../../../docs/data-model.md) for validation and JSON usage.

EET, reading state, Regions, features, and importer behavior are not implemented here.
