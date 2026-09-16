import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentStore
from lean_exposition.models import Workspace
from lean_exposition.reading import ReaderService
from lean_exposition.runtime import ExecutionResult


class StagedEetExecutor:
    def __init__(self, blocks):
        self.blocks = blocks
        self.draft_count = 0
        self.lock = threading.Lock()
        self.drafting = threading.Event()
        self.release_drafts = threading.Event()
        self.stitching = threading.Event()
        self.release_stitch = threading.Event()
        self.validating = threading.Event()
        self.release_validation = threading.Event()

    def execute(self, prompt, schema, *, trace_label=None):
        if trace_label.startswith('eet.draft.'):
            with self.lock:
                self.draft_count += 1
                if self.draft_count == 2:
                    self.drafting.set()
            self.release_drafts.wait(3)
            node_id = trace_label.removeprefix('eet.draft.')
            payload = dict(self.blocks[node_id])
            if {'lead_in', 'synopsis', 'lead_out'} <= payload.keys():
                payload['lead_in'] = payload['lead_in'] or f'Begin the {node_id} section.'
                payload['lead_out'] = payload['lead_out'] or f'This completes the {node_id} section.'
            if 'title' in schema.get('required', ()):
                payload['title'] = node_id.title()
            return ExecutionResult('succeeded', data=payload)
        if trace_label == 'eet.stitch':
            self.stitching.set()
            self.release_stitch.wait(3)
            value = json.loads(prompt.split('\n\nINPUT\n', 1)[1])
            left, right = value['ordered_node_ids']
            drafts = dict(zip(value['ordered_node_ids'], value['drafts']))
            return ExecutionResult('succeeded', data={'coherent': True, 'junctions': [{
                'left_node': left, 'right_node': right,
                'left_lead_out': drafts[left]['lead_out'],
                'right_lead_in': drafts[right]['lead_in'],
            }], 'issues': []})
        if trace_label == 'eet.validate':
            self.validating.set()
            self.release_validation.wait(3)
            return ExecutionResult('succeeded', data={'accepted': True, 'issues': []})
        raise AssertionError(trace_label)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = demo_fixture()
        self.store = ContentStore(Workspace.from_json(json.dumps(self.fixture['workspace'])), self.fixture['hierarchy'], Path(self.tmp.name) / 'content.json')
        self.store.publish(self.fixture['blocks'])
        self.service = ReaderService([self.store], Path(self.tmp.name) / 'reader.json')
        self.addCleanup(self.service.close)
        self.opened = self.service.call('open_reader', {'instance_id': self.store.instance_id})
        self.reader = self.opened['reader_id']

    def call(self, tool, **args):
        return self.service.call(tool, {'reader_id': self.reader, **args})

    def current(self):
        return self.service.state['readers'][self.reader]['current_view']

    def action(self, action, target):
        result = self.call('apply_action', expected_view=self.current(), action=action, target=target)
        self.assertTrue(result['ok'], result)
        return result

    def wait_job(self, job_id):
        deadline = time.monotonic() + 5
        active = {'queued', 'drafting', 'stitching', 'validating'}
        while time.monotonic() < deadline:
            result = self.call('inspect', ref=job_id, detail='job')
            if result['job']['status'] not in active:
                return result['job']
            time.sleep(.01)
        self.fail('job did not finish')

    def prepare_async(self):
        # A second fixed instance has only top-level child content initially.
        store = ContentStore(Workspace.from_json(json.dumps(self.fixture['workspace'])), self.fixture['hierarchy'], Path(self.tmp.name) / 'async.json')
        store.publish({id: self.fixture['blocks'][id] for id in ('root', 'setup', 'conclusion')})
        self.service.stores[store.instance_id] = store
        self.store = store
        # Existing reader's immutable full manifest belongs to the first store;
        # open a fresh reader against the partial-content store.
        self.reader = self.service.call('open_reader', {'instance_id': store.instance_id})['reader_id']
        self.action('expand', 'root')
        return store

    def prepare_staged_eet(self):
        executor = StagedEetExecutor(self.fixture['blocks'])
        workspace = Workspace.from_json(json.dumps(self.fixture['workspace']))
        path = Path(self.tmp.name) / ('staged-' + str(id(executor)) + '.json')
        store = ContentStore(workspace, self.fixture['hierarchy'], path, executor=executor, locale='en')
        # Leave the two section children unpublished so this fixture exercises
        # the observable stitching stage. Terminal-only sibling groups
        # intentionally skip stitching because they have no editable boundary.
        initial = {'root': dict(self.fixture['blocks']['root'])}
        store.publish(initial)
        self.service.stores[store.instance_id] = store
        self.store = store
        self.reader = self.service.call('open_reader', {'instance_id': store.instance_id})['reader_id']
        return store, executor

    def test_api_job_reports_frozen_pipeline_stages_and_progress(self):
        _, executor = self.prepare_staged_eet()
        result = self.action('expand', 'root')
        job_id = result['job']['job_id']
        self.assertEqual(result['job']['status'], 'queued')
        self.assertEqual(result['job']['total_children'], 2)
        self.assertTrue(executor.drafting.wait(2))
        drafting = self.call('inspect', ref=job_id, detail='job')['job']
        self.assertEqual((drafting['status'], drafting['completed_children']), ('drafting', 0))
        executor.release_drafts.set()
        self.assertTrue(executor.stitching.wait(2))
        stitching = self.call('inspect', ref=job_id, detail='job')['job']
        self.assertEqual((stitching['status'], stitching['completed_children']), ('stitching', 2))
        executor.release_stitch.set()
        self.assertTrue(executor.validating.wait(2))
        validating = self.call('inspect', ref=job_id, detail='job')['job']
        self.assertEqual((validating['status'], validating['completed_children']), ('validating', 2))
        executor.release_validation.set()
        published = self.wait_job(job_id)
        self.assertEqual((published['status'], published['completed_children']), ('published', 2))
        self.assertTrue(published['applied'])

    def test_api_job_cancel_during_parallel_drafts_never_publishes_or_applies(self):
        store, executor = self.prepare_staged_eet()
        base = store.state['latest_manifest']
        view = self.current()
        result = self.action('expand', 'root')
        job_id = result['job']['job_id']
        self.assertTrue(executor.drafting.wait(2))
        cancelled = self.action('cancel', job_id)['job']
        self.assertEqual(cancelled['status'], 'cancelled')
        executor.release_drafts.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and store.state['writing_jobs']:
            writing = next(iter(store.state['writing_jobs'].values()))
            if writing.get('pipeline_status') == 'cancelled':
                break
            time.sleep(.01)
        self.assertEqual(store.state['latest_manifest'], base)
        self.assertEqual(self.current(), view)
        self.assertNotIn('setup', store.manifest()['blocks'])

    def test_collapse_clears_descendants_preserves_old_views_and_stale_write(self):
        root_view = self.current()
        initial = self.call('read_text')['text']
        self.action('expand', 'root')
        direct_children = self.call('read_text')['text']
        self.action('expand', 'setup')
        detailed_view = self.current()
        detailed = self.call('read_text')['text']
        self.action('collapse', 'root')
        self.assertEqual(initial, self.call('read_text')['text'])
        self.action('expand', 'root')
        self.assertEqual(direct_children, self.call('read_text')['text'])
        self.assertEqual(['root'], self.service.state['views'][self.current()]['expanded'])
        self.assertEqual(detailed, self.call('read_text', view_id=detailed_view)['text'])
        self.assertEqual(initial, self.call('read_text', view_id=root_view)['text'])
        stale = self.call('apply_action', expected_view=root_view, action='expand', target='root')
        self.assertEqual(stale['error']['code'], 'stale_view')

    def test_frontier_and_real_projected_edges_keep_outline_ancestors(self):
        self.action('expand', 'root')
        self.action('expand', 'setup')
        overview = self.call('get_overview')
        nodes = {node['id']: node for node in overview['nodes']}
        self.assertFalse(nodes['root']['is_frontier'])
        self.assertFalse(nodes['setup']['is_frontier'])
        self.assertTrue(nodes['definition']['is_frontier'])
        frontier = {id for id, node in nodes.items() if node['is_frontier']}
        for edge in overview['edges']:
            self.assertIn(edge['provider_node'], frontier)
            self.assertIn(edge['consumer_node'], frontier)
            self.assertGreater(edge['evidence_count'], 0)
        old_view = self.current()
        self.action('collapse', 'setup')
        self.assertEqual(['root'], self.service.state['views'][self.current()]['expanded'])
        self.assertIn('setup', self.service.state['views'][old_view]['expanded'])

    def test_hidden_terminal_idempotence_and_random_legal_candidates(self):
        hidden = self.call('apply_action', expected_view=self.current(), action='expand', target='setup')
        self.assertEqual(hidden['error']['code'], 'not_found')
        self.assertEqual(self.call('locate', ref='definition')['expand_path'], ['root', 'setup'])
        self.action('expand', 'root')
        self.assertTrue(self.action('expand', 'root')['idempotent'])
        self.action('expand', 'setup')
        terminal = self.call('apply_action', expected_view=self.current(), action='expand', target='definition')
        self.assertEqual(terminal['error']['code'], 'not_expandable')
        first, second = self.call('recommend'), self.call('recommend')
        self.assertEqual(first, second)
        self.assertEqual([item['target_id'] for item in first['recommendations']], ['conclusion'])
        self.assertNotIn('result', [node['id'] for node in self.call('get_overview')['nodes']])

    def test_cursor_is_bound_to_view_and_old_page_still_readable(self):
        first = self.call('read_text', limit=1)
        old_view = self.current()
        cursor = first['next_cursor']
        self.assertIsNotNone(cursor)
        self.action('expand', 'root')
        self.assertEqual(self.call('read_text', cursor=cursor)['error']['code'], 'validation_error')
        old = self.call('read_text', view_id=old_view, cursor=cursor, limit=1)
        self.assertTrue(old['ok'])
        self.assertEqual(old['view_id'], old_view)

    def test_explicit_source_inspection_records_exposure(self):
        self.action('expand', 'root')
        result = self.call('inspect', ref={'repo_key': 'demo', 'local_id': 'result'}, detail='lean', limit=2)
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.service.state['exposures']), 1)
        self.assertEqual(self.service.state['exposures'][0]['line_count'], 2)

    def test_reader_cannot_read_other_views_or_jobs(self):
        other = self.service.call('open_reader', {'instance_id': self.store.instance_id})
        result = self.call('read_text', view_id=other['view_id'])
        self.assertEqual(result['error']['code'], 'not_found')
        result = self.call('inspect', ref='job-nonexistent', detail='job')
        self.assertEqual(result['error']['code'], 'not_found')

    def test_async_dedup_and_stale_completion_caches_without_applying(self):
        store = self.prepare_async()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def runtime(prompt, schema):
            node = json.loads(prompt.split('\n', 1)[1])['scope_view']['node']['id']
            calls.append(node)
            started.set()
            release.wait(3)
            return self.fixture['blocks'][node]
        store.runtime = runtime
        job = self.action('expand', 'setup')['job']
        self.assertTrue(started.wait(2))
        duplicate = self.action('expand', 'setup')['job']
        self.assertEqual(job['job_id'], duplicate['job_id'])
        self.action('collapse', 'root')
        collapsed = self.current()
        release.set()
        completed = self.wait_job(job['job_id'])
        self.assertEqual(completed['status'], 'published')
        self.assertFalse(completed['applied'])
        self.assertEqual(self.current(), collapsed)
        self.action('expand', 'root')
        self.action('expand', 'setup')
        self.assertEqual(calls, ['definition', 'bound'])

    def test_cancellation_and_job_ownership(self):
        store = self.prepare_async()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def runtime(prompt, schema):
            started.set()
            release.wait(3)
            node = json.loads(prompt.split('\n', 1)[1])['scope_view']['node']['id']
            return self.fixture['blocks'][node]
        store.runtime = runtime
        view = self.current()
        job = self.action('expand', 'setup')['job']['job_id']
        self.assertTrue(started.wait(2))
        other = self.service.call('open_reader', {'instance_id': store.instance_id})
        forbidden = self.service.call('apply_action', {'reader_id': other['reader_id'], 'expected_view': other['view_id'], 'action': 'cancel', 'target': job})
        self.assertEqual(forbidden['error']['code'], 'not_found')
        self.assertEqual(self.action('cancel', job)['job']['status'], 'cancelled')
        release.set()
        self.assertEqual(self.wait_job(job)['status'], 'cancelled')
        self.assertEqual(self.current(), view)

    def test_generation_failure_does_not_change_view(self):
        store = self.prepare_async()
        def runtime(*_):
            raise RuntimeError('controlled failure')
        store.runtime = runtime
        view = self.current()
        job = self.action('expand', 'setup')['job']['job_id']
        self.assertEqual(self.wait_job(job)['status'], 'failed')
        self.assertEqual(self.current(), view)

    def test_restart_preserves_fixed_views(self):
        self.action('expand', 'root')
        before = self.call('read_text')
        restarted = ReaderService([self.store], self.service.path)
        self.addCleanup(restarted.close)
        self.assertEqual(before, restarted.call('read_text', {'reader_id': self.reader}))

    def test_overview_page_boundary_stubs_and_internal_relation(self):
        self.action('expand', 'root')
        overview = self.call('get_overview')
        edge = next(e for e in overview['edges'] if e['provider_node'] == 'setup')
        self.assertEqual(edge['consumer_node'], 'conclusion')
        page = self.call('get_overview', limit=1)
        self.assertEqual(page['total_nodes'], 3)
        next_page = self.call('get_overview', limit=1, cursor=page['next_cursor'])
        self.assertIn({'id': 'conclusion', 'outside_page': True}, next_page['boundary_stubs'])

    def test_manual_budget_counts_titles_and_rejects_expansion_without_mutating_view(self):
        length = self.call('read_text')['length']
        self.assertGreater(length['title_codepoints'], 0)
        self.assertEqual(length['total_codepoints'], length['text_codepoints'] + length['title_codepoints'])
        result = self.call('apply_action', expected_view=self.current(), action='set_budget', target='root', budget_codepoints=0)
        self.assertTrue(result['ok'])
        self.assertTrue(result['length']['over_budget'])
        view = self.current()
        result = self.call('apply_action', expected_view=view, action='expand', target='root')
        self.assertEqual(result['error']['code'], 'budget_exceeded')
        self.assertEqual(self.current(), view)
        self.call('apply_action', expected_view=view, action='set_budget', target='root', budget_codepoints=None)
        self.action('expand', 'root')
        self.call('apply_action', expected_view=self.current(), action='set_budget', target='root', budget_codepoints=0)
        self.action('collapse', 'root')

    def test_async_budget_failure_retains_generated_cache(self):
        store = self.prepare_async()
        calls = []
        def runtime(prompt, schema):
            node = json.loads(prompt.split('\n', 1)[1])['scope_view']['node']['id']
            calls.append(node)
            return self.fixture['blocks'][node]
        store.runtime = runtime
        self.call('apply_action', expected_view=self.current(), action='set_budget', target='root', budget_codepoints=0)
        view = self.current()
        job = self.action('expand', 'setup')['job']['job_id']
        result = self.wait_job(job)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['error']['code'], 'budget_exceeded')
        self.assertEqual(self.current(), view)
        self.call('apply_action', expected_view=view, action='set_budget', target='root', budget_codepoints=None)
        self.action('expand', 'setup')
        self.assertEqual(calls, ['definition', 'bound'])

    def test_external_dependencies_are_grouped_and_evidence_is_paginated(self):
        hierarchy = self.fixture['hierarchy']
        for i in range(60):
            ref = {'repo_key': 'library', 'local_id': str(i)}
            hierarchy['external_refs'].append({'id': 'external-' + str(i), 'ref': ref, 'loaded': False})
            hierarchy['edges'].append({'id': 'external-edge-' + str(i), 'provider_node': 'external-' + str(i),
                                       'consumer_node': 'definition', 'provider_decl': ref,
                                       'consumer_decl': {'repo_key': 'demo', 'local_id': 'definition'}})
        store = ContentStore(Workspace.from_json(json.dumps(self.fixture['workspace'])), hierarchy, Path(self.tmp.name) / 'external.json')
        store.publish(self.fixture['blocks'])
        self.service.stores[store.instance_id] = store
        self.reader = self.service.call('open_reader', {'instance_id': store.instance_id})['reader_id']
        overview = self.call('get_overview')
        self.assertEqual(len(overview['nodes']), 2)
        external = next(n for n in overview['nodes'] if n['kind'] == 'external')
        self.assertEqual(external['declaration_count'], 60)
        edge = overview['edges'][0]
        self.assertEqual(edge['evidence_count'], 60)
        self.assertEqual(len(edge['evidence_ids']), 50)
        evidence = self.call('inspect', ref=edge['id'], limit=5)
        self.assertEqual(len(evidence['items']), 5)
        self.assertIsNotNone(evidence['next_cursor'])
        members = self.call('inspect', ref=external['id'], detail='members', limit=5)
        self.assertEqual(len(members['items']), 5)
        self.assertEqual(members['total'], 60)

    def test_interfaces_and_multiline_source_pages_are_structured_and_bounded(self):
        interface = self.call('inspect', ref='root', detail='interfaces', limit=1)
        self.assertEqual(len(interface['items']), 1)
        self.assertIn('relation_kind', interface['items'][0])
        self.assertIsNotNone(interface['next_cursor'])
        from dataclasses import replace
        declarations = list(self.store.workspace.declarations)
        definition = declarations[0]
        multiline = '\n'.join('line ' + str(i) for i in range(100))
        declarations[0] = replace(definition, statement=replace(definition.statement,
            formal=replace(definition.statement.formal, text=multiline)))
        self.store.workspace = replace(self.store.workspace, declarations=tuple(declarations))
        result = self.call('inspect', ref='definition', detail='lean', limit=3)
        self.assertEqual(result['text'], 'line 0\nline 1\nline 2')
        self.assertEqual([item['line'] for item in result['items']], [1, 2, 3])
        self.assertTrue(all(item['part'] == 'statement' for item in result['items']))
        next_page = self.call('inspect', ref='definition', detail='lean', limit=3, cursor=result['next_cursor'])
        self.assertEqual(next_page['items'][0]['line'], 4)

    def test_injected_recommendation_policy_receives_context_and_cannot_reveal_hidden_target(self):
        observed = []
        def policy(context, candidates):
            observed.append((context, candidates))
            return {'policy_id': 'fixture-policy', 'recommendations': [{'target_id': candidates[0], 'rank': 1,
                    'reason': 'Fixture rule', 'score': 2, 'components': {'fixture': 2}}]}
        self.service.recommendation_policy = policy
        result = self.call('recommend')
        self.assertEqual(result['policy_id'], 'fixture-policy')
        self.assertIn('length', observed[0][0])
        self.assertEqual(observed[0][1], ['root'])
        self.service.recommendation_policy = lambda *_: {'policy_id': 'bad', 'recommendations': [
            {'target_id': 'definition', 'rank': 1, 'reason': 'Hidden target'}]}
        self.assertEqual(self.call('recommend')['error']['code'], 'validation_error')
