from __future__ import annotations

from typing import Any, Iterable

from lean_exposition.interfaces.schema import TOOL_SCHEMAS
from lean_exposition.runtime import FunctionTool, RuntimeFailure

from .common import StructuredExecutorLike, ToolExecutorLike
from .tools import ReaderTaskWorkflow


class CallableExecutor:
    """Run every writing stage through an existing callable with the same schemas."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.config = getattr(runtime, "config", None)

    def execute(self, prompt, schema, *, trace_label=None):
        from lean_exposition.runtime import ExecutionResult
        return ExecutionResult("succeeded", data=self.runtime(prompt, schema), trace_label=trace_label)

    def run_json(self, prompt, schema, *, trace_label=None):
        return self.runtime(prompt, schema)


def content_runtime(
    executor: StructuredExecutorLike, *, trace_label: str = "content.generate"
):
    """Adapt the current API executor to ContentStore's narrow callable contract."""

    def run(prompt: str, schema: dict[str, Any]) -> Any:
        result = executor.execute(prompt, schema, trace_label=trace_label)
        if result.status != "succeeded":
            raise RuntimeFailure(result)
        return result.data

    run.config = getattr(executor, "config", None)
    run.executor = executor
    return run


_READER_DESCRIPTIONS = {
    "open_reader": "Open a fixed published instance. Returns reader_id and an immutable view_id; subsequent operations use these IDs.",
    "get_overview": "Read visible nodes, dependency edges and reading order. Continue with next_cursor for the same view and scope.",
    "read_text": "Read published mathematical Markdown with stable line anchors. Use view_id for historical content and cursor for complete pages.",
    "inspect": "Inspect a node, declaration, dependency or generation job. detail selects interfaces, members, NL, Lean, sources or job status. Follow next_cursor.",
    "locate": "Resolve an opaque node or declaration reference to its location and anchors in a fixed view.",
    "recommend": "Read optional next-step recommendations based on the current view and remaining reading budget.",
    "apply_action": "Expand, collapse, reset, cancel a job, set a budget or switch locale. Supply current expected_view; stale versions are rejected. Expansion may return a pending job to inspect.",
}

def reader_workflow(
    executor: ToolExecutorLike,
    service,
    *,
    allowed_tools: Iterable[str] | None = None,
    max_steps: int = 8,
) -> ReaderTaskWorkflow:
    """Bind a ReaderService through an explicit tool whitelist."""

    names = tuple(TOOL_SCHEMAS if allowed_tools is None else allowed_tools)
    unknown = set(names) - set(TOOL_SCHEMAS)
    if unknown:
        raise ValueError("unknown Reader tools: " + ", ".join(sorted(unknown)))
    tools = [
        FunctionTool(
            name,
            _READER_DESCRIPTIONS[name],
            TOOL_SCHEMAS[name],
        )
        for name in names
    ]
    handlers = {
        name: (lambda _name=name, **arguments: service.call(_name, arguments))
        for name in names
    }
    return ReaderTaskWorkflow(
        executor,
        tools=tools,
        handlers=handlers,
        max_steps=max_steps,
    )
