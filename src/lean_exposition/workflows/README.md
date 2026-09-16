# API Workflows

This package keeps product and experiment tasks independent of API providers.
Every call puts stable instructions before canonical dynamic JSON and retains the
prefix digest, request digest, provider usage, and cached-token count.

The current entry points cover Region and scope naming, EET sibling drafting and
junction review, Reader and restricted downstream tool tasks, feature annotation
with optional multi-model adjudication, and source-order evaluation adapters.
They consume `StructuredExecutor` or `ApiToolExecutor` capabilities rather than
branching on provider or model names.

`EetWorkflow` drafts siblings concurrently, preserves their input order, and
stops at the first failed stage. Junction stitching changes only adjacent
boundaries. Validation is a separate bounded call and may use an explicitly
configured executor with a different output budget. Failed or rejected groups
are never presented as successful output.

`content_runtime` adapts structured API execution to the current `ContentStore`
callable. `reader_workflow` binds `ReaderService` through an explicit tool
whitelist. Neither adapter grants shell or filesystem access.
