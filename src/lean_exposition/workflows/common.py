from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Iterable, Protocol

from lean_exposition.runtime import (
    ExecutionResult,
    FunctionTool,
    canonical_json,
    prompt_digest,
    stable_prompt,
)


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
) -> WorkflowCall:
    prompt = stable_prompt(prefix, dynamic)
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
) -> WorkflowCall:
    prompt = stable_prompt(prefix, dynamic)
    started = time.monotonic()
    result = executor.execute(
        prompt,
        schema,
        tools=tools,
        handlers=handlers,
        max_steps=max_steps,
        trace_label=stage,
    )
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
        "prefix_digests": sorted({call.prefix_digest for call in calls}),
        "request_digests": [call.prompt_digest for call in calls],
    }


def prompt_payload(call: WorkflowCall) -> str:
    """Small serializable call record; provider text and credentials are excluded."""

    return canonical_json(
        {
            "stage": call.stage,
            "status": call.execution.status,
            "prefix_digest": call.prefix_digest,
            "prompt_digest": call.prompt_digest,
            "cached_tokens": call.cached_tokens,
        }
    )
