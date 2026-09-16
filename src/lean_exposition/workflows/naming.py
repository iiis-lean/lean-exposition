from __future__ import annotations

from typing import Any

from .common import StructuredExecutorLike, WorkflowCall, structured_call


NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "short_description": {"type": "string", "minLength": 1},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "short_description", "evidence_refs"],
    "additionalProperties": False,
}


def _prefix(locale: str) -> str:
    language = "Chinese" if locale == "zh" else "English"
    return (
        f"Name a mathematical Region or scope in concise {language}. "
        "Describe its mathematical role rather than its Lean implementation. "
        "Do not expose node IDs, source paths, tactics, or interface jargon. "
        "Use only supplied evidence and return the requested JSON."
    )


class NamingWorkflow:
    def __init__(self, executor: StructuredExecutorLike, *, locale: str):
        if locale not in {"zh", "en"}:
            raise ValueError("locale must be zh or en")
        self.executor = executor
        self.locale = locale

    def name(self, *, kind: str, material: dict[str, Any]) -> WorkflowCall:
        if kind not in {"region", "scope"}:
            raise ValueError("kind must be region or scope")
        return structured_call(
            self.executor,
            prefix=_prefix(self.locale),
            dynamic={"locale": self.locale, "kind": kind, "material": material},
            schema=NAME_SCHEMA,
            stage=f"naming.{kind}",
        )
