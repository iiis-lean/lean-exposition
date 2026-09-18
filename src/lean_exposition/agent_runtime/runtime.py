"""Official model lifecycle, pre-transport budgets, and local SDK receipts.

Runner owns all model iterations, dispatch, validation, and history. This module
only observes public SDK lifecycle hooks and the serialized HTTP boundary.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from openai import APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import TypeAdapter
from agents import Agent, ModelSettings, OpenAIResponsesModel, OpenAIChatCompletionsModel, RunConfig, RunHooks, Runner
from agents.agent_output import AgentOutputSchema, AgentOutputSchemaBase
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, ModelRefusalError, UserError
from agents.model_settings import ModelRetrySettings
from agents.run import ToolExecutionConfig
from agents.tool import FunctionTool, ToolOriginType, get_function_tool_origin, resolve_function_tool_failure_error_function
from agents.tracing import set_trace_provider
from agents.tracing.provider import DefaultTraceProvider
from agents.usage import Usage

from .types import BudgetExceeded, RunLimits, RuntimeConfig, SecretInInput, UnsupportedAgent


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _usage(usage):
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "input_tokens_details": usage.input_tokens_details.model_dump(mode="json"),
        "output_tokens_details": usage.output_tokens_details.model_dump(mode="json"),
        "request_usage_entries": [
            {"input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
             "total_tokens": e.total_tokens,
             "input_tokens_details": e.input_tokens_details.model_dump(mode="json"),
             "output_tokens_details": e.output_tokens_details.model_dump(mode="json")}
            for e in usage.request_usage_entries
        ],
    }


def _check_schema(schema, *, require_closed=False):
    if not isinstance(schema, dict):
        return
    if require_closed and (schema.get("type") == "object" or "properties" in schema):
        if schema.get("additionalProperties") is not False:
            raise UnsupportedAgent("Dynamic output objects must explicitly set additionalProperties=false")
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        raise UnsupportedAgent("Open output objects are not supported; close the task schema explicitly")
    # Traverse schema positions, not property names or example/default data.
    for keyword in ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas"):
        for child in schema.get(keyword, {}).values():
            _check_schema(child, require_closed=require_closed)
    for keyword in ("items", "contains", "not", "if", "then", "else", "propertyNames"):
        _check_schema(schema.get(keyword), require_closed=require_closed)
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(keyword, []):
            _check_schema(child, require_closed=require_closed)


@dataclass
class _Run:
    limits: RunLimits
    receipt: dict
    directory: Path
    usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0
    failure: str | None = None
    active_tools: set[str] = field(default_factory=set)
    phase: str = "model"


_current: ContextVar[_Run | None] = ContextVar("sdk_runtime_run", default=None)


class _Hooks(RunHooks):
    async def on_llm_end(self, context, agent, response):
        state = _current.get()
        state.usage.add(response.usage)
        state.phase = "tools" if any(item.type == "function_call" for item in response.output) else "output"

    async def on_tool_start(self, context, agent, tool):
        state = _current.get()
        if resolve_function_tool_failure_error_function(tool, context) is not None:
            state.failure = "configuration_error"
            raise UnsupportedAgent("Tool failures must terminate; set failure_error_function=None")
        # No await between check and reservation: atomic within the run's event loop.
        if state.failure or state.tool_calls >= state.limits.max_tool_calls:
            state.failure = state.failure or "tool_budget"
            state.receipt["tool_events"].append({"event": "blocked", "name": tool.name})
            raise BudgetExceeded("Tool-call budget exceeded before handler invocation")
        state.tool_calls += 1
        call_id = context.tool_call_id
        state.active_tools.add(call_id)
        state.receipt["tool_events"].append({"event": "started", "name": tool.name, "call_id": call_id})

    async def on_tool_end(self, context, agent, tool, result):
        state = _current.get()
        # This is the SDK's result, not an independently dispatched tool call.
        text = str(result)
        state.active_tools.discard(context.tool_call_id)
        state.receipt["tool_events"].append({"event": "completed", "name": tool.name,
            "call_id": context.tool_call_id, "characters": len(text), "result": text})
        if len(text) > state.limits.max_tool_result_characters:
            state.failure = "tool_result_budget"
            raise BudgetExceeded("Tool result exceeded budget after handler execution")


class SdkRuntime:
    """Use as an async context manager; cancel individual run tasks, not this client.

    MCP servers are caller-owned SDK async context managers. Keep them connected
    around run(), and exit their context after success, failure, or cancellation.
    `transport` is an HTTP transport injection point for offline boundary tests.
    """

    def __init__(self, config: RuntimeConfig, *, evidence_dir: str | Path,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.evidence_dir = Path(evidence_dir)
        self._transport = transport
        self._client = None
        self._key = None
        self.model = None

    async def __aenter__(self):
        if self._client is not None:
            raise RuntimeError("Runtime is already open")
        key = os.environ.get(self.config.credential_env)
        if not key:
            raise ValueError("Configured credential environment variable is missing")
        # Public provider API; no default exporter/client, proxy reads or uploader.
        # SDK tracing is process-global: this runtime deliberately disables it.
        provider = DefaultTraceProvider()
        provider.set_disabled(True)
        set_trace_provider(provider)
        self._key = key
        http = httpx.AsyncClient(trust_env=self.config.proxy == "environment",
            transport=self._transport, timeout=self.config.transport_timeout,
            event_hooks={"request": [self._request], "response": [self._response]})
        self._client = AsyncOpenAI(api_key=key, base_url=self.config.base_url,
            max_retries=0, timeout=self.config.transport_timeout, http_client=http)
        model_class = OpenAIResponsesModel if self.config.protocol == "responses" else OpenAIChatCompletionsModel
        self.model = model_class(self.config.model, self._client)
        return self

    async def __aexit__(self, *exc):
        await self._client.close()
        self._client = None
        self._key = None
        self.model = None

    def _safe(self, value):
        if isinstance(value, str):
            return value.replace(self._key, "<redacted>")
        if isinstance(value, dict):
            return {self._safe(k): self._safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe(v) for v in value]
        return value

    def _secret_check(self, value):
        if self._key in _json(value):
            state = _current.get()
            if state:
                state.failure = "secret_input"
            raise SecretInInput("Configured credential cannot enter model-visible material")

    def _save(self, state):
        state.receipt["usage"] = _usage(state.usage)
        state.receipt["tool_calls_reserved"] = state.tool_calls
        path = state.directory / "receipt.json"
        path.write_text(json.dumps(self._safe(state.receipt), ensure_ascii=False, indent=2) + "\n")

    async def _request(self, request):
        state = _current.get()
        if state is None:
            raise UnsupportedAgent("Use runtime.run(); direct or nested model calls are unsupported")
        text = (await request.aread()).decode("utf-8")
        body = json.loads(text)
        self._secret_check(body)
        exchange = {"request": body, "wire_digest": sha256(text.encode()).hexdigest(),
                    "characters": len(text), "transport_allowed": False}
        state.receipt["exchanges"].append(exchange)
        if "contract_digest" not in state.receipt:
            contract = {"config": asdict(self.config), "versions": state.receipt["versions"],
                        "agent": state.receipt["agent"], "request": body}
            state.receipt["contract_digest"] = _digest(contract)
        if len(text) > state.limits.max_input_characters:
            state.failure = "input_budget"
            self._save(state)
            raise BudgetExceeded("Serialized request character budget exceeded before transport")
        # Also check exact SDK-serialized outputs (structured/image tool returns).
        for item in body.get("input", []):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                output = item.get("output", "")
                size = len(output if isinstance(output, str) else _json(output))
                if size > state.limits.max_tool_result_characters:
                    state.failure = "tool_result_budget"
                    self._save(state)
                    raise BudgetExceeded("Serialized tool result exceeded budget before transport")
        for item in body.get("messages", []):
            if isinstance(item, dict) and item.get("role") == "tool":
                output = item.get("content", "")
                size = len(output if isinstance(output, str) else _json(output))
                if size > state.limits.max_tool_result_characters:
                    state.failure = "tool_result_budget"
                    self._save(state)
                    raise BudgetExceeded("Tool result exceeded budget before transport")
        if state.failure:
            raise BudgetExceeded("Run already stopped by a local boundary")
        exchange["transport_allowed"] = True
        self._save(state)

    async def _response(self, response):
        state = _current.get()
        text = (await response.aread()).decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        state.receipt["exchanges"][-1].update(http_status=response.status_code, response=body)
        self._save(state)

    def _validate_agent(self, agent):
        if agent.model is not self.model:
            raise UnsupportedAgent("Agent.model must be this runtime.model")
        if agent.handoffs:
            raise UnsupportedAgent("Handoffs are unsupported")
        if agent.hooks or agent.input_guardrails or agent.output_guardrails:
            raise UnsupportedAgent("Custom agent hooks/guardrails may run nested agents; unsupported")
        if not isinstance(agent.instructions, (str, type(None))) or agent.prompt is not None:
            raise UnsupportedAgent("Use explicit instructions; dynamic/remote prompts are unsupported")
        if agent.tool_use_behavior != "run_llm_again":
            raise UnsupportedAgent("Tool results must return through the SDK model loop")
        if agent.model_settings != ModelSettings():
            raise UnsupportedAgent("Model settings are owned by RuntimeConfig (no retries or token cap)")
        for tool in agent.tools:
            if not isinstance(tool, FunctionTool):
                raise UnsupportedAgent("Only SDK function tools and local MCP servers are supported")
            origin = get_function_tool_origin(tool)
            if origin and origin.type == ToolOriginType.AGENT_AS_TOOL:
                raise UnsupportedAgent("Nested Agent.as_tool runs are unsupported")
            if tool.needs_approval or tool.tool_input_guardrails or tool.tool_output_guardrails:
                raise UnsupportedAgent("Approval and custom tool guardrails are not supported")
            if resolve_function_tool_failure_error_function(tool) is not None:
                raise UnsupportedAgent("Use function_tool(failure_error_function=None); return expected errors explicitly")
            if tool.timeout_seconds and tool.timeout_behavior != "raise_exception":
                raise UnsupportedAgent("Tool timeouts must terminate the run")
        for server in agent.mcp_servers:
            if server.tool_input_guardrails or server.tool_output_guardrails:
                raise UnsupportedAgent("Custom MCP tool guardrails are unsupported")
            if getattr(server, "max_retry_attempts", None) != 0:
                raise UnsupportedAgent("MCP servers must set max_retry_attempts=0")
        output = agent.output_type
        if output is not None and output is not str:
            schema = output.json_schema() if isinstance(output, AgentOutputSchemaBase) else TypeAdapter(output).json_schema()
            _check_schema(schema, require_closed=isinstance(output, AgentOutputSchemaBase))
            # Let the SDK validate/close the actual output contract, never rewrite it ourselves.
            if not isinstance(output, AgentOutputSchemaBase):
                AgentOutputSchema(output)
        return agent.clone(model_settings=ModelSettings(reasoning=Reasoning(effort=self.config.reasoning) if self.config.reasoning is not None else None,
            retry=ModelRetrySettings(max_retries=0)),
            mcp_config={**agent.mcp_config, "failure_error_function": None})

    async def run(self, agent: Agent, task_input, *, context: Any = None,
                  limits: RunLimits | None = None):
        if self._client is None:
            raise RuntimeError("Use async with SdkRuntime before run")
        if _current.get() is not None:
            raise UnsupportedAgent("Nested runtime.run calls are unsupported")
        run_id = uuid4().hex
        directory = self.evidence_dir / run_id
        directory.mkdir(parents=True, mode=0o700)
        state = _Run(limits or RunLimits(), {"run_id": run_id, "status": "running",
            "agent": agent.name, "config": asdict(self.config),
            "versions": {n: version(n) for n in ("openai-agents", "openai", "pydantic", "mcp", "httpx2")},
            "tool_events": [], "exchanges": []}, directory)
        token = _current.set(state)
        started = time.monotonic()
        try:
            prepared = self._validate_agent(agent)
            self._secret_check({"instructions": agent.instructions, "input": task_input})
            state.receipt.update(instructions=agent.instructions, input=task_input, limits=asdict(state.limits))
            async with asyncio.timeout(state.limits.timeout):
                result = await Runner.run(prepared, task_input, context=context,
                    max_turns=state.limits.max_turns, hooks=_Hooks(),
                    run_config=RunConfig(tracing_disabled=True,
                        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1)))
            state.receipt["status"] = "succeeded"
            output = result.final_output
            state.receipt["output"] = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
            return result
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                status = "cancelled"
            elif state.failure:
                status = state.failure
            elif isinstance(exc, APITimeoutError):
                status = "transport_timeout"
            elif isinstance(exc, TimeoutError):
                status = "run_timeout"
            elif isinstance(exc, MaxTurnsExceeded):
                status = "max_turns"
            elif isinstance(exc, APIStatusError):
                status = "provider_error"
            elif isinstance(exc, ModelRefusalError):
                status = "provider_refusal"
            elif isinstance(exc, ModelBehaviorError) or isinstance(exc.__cause__, ModelBehaviorError):
                status = ("tool_arguments_error" if state.active_tools else "tool_selection_error") if state.phase == "tools" else "schema_error"
            elif state.active_tools:
                status = "tool_error"
            elif isinstance(exc, (UnsupportedAgent, UserError)):
                status = "configuration_error"
            else:
                status = "runtime_error"
            state.receipt.update(status=status, error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            state.receipt["seconds"] = time.monotonic() - started
            try:
                self._save(state)
            finally:
                _current.reset(token)
