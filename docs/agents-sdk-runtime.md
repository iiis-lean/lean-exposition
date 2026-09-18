# Agents SDK runtime candidate

`lean_exposition.agent_runtime` is an independent, unconnected candidate. Existing
runtime, Reader, workflows, writing, app and script callers still use their existing
implementation. This foundation does not publish content or validate mathematics.

## Usage

```python
from agents import Agent, function_tool
from pydantic import BaseModel, ConfigDict
from lean_exposition.agent_runtime import SdkRuntime, RuntimeConfig, RunLimits

class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str

async def summarize(source: str):
    async with SdkRuntime(RuntimeConfig(), evidence_dir="./sdk-evidence") as runtime:
        agent = Agent(
            name="summary",
            model=runtime.model,
            instructions="Summarize the supplied declaration.",
            output_type=Summary,
        )
        result = await runtime.run(agent, source, limits=RunLimits())
        return result.final_output  # Summary; result itself is SDK RunResult
```

Set `DEEPSEEK_API_KEY` privately in the process environment. These are defaults, not provider restrictions. Configure `base_url`, `model`,
`credential_env`, and `protocol="responses"` or `"chat_completions"` for another
OpenAI-compatible service. `reasoning` is an explicit effort string; `None` omits
the field. The selected endpoint must support the requested protocol, tools and
output schema; no automatic capability downgrade is performed. Official DeepSeek
Flash Responses has live canary evidence; custom endpoints and Chat Completions
have offline protocol tests, not a claim of universal provider compatibility.
It owns model settings: client and SDK retries are zero, with no output token cap,
provider/protocol fallback or remote conversation storage. Agents must explicitly
bind `runtime.model`; arbitrary model settings and remote/dynamic prompts fail.
`proxy="direct"` ignores proxy environment variables without changing them;
`proxy="environment"` explicitly opts into the HTTP client's environment policy.
The pinned SOCKS dependency supports the environment policy when a SOCKS proxy is configured.

The runtime installs a disabled SDK trace provider with no processors/exporter.
This is a **process-global SDK tracing policy**, not a per-run uploader. Do not
combine it with a host that requires SDK tracing uploads. No proxy variables are
deleted, and disabled tracing does not construct the default exporter HTTP client.

## Tools, schemas and ownership

Use SDK `function_tool(failure_error_function=None)` and real task handlers.
Return expected business errors explicitly as readable values; unexpected errors
terminate the run. SDK strict function schemas make defaulted arguments required:
a parameter such as `offset: int = 0` still appears in `required` on the wire.
The model must supply zero to request that default meaning.

Runner owns tools, dispatch, argument/output validation, history and all model
iterations. Local tools execute serially through SDK `ToolExecutionConfig`.
The business context passes through unchanged; budget state is a separate
ContextVar. Independent runs share only the client, not sessions or histories.
For explicit continuation, pass SDK `result.to_input_list()` as the next input;
that history is counted in the complete request budget. No session/resume API or
remote previous-response chaining is exposed.

Pydantic outputs use SDK schema generation. Task-owned dynamic schemas may
implement public `AgentOutputSchemaBase`; their `validate_json` must enforce the
contract and raise SDK `ModelBehaviorError` on invalid output. Open object schemas
are rejected, not silently rewritten. Dynamic object nodes must explicitly close
`additionalProperties`. Business acceptance remains the caller's responsibility.

MCP servers are native SDK objects owned by the caller's async context manager:

```python
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(
    name="reader",
    params={"url": "http://127.0.0.1:8000/mcp"},
    max_retry_attempts=0,
    failure_error_function=None,
) as server:
    agent = Agent(name="reader", model=runtime.model, mcp_servers=[server])
    result = await runtime.run(agent, "Read the requested record")
```

Enter and exit an MCP context in the same async task. On cancellation, await the
cancelled task so the context exits. MCP schemas retain the SDK default non-strict
behavior unless the caller explicitly configures strict conversion. Conversion
failure does not trigger non-strict fallback. MCP HTTP 1.26 server compatibility
and cleanup were tested; stdio is not claimed as verified.

Handoffs, `Agent.as_tool`, nested `runtime.run`, hosted/non-function tools,
approval/resume, custom agent hooks/guardrails and custom tool guardrails are
rejected. Arbitrary Python handlers remain trusted application code: do not hide
other runners or external side effects inside them to bypass this boundary.

## Budgets and cancellation

`RunLimits` separates model turns, tool calls, complete request characters, tool
result characters, and whole-run timeout. HTTP transport timeout is separate in
`RuntimeConfig`.

- The HTTP request hook checks the **actual UTF-8 decoded serialized request**,
  including instructions, schema, tools and accumulated SDK history, before
  transport. Characters are neither raw bytes nor tokens. Nothing is truncated.
- A public SDK pre-tool hook atomically reserves a call before handler invocation.
  A zero tool budget prevents all handlers. Serial execution prevents extra
  same-turn side effects when the budget is exhausted.
- The end hook checks the SDK tool result, and the request hook additionally checks
  the exact serialized tool output. An oversized result stops continuation; the
  tool may already have performed its side effect. No rollback is promised.
- Cancel the specific task awaiting `runtime.run`, then await its cancellation.
  The shared client remains open for other runs. Await all runs before leaving
  the runtime context. Cancellation cannot guarantee remote inference stops or
  undo a synchronous tool's side effect.

SDK/client exceptions are re-raised after a terminal receipt is saved. SDK may
wrap local hook exceptions in `UserError` or client connection errors; receipts
use the isolated local failure marker rather than mislabeling budget failures as
provider failures. Provider refusal, HTTP errors, schema/tool-selection/argument
errors, unexpected tool errors, max turns, transport/run timeout and cancellation
have distinct terminal statuses. Raw failure responses remain in the receipt.

## Evidence and dependencies

Every run writes `<evidence_dir>/<run_id>/receipt.json`, including limits, input,
instructions, versions, numeric aggregate/per-request usage, SDK tool events,
serialized requests/responses and terminal status. Request headers are never
stored. The configured credential is rejected from model-visible material and
redacted from saved results/errors. This protects that credential, not arbitrary
secrets a task may independently place in its input. Evidence contains task data;
use an appropriate local directory (run directories are created with mode 0700).

`contract_digest` hashes canonical first-request content plus configuration, agent
identity and dependency versions, so schema/tool/provider/version changes change
the identity. `wire_digest` hashes each exact request body. Neither is an existing
product cache key; caller migration must invalidate/re-key old caches.

Critical package pins are in `configs/agents-sdk.requirements.txt` and the
`runtime`, `reader`, `agents-sdk` extras of `pyproject.toml`. Shared-environment
before/after freezes, installation logs and compatibility results are in
`dev_docs/task_packages/2026-09-18_agents_sdk_implementation/RESULTS.md`.
The existing benchmark interpreter is used with explicit `PYTHONPATH`; do not
install this detached worktree editable into the shared environment.

The shared environment still has an instructor/OpenAI 3 dependency conflict and
a pre-existing lean-lsp-mcp/leanclient conflict. Targeted legacy and SDK tests pass,
but a clean environment-wide dependency check is **not** claimed.

```bash
PYTHONPATH=/root/code/lean-exposition-sdk-grok/src \
  /root/miniconda3/envs/benchmark/bin/python -m unittest discover \
  -s tests/unit/agent_runtime -v
```

Streaming, persistent sessions, stdio verification, product publishing, old job
registry bridging and caller migration are outside this first implementation.
