from dataclasses import replace
from pathlib import Path
import hashlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structure"))
from test_graph import P, ref, workspace

from lean_exposition.construction import SourceTextContribution
from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentStore, decl_card
from lean_exposition.exposition.texts import DeclTextError, DeclTextStore, ensure_decl_texts
from lean_exposition.models import Workspace


class FakeExecutor:
    def __init__(self, *, partial=False):
        self.calls = 0
        self.partial = partial

    def run_json(self, prompt, schema, trace_label=None):
        self.calls += 1
        import json
        data = json.loads(prompt.split("\n\nINPUT\n", 1)[1])
        rows = data["declarations"][:1] if self.partial else data["declarations"]
        return {"records": [{
            "ref": item["ref"], "summary": "Summary of " + item["name"],
            "statement_nl": None, "proof_nl": None,
        } for item in rows]}


class DeclTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = workspace("abc")
        self.path = Path(self.tmp.name) / "texts.json"

    def test_source_summary_is_exact_persistent_and_wins(self):
        store = DeclTextStore(self.ws, self.path)
        contribution = SourceTextContribution(
            ref("a"), "summary", "en", "Author summary.", P, "a" * 64)
        store.import_source((contribution,))
        self.assertEqual(store.active(ref("a"), locale="en")["summary"], "Author summary.")
        self.assertEqual(DeclTextStore(self.ws, self.path).active(ref("a"), locale="en")["summary"],
                         "Author summary.")
        with self.assertRaisesRegex(DeclTextError, "Conflicting source"):
            store.import_source((replace(contribution, text="Different."),))

    def test_generation_batches_caches_locales_and_partial_results(self):
        store = DeclTextStore(self.ws, self.path)
        fake = FakeExecutor()
        first = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en", executor=fake)
        second = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en", executor=fake)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(first["record_ids"], second["record_ids"])
        chinese = ensure_decl_texts(self.ws, store, [ref("a")], locale="zh", executor=fake)
        self.assertNotEqual(chinese["record_ids"], first["record_ids"])
        partial = FakeExecutor(partial=True)
        outcome = ensure_decl_texts(self.ws, store, [ref("c"), ref("b")], locale="zh",
                                    profile="partial", executor=partial)
        self.assertEqual(outcome["failed"], ["r\0b"])

    def test_record_identity_includes_actual_output(self):
        one = DeclTextStore(self.ws, self.path)
        a = FakeExecutor()
        first = ensure_decl_texts(self.ws, one, [ref("a")], locale="en", profile="one", executor=a)

        class Different(FakeExecutor):
            def run_json(self, prompt, schema, trace_label=None):
                value = super().run_json(prompt, schema, trace_label)
                value["records"][0]["summary"] = "A different valid summary."
                return value
        second = ensure_decl_texts(self.ws, one, [ref("a")], locale="en", profile="two",
                                   executor=Different())
        self.assertNotEqual(next(iter(first["record_ids"].values())),
                            next(iter(second["record_ids"].values())))

    def test_concurrent_identical_request_submits_once(self):
        store = DeclTextStore(self.ws, self.path)
        fake = FakeExecutor()
        results = []
        def run():
            results.append(ensure_decl_texts(self.ws, store, [ref("a")], locale="en", executor=fake))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(fake.calls, 1)
        self.assertEqual(results[0]["record_ids"], results[1]["record_ids"])

    def test_workspace_digest_is_enforced(self):
        DeclTextStore(self.ws, self.path)
        changed = replace(self.ws, declarations=tuple(reversed(self.ws.declarations)))
        with self.assertRaisesRegex(DeclTextError, "another Workspace"):
            DeclTextStore(changed, self.path)

    def test_views_are_read_only_and_manifest_pins_exact_records(self):
        fixture = demo_fixture()
        workspace_value = Workspace.from_json(__import__("json").dumps(fixture["workspace"]))
        text_store = DeclTextStore(workspace_value, Path(self.tmp.name) / "demo-texts.json")
        declaration = workspace_value.declarations[0]
        source = SourceTextContribution(
            declaration.ref, "summary", "en", "An exact catalog summary.",
            declaration.provenance, "b" * 64,
        )
        text_store.import_source((source,))
        fake = FakeExecutor()
        card = decl_card(
            workspace_value, declaration.ref, text_store=text_store, locale="en",
        )
        self.assertEqual(card["summary"], "An exact catalog summary.")
        self.assertEqual(fake.calls, 0)

        content = ContentStore(
            workspace_value, fixture["hierarchy"], Path(self.tmp.name) / "content.json",
            locale="en", decl_text_store=text_store,
        )
        job_id = content.create_writing_job(None)
        pinned = text_store.pin([declaration.ref], locale="en")
        content.state["writing_jobs"][job_id]["decl_text_records"] = pinned
        content._save()
        manifest_id = content.publish(
            {"root": fixture["blocks"]["root"]}, _complete_job=job_id,
        )
        self.assertEqual(content.manifest(manifest_id)["decl_text_records"], pinned)

        ensure_decl_texts(
            workspace_value, text_store, [declaration.ref], locale="en",
            profile="alternate", executor=fake,
        )
        self.assertEqual(content.manifest(manifest_id)["decl_text_records"], pinned)


if __name__ == "__main__":
    unittest.main()
