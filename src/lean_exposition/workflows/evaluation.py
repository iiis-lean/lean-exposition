from __future__ import annotations

from typing import Any

from .common import StructuredExecutorLike, WorkflowCall, structured_call, checked_call, exact_ids


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
        ids = [item["candidate"] for item in candidates]
        if not ids or any(not isinstance(key, str) or not key.strip() for key in ids) or len(ids) != len(set(ids)):
            raise ValueError("unique candidate IDs are required")
        call = structured_call(
            self.executor,
            prefix=(
                "Blindly compare mathematically valid presentation orders. Candidate labels carry no rank. "
                "Judge only coherence, prerequisite timing, and conceptual locality under the fixed rubric. "
                "Do not infer which algorithm produced a candidate. Return JSON."
            ),
            dynamic={"rubric": rubric, "candidates": candidates},
            schema=BLIND_ORDER_SCHEMA,
            stage="source_order.blind_review",
            max_input_characters=360000,
        )
        def validate(data):
            exact_ids(data["scores"], "candidate", ids)
            if data["preferred_candidate"] not in ids or not data["reason"].strip():
                raise ValueError("invalid preference or reason")
        return checked_call(call, BLIND_ORDER_SCHEMA, validate)

    def reading_comparison(
        self, *, condition: dict[str, Any], questions: list[str]
    ) -> WorkflowCall:
        if not questions or any(not isinstance(q, str) or not q.strip() for q in questions):
            raise ValueError("nonempty questions are required")
        refs = condition.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError("condition evidence_refs must be a list of opaque strings")
        call = structured_call(
            self.executor,
            prefix=(
                "Answer the reading-comprehension questions from one supplied presentation condition. "
                "Use only visible evidence, cite its opaque references, and return JSON."
            ),
            dynamic={"condition": condition, "questions": questions},
            schema=READING_COMPARISON_SCHEMA,
            stage="source_order.reading_comparison",
            max_input_characters=360000,
        )
        def validate(data):
            if len(data["answers"]) != len(questions) or any(not answer.strip() for answer in data["answers"]):
                raise ValueError("one nonempty answer is required per question, in input order")
            if not set(data["evidence_refs"]) <= set(refs) or (refs and not data["evidence_refs"]):
                raise ValueError("cite known condition evidence references")
        return checked_call(call, READING_COMPARISON_SCHEMA, validate)
