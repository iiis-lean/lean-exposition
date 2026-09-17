# configs

Shareable configuration examples live here. Keep credentials and real local settings under ignored paths.

- `source-order.example.json` is the strict optional source-sequence format used by
  `SourceSequenceSpec.load`. Replace the repository identity and keep only the
  sequences that apply. Exactly one of `roots`, `paths`, or `modules` is populated
  according to `kind`; the other arrays remain present and empty.
- `math-reader.api.example.json` and `math-reader.agent.example.json` configure
  reader generation clients.
- `runtime.api.example.json` configures the shared structured API executor.
- `project_profiles/` contains strict declarative profiles for fixed real
  repositories. A profile selects contributors, target slices, primary outcomes,
  materials, and order hints. Its `stage` is a capability gate (`inventory`,
  `provisional`, `verified_slice`, or `formal`), not a schema version. Profiles do
  not themselves build a Workspace or claim a stronger stage than their inputs.

LC profiles use `lc_catalog`; they do not select source inventory and preserve
catalog declarations as singleton units. Native profiles may use source inventory
for repository-wide discovery and a compiled contributor for a bounded verified
slice. Multi-target repositories use disjoint target slices so their roots and
materials cannot mix.
The shipped global foundation catalog lives beside the structure package as
`src/lean_exposition/structure/foundation_catalog.json`. It hides reviewed
Lean and Mathlib infrastructure from analysis views; identity mismatches and
unknown declarations are kept conservatively.

## Runtime smoke commands

`scripts/runtime_smoke.py` exposes the three current execution backends. The
API and Pi examples default to the official `deepseek-flash` endpoint/model;
the Codex example defaults to `gpt-5.6-sol`. DeepSeek smoke runs reject every
model name other than `deepseek-flash`.

```bash
PYTHONPATH=src python scripts/runtime_smoke.py api \
  --env-file /root/.config/lean-exposition/model-providers.env
PYTHONPATH=src python scripts/runtime_smoke.py codex
PYTHONPATH=src python scripts/runtime_smoke.py pi \
  --credential-env DEEPSEEK_API_KEY \
  --provider-credential-env DEEPSEEK_API_KEY \
  --env-file /root/.config/lean-exposition/model-providers.env
```

For another OpenAI-compatible API, pass its model, base URL, protocol, and the
name of its credential environment variable explicitly. `--env-file` is
optional; values already present in the process environment take precedence.
Saved records include only the environment variable name and normalized,
redacted runtime errors.

The generic API runtime does not send an output-token limit unless
`max_output_tokens` is set to a positive integer. Current product examples use
`null`, and the smoke CLI leaves the field unset unless an explicit diagnostic
supplies `--max-output-tokens`. Product workflows use timeout, local parsing,
schema validation, and bounded retries rather than guessing a shared budget for
reasoning and visible output.

`scripts/reader_agent_smoke.py` is the API Reader canary. It uses
`ApiToolExecutor` through `reader_workflow` and exposes only the seven fixed
Reader operations:

```bash
PYTHONPATH=src python scripts/reader_agent_smoke.py \
  --env-file /root/.config/lean-exposition/model-providers.env
```

Both commands perform live calls when invoked. Unit coverage uses fake
executors and does not contact a provider.
