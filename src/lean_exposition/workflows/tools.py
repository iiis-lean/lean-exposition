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
        max_tool_calls: int = 32,
        max_input_characters: int = 360000,
        max_tool_result_characters: int = 60000,
    ):
        self.executor = executor
        self.tools = tuple(tools)
        self.handlers = dict(handlers)
        self.max_steps = max_steps
        self.limits = {"max_tool_calls": max_tool_calls, "max_input_characters": max_input_characters,
                       "max_tool_result_characters": max_tool_result_characters}
        for key, value in self.limits.items():
            if type(value) is not int or value < (0 if key == "max_tool_calls" else 1):
                raise ValueError("invalid tool resource limit: " + key)
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
        limits: dict[str, int] | None = None,
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
            limits={key: min(value, (limits or {}).get(key, value)) for key, value in self.limits.items()},
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
        limits = dict(protocol)
        if "tool_calls" in limits:
            if "max_tool_calls" in limits:
                raise ValueError("specify only max_tool_calls")
            limits["max_tool_calls"] = limits.pop("tool_calls")
        unknown = set(limits) - set(self.limits)
        if unknown:
            raise ValueError("unsupported protocol limits: " + ", ".join(sorted(unknown)))
        for key, value in limits.items():
            if type(value) is not int or value < (0 if key == "max_tool_calls" else 1):
                raise ValueError("invalid protocol limit: " + key)
        return self.run(
            instructions=(
                "Complete the bounded downstream evaluation protocol. Record conclusions only from "
                "the permitted tools; the protocol's resource limits are mandatory."
            ),
            task={"protocol": protocol, "task": task},
            output_schema=output_schema,
            stage="downstream.task",
            limits=limits,
        )
