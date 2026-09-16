from __future__ import annotations

from typing import Any

from .common import StructuredExecutorLike, WorkflowCall, structured_call


BLIND_ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "preferred_candidate": {"type": "string"},
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string"},
                    "coherence": {"type": "number", "minimum": 0, "maximum": 5},
                    "prerequisite_timing": {"type": "number", "minimum": 0, "maximum": 5},
                    "locality": {"type": "number", "minimum": 0, "maximum": 5},
                },
                "required": ["candidate", "coherence", "prerequisite_timing", "locality"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["preferred_candidate", "scores", "reason"],
    "additionalProperties": False,
}

READING_COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answers", "confidence", "evidence_refs"],
    "additionalProperties": False,
}


class SourceOrderEvaluationWorkflow:
    """API adapters only; candidate generation and metric claims stay in source-order."""

    def __init__(self, executor: StructuredExecutorLike):
        self.executor = executor

    def blind_review(
        self, *, candidates: list[dict[str, Any]], rubric: dict[str, Any]
    ) -> WorkflowCall:
        return structured_call(
            self.executor,
            prefix=(
                "Blindly compare mathematically valid presentation orders. Candidate labels carry no rank. "
                "Judge only coherence, prerequisite timing, and conceptual locality under the fixed rubric. "
                "Do not infer which algorithm produced a candidate. Return JSON."
            ),
            dynamic={"rubric": rubric, "candidates": candidates},
            schema=BLIND_ORDER_SCHEMA,
            stage="source_order.blind_review",
        )

    def reading_comparison(
        self, *, condition: dict[str, Any], questions: list[str]
    ) -> WorkflowCall:
        return structured_call(
            self.executor,
            prefix=(
                "Answer the reading-comprehension questions from one supplied presentation condition. "
                "Use only visible evidence, cite its opaque references, and return JSON."
            ),
            dynamic={"condition": condition, "questions": questions},
            schema=READING_COMPARISON_SCHEMA,
            stage="source_order.reading_comparison",
        )
