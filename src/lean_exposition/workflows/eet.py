from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from lean_exposition.exposition.writing import mathematical_draft_instructions
from lean_exposition.runtime import ApiError, ExecutionResult, prompt_digest, stable_prompt

from .common import (
    StructuredExecutorLike,
    WorkflowCall,
    cache_report,
    structured_call,
    successful_data,
)


STITCH_SCHEMA = {
    "type": "object",
    "properties": {
        "coherent": {"type": "boolean"},
        "junctions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "left_node": {"type": "string"},
                    "right_node": {"type": "string"},
                    "left_lead_out": {"type": "string"},
                    "right_lead_in": {"type": "string"},
                },
                "required": ["left_node", "right_node", "left_lead_out", "right_lead_in"],
                "additionalProperties": False,
            },
        },
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["coherent", "junctions", "issues"],
    "additionalProperties": False,
}

VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["mathematical", "continuity", "format", "unsupported"],
                    },
                    "message": {"type": "string", "maxLength": 240},
                },
                "required": ["node_id", "category", "message"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["accepted", "issues"],
    "additionalProperties": False,
}


def stitch_instructions(locale: str) -> str:
    language = "Chinese" if locale == "zh" else "English"
    return (
        f"Join an ordered sibling group of mathematical exposition in {language}. "
        "Preserve each draft's mathematical substance. Revise only the left lead_out and right "
        "lead_in at sibling junctions so the combined text is continuous, avoids repeated setup, "
        "and leads to the fixed parent ending. For a terminal draft that has no such boundary field, "
        "the supplied editable flag is false and you must return the corresponding boundary as an empty "
        "string. Never synthesize a boundary field that the draft does not have. Do not add unsupported facts or raw "
        "structural IDs. Return every adjacent junction exactly once and in order as JSON."
    )


VALIDATION_INSTRUCTIONS = (
    "Perform a brief final check of a proposed mathematical exposition sibling group. Check only "
    "the supplied hypotheses and claims, readable LaTeX, I/S/O roles, adjacent continuity, and raw IDs. "
    "Do not reconstruct any proof or rewrite prose. Return accepted=true with an empty issue list when "
    "no concrete defect is visible; otherwise report at most four concise defects in JSON."
)


@dataclass(frozen=True)
class EetDraftRequest:
    node_id: str
    locale: str
    material: dict[str, Any]
    context: dict[str, Any]
    schema: dict[str, Any]
    max_input_characters: int | None = None


@dataclass(frozen=True)
class EetGroupResult:
    drafts: tuple[WorkflowCall, ...]
    stitching: WorkflowCall | None
    validation: WorkflowCall | None
    final_drafts: tuple[tuple[str, dict[str, Any]], ...] = ()
    local_checks: dict[str, Any] | None = None
    cancelled: bool = False
    issues: tuple[str, ...] = ()
    strategy: str = "concurrent"

    @property
    def succeeded(self) -> bool:
        if self.cancelled or self.issues or not self.final_drafts:
            return False
        if any(call.execution.status != "succeeded" for call in self.drafts):
            return False
        if self.stitching is not None and (
            self.stitching.execution.status != "succeeded"
            or not self.stitching.execution.data.get("coherent")
            or bool(self.stitching.execution.data.get("issues"))
        ):
            return False
        if self.validation is None or self.validation.execution.status != "succeeded":
            return False
        return bool(
            self.validation.execution.data.get("accepted")
            and not self.validation.execution.data.get("issues")
        )

    @property
    def evidence(self) -> dict[str, Any]:
        calls = [*self.drafts]
        if self.stitching is not None:
            calls.append(self.stitching)
        if self.validation is not None:
            calls.append(self.validation)
        report = cache_report(calls)
        report.update(
            stages=[
                {
                    "stage": call.stage,
                    "status": call.execution.status,
                    "prefix_digest": call.prefix_digest,
                    "prompt_digest": call.prompt_digest,
                    "usage": {
                        "input_tokens": call.execution.usage.input_tokens,
                        "output_tokens": call.execution.usage.output_tokens,
                        "cached_tokens": call.execution.usage.cached_tokens,
                        "reasoning_tokens": call.execution.usage.reasoning_tokens,
                    },
                    "duration_seconds": call.duration_seconds,
                    "provider_status": call.execution.provider_status,
                    "finish_reason": call.execution.finish_reason,
                    "incomplete_details": deepcopy(call.execution.incomplete_details),
                    "requested_model": call.execution.requested_model,
                    "response_model": call.execution.response_model,
                    "error_kind": call.execution.error.kind if call.execution.error else None,
                }
                for call in calls
            ],
            local_checks=deepcopy(self.local_checks or {}),
            cancelled=self.cancelled,
            issues=list(self.issues),
            generation_strategy=self.strategy,
        )
        return report


class EetWorkflow:
    """Draft one sibling group with an explicit strategy and validate once."""

    def __init__(
        self,
        executor: StructuredExecutorLike,
        *,
        validator_executor: StructuredExecutorLike | None = None,
        max_workers: int = 4,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.executor = executor
        self.validator_executor = validator_executor or executor
        self.max_workers = max_workers

    def draft(self, request: EetDraftRequest) -> WorkflowCall:
        if request.locale not in {"zh", "en"}:
            raise ValueError("locale must be zh or en")
        context = deepcopy(request.context)
        writing_convention = context.pop("writing_convention", {})
        prefix = mathematical_draft_instructions(request.locale, writing_convention)
        dynamic = {
            "locale": request.locale,
            "scope_view": request.material,
            "write_context": context,
        }
        prompt = stable_prompt(prefix, dynamic)
        if request.max_input_characters is not None and len(prompt) > request.max_input_characters:
            return WorkflowCall(
                stage=f"eet.draft.{request.node_id}",
                execution=ExecutionResult(
                    "failed",
                    error=ApiError("input_budget"),
                    trace_label=f"eet.draft.{request.node_id}",
                ),
                prefix_digest=prompt_digest(prefix.rstrip()),
                prompt_digest=prompt_digest(prompt),
            )
        try:
            return structured_call(
                self.executor,
                prefix=prefix,
                dynamic=dynamic,
                schema=request.schema,
                stage=f"eet.draft.{request.node_id}",
            )
        except Exception as exc:
            # Executors normally return a failed result. Keep the same auditable
            # contract if a custom executor raises before producing one.
            return WorkflowCall(
                stage=f"eet.draft.{request.node_id}",
                execution=ExecutionResult(
                    "failed",
                    error=ApiError("executor_exception", exception_type=type(exc).__name__),
                    trace_label=f"eet.draft.{request.node_id}",
                ),
                prefix_digest="",
                prompt_digest="",
            )

    def draft_siblings(
        self,
        requests: Iterable[EetDraftRequest],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[WorkflowCall, ...]:
        requests = tuple(requests)
        if not requests:
            raise ValueError("at least one draft request is required")
        locales = {request.locale for request in requests}
        if len(locales) != 1:
            raise ValueError("a sibling group must use one locale")
        results: list[WorkflowCall | None] = [None] * len(requests)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(requests))) as pool:
            futures = {pool.submit(self.draft, request): index for index, request in enumerate(requests)}
            completed = 0
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                completed += 1
                if progress:
                    progress(completed, len(requests))
        return tuple(result for result in results if result is not None)

    @staticmethod
    def _preceding_outcome(node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "title": payload.get("title"),
            "text": payload.get(
                "lead_out", payload.get("statement", payload.get("content", ""))
            ),
        }

    def draft_siblings_sequentially(
        self,
        requests: Iterable[EetDraftRequest],
        *,
        ordered_node_ids: list[str],
        prepared: dict[str, dict[str, Any]],
        local_validator: Callable[[str, dict[str, Any]], Any] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[tuple[WorkflowCall, ...], dict[str, Any]]:
        """Draft in reading order, exposing only locally accepted outcomes."""
        by_node = {request.node_id: request for request in requests}
        outcomes = []
        results = []
        local_checks = {}
        completed = len(prepared)
        for node_id in ordered_node_ids:
            if node_id in prepared:
                outcomes.append(self._preceding_outcome(node_id, prepared[node_id]))
                continue
            request = by_node[node_id]
            context = deepcopy(request.context)
            context["preceding_sibling_outcomes"] = deepcopy(outcomes)
            call = self.draft(replace(request, context=context))
            results.append(call)
            completed += 1
            if progress:
                progress(completed, len(ordered_node_ids))
            if call.execution.status != "succeeded":
                break
            payload = successful_data(call)
            if local_validator:
                try:
                    local_checks[node_id] = local_validator(node_id, deepcopy(payload))
                except Exception as exc:
                    local_checks[node_id] = {
                        "accepted": False,
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                    }
                if not self._local_accepted(local_checks[node_id]):
                    break
            outcomes.append(self._preceding_outcome(node_id, payload))
        return tuple(results), local_checks

    def stitch(
        self,
        *,
        locale: str,
        ordered_node_ids: list[str],
        drafts: list[dict[str, Any]],
        parent_lead_in: str | None,
        parent_lead_out: str | None,
        writing_convention: dict[str, Any] | None = None,
    ) -> WorkflowCall:
        editability = self._junction_editability(ordered_node_ids, drafts)
        try:
            return structured_call(
                self.executor,
                prefix=stitch_instructions(locale),
                dynamic={
                    "ordered_node_ids": ordered_node_ids,
                    "drafts": drafts,
                    "junction_editability": editability,
                    "parent_lead_in": parent_lead_in,
                    "parent_lead_out": parent_lead_out,
                    "writing_convention": writing_convention or {},
                },
                schema=STITCH_SCHEMA,
                stage="eet.stitch",
            )
        except Exception as exc:
            return self._exception_call("eet.stitch", exc)

    def validate(
        self,
        *,
        locale: str,
        ordered_node_ids: list[str],
        drafts: list[dict[str, Any]],
        stitching: dict[str, Any],
        local_checks: dict[str, Any] | None = None,
        writing_convention: dict[str, Any] | None = None,
        strategy: str = "concurrent",
    ) -> WorkflowCall:
        try:
            return structured_call(
                self.validator_executor,
                prefix=VALIDATION_INSTRUCTIONS,
                dynamic={
                    "locale": locale,
                    "ordered_node_ids": ordered_node_ids,
                    "drafts": drafts,
                    "stitching": stitching,
                    "local_checks": local_checks or {},
                    "writing_convention": writing_convention or {},
                    "generation_strategy": strategy,
                },
                schema=VALIDATION_SCHEMA,
                stage="eet.validate",
            )
        except Exception as exc:
            return self._exception_call("eet.validate", exc)

    def generate_group(
        self,
        requests: Iterable[EetDraftRequest],
        *,
        locale: str | None = None,
        ordered_node_ids: Iterable[str] | None = None,
        prepared: dict[str, dict[str, Any]] | None = None,
        parent_lead_in: str | None = None,
        parent_lead_out: str | None = None,
        writing_convention: dict[str, Any] | None = None,
        strategy: str = "concurrent",
        local_validator: Callable[[str, dict[str, Any]], Any] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> EetGroupResult:
        requests = tuple(requests)
        prepared = deepcopy(prepared or {})
        if strategy not in {"sequential", "concurrent"}:
            raise ValueError("strategy must be sequential or concurrent")
        if locale is None:
            if not requests:
                raise ValueError("locale is required when every draft is prepared locally")
            locale = requests[0].locale
        if locale not in {"zh", "en"}:
            raise ValueError("locale must be zh or en")
        if any(request.locale != locale for request in requests):
            raise ValueError("a sibling group must use one locale")
        node_ids = list(ordered_node_ids or [request.node_id for request in requests])
        request_ids = [request.node_id for request in requests]
        if len(node_ids) != len(set(node_ids)) or set(node_ids) != set(request_ids) | set(prepared):
            raise ValueError("ordered_node_ids must exactly cover model and prepared drafts")
        if not node_ids:
            raise ValueError("at least one draft is required")
        if cancelled():
            return EetGroupResult((), None, None, cancelled=True, strategy=strategy)
        if progress:
            progress("drafting", len(prepared), len(node_ids))
        if not requests:
            drafts = ()
        elif strategy == "concurrent":
            drafts = self.draft_siblings(
                requests,
                progress=(lambda done, total: progress("drafting", len(prepared) + done, len(node_ids)))
                if progress
                else None,
            )
            early_local_checks = {}
        else:
            drafts, early_local_checks = self.draft_siblings_sequentially(
                requests,
                ordered_node_ids=node_ids,
                prepared=prepared,
                local_validator=local_validator,
                progress=(lambda done, total: progress("drafting", done, total)) if progress else None,
            )
        if not requests:
            early_local_checks = {}
        generated = {
            request.node_id: successful_data(call)
            for request, call in zip(requests, drafts)
            if call.execution.status == "succeeded"
        }
        current = {**prepared, **generated}
        ordered_current = tuple((node_id, deepcopy(current[node_id])) for node_id in node_ids if node_id in current)
        early_rejected = [
            node_id for node_id, check in early_local_checks.items()
            if not self._local_accepted(check)
        ]
        if (len(drafts) != len(requests) or any(call.execution.status != "succeeded" for call in drafts)
                or early_rejected):
            issues = (("local validation rejected: " + ", ".join(early_rejected)),) if early_rejected else ()
            return EetGroupResult(
                drafts, None, None, ordered_current, early_local_checks,
                issues=issues, strategy=strategy,
            )
        if cancelled():
            return EetGroupResult(drafts, None, None, ordered_current, cancelled=True, strategy=strategy)
        data = [deepcopy(current[node_id]) for node_id in node_ids]
        stitching = None
        editability = self._junction_editability(node_ids, data)
        if strategy == "concurrent" and any(item["left_lead_out"] or item["right_lead_in"] for item in editability):
            if progress:
                progress("stitching", len(node_ids), len(node_ids))
            stitching = self.stitch(
                locale=locale,
                ordered_node_ids=node_ids,
                drafts=data,
                parent_lead_in=parent_lead_in,
                parent_lead_out=parent_lead_out,
                writing_convention=writing_convention,
            )
            if stitching.execution.status != "succeeded":
                return EetGroupResult(drafts, stitching, None, tuple(zip(node_ids, data)), strategy=strategy)
            try:
                data = self._apply_stitching(node_ids, data, successful_data(stitching))
            except ValueError as exc:
                return EetGroupResult(
                    drafts,
                    stitching,
                    None,
                    tuple(zip(node_ids, data)),
                    issues=(str(exc),),
                    strategy=strategy,
                )
        if cancelled():
            return EetGroupResult(drafts, stitching, None, tuple(zip(node_ids, data)), cancelled=True, strategy=strategy)
        local_checks = deepcopy(early_local_checks)
        if local_validator:
            for node_id, draft in zip(node_ids, data):
                if node_id in local_checks:
                    continue
                try:
                    local_checks[node_id] = local_validator(node_id, deepcopy(draft))
                except Exception as exc:
                    local_checks[node_id] = {
                        "accepted": False,
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                    }
        rejected = [node_id for node_id, check in local_checks.items() if not self._local_accepted(check)]
        if rejected:
            return EetGroupResult(
                drafts,
                stitching,
                None,
                tuple(zip(node_ids, data)),
                local_checks,
                issues=("local validation rejected: " + ", ".join(rejected),),
                strategy=strategy,
            )
        if cancelled():
            return EetGroupResult(drafts, stitching, None, tuple(zip(node_ids, data)), local_checks, cancelled=True, strategy=strategy)
        if progress:
            progress("validating", len(node_ids), len(node_ids))
        validation = self.validate(
            locale=locale,
            ordered_node_ids=node_ids,
            drafts=data,
            stitching=successful_data(stitching) if stitching else {"coherent": True, "junctions": [], "issues": []},
            local_checks=local_checks,
            writing_convention=writing_convention,
            strategy=strategy,
        )
        return EetGroupResult(drafts, stitching, validation, tuple(zip(node_ids, data)), local_checks, strategy=strategy)

    @staticmethod
    def _local_accepted(check: Any) -> bool:
        if check is None:
            return True
        if isinstance(check, bool):
            return check
        if isinstance(check, dict):
            if "accepted" in check:
                return bool(check["accepted"])
            diagnostics = check.get("diagnostics", check)
            return not diagnostics.get("errors", []) if isinstance(diagnostics, dict) else True
        return True

    @staticmethod
    def _exception_call(stage: str, exc: Exception) -> WorkflowCall:
        return WorkflowCall(
            stage=stage,
            execution=ExecutionResult(
                "failed",
                error=ApiError("executor_exception", exception_type=type(exc).__name__),
                trace_label=stage,
            ),
            prefix_digest="",
            prompt_digest="",
        )

    @staticmethod
    def _apply_stitching(
        node_ids: list[str], drafts: list[dict[str, Any]], stitching: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not stitching.get("coherent") or stitching.get("issues"):
            raise ValueError("stitching rejected sibling coherence")
        junctions = stitching.get("junctions")
        expected = list(zip(node_ids, node_ids[1:]))
        if not isinstance(junctions, list) or len(junctions) != len(expected):
            raise ValueError("stitching must return every adjacent junction exactly once")
        result = deepcopy(drafts)
        for index, (junction, pair) in enumerate(zip(junctions, expected)):
            if (junction.get("left_node"), junction.get("right_node")) != pair:
                raise ValueError("stitching changed sibling order or addressed a nonadjacent pair")
            if "lead_out" in result[index]:
                result[index]["lead_out"] = junction["left_lead_out"]
            elif junction["left_lead_out"]:
                raise ValueError("stitching attempted to add lead_out to a terminal draft")
            if "lead_in" in result[index + 1]:
                result[index + 1]["lead_in"] = junction["right_lead_in"]
            elif junction["right_lead_in"]:
                raise ValueError("stitching attempted to add lead_in to a terminal draft")
        return result

    @staticmethod
    def _junction_editability(
        node_ids: list[str], drafts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "left_node": node_ids[index],
                "right_node": node_ids[index + 1],
                "left_lead_out": "lead_out" in drafts[index],
                "right_lead_in": "lead_in" in drafts[index + 1],
            }
            for index in range(len(node_ids) - 1)
        ]
