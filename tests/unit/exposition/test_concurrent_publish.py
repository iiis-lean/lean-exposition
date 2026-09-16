from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentError, ContentStore
from lean_exposition.exposition.writing import PublicationControl
from lean_exposition.models import Workspace
from lean_exposition.runtime import ApiUsage, ExecutionResult


class EetExecutor:
    def __init__(self, blocks, *, reject_validation=False, raise_stage=None):
        self.blocks = blocks
        self.reject_validation = reject_validation
        self.raise_stage = raise_stage
        self.calls = []
        self.active = 0
        self.overlap = threading.Event()
        self.lock = threading.Lock()
        self.before_validation = None

    def execute(self, prompt, schema, *, trace_label=None):
        if trace_label == self.raise_stage:
            raise RuntimeError("secret provider response")
        with self.lock:
            self.calls.append(trace_label)
        if trace_label.startswith("eet.draft."):
            with self.lock:
                self.active += 1
                if self.active == 2:
                    self.overlap.set()
            self.overlap.wait(1)
            time.sleep(0.01)
            node_id = trace_label.removeprefix("eet.draft.")
            with self.lock:
                self.active -= 1
            return ExecutionResult("succeeded", data=deepcopy(self.blocks[node_id]),
                                   usage=ApiUsage(input_tokens=10, cached_tokens=3))
        if trace_label == "eet.stitch":
            value = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
            left, right = value["ordered_node_ids"]
            return ExecutionResult("succeeded", data={"coherent": True, "junctions": [{
                "left_node": left, "right_node": right,
                "left_lead_out": "The setup now hands its conclusion to the next argument.",
                "right_lead_in": "Using that conclusion, we turn to the final comparison.",
            }], "issues": []}, usage=ApiUsage(input_tokens=8, cached_tokens=2))
        if trace_label == "eet.validate":
            if self.before_validation:
                callback, self.before_validation = self.before_validation, None
                callback()
            accepted = not self.reject_validation
            return ExecutionResult("succeeded", data={"accepted": accepted, "issues": [] if accepted else [{
                "node_id": "setup", "category": "continuity", "message": "Controlled rejection."
            }]}, usage=ApiUsage(input_tokens=6, cached_tokens=1))
        raise AssertionError(trace_label)


class ConcurrentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = demo_fixture()
        self.fixture["blocks"]["setup"]["lead_in"] = "Fix a natural number and define its successor."
        self.fixture["blocks"]["conclusion"]["lead_out"] = "The successor is strictly larger."
        self.workspace = Workspace.from_json(json.dumps(self.fixture["workspace"]))

    def make(self, executor):
        store = ContentStore(
            self.workspace,
            self.fixture["hierarchy"],
            Path(self.temporary.name) / ("content-" + str(id(executor)) + ".json"),
            executor=executor,
            locale="en",
        )
        base = store.publish({"root": self.fixture["blocks"]["root"]})
        return store, base

    def test_siblings_overlap_stitch_is_applied_and_group_publishes_once(self):
        executor = EetExecutor(self.fixture["blocks"])
        store, base = self.make(executor)
        before_manifests = len(store.state["manifests"])
        result = store.generate_children("root")

        self.assertTrue(executor.overlap.is_set())
        self.assertNotEqual(result, base)
        self.assertEqual(len(store.state["manifests"]), before_manifests + 1)
        blocks = store.manifest()["blocks"]
        self.assertEqual(blocks["setup"]["lead_out"], "The setup now hands its conclusion to the next argument.")
        self.assertEqual(blocks["conclusion"]["lead_in"], "Using that conclusion, we turn to the final comparison.")
        self.assertEqual(executor.calls, ["eet.draft.setup", "eet.draft.conclusion", "eet.stitch", "eet.validate"])
        evidence = store.manifest()["review_evidence"]["eet_workflow"]
        self.assertEqual(evidence["calls"], 4)
        self.assertEqual(evidence["cached_tokens"], 9)
        count = len(executor.calls)
        self.assertEqual(store.generate_children("root"), result)
        self.assertEqual(len(executor.calls), count)

    def test_root_uses_single_draft_and_validation_without_stitch(self):
        executor = EetExecutor(self.fixture["blocks"])
        store = ContentStore(
            self.workspace,
            self.fixture["hierarchy"],
            Path(self.temporary.name) / "root.json",
            executor=executor,
            locale="en",
        )
        manifest_id = store.generate_root()
        self.assertEqual(store.state["latest_manifest"], manifest_id)
        self.assertEqual(executor.calls, ["eet.draft.root", "eet.validate"])
        self.assertEqual(set(store.manifest()["blocks"]), {"root"})
        self.assertEqual(store.manifest()["review_evidence"]["eet_workflow"]["calls"], 2)

    def test_validation_failure_retains_drafts_and_evidence_without_publish(self):
        executor = EetExecutor(self.fixture["blocks"], reject_validation=True)
        store, base = self.make(executor)
        with self.assertRaisesRegex(ContentError, "retained without publication"):
            store.generate_children("root")
        self.assertEqual(store.state["latest_manifest"], base)
        job = next(iter(store.state["writing_jobs"].values()))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(set(job["final_drafts"]), {"setup", "conclusion"})
        self.assertFalse(job["validation"]["accepted"])
        self.assertEqual(job["workflow_evidence"]["calls"], 4)

    def test_local_validation_failure_skips_model_validation_and_publication(self):
        invalid = deepcopy(self.fixture["blocks"])
        invalid["setup"]["lead_in"] = ""
        executor = EetExecutor(invalid)
        store, base = self.make(executor)
        with self.assertRaisesRegex(ContentError, "retained without publication"):
            store.generate_children("root")
        self.assertEqual(store.state["latest_manifest"], base)
        self.assertNotIn("eet.validate", executor.calls)
        job = next(iter(store.state["writing_jobs"].values()))
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job["workflow_evidence"]["local_checks"]["setup"]["accepted"])

    def test_manifest_race_fails_cas_and_keeps_generated_group_unpublished(self):
        executor = EetExecutor(self.fixture["blocks"])
        store, base = self.make(executor)
        executor.before_validation = lambda: store.publish({"definition": self.fixture["blocks"]["definition"]})
        with self.assertRaisesRegex(ContentError, "base manifest changed"):
            store.generate_children("root")
        self.assertNotEqual(store.state["latest_manifest"], base)
        self.assertNotIn("setup", store.manifest()["blocks"])
        job = next(iter(store.state["writing_jobs"].values()))
        self.assertEqual(job["failure"]["kind"], "cas_failed")
        self.assertEqual(set(job["final_drafts"]), {"setup", "conclusion"})

    def test_cancel_wins_at_commit_boundary_and_prevents_publication(self):
        executor = EetExecutor(self.fixture["blocks"])
        store, base = self.make(executor)
        control = PublicationControl()
        reached_commit = threading.Event()
        release_commit = threading.Event()
        original_commit = control.commit

        def delayed_commit(operation):
            reached_commit.set()
            release_commit.wait(3)
            return original_commit(operation)

        control.commit = delayed_commit
        failures = []
        worker = threading.Thread(target=lambda: self._capture(
            failures,
            lambda: store.generate_children(
                "root", cancelled=control.is_cancelled, publication_control=control
            ),
        ))
        worker.start()
        self.assertTrue(reached_commit.wait(2))
        self.assertTrue(control.cancel())
        release_commit.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ContentError)
        self.assertEqual(store.state["latest_manifest"], base)
        self.assertNotIn("setup", store.manifest()["blocks"])
        job = next(iter(store.state["writing_jobs"].values()))
        self.assertEqual(job["status"], "cancelled")

    def test_stage_exception_keeps_group_drafts_and_safe_evidence(self):
        for stage in ("eet.stitch", "eet.validate"):
            with self.subTest(stage=stage):
                executor = EetExecutor(self.fixture["blocks"], raise_stage=stage)
                store, base = self.make(executor)
                with self.assertRaisesRegex(ContentError, "retained without publication"):
                    store.generate_children("root")
                self.assertEqual(store.state["latest_manifest"], base)
                job = next(iter(store.state["writing_jobs"].values()))
                self.assertEqual(set(job["final_drafts"]), {"setup", "conclusion"})
                self.assertEqual(job["workflow_evidence"]["stages"][-1]["error_kind"], "executor_exception")
                self.assertNotIn("secret provider response", json.dumps(job["workflow_evidence"]))

    @staticmethod
    def _capture(failures, operation):
        try:
            operation()
        except Exception as exc:
            failures.append(exc)


if __name__ == "__main__":
    unittest.main()
