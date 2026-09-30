from __future__ import annotations

from typing import Any

from .common import StructuredExecutorLike, WorkflowCall, structured_call, checked_call


# Use the same structured evidence targets as content publication.
from lean_exposition.exposition.content import TARGET_SCHEMA

NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "short_description": {"type": "string", "minLength": 1},
        "evidence_refs": {"type": "array", "items": TARGET_SCHEMA},
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
        "Name only objects and results delivered inside this node. Outgoing consumers are future uses, "
        "not outcomes of this scope. Use only supplied evidence and return the requested JSON."
    )


class NamingWorkflow:
    def __init__(self, executor: StructuredExecutorLike, *, locale: str):
        if locale not in {"zh", "en"}:
            raise ValueError("locale must be zh or en")
        self.executor = executor
        self.locale = locale

    def name(self, *, kind: str, material: dict[str, Any], max_input_characters=360000) -> WorkflowCall:
        if kind not in {"region", "scope"}:
            raise ValueError("kind must be region or scope")
        call = structured_call(
            self.executor,
            prefix=_prefix(self.locale),
            dynamic={"locale": self.locale, "kind": kind, "material": material},
            schema=NAME_SCHEMA,
            stage=f"naming.{kind}",
            max_input_characters=max_input_characters,
        )
        def validate(data):
            import re
            from lean_exposition.exposition.writing import prose_diagnostics
            if any(not data[key].strip() for key in ("title", "short_description")):
                raise ValueError("nonempty metadata is required")
            if prose_diagnostics(data, self.locale)["errors"]:
                raise ValueError("metadata contains internal identifiers or control characters")
            if self.locale == "zh" and not re.search(r"[\u3400-\u9fff]", data["short_description"]):
                raise ValueError("Chinese description required")
            if self.locale == "en" and re.search(r"[\u3400-\u9fff]", data["short_description"]):
                raise ValueError("English description required")
        return checked_call(call, NAME_SCHEMA, validate)
