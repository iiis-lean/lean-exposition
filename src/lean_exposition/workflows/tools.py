from __future__ import annotations

from typing import Any, Callable, Iterable

from lean_exposition.runtime import FunctionTool

from .common import ToolExecutorLike, WorkflowCall, tool_call


class RestrictedToolWorkflow:
    """A provider-neutral, bounded API tool loop for Reader and experiments."""

    def __init__(
        self,
        executor: ToolExecutorLike,
        *,
        tools: Iterable[FunctionTool],
        handlers: dict[str, Callable[..., Any]],
        max_steps: int = 8,
    ):
        self.executor = executor
        self.tools = tuple(tools)
        self.handlers = dict(handlers)
        self.max_steps = max_steps
        names = {tool.name for tool in self.tools}
        if names != set(self.handlers):
            raise ValueError("tool schemas and handlers must have identical names")
        if max_steps < 1:
            raise ValueError("max_steps must be positive")

    def run(
        self,
        *,
        instructions: str,
        task: dict[str, Any],
        output_schema: dict[str, Any],
        stage: str = "tools.task",
    ) -> WorkflowCall:
        prefix = (
            instructions.rstrip()
            + "\nUse only the bound tools and their returned data. Do not invent results. "
            "Stop when the requested JSON is supported by tool evidence."
        )
        return tool_call(
            self.executor,
            prefix=prefix,
            dynamic=task,
            schema=output_schema,
            tools=self.tools,
            handlers=self.handlers,
            max_steps=self.max_steps,
            stage=stage,
        )


class ReaderTaskWorkflow(RestrictedToolWorkflow):
    def run_reader_task(
        self, *, task: dict[str, Any], output_schema: dict[str, Any]
    ) -> WorkflowCall:
        return self.run(
            instructions=(
                "Read a fixed mathematical exposition through the supplied Reader tools. "
                "Keep views immutable, pass expected_view for actions, and respect paging and budgets."
            ),
            task=task,
            output_schema=output_schema,
            stage="reader.task",
        )


class DownstreamTaskWorkflow(RestrictedToolWorkflow):
    def run_experiment(
        self,
        *,
        protocol: dict[str, Any],
        task: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> WorkflowCall:
        return self.run(
            instructions=(
                "Complete the bounded downstream evaluation protocol. Record conclusions only from "
                "the permitted tools; the protocol's resource limits are mandatory."
            ),
            task={"protocol": protocol, "task": task},
            output_schema=output_schema,
            stage="downstream.task",
        )
