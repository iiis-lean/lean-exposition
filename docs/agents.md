# Agent execution

`lean_exposition.runtime.agents` implements the shared `AgentExecutor` lifecycle for Codex and
Pi: `start`, `resume`, `follow_up`, `steer`, `cancel`, `status`, and `result`.
The API executor remains the primary path for model-assisted product work;
these adapters are for workflows that benefit from a native Agent session.

`CodexAgentExecutor` uses the OpenAI Codex Python SDK. The SDK is loaded lazily,
so installations that use only API execution do not need it. A local SDK source
checkout can be selected with `sdk_python_root`. The executor denies approvals,
uses a read-only sandbox, disables shell and patch features, and ignores host
skills and plugins. A workflow may bind only the MCP servers explicitly listed
in `mcp_servers`, and every server requires an `enabled_tools` allowlist.

`PiAgentExecutor` talks directly to `pi --mode rpc`; it has no runtime dependency
on the harness-delegation project. It waits for `agent_settled`, verifies the
final idle state, returns session-scoped statistics, and resumes from the native
session file. During Pi's retry backoff it sends `abort_retry` and never replays
an ambiguous prompt. Command timeouts therefore fail closed. Extensions, skills,
prompt templates, project context files, shell, and file writes are disabled.
The default tool set is empty; the only permitted built-ins are the explicit
read-only tools `read`, `grep`, `find`, and `ls`.

Credentials are named by environment variable and are never included in result
objects. The checked-in Pi example uses Pi's official `deepseek-v4-flash` model
(the direct API endpoint names the same Flash tier `deepseek-flash`).
DeepSeek Pro is not a supported project configuration.

See `configs/runtime.codex-agent.example.json` and
`configs/runtime.pi-agent.example.json` for credential-free current examples.

The writing integration accepts an executor factory because its MCP endpoint is
short-lived and bound to one writing job. The factory receives that URL and may
construct the desired Agent adapter. Publication is confirmed from the content
store after the Agent finishes; a successful Agent response alone is not proof
that content was published.

Focused fake tests cover both lifecycle implementations, including resume,
active controls, cancellation, timeout, structured validation, Pi retry
suppression, tool events, and session/turn usage. Live canaries on 2026-09-16
also passed for Codex `gpt-5.6-sol` structured output and Pi
`deepseek-v4-flash` structured output. A second Pi canary enabled only `read`,
observed its start and finish events, and returned the project title from the
repository README.
