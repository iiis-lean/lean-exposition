import json
from pathlib import Path
import tempfile
import unittest

from lean_exposition.app.bilingual_demo import create_bilingual_demo
from lean_exposition.app.demo import create_demo
from lean_exposition.reading import ReaderService


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.stores = create_bilingual_demo(self.path)
        self.service = ReaderService(self.stores, self.path / 'readers.json')
        self.addCleanup(self.service.close)
        self.opened = self.service.call('open_reader', {'instance_id': self.stores[0].instance_id})
        self.reader = self.opened['reader_id']

    def call(self, tool, **kwargs):
        return self.service.call(tool, {'reader_id': self.reader, **kwargs})

    def action(self, action, target='root', **kwargs):
        current = self.service.state['readers'][self.reader]['current_view']
        result = self.call('apply_action', expected_view=current, action=action, target=target, **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def test_old_view_pages_inspect_locate_and_restart(self):
        self.action('expand')
        self.action('expand', 'setup')
        old = self.call('read_text', limit=1)
        old_inspect = self.call('inspect', ref='bound')
        old_locate = self.call('locate', ref='result')
        expanded = self.service.state['views'][old['view_id']]['expanded']
        switched = self.action('switch_locale', locale='en')
        self.assertEqual(switched['locale'], 'en')
        self.assertEqual(self.service.state['views'][switched['view_id']]['expanded'], expanded)
        self.assertEqual(self.call('read_text', view_id=old['view_id'], limit=1), old)
        self.assertEqual(self.call('inspect', view_id=old['view_id'], ref='bound'), old_inspect)
        self.assertEqual(self.call('locate', view_id=old['view_id'], ref='result'), old_locate)
        page = self.call('read_text', view_id=old['view_id'], cursor=old['next_cursor'], limit=1)
        self.assertEqual(page['locale'], 'zh')
        self.service.close()
        restarted = ReaderService(create_bilingual_demo(self.path), self.path / 'readers.json')
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.call('read_text', {'reader_id':self.reader,'view_id':old['view_id'],'limit':1}), old)

    def test_missing_locale_does_not_change_view(self):
        old = self.opened['view_id']
        del self.service.stores[self.stores[1].instance_id]
        result = self.call('apply_action', expected_view=old, action='switch_locale', target='root', locale='en')
        self.assertEqual(result['error']['code'], 'locale_unavailable')
        self.assertEqual(self.call('read_text')['view_id'], old)

    def test_unlocalized_view_identity_is_fixed_before_switch(self):
        unlocalized = create_demo(self.path / 'unlocalized')
        self.service.stores[unlocalized.instance_id] = unlocalized
        opened = self.service.call('open_reader', {'instance_id':unlocalized.instance_id})
        view = self.service.state['views'][opened['view_id']]
        del view['instance_id']
        self.service._save()
        restarted = ReaderService([unlocalized, *self.stores], self.path / 'readers.json')
        self.addCleanup(restarted.close)
        result = restarted.call('apply_action', {'reader_id':opened['reader_id'], 'expected_view':opened['view_id'], 'action':'switch_locale','target':'root','locale':'zh'})
        self.assertTrue(result['ok'], result)
        old = restarted.call('read_text', {'reader_id':opened['reader_id'],'view_id':opened['view_id']})
        self.assertIsNone(old['locale'])
        self.assertEqual(old['instance_id'], unlocalized.instance_id)

    def test_alias_and_policy_context_excludes_hidden_content(self):
        root = self.stores[0].nodes['root']
        root['metadata'] = {'aliases': {'old-scope':'setup'}}
        self.assertEqual(self.call('locate',ref='old-scope')['node_id'], 'setup')
        contexts = []
        def policy(context, candidates):
            contexts.append(context)
            return {'policy_id':'probe','recommendations':[]}
        self.service.recommendation_policy = policy
        self.call('recommend')
        self.assertEqual(contexts[0]['expanded'], [])
        self.assertEqual(contexts[0]['locale'], 'zh')
        self.assertEqual(set(contexts[0]['synopsis_codepoints']), {'root'})
        self.assertNotIn('proof', json.dumps(contexts))

    def test_pending_generation_cannot_overwrite_language_switch(self):
        import threading
        import time
        from lean_exposition.exposition import ContentStore
        original = self.stores[0]
        partial = ContentStore(original.workspace, original.hierarchy, self.path / 'partial.zh.json', locale='zh')
        blocks = original.manifest()['blocks']
        # Published blocks carry content IDs; use only submission fields.
        from lean_exposition.exposition.content import PARTS
        def submission(node_id):
            block = blocks[node_id]
            return {key:block[key] for key in (*PARTS[block['kind']], 'anchors')}
        partial.publish({key:submission(key) for key in ('root','setup','conclusion')})
        self.service.stores[partial.instance_id] = partial
        self.reader = self.service.call('open_reader', {'instance_id':partial.instance_id})['reader_id']
        self.action('expand')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def generate(target, cancelled, progress=None, publication_control=None):
            started.set()
            release.wait(3)
            return partial.publish({key:submission(key) for key in ('definition','bound')})
        partial.generate_children = generate
        result = self.action('expand','setup')
        self.assertTrue(started.wait(2))
        switched = self.action('switch_locale',locale='en')
        release.set()
        deadline = time.monotonic()+3
        active = {'queued', 'drafting', 'stitching', 'validating'}
        while time.monotonic()<deadline:
            job = self.call('inspect',ref=result['job']['job_id'],detail='job')['job']
            if job['status'] not in active: break
            time.sleep(.01)
        self.assertEqual(job['status'],'published')
        self.assertFalse(job['applied'])
        self.assertEqual(self.call('read_text')['view_id'],switched['view_id'])
        self.assertEqual(self.call('read_text')['locale'],'en')

    def test_real_structural_policy_with_content_identity_and_unlocalized_content(self):
        from lean_exposition.features import extract_features
        from lean_exposition.recommendation import make_structural_policy
        from lean_exposition.app.demo import create_demo
        for store in (self.stores[0], create_demo(self.path / 'unlocalized-policy')):
            self.service.stores[store.instance_id] = store
            features = extract_features(store.workspace, store.hierarchy)
            self.service.recommendation_policy = make_structural_policy(store.workspace, store.hierarchy, features)
            opened = self.service.call('open_reader', {'instance_id':store.instance_id})
            result = self.service.call('recommend', {'reader_id':opened['reader_id']})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['policy_id'], 'structural')
            self.assertEqual([item['target_id'] for item in result['recommendations']], ['root'])
            self.assertFalse(result['recommendations'][0]['components']['cost_basis']['uses_generated_after_text'])
            self.assertEqual(result['structure_id'], store.structure_id)
            self.assertNotEqual(store.structure_id, store.hierarchy['hierarchy_id'])

    def test_terminal_math_title_is_bound_to_each_manifest(self):
        from lean_exposition.exposition import ContentStore
        from lean_exposition.exposition.content import PARTS
        original_store = self.stores[0]
        blocks = original_store.manifest()['blocks']
        def submission(node_id):
            block = blocks[node_id]
            return {key:block[key] for key in (*PARTS[block['kind']], 'anchors')}
        partial = ContentStore(original_store.workspace, original_store.hierarchy, self.path / 'title-publication.zh.json', locale='zh')
        partial.publish({node:submission(node) for node in ('root','setup','conclusion')})
        self.service.stores[partial.instance_id] = partial
        self.reader = self.service.call('open_reader', {'instance_id':partial.instance_id})['reader_id']
        old = self.action('expand')['view_id']
        original = self.call('inspect', ref='bound', view_id=old)['title']
        self.assertEqual(self.call('read_text',view_id=old)['published_node_count'],3)
        title = '后继的非严格下界'
        partial.publish({'definition':submission('definition'), 'bound':{**submission('bound'),'title':title}})
        self.assertEqual(self.call('inspect', ref='bound', view_id=old)['title'], original)
        self.action('expand', 'setup')
        self.assertEqual(self.call('inspect', ref='bound')['title'], title)
        self.assertEqual(self.call('read_text')['published_node_count'],5)
        self.assertEqual(self.call('read_text',view_id=old)['published_node_count'],3)
        self.assertFalse(self.call('read_text')['content_complete'])
        self.assertEqual(next(node['title'] for node in self.call('get_overview')['nodes'] if node['id']=='bound'), title)
        self.assertEqual(self.call('inspect', ref='bound', view_id=old)['title'], original)


    def test_instance_count_uses_reading_root_not_loaded_context(self):
        from dataclasses import replace
        from lean_exposition.construction import (
            COVERAGE_DOMAINS, CoverageEntry, DependencyCoverage,
            RepositoryBuildBundle, StructurePolicy,
        )
        from lean_exposition.models.facts import DeclRef, DeclUnit, Repository, Scope
        from lean_exposition.exposition import ContentStore
        from lean_exposition.structure import build_hierarchy
        workspace = self.stores[0].workspace
        provenance = workspace.declarations[0].provenance
        extra = replace(workspace.declarations[0], ref=DeclRef('context', 'context_only'),
                        lean_name='context_only', native_scope='context-scope')
        context_repo = Repository('context', 'leanprover/lean4:v4.28.0', 'context-scope', revision='b' * 40)
        context_scope = Scope('context-scope', 'context', 'repository', 'Context', provenance)
        context_workspace = replace(
            workspace,
            manifest=replace(workspace.manifest, repositories=(*workspace.manifest.repositories, context_repo)),
            declarations=(*workspace.declarations, extra), scopes=(*workspace.scopes, context_scope),
            units=tuple(DeclUnit(f'{decl.ref.repo_key}:{decl.ref.local_id}', decl.ref)
                        for decl in (*workspace.declarations, extra)))
        digest = context_workspace.digest()
        coverage = DependencyCoverage(digest, tuple(
            CoverageEntry(decl.ref, part, domain, 'unknown', provenance)
            for decl in context_workspace.declarations
            for part in (("statement", "proof") if decl.proof is not None else ("statement",))
            for domain in COVERAGE_DOMAINS
        ))
        bundle = RepositoryBuildBundle(
            context_workspace, StructurePolicy(digest, 'preserve', provenance, True), coverage)
        hierarchy = build_hierarchy(bundle, 'demo')
        store = ContentStore(context_workspace, hierarchy, self.path / 'context.json')
        store.publish({hierarchy.root_id:{'lead_in':'Start.','synopsis':'Reading subset.','lead_out':'End.','anchors':[]}})
        self.service.stores[store.instance_id] = store
        info = next(item for item in self.service.instances()['instances'] if item['instance_id']==store.instance_id)
        self.assertEqual(len(context_workspace.declarations), 4)
        self.assertEqual(info['declaration_count'], 3)
