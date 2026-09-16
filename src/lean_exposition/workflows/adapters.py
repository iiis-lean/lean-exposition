from __future__ import annotations

from typing import Any, Iterable

from lean_exposition.interfaces.schema import TOOL_SCHEMAS
from lean_exposition.runtime import FunctionTool, RuntimeFailure

from .common import StructuredExecutorLike, ToolExecutorLike
from .tools import ReaderTaskWorkflow


def content_runtime(
    executor: StructuredExecutorLike, *, trace_label: str = "content.generate"
):
    """Adapt the current API executor to ContentStore's narrow callable contract."""

    def run(prompt: str, schema: dict[str, Any]) -> Any:
        result = executor.execute(prompt, schema, trace_label=trace_label)
        if result.status != "succeeded":
            raise RuntimeFailure(result)
        return result.data

    return run


def reader_workflow(
    executor: ToolExecutorLike,
    service,
    *,
    allowed_tools: Iterable[str] | None = None,
    max_steps: int = 8,
) -> ReaderTaskWorkflow:
    """Bind a ReaderService through an explicit tool whitelist."""

    names = tuple(allowed_tools or TOOL_SCHEMAS)
    unknown = set(names) - set(TOOL_SCHEMAS)
    if unknown:
        raise ValueError("unknown Reader tools: " + ", ".join(sorted(unknown)))
    tools = [
        FunctionTool(
            name,
            "Call the fixed Reader operation " + name + ".",
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
