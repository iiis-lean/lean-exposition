import json
import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentError, ContentStore, writing_view
from lean_exposition.models import Workspace


def bind_current_hierarchy(workspace, hierarchy):
    """Rebind a hand-authored test hierarchy after changing its fixed workspace."""
    from copy import deepcopy
    value = deepcopy(hierarchy)
    value['workspace_digest'] = workspace.digest()
    value['hierarchy_id'] = ''
    payload = dict(value)
    payload.pop('hierarchy_id')
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    value['hierarchy_id'] = 'hierarchy:' + hashlib.sha256(encoded).hexdigest()[:24]
    return value


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = demo_fixture()
        self.fixture['blocks']['setup']['lead_in'] = 'Fix a natural number and define its successor.'
        self.fixture['blocks']['conclusion']['lead_out'] = 'The successor is strictly larger.'
        self.workspace = Workspace.from_json(json.dumps(self.fixture['workspace']))
        self.store = self.make('zh')

    def make(self, locale, name=None):
        return ContentStore(self.workspace, self.fixture['hierarchy'], Path(self.tmp.name) / (name or str(locale)), locale=locale)

    def test_locale_identity_and_reload_bytes_unchanged(self):
        en = self.make('en')
        default = self.make(None)
        default.publish({'root': self.fixture['blocks']['root']})
        before = default.path.read_bytes()
        loaded = ContentStore(self.workspace, self.fixture['hierarchy'], default.path)
        self.assertEqual(default.instance_id, loaded.instance_id)
        self.assertIsNone(loaded.locale)
        self.assertEqual(before, default.path.read_bytes())
        self.assertEqual(en.structure_id, self.store.structure_id)
        self.assertEqual(en.structure_id, default.structure_id)
        self.assertEqual(len({en.instance_id, self.store.instance_id, default.instance_id}), 3)
        with self.assertRaises(ContentError):
            ContentStore(self.workspace, self.fixture['hierarchy'], en.path, locale='zh')
        en.publish({'root': self.fixture['blocks']['root']})
        self.assertEqual(en.manifest()['locale'], 'en')
        self.assertFalse(en.manifest()['complete'])

    def test_revisions_prefix_final_parent_and_atomic_publication(self):
        base = self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        first = self.store.submit_draft(job, self.fixture['blocks']['setup'])
        self.assertFalse(first['advanced'])
        self.assertFalse(first['preview']['includes_parent_ending'])
        self.assertEqual(self.store.get_step(job)['step'], 0)
        revised = self.store.submit_draft(job, {**self.fixture['blocks']['setup'], 'synopsis': 'Revised argument.'})
        with self.assertRaises(ContentError):
            self.store.accept_draft(job, first['draft_id'])
        self.store.accept_draft(job, revised['draft_id'])
        self.assertEqual(self.store.state['latest_manifest'], base)
        last = self.store.submit_draft(job, self.fixture['blocks']['conclusion'])
        self.assertTrue(last['preview']['includes_parent_ending'])
        self.assertEqual(last['preview']['original_parent_synopsis'], self.fixture['blocks']['root']['synopsis'])
        self.assertEqual(self.store.state['latest_manifest'], base)
        result = self.store.accept_draft(job, last['draft_id'])
        self.assertEqual(result['status'], 'published')
        self.assertEqual(set(self.store.manifest()['blocks']), {'root', 'setup', 'conclusion'})
        restarted = self.make('zh')
        self.assertEqual(restarted.get_step(job)['status'], 'published')
        self.assertEqual(set(restarted.manifest(base)['blocks']), {'root'})

    def test_branch_cas_rejects_stale_and_preserves_draft(self):
        self.store.publish({key: self.fixture['blocks'][key] for key in ['root', 'setup', 'conclusion']})
        left = self.store.create_writing_job('setup')
        right = self.store.create_writing_job('conclusion')
        step = self.store.get_step(right)
        draft = self.store.submit_draft(right, self.fixture['blocks'][step['node_id']])
        while self.store.get_step(left)['status'] == 'active':
            step = self.store.get_step(left)
            item = self.store.submit_draft(left, self.fixture['blocks'][step['node_id']])
            self.store.accept_draft(left, item['draft_id'])
        latest = self.store.state['latest_manifest']
        with self.assertRaises(ContentError):
            self.store.accept_draft(right, draft['draft_id'])
        self.assertEqual(self.store.state['latest_manifest'], latest)
        self.assertEqual(self.store.get_step(right)['status'], 'stale')
        self.assertIsNotNone(self.store.state['writing_jobs'][right]['draft'])

    def test_canonical_ancestors_and_prior_outcomes_exclude_synopsis_proof(self):
        self.store.publish({key: self.fixture['blocks'][key] for key in ['root', 'setup', 'conclusion']})
        job = self.store.create_writing_job('conclusion')
        context = self.store.get_step(job)['context']
        self.assertEqual(context['path'][:2], ['root', 'conclusion'])
        facts = [item['text'] for item in context['available_facts']]
        self.assertIn(self.fixture['blocks']['setup']['lead_out'], facts)
        self.assertNotIn(self.fixture['blocks']['root']['synopsis'], facts)
        self.assertNotIn(self.fixture['blocks']['conclusion']['lead_out'], facts)
        self.assertEqual(context['future_goal'], self.fixture['blocks']['conclusion']['lead_out'])

    def test_failure_restart_resume_and_cancel_preserve_base(self):
        base = self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        first = self.store.submit_draft(job, self.fixture['blocks']['setup'])
        self.store.accept_draft(job, first['draft_id'])
        loaded = self.make('zh')
        self.assertEqual(loaded.create_writing_job('root'), job)
        self.assertEqual(loaded.get_step(job)['node_id'], 'conclusion')
        loaded.runtime = lambda *_: (_ for _ in ()).throw(RuntimeError('controlled'))
        with self.assertRaises(RuntimeError):
            loaded.generate_children('root')
        self.assertEqual(loaded.state['latest_manifest'], base)
        loaded.cancel_writing_job(job)
        self.assertEqual(loaded.get_step(job)['status'], 'cancelled')
        self.assertIn('setup', loaded.state['writing_jobs'][job]['accepted'])

    def test_queries_are_bound_and_paged_and_new_sections_nonempty(self):
        self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        full = self.store.query_job(job, 'scope', limit=16000)
        pieces = []
        offset = 0
        while True:
            page = self.store.query_job(job, 'scope', offset=offset, limit=47)
            self.assertLessEqual(len(page['text']), 47)
            pieces.append(page['text'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(''.join(pieces), full['text'])
        for kwargs in [{'query': '../auth.json'}, {'query': 'decl', 'decl_ref': {'repo_key': 'other', 'local_id': 'x'}},
                       {'query': 'scope', 'node_id': 'absent'}, {'query': 'scope', 'limit': 16001}]:
            with self.assertRaises(ContentError):
                self.store.query_job(job, **kwargs)
        with self.assertRaises(ContentError):
            self.store.submit_draft(job, {**self.fixture['blocks']['setup'], 'lead_in': ' '})

    def test_mathematical_material_retains_proof_evidence_and_parts(self):
        declarations = list(self.workspace.declarations)
        declarations[2] = replace(declarations[2], proof=replace(declarations[2].proof, deps=declarations[2].statement.deps))
        workspace = replace(self.workspace, declarations=tuple(declarations))
        material = writing_view(workspace, self.store.hierarchy, 'root', mathematical=True)
        self.assertTrue(any(card.get('proof') for card in material['cards']))
        self.assertTrue(any(edge[2] == 1 for edge in material['internal']))
        compact = writing_view(self.workspace, self.store.hierarchy, 'root')
        self.assertFalse(any('proof' in card for card in compact['cards']))


if __name__ == '__main__':
    unittest.main()

class WritingBoundaryTests(unittest.TestCase):
    setUp = WritingTests.setUp
    make = WritingTests.make

    def test_ancestor_interfaces_dependency_direction_and_api_budget_fail_before_call(self):
        self.store.publish({key: self.fixture['blocks'][key] for key in ['root', 'setup', 'conclusion']})
        job = self.store.create_writing_job('conclusion')
        ancestor = json.loads(self.store.query_job(job, 'scope', node_id='root')['text'])
        self.assertEqual(ancestor['node']['id'], 'root')
        path = json.loads(self.store.query_job(job, 'dependency_path', provider_ref={'repo_key': 'demo', 'local_id': 'definition'},
                                              consumer_ref={'repo_key': 'demo', 'local_id': 'result'})['text'])
        self.assertTrue(path['found'])
        self.assertEqual(path['edges'][0]['part'], 'statement')
        reverse = json.loads(self.store.query_job(job, 'dependency_path', provider_ref={'repo_key': 'demo', 'local_id': 'result'},
                                                 consumer_ref={'repo_key': 'demo', 'local_id': 'definition'})['text'])
        self.assertFalse(reverse['found'])
        self.store.runtime = lambda *_: self.fail('over-budget source must not call the model')
        with self.assertRaisesRegex(ContentError, '360000'):
            self.store._generate_mathematical('result', {}, {'source': 'x' * 360001})

    def test_agent_material_budget_and_api_full_source(self):
        declarations = list(self.workspace.declarations)
        declarations[2] = replace(declarations[2], proof=replace(declarations[2].proof,
                                  formal=replace(declarations[2].proof.formal, text='known proof ' * 2000)))
        workspace = replace(self.workspace, declarations=tuple(declarations))
        hierarchy = bind_current_hierarchy(workspace, self.fixture['hierarchy'])
        store = ContentStore(workspace, hierarchy, Path(self.tmp.name) / 'large', locale='en')
        job = store.create_writing_job()
        step = store.get_step(job)
        self.assertLess(len(json.dumps(step['material'])), 36000)
        captured = []
        store.runtime = lambda prompt, schema: captured.append(prompt) or self.fixture['blocks']['root']
        store.generate_root()
        self.assertIn('known proof ' * 2000, captured[0])
        self.assertIn('$$', captured[0])

    def test_future_consumer_is_not_a_scope_result_or_default_card(self):
        from copy import deepcopy
        from lean_exposition.models import DeclRef, Dependency, Provenance
        declarations = list(self.workspace.declarations)
        declarations[2] = replace(declarations[2], statement=replace(declarations[2].statement,
            deps=(Dependency(DeclRef('demo', 'bound'), 'lean_type', (Provenance('fixture', 'future-use'),)),)))
        workspace = replace(self.workspace, declarations=tuple(declarations))
        hierarchy = deepcopy(self.fixture['hierarchy'])
        hierarchy['nodes'] = [node for node in hierarchy['nodes'] if node['id'] in {'root', 'bound'}]
        root = next(node for node in hierarchy['nodes'] if node['id'] == 'root')
        root['children'] = ['bound']
        root['decl_refs'] = [{'repo_key': 'demo', 'local_id': 'bound'}]
        next(node for node in hierarchy['nodes'] if node['id'] == 'bound')['parent'] = 'root'
        material = writing_view(workspace, hierarchy, 'root', mathematical=True)
        self.assertEqual(material['primary_outcomes'], [])
        cards = {card['ref']['local_id']: card for card in material['cards']}
        self.assertEqual(set(cards), {'bound', 'definition'})
        self.assertEqual(cards['definition']['writing_role'], 'incoming_provider_interface')
        self.assertEqual(cards['bound']['writing_role'], 'local_declaration')
        self.assertFalse(any(card['primary_outcome'] for card in cards.values()))
        seen = []
        hierarchy = bind_current_hierarchy(workspace, hierarchy)
        store = ContentStore(workspace, hierarchy, Path(self.tmp.name) / 'future', locale='en',
                             runtime=lambda prompt, schema: seen.append(prompt) or {'title': 'The weak bound', 'short_description': 'The successor does not decrease its input.', 'evidence_refs': []})
        store.name_regions()
        prompt_material = json.loads(seen[0].rsplit('\n', 1)[1])['scope_view']
        self.assertEqual({card['ref']['local_id'] for card in prompt_material['cards']}, {'bound', 'definition'})

    def test_new_technical_entry_keeps_anchor_without_lean_proof_narration(self):
        for locale in ('zh', 'en'):
            with self.subTest(locale=locale):
                store = self.store if locale == 'zh' else self.make('en')
                store.nodes['definition']['metadata'] = {'technical': True, 'source_missing': True}
                block = store._generate('definition', {})
                self.assertNotIn('```', block['content'])
                self.assertNotIn('def f', block['content'])
                self.assertNotIn('形式类型', block['content'])
                self.assertNotIn('formal type', block['content'])
                self.assertIn('身份、依赖和来源信息' if locale == 'zh' else 'identity, dependencies, and source information', block['content'])
                self.assertIn('不补写缺失证明' if locale == 'zh' else 'no missing proof is reconstructed', block['content'])
                self.assertEqual(block['anchors'][0]['targets'][0]['decl_ref']['local_id'], 'definition')

    def test_terminal_title_is_published_atomically_with_its_group(self):
        self.store.publish({key: self.fixture['blocks'][key] for key in ['root', 'setup', 'conclusion']})
        job = self.store.create_writing_job('setup')
        first = self.store.get_step(job)
        self.assertIn('title', first['schema']['required'])
        draft = self.store.submit_draft(job, {**self.fixture['blocks'][first['node_id']], 'title': '后继函数'})
        self.store.accept_draft(job, draft['draft_id'])
        self.assertNotIn(first['node_id'], self.store.manifest()['metadata'])
        second = self.store.get_step(job)
        draft = self.store.submit_draft(job, {**self.fixture['blocks'][second['node_id']], 'title': '后继的下界'})
        self.store.accept_draft(job, draft['draft_id'])
        self.assertEqual(self.store.manifest()['metadata'][first['node_id']]['title'], '后继函数')
        self.assertEqual(self.store.manifest()['metadata'][second['node_id']]['title'], '后继的下界')

    def test_canonical_ancestor_anchor_is_valid_but_unknown_node_is_not(self):
        payload = {**self.fixture['blocks']['conclusion'], 'anchors': [{'part': 'lead_in', 'targets': [{'node_id': 'root'}]}]}
        self.store.validate_submission('conclusion', payload)
        payload['anchors'][0]['targets'] = [{'node_id': 'arbitrary-other-scope'}]
        with self.assertRaises(ContentError):
            self.store.validate_submission('conclusion', payload)

    def test_sibling_anchor_permission_does_not_make_future_ending_a_premise(self):
        self.store.publish({'root': self.fixture['blocks']['root']})
        job = self.store.create_writing_job('root')
        step = self.store.get_step(job)
        self.assertIn('conclusion', step['context']['allowed_node_anchors'])
        self.assertNotIn(self.fixture['blocks']['conclusion']['lead_out'],
                         [fact['text'] for fact in step['context']['available_facts']])
        self.store.submit_draft(job, {**self.fixture['blocks']['setup'],
            'anchors': [{'part': 'lead_out', 'targets': [{'node_id': 'conclusion'}]}]})
