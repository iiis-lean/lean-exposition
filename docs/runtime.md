# API runtime

`lean_exposition.runtime` is the API-first model execution layer. `StructuredExecutor` performs one explicitly configured structured-output call. `ApiToolExecutor` adds a bounded function-tool loop. Optional stateful Codex and Pi backends implement the shared [`AgentExecutor`](agents.md) contract and use separate configuration.

The credential-free example in [runtime.api.example.json](../configs/runtime.api.example.json) targets the official `deepseek-flash` endpoint. Load its credential only from the named environment variable:

```python
import json
from pathlib import Path

from lean_exposition.runtime import ApiConfig, StructuredExecutor

config = ApiConfig(**json.loads(Path("configs/runtime.api.example.json").read_text()))
executor = StructuredExecutor(config)
schema = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}
result = executor.execute("Name the addition laws region.", schema)
if result.status == "succeeded":
    print(result.data)
```

`protocol` is explicitly `responses` or `chat_completions`; Responses is the product default. `structured_output_mode` is independently and explicitly one of:

- `native_schema` (the default) sends the existing provider-native strict JSON Schema request and preserves the previous response contract.
- `prompt_json` omits the provider-native final-output schema. A deterministic prefix contains the same canonical schema, the exact output rules, and a schema-derived fenced example. The task is placed after that stable prefix. The final answer must be exactly one lowercase `json` fenced object with no surrounding prose.

Both modes parse and validate locally against the same caller-provided JSON Schema. There is no fallback between modes, and the runtime never selects a mode, protocol, or reasoning setting from the model name. Responses reasoning settings are passed unchanged from the `reasoning` mapping. Chat Completions accepts only an explicit `reasoning.effort` because its API surface does not support the other Responses reasoning fields; the value is passed unchanged as `reasoning_effort` in either output mode.

The HTTP transport disables redirects, SDK retries, and environment proxy discovery. Set `proxy_url` explicitly when the endpoint requires a proxy. Configuration contains only a credential environment-variable name, never the credential value. `extra_body` is an explicit provider escape hatch; callers own its compatibility.

`max_output_tokens` defaults to `None`. In that state the runtime omits
`max_output_tokens` from Responses requests and `max_tokens` from Chat
Completions requests, leaving the endpoint to apply its own supported limit.
Set a positive integer only when a particular workflow intentionally requires
an explicit output budget.

## Results and failures

`ExecutionResult` records the normalized status, validated data, raw completion text, provider status, Chat finish reason, Responses incomplete details, model and response identifiers, protocol, actual structured-output mode, caller trace label, request digest, tool events, and usage. The request digest distinguishes the output modes while preserving existing `native_schema` digests; it also covers the prompt, schema, endpoint/model, protocol, output limit, reasoning, provider options, and cache key without including credentials. Usage includes input, output, total, cached, and reasoning tokens plus the original provider usage reports for each call in a tool loop. Queued, cancelled, timed-out, failed, and successful background results retain the configured mode evidence.

Errors use stable categories such as `missing_credential`, `provider_error`, `provider_failed`, `length`, `incomplete`, `empty_output`, `invalid_json`, `trailing_output`, `schema_validation`, `unknown_tool`, `invalid_tool_json`, `invalid_tool_arguments`, `tool_handler_error`, `tool_step_limit`, `tool_call_limit`, `input_budget`, `tool_result_budget`, `timeout`, and `cancelled`. Prompt JSON additionally distinguishes `missing_json_fence`, `multiple_json_fences`, `invalid_json_fence`, `leading_output`, and `non_object`; non-empty text after the closing fence remains `trailing_output`. Provider exception text and rejected output are never copied into `ApiError`, so error records remain stable and redacted. HTTP status, exception type, and provider code are retained when available.

`run_json` returns validated data and raises `RuntimeFailure` with the complete result on failure. `start`, `status`, `result`, and `cancel` provide process-local background execution. A configured timeout or cancellation publishes one terminal result; late provider output cannot replace it. Closing the local client requests interruption but does not guarantee remote billing cancellation.

## Function tools and client history

Define each function with a strict input schema and bind a handler separately:

```python
from lean_exposition.runtime import ApiToolExecutor, FunctionTool

tool = FunctionTool(
    name="read_page",
    description="Read one page.",
    parameters={
        "type": "object",
        "properties": {"page": {"type": "integer"}},
        "required": ["page"],
        "additionalProperties": False,
    },
)
result = ApiToolExecutor(config).execute(
    "Read page 1, then answer.",
    schema,
    tools=[tool],
    handlers={"read_page": lambda page: {"text": f"page {page}"}},
    max_steps=4,
)
```

The executor validates tool arguments before every dispatch. It enforces `max_steps`, `max_tool_calls` (default 32), `max_input_characters` (360,000, including cumulative history), and `max_tool_result_characters` (60,000). Multiple tool calls in a single response each consume the call quota. Oversized tool results stop the loop before another provider call. Tool schemas, allowlists and limits are included in request identity. Responses loops append returned output items and `function_call_output` items to client-owned history. Chat loops append assistant tool calls and tool messages. Neither path sends `previous_response_id`. In `native_schema`, fully required closed object schemas use `strict=true`; schemas with optional local parameters explicitly use `strict=false`, preserving omission/default semantics while retaining local validation. In `prompt_json`, the marker is omitted for provider portability while the parameter schema is still supplied and enforced locally before the handler runs. The final response uses the selected mode's parser. Tool definitions are sorted by name, and `canonical_json` plus `stable_prompt` keep shared prefixes and dynamic suffixes deterministic for cache reuse. Cache effectiveness must be read from `result.usage.cached_tokens`.

## API-first workflows

`lean_exposition.workflows` binds product tasks to executor capabilities rather
than providers or model names. Current adapters cover Region and scope naming,
concurrent EET sibling drafting and junction review, Reader and restricted
downstream tools, feature annotation with optional adjudication, and source-order
evaluation. Each call records stable prefix and request digests plus normalized
usage and cached-token counts. The workflows grant only their explicit function
tools; they do not expose shell or filesystem access.

An empty Reader tool allowlist grants no tools. Downstream protocols support `max_tool_calls` (or the `tool_calls` alias), `max_input_characters`, and `max_tool_result_characters`; unknown limits fail before execution. Protocol limits can tighten, but cannot increase, the workflow bounds.

Feature annotation requires unique complete item coverage, rubric labels and known evidence field names. Adjudication receives original items; agreement is computed locally as the largest label count divided by the number of successful annotators. Order evaluation requires one score per candidate and a known preferred candidate. Reading comparisons require one nonempty answer per question and citations from `condition.evidence_refs`.

The OpenAI Agents SDK is not a dependency of this API layer. Stateful Codex and
Pi sessions are available through the separate [Agent execution](agents.md)
interface when a workflow needs a native Agent lifecycle.

## Live canary result

The official `deepseek-flash` Responses endpoint passed one strict structured-output call and one client-history function-tool loop. The tool call returned the exact bound value; normalized usage recorded cached and reasoning tokens. Checked-in DeepSeek configuration and smoke commands use only Flash; DeepSeek Pro is not a supported project configuration.

BeeAPI Grok passed strict Responses calls in the 2026-09-16 canary when the required proxy was supplied explicitly. On 2026-09-17 the same provider still passed ordinary Responses, streaming, long-output, and client-history tool calls, while minimal native strict-schema routes returned 404. Those probes motivated the now-explicit `prompt_json` mode; provider support must still be checked by mode rather than inferred from a model name or an older successful call.

The implemented `prompt_json` path was then checked through the shared executors with Paratera `GLM-5.3-Flash`, Paratera `DeepSeek-V4.1-Flash`, and BeeAPI `grok-4.6`. Each model passed one small structured call and one one-tool client-history loop with high reasoning and no output-token limit. This is protocol and endpoint evidence only, not a quality, reliability, latency, or cost conclusion.

## Verification

Run the focused fake suite with:

```console
PYTHONPATH=src /root/miniconda3/envs/benchmark/bin/python -m unittest discover -s tests/unit/runtime -v
```

The suite covers both protocols, strict schemas, normalized usage, error categories, redaction, client history, tool validation, step limits, cancellation, timeout, and deterministic prompt helpers. Live endpoint canaries load `/root/.config/lean-exposition/model-providers.env` outside the repository and must never print keys or full request content.
