import json
import threading
import time
import unittest

from lean_exposition.runtime import ApiError, ApiUsage, ExecutionResult, FunctionTool
from lean_exposition.workflows import (
    DownstreamTaskWorkflow,
    EetDraftRequest,
    EetWorkflow,
    FeatureAnnotationWorkflow,
    NamingWorkflow,
    ReaderTaskWorkflow,
    SourceOrderEvaluationWorkflow,
    content_runtime,
    reader_workflow,
)


class FakeStructured:
    def __init__(self, factory=None, *, fail_stage=None):
        self.factory = factory or (lambda prompt, schema, label: _example(schema))
        self.fail_stage = fail_stage
        self.calls = []
        self.lock = threading.Lock()

    def execute(self, prompt, schema, *, trace_label=None):
        with self.lock:
            self.calls.append((prompt, schema, trace_label))
        if self.fail_stage and trace_label.startswith(self.fail_stage):
            return ExecutionResult(
                "failed", error=ApiError("fixture_failure"), trace_label=trace_label
            )
        return ExecutionResult(
            "succeeded",
            data=self.factory(prompt, schema, trace_label),
            usage=ApiUsage(input_tokens=100, cached_tokens=40),
            trace_label=trace_label,
        )


class FakeTools:
    def __init__(self):
        self.calls = []

    def execute(
        self,
        prompt,
        schema,
        *,
        tools,
        handlers,
        max_steps=8,
        trace_label=None,
    ):
        self.calls.append((prompt, tuple(tools), max_steps, trace_label))
        tool = tuple(tools)[0]
        result = handlers[tool.name](query="finite sets")
        return ExecutionResult(
            "succeeded",
            data={"answer": result["answer"]},
            usage=ApiUsage(input_tokens=50, cached_tokens=7),
            trace_label=trace_label,
        )


def _example(schema):
    properties = schema.get("properties", {})
    if set(properties) == {"title", "short_description", "evidence_refs"}:
        return {"title": "Finite sets", "short_description": "Counts a union.", "evidence_refs": []}
    if "junctions" in properties:
        return {"coherent": True, "junctions": [], "issues": []}
    if "accepted" in properties:
        return {"accepted": True, "issues": []}
    if "labels" in properties:
        return {"labels": [{"item_id": "x", "label": "high", "confidence": 0.8, "evidence": ["e"]}]}
    if "decisions" in properties:
        return {"decisions": [{"item_id": "x", "label": "high", "agreement": 1, "needs_human_review": False, "reason": "agrees"}]}
    if "preferred_candidate" in properties:
        return {"preferred_candidate": "b", "scores": [], "reason": "local"}
    if "answers" in properties:
        return {"answers": ["yes"], "confidence": 0.9, "evidence_refs": ["r"]}
    return {key: "text" for key in schema.get("required", [])}


SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "lead_in": {"type": "string"},
        "synopsis": {"type": "string"},
        "lead_out": {"type": "string"},
        "anchors": {"type": "array"},
    },
    "required": ["lead_in", "synopsis", "lead_out", "anchors"],
    "additionalProperties": False,
}

TERMINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
        "anchors": {"type": "array"},
    },
    "required": ["title", "content", "anchors"],
    "additionalProperties": False,
}


class WorkflowTests(unittest.TestCase):
    def test_naming_has_stable_prefix_and_cache_evidence(self):
        executor = FakeStructured()
        workflow = NamingWorkflow(executor, locale="en")
        first = workflow.name(kind="region", material={"id": "UNIQUE_DYNAMIC_MARKER"})
        second = workflow.name(kind="region", material={"id": "b"})
        self.assertEqual(first.prefix_digest, second.prefix_digest)
        self.assertNotEqual(first.prompt_digest, second.prompt_digest)
        self.assertEqual(first.cached_tokens, 40)
        payload = json.loads(executor.calls[0][0].split("\n\nINPUT\n", 1)[1])
        self.assertEqual(payload["material"]["id"], "UNIQUE_DYNAMIC_MARKER")
        self.assertNotIn("UNIQUE_DYNAMIC_MARKER", executor.calls[0][0].split("\n\nINPUT\n", 1)[0])

    def test_eet_siblings_are_concurrent_then_stitched_and_validated(self):
        active = 0
        overlap = threading.Event()
        lock = threading.Lock()

        def factory(prompt, schema, label):
            nonlocal active
            if label.startswith("eet.draft"):
                with lock:
                    active += 1
                    if active == 2:
                        overlap.set()
                overlap.wait(1)
                time.sleep(0.01)
                with lock:
                    active -= 1
                node = label.rsplit(".", 1)[1]
                return {"lead_in": f"I {node}", "synopsis": f"S {node}", "lead_out": f"O {node}", "anchors": []}
            if label == "eet.stitch":
                payload = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
                left, right = payload["ordered_node_ids"]
                return {"coherent": True, "junctions": [{"left_node": left, "right_node": right,
                    "left_lead_out": "joined left", "right_lead_in": "joined right"}], "issues": []}
            return _example(schema)

        executor = FakeStructured(factory)
        workflow = EetWorkflow(executor, max_workers=2)
        requests = [
            EetDraftRequest(node, "en", {"node": node}, {"index": index}, SECTION_SCHEMA)
            for index, node in enumerate(("a", "b"))
        ]
        result = workflow.generate_group(requests, parent_lead_in="P I", parent_lead_out="P O")
        self.assertTrue(overlap.is_set())
        self.assertTrue(result.succeeded)
        self.assertEqual([call.stage for call in result.drafts], ["eet.draft.a", "eet.draft.b"])
        self.assertEqual(result.drafts[0].prefix_digest, result.drafts[1].prefix_digest)
        self.assertEqual(result.evidence["calls"], 4)
        self.assertEqual(result.evidence["cached_tokens"], 160)
        self.assertEqual(dict(result.final_drafts)["a"]["lead_out"], "joined left")
        self.assertEqual(dict(result.final_drafts)["b"]["lead_in"], "joined right")

    def test_eet_single_item_skips_stitch_but_runs_local_and_model_validation(self):
        executor = FakeStructured(lambda prompt, schema, label: (
            {"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []}
            if label.startswith("eet.draft") else _example(schema)
        ))
        checks = []
        result = EetWorkflow(executor).generate_group(
            [EetDraftRequest("only", "en", {}, {}, SECTION_SCHEMA)],
            local_validator=lambda node, draft: checks.append((node, draft)) or {"accepted": True},
        )
        self.assertTrue(result.succeeded)
        self.assertIsNone(result.stitching)
        self.assertEqual([call[2] for call in executor.calls], ["eet.draft.only", "eet.validate"])
        self.assertEqual(checks[0][0], "only")

    def test_eet_terminal_siblings_skip_stitch_but_run_validation(self):
        def factory(prompt, schema, label):
            if label.startswith("eet.draft"):
                node = label.rsplit(".", 1)[1]
                return {"title": node.title(), "content": f"Content {node}", "anchors": []}
            if label == "eet.stitch":
                self.fail("a terminal-only group must not call stitching")
            return _example(schema)

        executor = FakeStructured(factory)
        checks = []
        result = EetWorkflow(executor).generate_group(
            [EetDraftRequest(node, "en", {}, {}, TERMINAL_SCHEMA) for node in ("a", "b")],
            local_validator=lambda node, draft: checks.append((node, draft)) or {"accepted": True},
        )

        self.assertTrue(result.succeeded)
        self.assertIsNone(result.stitching)
        self.assertEqual(
            [call[2] for call in executor.calls],
            ["eet.draft.a", "eet.draft.b", "eet.validate"],
        )
        self.assertEqual([node for node, _draft in checks], ["a", "b"])
        self.assertEqual(dict(result.final_drafts)["a"]["content"], "Content a")

    def test_eet_mixed_group_stitches_only_editable_boundaries(self):
        def factory(prompt, schema, label):
            if label == "eet.draft.left":
                return {"lead_in": "left I", "synopsis": "left S", "lead_out": "old left O", "anchors": []}
            if label == "eet.draft.middle":
                return {"title": "Middle", "content": "middle content", "anchors": []}
            if label == "eet.draft.right":
                return {"lead_in": "old right I", "synopsis": "right S", "lead_out": "right O", "anchors": []}
            if label == "eet.stitch":
                payload = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
                self.assertEqual(payload["junction_editability"], [
                    {"left_node": "left", "right_node": "middle", "left_lead_out": True, "right_lead_in": False},
                    {"left_node": "middle", "right_node": "right", "left_lead_out": False, "right_lead_in": True},
                ])
                return {"coherent": True, "junctions": [
                    {"left_node": "left", "right_node": "middle",
                     "left_lead_out": "new left O", "right_lead_in": ""},
                    {"left_node": "middle", "right_node": "right",
                     "left_lead_out": "", "right_lead_in": "new right I"},
                ], "issues": []}
            return _example(schema)

        executor = FakeStructured(factory)
        result = EetWorkflow(executor).generate_group([
            EetDraftRequest("left", "en", {}, {}, SECTION_SCHEMA),
            EetDraftRequest("middle", "en", {}, {}, TERMINAL_SCHEMA),
            EetDraftRequest("right", "en", {}, {}, SECTION_SCHEMA),
        ])

        self.assertTrue(result.succeeded)
        final = dict(result.final_drafts)
        self.assertEqual(final["left"]["lead_out"], "new left O")
        self.assertEqual(final["middle"], {"title": "Middle", "content": "middle content", "anchors": []})
        self.assertEqual(final["right"]["lead_in"], "new right I")
        self.assertEqual([call[2] for call in executor.calls][-2:], ["eet.stitch", "eet.validate"])

    def test_eet_mixed_group_rejects_noneditable_boundary_content(self):
        def factory(prompt, schema, label):
            if label == "eet.draft.left":
                return {"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []}
            if label == "eet.draft.terminal":
                return {"title": "Terminal", "content": "content", "anchors": []}
            if label == "eet.stitch":
                return {"coherent": True, "junctions": [{
                    "left_node": "left", "right_node": "terminal",
                    "left_lead_out": "new O", "right_lead_in": "forbidden",
                }], "issues": []}
            return _example(schema)

        executor = FakeStructured(factory)
        result = EetWorkflow(executor).generate_group([
            EetDraftRequest("left", "en", {}, {}, SECTION_SCHEMA),
            EetDraftRequest("terminal", "en", {}, {}, TERMINAL_SCHEMA),
        ])

        self.assertFalse(result.succeeded)
        self.assertIn("terminal draft", result.issues[0])
        self.assertNotIn("eet.validate", [call[2] for call in executor.calls])

    def test_eet_rejects_nonadjacent_or_missing_junction_without_validation(self):
        def factory(prompt, schema, label):
            if label.startswith("eet.draft"):
                return {"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []}
            if label == "eet.stitch":
                return {"coherent": True, "junctions": [], "issues": []}
            return _example(schema)
        executor = FakeStructured(factory)
        result = EetWorkflow(executor).generate_group([
            EetDraftRequest(node, "en", {}, {}, SECTION_SCHEMA) for node in ("a", "b")
        ])
        self.assertFalse(result.succeeded)
        self.assertIn("every adjacent junction", result.issues[0])
        self.assertEqual([call[2] for call in executor.calls][-1], "eet.stitch")

    def test_eet_never_accepts_nonempty_stitch_or_validation_issues(self):
        def factory(prompt, schema, label):
            if label.startswith("eet.draft"):
                return {"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []}
            if label == "eet.stitch":
                value = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
                left, right = value["ordered_node_ids"]
                return {"coherent": True, "junctions": [{"left_node": left, "right_node": right,
                    "left_lead_out": "O", "right_lead_in": "I"}], "issues": ["controlled issue"]}
            return {"accepted": True, "issues": [{"node_id": "a", "category": "continuity", "message": "controlled"}]}
        stitch_result = EetWorkflow(FakeStructured(factory)).generate_group([
            EetDraftRequest(node, "en", {}, {}, SECTION_SCHEMA) for node in ("a", "b")
        ])
        self.assertFalse(stitch_result.succeeded)
        self.assertIn("stitching rejected", stitch_result.issues[0])

        validation_executor = FakeStructured(lambda prompt, schema, label: (
            {"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []}
            if label.startswith("eet.draft") else
            {"accepted": True, "issues": [{"node_id": "only", "category": "continuity", "message": "controlled"}]}
        ))
        validation_result = EetWorkflow(validation_executor).generate_group([
            EetDraftRequest("only", "en", {}, {}, SECTION_SCHEMA)
        ])
        self.assertFalse(validation_result.succeeded)

    def test_eet_stage_exception_retains_drafts_and_safe_evidence(self):
        class RaisingExecutor:
            def __init__(self, stage):
                self.stage = stage

            def execute(self, prompt, schema, *, trace_label=None):
                if trace_label == self.stage:
                    raise RuntimeError("secret provider text")
                if trace_label.startswith("eet.draft"):
                    return ExecutionResult("succeeded", data={"lead_in": "I", "synopsis": "S", "lead_out": "O", "anchors": []})
                if trace_label == "eet.stitch":
                    value = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
                    left, right = value["ordered_node_ids"]
                    return ExecutionResult("succeeded", data={"coherent": True, "junctions": [{
                        "left_node": left, "right_node": right, "left_lead_out": "O", "right_lead_in": "I"
                    }], "issues": []})
                return ExecutionResult("succeeded", data={"accepted": True, "issues": []})

        for stage in ("eet.stitch", "eet.validate"):
            with self.subTest(stage=stage):
                requests = [EetDraftRequest(node, "en", {}, {}, SECTION_SCHEMA) for node in ("a", "b")]
                result = EetWorkflow(RaisingExecutor(stage)).generate_group(requests)
                self.assertFalse(result.succeeded)
                self.assertEqual(set(dict(result.final_drafts)), {"a", "b"})
                self.assertEqual(result.evidence["stages"][-1]["error_kind"], "executor_exception")
                self.assertNotIn("secret provider text", json.dumps(result.evidence))

    def test_eet_failure_stops_before_stitching_without_repair(self):
        executor = FakeStructured(fail_stage="eet.draft.bad")
        workflow = EetWorkflow(executor)
        requests = [EetDraftRequest("bad", "en", {}, {}, SECTION_SCHEMA)]
        result = workflow.generate_group(requests)
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.stitching)
        self.assertEqual([call[2] for call in executor.calls], ["eet.draft.bad"])

    def test_reader_and_downstream_share_bounded_explicit_tool_loop(self):
        executor = FakeTools()
        tool = FunctionTool(
            "search",
            "Search fixed evidence.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        handler = {"search": lambda query: {"answer": query}}
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        reader = ReaderTaskWorkflow(executor, tools=[tool], handlers=handler, max_steps=3)
        downstream = DownstreamTaskWorkflow(executor, tools=[tool], handlers=handler, max_steps=2)
        self.assertEqual(reader.run_reader_task(task={"question": "q"}, output_schema=schema).execution.data["answer"], "finite sets")
        downstream.run_experiment(protocol={"tool_calls": 2}, task={"question": "q"}, output_schema=schema)
        self.assertEqual([(call[2], call[3]) for call in executor.calls], [(3, "reader.task"), (2, "downstream.task")])

    def test_feature_annotation_runs_models_and_adjudicates_completed_results(self):
        one, two, judge = FakeStructured(), FakeStructured(), FakeStructured()
        result = FeatureAnnotationWorkflow({"one": one, "two": two}, adjudicator=judge).annotate(
            rubric={"labels": ["high", "low"]}, items=[{"item_id": "x", "evidence": "e"}]
        )
        self.assertEqual(set(result.annotations), {"one", "two"})
        self.assertEqual(
            result.annotations["one"].prefix_digest,
            result.annotations["two"].prefix_digest,
        )
        self.assertEqual(result.adjudication.execution.data["decisions"][0]["label"], "high")

    def test_source_order_exposes_blind_and_reading_adapters(self):
        executor = FakeStructured()
        workflow = SourceOrderEvaluationWorkflow(executor)
        blind = workflow.blind_review(candidates=[{"candidate": "a"}], rubric={})
        reading = workflow.reading_comparison(condition={"text": "x"}, questions=["q"])
        self.assertEqual(blind.execution.data["preferred_candidate"], "b")
        self.assertEqual(reading.execution.data["answers"], ["yes"])
        self.assertEqual([call[2] for call in executor.calls], ["source_order.blind_review", "source_order.reading_comparison"])

    def test_content_and_reader_adapters_preserve_narrow_existing_contracts(self):
        content = content_runtime(FakeStructured(lambda *_: {"value": "fixed"}))
        self.assertEqual(content("prompt", {"type": "object"}), {"value": "fixed"})

        class Service:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments):
                self.calls.append((name, arguments))
                return {"answer": arguments.get("ref", "none")}

        service = Service()
        executor = FakeTools()
        bound = reader_workflow(executor, service, allowed_tools=["inspect"], max_steps=2)
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        # The fake executor supplies its own fixture arguments, so use the adapter handler directly.
        self.assertEqual(bound.handlers["inspect"](reader_id="r", ref="x")["answer"], "x")
        self.assertEqual(service.calls, [("inspect", {"reader_id": "r", "ref": "x"})])


if __name__ == "__main__":
    unittest.main()
