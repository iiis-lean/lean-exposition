from dataclasses import replace
from pathlib import Path
import json
import time
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structure"))
from test_graph import P, ref, workspace

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
            "statement_nl": "A mathematical statement." if item["need_statement_nl"] else None,
            "proof_nl": "A source-supported proof." if item["need_proof_nl"] else None,
        } for item in rows]}


class DeclTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = workspace("abc")
        self.path = Path(self.tmp.name) / "texts.json"

    def test_generated_summary_persists_and_reads_across_languages(self):
        store = DeclTextStore(self.ws, self.path)
        outcome = ensure_decl_texts(self.ws, store, [ref("a")], locale="en", executor=FakeExecutor())
        reopened = DeclTextStore(self.ws, self.path)
        self.assertEqual(reopened.active(ref("a"), locale="zh")["summary"], "Summary of a")
        self.assertEqual(reopened.pinned(outcome["record_ids"]["r\0a"], ref("a"), locale="zh")["summary"],
                         "Summary of a")

    def test_generation_batches_caches_locales_and_partial_results(self):
        store = DeclTextStore(self.ws, self.path)
        fake = FakeExecutor()
        first = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en", executor=fake)
        second = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en", executor=fake)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(first["record_ids"], second["record_ids"])
        chinese = ensure_decl_texts(self.ws, store, [ref("a")], locale="zh", executor=fake)
        self.assertEqual(chinese["record_ids"]["r\0a"], first["record_ids"]["r\0a"])
        self.assertEqual(fake.calls, 1)
        partial = FakeExecutor(partial=True)
        outcome = ensure_decl_texts(self.ws, store, [ref("c"), ref("b")], locale="zh",
                                    profile="partial", executor=partial)
        self.assertEqual(outcome["failed"], ["r\0b"])

    def test_oversized_summary_input_does_not_submit_api_call(self):
        store = DeclTextStore(self.ws, self.path)
        fake = FakeExecutor()
        result = ensure_decl_texts(self.ws, store, [ref("a")], locale="en", executor=fake,
                                   max_batch_characters=1)
        self.assertEqual(fake.calls, 0)
        self.assertEqual(result["failed"], ["r\0a"])

    def test_summary_compacts_scope_but_keeps_unit_details(self):
        from lean_exposition.exposition.views import _mathematical_projection
        card = decl_card(self.ws, ref("a"), proof=True)
        card['summary'] = 'A short mathematical account.'
        view = {'node': {}, 'cards': [card], 'primary_outcomes': [],
                'incoming': [], 'outgoing': [], 'internal': []}
        node = {'kind': 'scope', 'decl_refs': [card['ref']]}
        compact = _mathematical_projection(view, node, {})['cards'][0]
        self.assertEqual(compact['summary'], card['summary'])
        self.assertNotIn('statement', compact)
        detailed = _mathematical_projection(view, dict(node, kind='unit'), {})['cards'][0]
        self.assertIn('statement', detailed)

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
        fake = FakeExecutor()
        generated = ensure_decl_texts(workspace_value, text_store, [declaration.ref], locale="en", executor=fake)
        card = decl_card(workspace_value, declaration.ref, text_store=text_store, locale="en",
                         text_record_ids=generated["record_ids"])
        self.assertTrue(card["summary"].startswith("Summary of "))
        self.assertEqual(fake.calls, 1)

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

    def test_failed_later_batch_preserves_completed_records_on_disk(self):
        class Failure(FakeExecutor):
            def run_json(self, prompt, schema, trace_label=None):
                if self.calls:
                    raise RuntimeError("synthetic provider failure")
                return super().run_json(prompt, schema, trace_label)
        store = DeclTextStore(self.ws, self.path)
        outcome = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en",
                                   executor=Failure(), batch_size=1, max_workers=1)
        self.assertEqual(outcome["failed"], ["r\0b"])
        self.assertEqual(len(DeclTextStore(self.ws, self.path).state["records"]), 1)

    def test_punctuation_only_summary_is_not_cached(self):
        class Punctuation(FakeExecutor):
            def run_json(inner, *args, **kwargs):
                value = super().run_json(*args, **kwargs)
                value['records'][0]['summary'] = ', $$$ .'
                return value
        store = DeclTextStore(self.ws, self.path)
        result = ensure_decl_texts(self.ws, store, [ref('a')], locale='en', executor=Punctuation())
        self.assertEqual(result['failed'], ['r\0a'])
        self.assertIn('punctuation-only', result['failure_reasons']['r\0a'])
        self.assertEqual(store.state['records'], {})

    def test_model_change_invalidates_generated_summary(self):
        fake = FakeExecutor()
        fake.config = {"model": "first"}
        store = DeclTextStore(self.ws, self.path)
        first = ensure_decl_texts(self.ws, store, [ref("a")], locale="en", executor=fake)
        fake.config = {"model": "second"}
        second = ensure_decl_texts(self.ws, store, [ref("a")], locale="en", executor=fake)
        self.assertEqual(fake.calls, 2)
        self.assertNotEqual(first["record_ids"], second["record_ids"])
        self.assertIsNone(store.active(ref("a"), locale="en"))

    def test_duplicate_ids_and_required_missing_text_are_not_cached(self):
        from lean_exposition.models import TextContent
        missing = replace(self.ws.declarations[0].statement,
                          nl=TextContent(None, "missing", P, reason="test"))
        ws = replace(self.ws, declarations=(replace(self.ws.declarations[0], statement=missing),
                                           *self.ws.declarations[1:]))
        class Invalid(FakeExecutor):
            duplicate = False
            def run_json(self, prompt, schema, trace_label=None):
                value = super().run_json(prompt, schema, trace_label)
                if self.duplicate:
                    value["records"].append(value["records"][0].copy())
                else:
                    value["records"][0]["statement_nl"] = None
                return value
        store, fake = DeclTextStore(ws, self.path), Invalid()
        for duplicate in (False, True):
            fake.duplicate = duplicate
            result = ensure_decl_texts(ws, store, [ref("a")], locale="en", executor=fake)
            self.assertEqual(result["failed"], ["r\0a"])
            self.assertFalse(store.state["records"])

    def test_adaptive_batches_enforce_final_request_size(self):
        from lean_exposition.runtime.api import input_characters
        class Capture(FakeExecutor):
            def __init__(self):
                super().__init__()
                self.sizes = []
            def run_json(self, prompt, schema, trace_label=None):
                self.sizes.append(input_characters(prompt, schema, self))
                return super().run_json(prompt, schema, trace_label)
        one = Capture()
        ensure_decl_texts(self.ws, DeclTextStore(self.ws, self.path), [ref("a")],
                          locale="en", executor=one)
        budget = one.sizes[0]
        store = DeclTextStore(self.ws, self.path.with_name("bounded.json"))
        fake = Capture()
        outcome = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")],
                                   locale="en", executor=fake, max_batch_characters=budget)
        self.assertFalse(outcome["failed"])
        self.assertEqual(fake.sizes, [budget, budget])
        tiny = Capture()
        result = ensure_decl_texts(self.ws, store, [ref("c")], locale="en", executor=tiny,
                                   max_batch_characters=budget - 1)
        self.assertEqual(tiny.calls, 0)
        self.assertEqual(result["failed"], ["r\0c"])

    def test_batches_overlap_without_locking_reads(self):
        barrier = threading.Barrier(2)
        store = DeclTextStore(self.ws, self.path)
        class Concurrent(FakeExecutor):
            def run_json(self, prompt, schema, trace_label=None):
                # Both model calls must run together and allow another thread to read.
                with store.lock:
                    pass
                barrier.wait(timeout=2)
                return super().run_json(prompt, schema, trace_label)
        result = ensure_decl_texts(self.ws, store, [ref("a"), ref("b")], locale="en",
                                   executor=Concurrent(), batch_size=1, max_workers=2)
        self.assertFalse(result["failed"])
        self.assertEqual(len(result["generated"]), 2)

    def test_cancel_stops_remaining_batches(self):
        stopped = threading.Event()
        class Cancels(FakeExecutor):
            def run_json(self, prompt, schema, trace_label=None):
                result = super().run_json(prompt, schema, trace_label)
                stopped.set()
                return result
        fake = Cancels()
        result = ensure_decl_texts(self.ws, DeclTextStore(self.ws, self.path), [ref("a"), ref("b")],
                                   locale="en", executor=fake, batch_size=1, max_workers=1,
                                   cancelled=stopped.is_set)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(result["failed"], ["r\0b"])


if __name__ == "__main__":
    unittest.main()
