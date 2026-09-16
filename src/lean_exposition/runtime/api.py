from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .config import ApiConfig
from .service import Runtime
from .types import ApiError, ApiUsage, ExecutionResult, JobHandle, RuntimeFailure, ToolEvent


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_prompt(prefix: str, dynamic: Any) -> str:
    """Keep a reusable prefix byte-stable and place changing JSON last."""

    return f"{prefix.rstrip()}\n\nINPUT\n{canonical_json(dynamic)}"


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def request_digest(prompt: str, schema: dict[str, Any], config: ApiConfig) -> str:
    payload = {
        "prompt": prompt,
        "schema": schema,
        "model": config.model,
        "base_url": config.base_url,
        "protocol": config.protocol,
        "max_output_tokens": config.max_output_tokens,
        "reasoning": config.reasoning,
        "extra_body": config.extra_body,
        "prompt_cache_key": config.prompt_cache_key,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FunctionTool:
    name: str
    description: str
    parameters: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name is required")
        _check_schema(self.parameters)
        object.__setattr__(self, "parameters", copy.deepcopy(self.parameters))


class _Failure(Exception):
    def __init__(self, kind: str, *, provider_code: str | None = None):
        self.kind = kind
        self.provider_code = provider_code


@dataclass
class _Trace:
    raw_text: str | None = None
    provider_status: str | None = None
    finish_reason: str | None = None
    incomplete_details: dict[str, Any] | None = None
    response_model: str | None = None
    response_id: str | None = None
    provider_error_code: str | None = None
    trace_label: str | None = None
    input_digest: str | None = None
    usage_reports: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[ToolEvent] = field(default_factory=list)


class StructuredExecutor:
    def __init__(self, config: ApiConfig, *, client_factory=None, runtime: Runtime | None = None):
        self.config = config
        self._client_factory = client_factory
        self._runtime = runtime or Runtime()

    def execute(
        self, prompt: str, schema: dict[str, Any], *, trace_label: str | None = None
    ) -> ExecutionResult:
        return self._execute(
            prompt, schema, trace_label, threading.Event(), lambda stop: True
        )

    def run_json(
        self, prompt: str, schema: dict[str, Any], *, trace_label: str | None = None
    ) -> Any:
        result = self.execute(prompt, schema, trace_label=trace_label)
        if result.status != "succeeded":
            raise RuntimeFailure(result)
        return result.data

    def start(
        self, prompt: str, schema: dict[str, Any], *, trace_label: str | None = None
    ) -> JobHandle:
        schema = copy.deepcopy(schema)
        return self._runtime.submit(
            lambda cancelled, register: self._execute(
                prompt, schema, trace_label, cancelled, register
            ),
            timeout=self.config.timeout,
        )

    def status(self, handle: JobHandle) -> ExecutionResult:
        return self._runtime.status(handle)

    def result(self, handle: JobHandle, timeout: float | None = None) -> ExecutionResult:
        return self._runtime.result(handle, timeout)

    def cancel(self, handle: JobHandle) -> bool:
        return self._runtime.cancel(handle)

    def _execute(self, prompt, schema, trace_label, cancelled, register_stop) -> ExecutionResult:
        trace = _Trace(
            trace_label=trace_label,
            input_digest=request_digest(prompt, schema, self.config),
        )
        try:
            _check_schema(schema)
            with _client(self.config, self._client_factory) as client:
                if not register_stop(client.close) or cancelled.is_set():
                    raise _Failure("cancelled")
                response = _create_response(client, self.config, prompt, schema)
                _capture_response(response, self.config.protocol, trace)
                _require_complete(trace)
                data = _parse_and_validate(trace.raw_text, schema)
                return _result("succeeded", self.config, trace, data=data)
        except Exception as exc:
            return _error_result(exc, self.config, trace, cancelled)


class ApiToolExecutor(StructuredExecutor):
    def execute(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        tools: Iterable[FunctionTool],
        handlers: dict[str, Callable[..., Any]],
        max_steps: int = 8,
        trace_label: str | None = None,
    ) -> ExecutionResult:
        return self._execute_tools(
            prompt,
            schema,
            tuple(tools),
            handlers,
            max_steps,
            trace_label,
            threading.Event(),
            lambda stop: True,
        )

    def run_json(self, prompt: str, schema: dict[str, Any], **options) -> Any:
        result = self.execute(prompt, schema, **options)
        if result.status != "succeeded":
            raise RuntimeFailure(result)
        return result.data

    def start(self, prompt: str, schema: dict[str, Any], **options) -> JobHandle:
        tools = tuple(options.pop("tools"))
        handlers = dict(options.pop("handlers"))
        max_steps = options.pop("max_steps", 8)
        trace_label = options.pop("trace_label", None)
        schema = copy.deepcopy(schema)
        tools = copy.deepcopy(tools)
        if options:
            raise TypeError(f"unknown options: {', '.join(sorted(options))}")
        return self._runtime.submit(
            lambda cancelled, register: self._execute_tools(
                prompt,
                schema,
                tools,
                handlers,
                max_steps,
                trace_label,
                cancelled,
                register,
            ),
            timeout=self.config.timeout,
        )

    def _execute_tools(
        self,
        prompt,
        schema,
        tools,
        handlers,
        max_steps,
        trace_label,
        cancelled,
        register_stop,
    ):
        trace = _Trace(
            trace_label=trace_label,
            input_digest=request_digest(prompt, schema, self.config),
        )
        try:
            _check_schema(schema)
            ordered_tools = _validate_tools(tools, handlers, max_steps)
            history: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            with _client(self.config, self._client_factory) as client:
                if not register_stop(client.close) or cancelled.is_set():
                    raise _Failure("cancelled")
                for step in range(1, max_steps + 1):
                    if cancelled.is_set():
                        raise _Failure("cancelled")
                    response = _create_response(
                        client, self.config, history, schema, tools=ordered_tools
                    )
                    _capture_response(response, self.config.protocol, trace)
                    calls = _tool_calls(response, self.config.protocol)
                    if not calls:
                        _require_complete(trace)
                        data = _parse_and_validate(trace.raw_text, schema)
                        return _result("succeeded", self.config, trace, data=data)
                    if step == max_steps:
                        raise _Failure("tool_step_limit")
                    _append_assistant_history(history, response, self.config.protocol)
                    for call in calls:
                        if cancelled.is_set():
                            raise _Failure("cancelled")
                        call_id, name, encoded_arguments = _call_parts(call, self.config.protocol)
                        tool = next((item for item in ordered_tools if item.name == name), None)
                        if tool is None or name not in handlers:
                            raise _Failure("unknown_tool")
                        try:
                            arguments = json.loads(encoded_arguments)
                        except (json.JSONDecodeError, TypeError):
                            raise _Failure("invalid_tool_json") from None
                        try:
                            import jsonschema

                            jsonschema.validate(arguments, tool.parameters)
                        except jsonschema.ValidationError:
                            raise _Failure("invalid_tool_arguments") from None
                        try:
                            tool_result = handlers[name](**arguments)
                        except Exception as exc:
                            raise _Failure("tool_handler_error", provider_code=type(exc).__name__) from None
                        trace.tool_events.append(
                            ToolEvent(step, call_id, name, arguments, tool_result)
                        )
                        _append_tool_result(history, self.config.protocol, call_id, tool_result)
                raise _Failure("tool_step_limit")
        except Exception as exc:
            return _error_result(exc, self.config, trace, cancelled)


def _client(config: ApiConfig, factory):
    from openai import OpenAI
    import httpx

    key = os.environ.get(config.credential_env)
    if not key:
        raise _Failure("missing_credential")
    http_client = httpx.Client(
        follow_redirects=False,
        trust_env=False,
        proxy=config.proxy_url,
    )
    client_factory = factory or OpenAI
    try:
        return client_factory(
            api_key=key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=0,
            http_client=http_client,
        )
    except Exception:
        http_client.close()
        raise


def _create_response(client, config, prompt_or_history, schema, *, tools=()):
    cache = {"prompt_cache_key": config.prompt_cache_key} if config.prompt_cache_key else {}
    if config.protocol == "responses":
        kwargs: dict[str, Any] = {
            "model": config.model,
            "input": prompt_or_history,
            "max_output_tokens": config.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "result",
                    "strict": True,
                    "schema": schema,
                }
            },
            **cache,
        }
        if config.reasoning is not None:
            kwargs["reasoning"] = config.reasoning
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": True,
                }
                for tool in tools
            ]
        if config.extra_body:
            kwargs["extra_body"] = config.extra_body
        return client.responses.create(**kwargs)
    messages = (
        prompt_or_history
        if isinstance(prompt_or_history, list)
        else [{"role": "user", "content": prompt_or_history}]
    )
    kwargs = {
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "result",
                "strict": True,
                "schema": schema,
            },
        },
        **cache,
    }
    if config.reasoning is not None:
        kwargs["reasoning_effort"] = config.reasoning["effort"]
    if tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": True,
                },
            }
            for tool in tools
        ]
    if config.extra_body:
        kwargs["extra_body"] = config.extra_body
    return client.chat.completions.create(**kwargs)


def _capture_response(response, protocol: str, trace: _Trace) -> None:
    trace.response_model = _get(response, "model")
    trace.response_id = _get(response, "id")
    usage = _dump(_get(response, "usage"))
    if usage:
        trace.usage_reports.append(usage)
    if protocol == "responses":
        trace.raw_text = _get(response, "output_text")
        trace.provider_status = _get(response, "status")
        trace.incomplete_details = _dump(_get(response, "incomplete_details")) or None
        trace.provider_error_code = _get(_get(response, "error"), "code")
        return
    choice = (_get(response, "choices") or [None])[0]
    message = _get(choice, "message")
    trace.raw_text = _get(message, "content")
    trace.finish_reason = _get(choice, "finish_reason")
    trace.provider_status = (
        "incomplete" if trace.finish_reason in {"length", "content_filter"} else "completed"
    )


def _require_complete(trace: _Trace) -> None:
    reason = (trace.incomplete_details or {}).get("reason")
    if trace.finish_reason == "length" or reason in {"max_output_tokens", "length"}:
        raise _Failure("length")
    if trace.provider_status == "failed":
        raise _Failure("provider_failed", provider_code=trace.provider_error_code)
    if trace.provider_status == "incomplete" or trace.finish_reason == "content_filter":
        raise _Failure("incomplete", provider_code=reason or trace.finish_reason)


def _parse_and_validate(raw_text: str | None, schema: dict[str, Any]) -> Any:
    if raw_text is None or not raw_text.strip():
        raise _Failure("empty_output")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        stripped = raw_text.lstrip()
        try:
            _, end = decoder.raw_decode(stripped)
        except json.JSONDecodeError:
            raise _Failure("invalid_json") from None
        if stripped[end:].strip():
            raise _Failure("trailing_output") from None
        raise _Failure("invalid_json") from None
    try:
        import jsonschema

        jsonschema.validate(data, schema)
    except jsonschema.ValidationError:
        raise _Failure("schema_validation") from None
    return data


def _check_schema(schema: dict[str, Any]) -> None:
    import jsonschema

    jsonschema.Draft202012Validator.check_schema(schema)


def _validate_tools(tools, handlers, max_steps):
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    ordered = tuple(sorted(tools, key=lambda tool: tool.name))
    names = [tool.name for tool in ordered]
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    missing = set(names) - set(handlers)
    if missing:
        raise ValueError(f"missing handlers: {', '.join(sorted(missing))}")
    return ordered


def _tool_calls(response, protocol):
    if protocol == "responses":
        return [
            item
            for item in (_get(response, "output") or [])
            if _get(item, "type") == "function_call"
        ]
    choice = (_get(response, "choices") or [None])[0]
    return _get(_get(choice, "message"), "tool_calls") or []


def _append_assistant_history(history, response, protocol):
    if protocol == "responses":
        history.extend(_dump(item) for item in (_get(response, "output") or []))
    else:
        choice = (_get(response, "choices") or [None])[0]
        history.append(_dump(_get(choice, "message")))


def _call_parts(call, protocol):
    function = call if protocol == "responses" else _get(call, "function")
    return (
        _get(call, "call_id") if protocol == "responses" else _get(call, "id"),
        _get(function, "name"),
        _get(function, "arguments"),
    )


def _append_tool_result(history, protocol, call_id, result):
    encoded = canonical_json(result)
    if protocol == "responses":
        history.append({"type": "function_call_output", "call_id": call_id, "output": encoded})
    else:
        history.append({"role": "tool", "tool_call_id": call_id, "content": encoded})


def _usage(reports: list[dict[str, Any]]) -> ApiUsage:
    input_tokens = output_tokens = total_tokens = cached_tokens = reasoning_tokens = 0
    for report in reports:
        input_tokens += int(report.get("input_tokens", report.get("prompt_tokens", 0)) or 0)
        output_tokens += int(report.get("output_tokens", report.get("completion_tokens", 0)) or 0)
        total_tokens += int(report.get("total_tokens", 0) or 0)
        input_details = report.get(
            "input_tokens_details", report.get("prompt_tokens_details", {})
        ) or {}
        output_details = report.get(
            "output_tokens_details", report.get("completion_tokens_details", {})
        ) or {}
        cached_tokens += int(input_details.get("cached_tokens", 0) or 0)
        reasoning_tokens += int(output_details.get("reasoning_tokens", 0) or 0)
    return ApiUsage(
        input_tokens,
        output_tokens,
        total_tokens,
        cached_tokens,
        reasoning_tokens,
        tuple(reports),
    )


def _result(status, config, trace, *, data=None, error=None):
    return ExecutionResult(
        status=status,
        data=data,
        raw_text=trace.raw_text,
        error=error,
        provider_status=trace.provider_status,
        finish_reason=trace.finish_reason,
        incomplete_details=trace.incomplete_details,
        usage=_usage(trace.usage_reports),
        tool_events=tuple(trace.tool_events),
        requested_model=config.model,
        response_model=trace.response_model,
        protocol=config.protocol,
        response_id=trace.response_id,
        trace_label=trace.trace_label,
        input_digest=trace.input_digest,
    )


def _error_result(exc, config, trace, cancelled):
    if isinstance(exc, _Failure):
        kind = exc.kind
        provider_code = exc.provider_code
    else:
        kind = "provider_error"
        provider_code = _get(_get(exc, "body"), "code") or _get(exc, "code")
    if cancelled.is_set() and kind in {"provider_error", "cancelled"}:
        kind = "cancelled"
    status = "cancelled" if kind == "cancelled" else "failed"
    error = ApiError(
        kind=kind,
        exception_type=None if isinstance(exc, _Failure) else type(exc).__name__,
        status_code=getattr(exc, "status_code", None),
        provider_code=str(provider_code) if provider_code is not None else None,
    )
    return _result(status, config, trace, error=error)


def _get(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _dump(value) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not callable(item)}
    return {}
