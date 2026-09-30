from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Callable, Iterable, Protocol

from lean_exposition.runtime import (
    ApiError,
    ExecutionResult,
    FunctionTool,
    canonical_json,
    prompt_digest,
    stable_prompt,
)
from lean_exposition.runtime.api import input_characters


class StructuredExecutorLike(Protocol):
    def execute(
        self, prompt: str, schema: dict[str, Any], *, trace_label: str | None = None
    ) -> ExecutionResult: ...


class ToolExecutorLike(Protocol):
    def execute(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        tools: Iterable[FunctionTool],
        handlers: dict[str, Callable[..., Any]],
        max_steps: int = 8,
        max_tool_calls: int = 32,
        max_input_characters: int = 360000,
        max_tool_result_characters: int = 60000,
        trace_label: str | None = None,
    ) -> ExecutionResult: ...


@dataclass(frozen=True)
class WorkflowCall:
    """One bounded model call with cache-relevant prompt evidence."""

    stage: str
    execution: ExecutionResult
    prefix_digest: str
    prompt_digest: str
    duration_seconds: float = 0.0

    @property
    def cached_tokens(self) -> int:
        return self.execution.usage.cached_tokens


def structured_call(
    executor: StructuredExecutorLike,
    *,
    prefix: str,
    dynamic: Any,
    schema: dict[str, Any],
    stage: str,
    max_input_characters: int | None = None,
) -> WorkflowCall:
    prompt = stable_prompt(prefix, dynamic)
    if max_input_characters is not None and input_characters(prompt, schema, executor) > max_input_characters:
        return WorkflowCall(stage, ExecutionResult("failed", error=ApiError("input_budget")),
                            prompt_digest(prefix.rstrip()), prompt_digest(prompt))
    started = time.monotonic()
    result = executor.execute(prompt, schema, trace_label=stage)
    duration = time.monotonic() - started
    return WorkflowCall(
        stage=stage,
        execution=result,
        prefix_digest=prompt_digest(prefix.rstrip()),
        prompt_digest=prompt_digest(prompt),
        duration_seconds=duration,
    )


def tool_call(
    executor: ToolExecutorLike,
    *,
    prefix: str,
    dynamic: Any,
    schema: dict[str, Any],
    tools: Iterable[FunctionTool],
    handlers: dict[str, Callable[..., Any]],
    max_steps: int,
    stage: str,
    limits: dict[str, int] | None = None,
) -> WorkflowCall:
    prompt = stable_prompt(prefix, dynamic)
    started = time.monotonic()
    options = {"tools": tools, "handlers": handlers, "max_steps": max_steps,
               "trace_label": stage, **(limits or {})}
    import inspect
    try:
        parameters = inspect.signature(executor.execute).parameters
        if not any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            options = {key: value for key, value in options.items() if key in parameters}
    except (TypeError, ValueError):
        pass
    result = executor.execute(prompt, schema, **options)
    duration = time.monotonic() - started
    return WorkflowCall(
        stage=stage,
        execution=result,
        prefix_digest=prompt_digest(prefix.rstrip()),
        prompt_digest=prompt_digest(prompt),
        duration_seconds=duration,
    )


def successful_data(call: WorkflowCall) -> Any:
    if call.execution.status != "succeeded":
        return None
    return call.execution.data


def cache_report(calls: Iterable[WorkflowCall]) -> dict[str, Any]:
    calls = tuple(calls)
    return {
        "calls": len(calls),
        "cached_tokens": sum(call.cached_tokens for call in calls),
        "input_tokens": sum(call.execution.usage.input_tokens for call in calls),
        "structured_output_modes": sorted(
            {
                call.execution.structured_output_mode
                for call in calls
                if call.execution.structured_output_mode is not None
            }
        ),
        "prefix_digests": sorted({call.prefix_digest for call in calls}),
        "request_digests": [call.prompt_digest for call in calls],
    }


def prompt_payload(call: WorkflowCall) -> str:
    """Small serializable call record; provider text and credentials are excluded."""

    return canonical_json(
        {
            "stage": call.stage,
            "status": call.execution.status,
            "structured_output_mode": call.execution.structured_output_mode,
            "prefix_digest": call.prefix_digest,
            "prompt_digest": call.prompt_digest,
            "cached_tokens": call.cached_tokens,
        }
    )


def checked_call(call: WorkflowCall, schema, validate) -> WorkflowCall:
    """Keep provider evidence while rejecting locally invalid task results."""
    if call.execution.status != "succeeded":
        return call
    import jsonschema
    try:
        jsonschema.validate(call.execution.data, schema)
        validate(call.execution.data)
    except (ValueError, TypeError, KeyError, jsonschema.ValidationError) as exc:
        return replace(call, execution=replace(call.execution, status="failed",
            error=ApiError("output_contract", exception_type=type(exc).__name__)))
    return call


def exact_ids(rows, field, expected):
    actual = [row[field] for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("result IDs must cover the input exactly once")
