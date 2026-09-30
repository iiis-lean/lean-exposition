import json
from dataclasses import replace
from pathlib import Path
import unittest

import test_writing as fixtures
from test_writing import query_fields
from test_concurrent_publish import EetExecutor
from test_texts import FakeExecutor
from lean_exposition.exposition import ContentError
from lean_exposition.exposition.texts import ensure_decl_texts
from lean_exposition.exposition.views import mathematical_source_text
from lean_exposition.reading import ReaderService


class RemediationTests(unittest.TestCase):
    setUp = fixtures.WritingTests.setUp
    make = fixtures.WritingTests.make

    def test_context_query_restores_whole_large_context(self):
        self.fixture['blocks']['root']['lead_in'] = 'Context paragraph.\n' * 1200
        self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        step = self.store.get_step(job)
        self.assertTrue(step['context']['context_query_required'])
        fields = query_fields(self.store, job, 'path', node_id=step['node_id'], limit=2048)
        self.assertTrue(any('writing_convention' in path for path in fields))
        self.assertTrue(any('allowed_node_anchors' in path for path in fields))
        self.assertIn(self.fixture['blocks']['root']['lead_in'], fields.values())
        self.assertNotIn('text', self.store.query_job(job, 'path'))

    def test_large_context_requires_complete_pages_before_agent_submission(self):
        self.fixture['blocks']['root']['lead_in'] = 'Context paragraph.\n' * 1200
        self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        self.store._job(job)['agent_review_required'] = True
        query_fields(self.store, job, 'scope')
        with self.assertRaisesRegex(ContentError, 'required path query pages'):
            self.store.submit_draft(job, self.fixture['blocks']['setup'])
        query_fields(self.store, job, 'path', limit=2048)
        self.store.submit_draft(job, self.fixture['blocks']['setup'])

    def test_agent_query_and_independent_review_gate_publication(self):
        self.store.model_executor = EetExecutor(self.fixture['blocks'], reject_validation=True)
        job = self.store.create_writing_job()
        self.store.prepare_writing_job(job)
        self.store._job(job)['agent_review_required'] = True
        with self.assertRaisesRegex(ContentError, 'source query'):
            self.store.submit_draft(job, self.fixture['blocks']['root'])
        query_fields(self.store, job, 'scope')
        draft = self.store.submit_draft(job, self.fixture['blocks']['root'])
        with self.assertRaisesRegex(ContentError, 'source review'):
            self.store.accept_draft(job, draft['draft_id'])
        self.assertIsNone(self.store.state['latest_manifest'])
        self.assertEqual(self.store._job(job)['draft_id'], draft['draft_id'])

    def test_historical_reader_uses_no_future_generated_text(self):
        # Remove NL so the distinction between raw and future generated text is observable.
        decl = self.workspace.declarations[0]
        missing = replace(decl.statement.nl, text=None, status='missing', reason='Fixture missing NL')
        ws = replace(self.workspace, declarations=(replace(decl, statement=replace(decl.statement, nl=missing)), *self.workspace.declarations[1:]))
        from test_writing import bind_current_hierarchy
        from lean_exposition.exposition import ContentStore
        store = ContentStore(ws, bind_current_hierarchy(ws, self.fixture['hierarchy']), Path(self.tmp.name) / 'history', locale='en')
        first = store.publish({'root': self.fixture['blocks']['root']})
        service = ReaderService([store], Path(self.tmp.name) / 'reader')
        self.addCleanup(service.close)
        opened = service.call('open_reader', {'instance_id': store.instance_id})
        args = {'reader_id': opened['reader_id'], 'view_id': opened['view_id'], 'ref': {'repo_key': decl.ref.repo_key, 'local_id': decl.ref.local_id}, 'detail': 'nl'}
        before = service.call('inspect', args)
        ensure_decl_texts(ws, store.decl_text_store, [decl.ref], locale='en', executor=FakeExecutor())
        self.assertEqual(service.call('inspect', args), before)
        store.publish({'setup': self.fixture['blocks']['setup']})
        current = service.call('open_reader', {'instance_id': store.instance_id})
        after = service.call('inspect', {**args, 'reader_id': current['reader_id'], 'view_id': current['view_id']})
        self.assertTrue(any(row.get('text') == 'A mathematical statement.' for row in after['items']))
        self.assertEqual(service.call('inspect', args), before)
        self.assertEqual(store.manifest(first).get('decl_text_records'), {})

    def test_published_pins_survive_new_summary_and_next_job_preparation(self):
        executor = EetExecutor(self.fixture['blocks'])
        self.store.model_executor = executor
        root = self.store.create_writing_job()
        old = self.store.prepare_writing_job(root)
        draft = self.store.submit_draft(root, self.fixture['blocks']['root'])
        self.store.accept_draft(root, draft['draft_id'])
        ensure_decl_texts(self.workspace, self.store.decl_text_store, [d.ref for d in self.workspace.declarations],
                          locale='en', executor=FakeExecutor())
        child = self.store.create_writing_job('root')
        pins = self.store.prepare_writing_job(child)
        self.assertEqual({key: pins[key] for key in old}, old)

    def test_same_source_cache_is_shared_by_locale_and_survives_reopen(self):
        en = self.make('en')
        self.assertIs(en.decl_text_store, self.store.decl_text_store)
        fake = FakeExecutor()
        refs = [d.ref for d in self.workspace.declarations]
        first = ensure_decl_texts(self.workspace, self.store.decl_text_store, refs, locale='zh', executor=fake)
        before = fake.calls
        second = ensure_decl_texts(self.workspace, en.decl_text_store, refs, locale='en', executor=fake)
        self.assertEqual(fake.calls, before)
        self.assertEqual(first['record_ids'], second['record_ids'])
        reopened = self.make('en')
        self.assertEqual(reopened.decl_text_store.path, en.decl_text_store.path)

    def test_publication_rejects_empty_terminal_and_missing_title(self):
        payload = {**self.fixture['blocks']['bound'], 'title': 'Bound'}
        for key in ('statement', 'proof', 'title'):
            with self.subTest(key=key), self.assertRaises(ContentError):
                self.store.validate_submission('bound', {**payload, key: ' '})
        with self.assertRaises(ContentError):
            self.store.validate_submission('bound', self.fixture['blocks']['bound'])

    def test_pi_writing_refused_before_summary_calls(self):
        from lean_exposition.exposition.agent import run_agent_job
        from lean_exposition.runtime.agents import PiAgentConfig, PiAgentExecutor
        job = self.store.create_writing_job()
        config = PiAgentConfig(model='deepseek-flash', provider='deepseek', cwd=self.tmp.name,
                               session_dir=self.tmp.name)
        with self.assertRaisesRegex(ValueError, 'no bound writing MCP'):
            run_agent_job(self.store, job, lambda _: PiAgentExecutor(config))
        self.assertEqual(self.store.decl_text_store.state['records'], {})

    def test_lc_wrapper_cleanup_keeps_scope_context(self):
        text = ('import Mathlib\n-- lean-constellation: declaration-source-begin\n'
                'namespace N\nvariable (n : Nat)\n'
                '/--\n# lean-constellation target x\nDuplicated description\n-/\n'
                'theorem x : n = n := rfl\nend N\n')
        cleaned = mathematical_source_text(text)
        self.assertIn('namespace N', cleaned)
        self.assertIn('variable (n : Nat)', cleaned)
        self.assertIn('theorem x', cleaned)
        self.assertNotIn('Duplicated description', cleaned)
        native = 'import Mathlib\nnamespace N\nvariable (n : Nat)\n'
        self.assertEqual(mathematical_source_text(native), native)
