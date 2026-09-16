# API Workflow Canary

This experiment exercises the F3 product workflow code against the official
DeepSeek endpoint with `deepseek-flash`. Run it with:

```bash
PYTHONPATH=src python scripts/api_workflows_canary.py \
  --output /tmp/lean-exposition-f3-canary.json
```

Credentials are read from `/root/.config/lean-exposition/model-providers.env`
by default. The report contains model output, status, usage, cache counts, and
prompt digests; it never contains credential values.

The accepted 2026-09-16 run completed Region naming, a two-turn Reader tool
task, and one concurrent two-child EET group with stitching and validation in
100.3 seconds. All three gates passed. The Reader run reported 640 cached input
tokens. The four EET stages reported 2,688 cached input tokens in total.

The endpoint rejected an early EET schema because one `enum` omitted an
explicit string `type`, even though the schema is valid JSON Schema. Adding the
explicit type fixed the request. A separate validation call twice exhausted an
8,192-token output budget almost entirely in reasoning. The accepted run uses
an explicitly configured 16,384-token budget for validation and does not alter
reasoning settings or branch on the model name. That call used 13,044 reasoning
tokens for 902 input tokens. This makes API validation latency and budget a
measured provider limitation rather than an automatic compatibility rewrite.

No output was retried and presented as a single attempt, and no generated prose
was edited by hand. Failed stages stopped the group before publication.
