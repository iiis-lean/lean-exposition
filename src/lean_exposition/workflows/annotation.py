from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from collections import Counter
from typing import Any, Mapping

from .common import StructuredExecutorLike, WorkflowCall, structured_call, successful_data, checked_call, exact_ids


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
                    "needs_human_review": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["item_id", "label", "needs_human_review", "reason"],
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
        ids = [item["item_id"] for item in items]
        allowed_labels = rubric.get("labels", [])
        if (not ids or any(not isinstance(key, str) or not key.strip() for key in ids)
                or len(ids) != len(set(ids)) or not allowed_labels
                or any(not isinstance(label, str) or not label.strip() for label in allowed_labels)):
            raise ValueError("unique item IDs and a nonempty list of rubric labels are required")
        evidence_fields = {item["item_id"]: set(item) - {"item_id"} for item in items}

        def validate_labels(data):
            exact_ids(data["labels"], "item_id", ids)
            for row in data["labels"]:
                if row["label"] not in allowed_labels or not row["evidence"]:
                    raise ValueError("label and supporting evidence are required")
                if not set(row["evidence"]) <= evidence_fields[row["item_id"]]:
                    raise ValueError("unknown evidence field")

        prefix = (
            "Label the supplied local mathematical-structure examples using only the fixed rubric. "
            "Label every item exactly once. Treat each item independently, cite supplied top-level evidence field names, and return JSON. "
            "Do not infer a training target or call one model a gold standard."
        )

        def one(entry):
            name, executor = entry
            call = structured_call(
                executor,
                prefix=prefix,
                dynamic={"rubric": rubric, "items": items},
                schema=LABEL_SCHEMA,
                stage=f"features.annotate.{name}",
                max_input_characters=360000,
            )
            return name, checked_call(call, LABEL_SCHEMA, validate_labels)

        with ThreadPoolExecutor(max_workers=len(self.annotators)) as pool:
            calls = dict(pool.map(one, self.annotators.items()))
        completed = {
            name: successful_data(call)
            for name, call in calls.items()
            if call.execution.status == "succeeded"
        }
        if self.adjudicator is None or len(completed) < 2:
            return AnnotationResult(calls, None)
        agreement = {key: max(Counter(next(row["label"] for row in data["labels"]
            if row["item_id"] == key) for data in completed.values()).values()) / len(completed)
            for key in ids}
        adjudication = structured_call(
            self.adjudicator,
            prefix=(
                "Adjudicate independent feature labels under the supplied rubric. Preserve disagreements, "
                "Use the original item evidence and supplied deterministic agreement; mark uncertain cases for human review. "
                "No annotator is authoritative. Return JSON."
            ),
            dynamic={"rubric": rubric, "items": items, "annotations": completed,
                     "agreement": agreement, "agreement_definition": "largest label count / completed annotators"},
            schema=ADJUDICATION_SCHEMA,
            stage="features.adjudicate",
            max_input_characters=360000,
        )
        def validate_decisions(data):
            exact_ids(data["decisions"], "item_id", ids)
            for row in data["decisions"]:
                if row["label"] not in allowed_labels or not row["reason"].strip():
                    raise ValueError("invalid adjudicated label or reason")
        adjudication = checked_call(adjudication, ADJUDICATION_SCHEMA, validate_decisions)
        if adjudication.execution.status == "succeeded":
            data = {"decisions": [{**row, "agreement": agreement[row["item_id"]]}
                                  for row in adjudication.execution.data["decisions"]]}
            adjudication = replace(adjudication, execution=replace(adjudication.execution, data=data))
        return AnnotationResult(calls, adjudication)
