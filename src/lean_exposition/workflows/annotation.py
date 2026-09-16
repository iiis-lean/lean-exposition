from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Mapping

from .common import StructuredExecutorLike, WorkflowCall, structured_call, successful_data


LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "label": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item_id", "label", "confidence", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "label": {"type": "string"},
                    "agreement": {"type": "number", "minimum": 0, "maximum": 1},
                    "needs_human_review": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["item_id", "label", "agreement", "needs_human_review", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AnnotationResult:
    annotations: Mapping[str, WorkflowCall]
    adjudication: WorkflowCall | None


class FeatureAnnotationWorkflow:
    def __init__(
        self,
        annotators: Mapping[str, StructuredExecutorLike],
        *,
        adjudicator: StructuredExecutorLike | None = None,
    ):
        if not annotators:
            raise ValueError("at least one annotator is required")
        self.annotators = dict(annotators)
        self.adjudicator = adjudicator

    def annotate(
        self,
        *,
        rubric: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> AnnotationResult:
        prefix = (
            "Label the supplied local mathematical-structure examples using only the fixed rubric. "
            "Treat each item independently, cite supplied evidence fields, and return JSON. "
            "Do not infer a training target or call one model a gold standard."
        )

        def one(entry):
            name, executor = entry
            return name, structured_call(
                executor,
                prefix=prefix,
                dynamic={"rubric": rubric, "items": items},
                schema=LABEL_SCHEMA,
                stage=f"features.annotate.{name}",
            )

        with ThreadPoolExecutor(max_workers=len(self.annotators)) as pool:
            calls = dict(pool.map(one, self.annotators.items()))
        completed = {
            name: successful_data(call)
            for name, call in calls.items()
            if call.execution.status == "succeeded"
        }
        if self.adjudicator is None or len(completed) < 2:
            return AnnotationResult(calls, None)
        adjudication = structured_call(
            self.adjudicator,
            prefix=(
                "Adjudicate independent feature labels under the supplied rubric. Preserve disagreements, "
                "calculate agreement from the submitted labels, and mark uncertain cases for human review. "
                "No annotator is authoritative. Return JSON."
            ),
            dynamic={"rubric": rubric, "annotations": completed},
            schema=ADJUDICATION_SCHEMA,
            stage="features.adjudicate",
        )
        return AnnotationResult(calls, adjudication)
