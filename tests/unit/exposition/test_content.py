from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentError, ContentStore, render, decl_card, scope_view
from lean_exposition.exposition.content import submission_schema
from lean_exposition.models import Workspace, DeclRef, Dependency, Provenance, Repository


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = demo_fixture()
        self.workspace = Workspace.from_json(json.dumps(self.fixture['workspace']))
        self.store = ContentStore(self.workspace, self.fixture['hierarchy'], Path(self.tmp.name) / 'content.json')

    def test_three_submissions_and_out_of_context_rejection(self):
        self.store.submit_section('root', **self.fixture['blocks']['root'])
        self.store.submit_theorem('bound', **self.fixture['blocks']['bound'])
        self.store.submit_content('definition', **self.fixture['blocks']['definition'])
        with self.assertRaises(ContentError):
            self.store.submit_content('bound', content='wrong kind', anchors=[])
        with self.assertRaises(ContentError):
            self.store.submit_content('definition', content='bad', anchors=[{'part': 'content', 'targets': [{'decl_ref': {'repo_key': 'other', 'local_id': 'invented'}}]}])

    def test_submission_enum_has_an_explicit_string_type(self):
        part = submission_schema('section')['properties']['anchors']['items']['properties']['part']
        self.assertEqual(part['type'], 'string')

    def test_immutable_manifest_fold_restore_and_commuting_branches(self):
        first = self.store.publish({'root': self.fixture['blocks']['root']})
        second = self.store.publish(self.fixture['blocks'])
        self.assertNotEqual(first, second)
        self.assertEqual(set(self.store.manifest(first)['blocks']), {'root'})
        folded = render(self.store.hierarchy, self.store.manifest(second), set())['text']
        expanded = render(self.store.hierarchy, self.store.manifest(second), {'root', 'setup', 'conclusion'})['text']
        self.assertIn('Adding one', expanded)
        self.assertEqual(folded, render(self.store.hierarchy, self.store.manifest(second), {'setup', 'conclusion'})['text'])
        self.assertEqual(expanded, render(self.store.hierarchy, self.store.manifest(second), {'conclusion', 'root', 'setup'})['text'])
        with self.assertRaises(ContentError):
            self.store.publish({'root': {**self.fixture['blocks']['root'], 'synopsis': 'rewritten'}})
        self.assertEqual(self.store.state['latest_manifest'], second)

    def test_failure_retains_draft_but_publishes_nothing_then_retries_only_failed_child(self):
        original = self.store.publish({'root': self.fixture['blocks']['root']})
        calls = []
        def runtime(prompt, schema):
            context = json.loads(prompt.split('\n', 1)[1])
            node = context['scope_view']['node']['id']
            calls.append(node)
            if node == 'conclusion' and calls.count(node) == 1:
                raise RuntimeError('controlled failure')
            if node == 'conclusion':
                self.assertEqual(context['write_context']['previous_fixed_boundary'], self.fixture['blocks']['setup']['lead_out'])
            return self.fixture['blocks'][node]
        self.store.runtime = runtime
        with self.assertRaises(RuntimeError):
            self.store.generate_children('root')
        self.assertEqual(self.store.state['latest_manifest'], original)
        self.assertEqual(list(self.store.state['drafts']['root']), ['setup'])
        self.store.generate_children('root')
        self.assertEqual(calls, ['setup', 'conclusion', 'conclusion'])
        self.assertEqual(set(self.store.manifest()['blocks']), {'root', 'setup', 'conclusion'})
        self.assertEqual(self.store.manifest(original)['blocks']['root']['synopsis'], self.fixture['blocks']['root']['synopsis'])

    def test_metadata_is_display_only_and_frozen(self):
        self.store.runtime = lambda prompt, schema: {'title': 'A display title', 'short_description': 'One sentence.', 'evidence_refs': []}
        ids = list(self.store.nodes)
        self.store.name_regions()
        self.assertEqual(self.store.state['metadata']['setup']['title'], 'A display title')
        self.assertEqual(ids, list(self.store.nodes))
        self.store.publish({'root': self.fixture['blocks']['root']})
        with self.assertRaises(ContentError):
            self.store.name_regions()

    def test_cards_are_compact_and_all_internal_material_precedes_unloaded_boundaries(self):
        p = (Provenance('fixture', 'edge'),)
        decls = list(self.workspace.declarations)
        deps = tuple(Dependency(DeclRef('aaa', str(i)), 'lean_type', p) for i in range(40))
        decls[0] = replace(decls[0], statement=replace(decls[0].statement, deps=deps))
        workspace = replace(self.workspace, declarations=tuple(decls), manifest=replace(self.workspace.manifest,
                            repositories=self.workspace.manifest.repositories + (Repository('aaa', None, None, version_status='unresolved', unresolved_reason='fixture'),)))
        small = scope_view(workspace, self.store.hierarchy, 'root', limit=3)
        self.assertEqual({card['ref']['repo_key'] for card in small['cards']}, {'demo'})
        full = scope_view(workspace, self.store.hierarchy, 'root', limit=None)
        self.assertEqual(len(full['cards']), 43)
        self.assertIsNone(full['next_offset'])
        card = decl_card(workspace, DeclRef('demo', 'definition'))
        self.assertNotIn('deps', card['statement'])
        self.assertNotIn('provenance', card['statement']['formal'])

    def test_compiler_only_source_missing_uses_fixed_text_without_runtime(self):
        self.store.nodes['definition']['metadata'] = {'technical': True, 'source_missing': True}
        self.store.runtime = None
        block = self.store._generate('definition', {})
        self.assertIn('Original source was not located.', block['content'])
        self.assertIn('def f', block['content'])

    def test_section_writing_context_omits_internal_proofs_and_compresses_unloaded_cards(self):
        from lean_exposition.exposition import writing_view
        p = (Provenance('fixture', 'edge'),)
        declarations = list(self.workspace.declarations)
        declarations[0] = replace(declarations[0], statement=replace(declarations[0].statement,
            deps=tuple(Dependency(DeclRef('library', str(i)), 'lean_type', p) for i in range(100))))
        for index in (1, 2):
            declarations[index] = replace(declarations[index], proof=replace(declarations[index].proof,
                formal=replace(declarations[index].proof.formal, text='LONG INTERNAL PROOF\n' * 10000)))
        workspace = replace(self.workspace, declarations=tuple(declarations), manifest=replace(self.workspace.manifest,
            repositories=self.workspace.manifest.repositories + (Repository('library', None, None, version_status='unresolved', unresolved_reason='fixture'),)))
        compact = writing_view(workspace, self.store.hierarchy, 'root')
        self.assertTrue(all('proof' not in card for card in compact['cards']))
        self.assertTrue(all(card['loaded'] for card in compact['cards']))
        self.assertEqual(compact['unloaded_external_counts'], {'library': 100})
        self.assertTrue(compact['omitted_internal_refs'])
        self.assertNotIn('LONG INTERNAL PROOF', json.dumps(compact))
        complete = scope_view(workspace, self.store.hierarchy, 'root', limit=None)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(complete)) / 3)
        terminal = writing_view(workspace, self.store.hierarchy, 'bound')
        self.assertTrue(any(card.get('proof') for card in terminal['cards']))

    def test_reference_schema_branches_remain_disjoint_with_codex_compatible_anyof(self):
        import jsonschema
        from lean_exposition.exposition.content import TARGET_SCHEMA
        for target in ({'node_id': 'n'}, {'edge_id': 'e'}, {'decl_ref': {'repo_key': 'r', 'local_id': 'd'}}):
            jsonschema.validate(target, TARGET_SCHEMA)
            matches = sum(jsonschema.Draft202012Validator(branch).is_valid(target) for branch in TARGET_SCHEMA['anyOf'])
            self.assertEqual(matches, 1)
        for target in ({'node_id': 'n', 'edge_id': 'e'}, {}, {'decl_ref': {'repo_key': 'r'}}, {'invented': 'x'}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(target, TARGET_SCHEMA)

    def test_review_evidence_is_immutable_and_inherited_by_later_publications(self):
        evidence = {'method': 'human_review', 'finding': 'Explicit correction evidence'}
        first = self.store.publish({'root': self.fixture['blocks']['root']}, review_evidence=evidence)
        second = self.store.publish(self.fixture['blocks'])
        self.assertEqual(self.store.manifest(first)['review_evidence'], evidence)
        self.assertEqual(self.store.manifest(second)['review_evidence'], evidence)
        self.assertEqual(set(self.store.manifest(first)['blocks']), {'root'})

    def test_current_content_identity_covers_prompt_schema_and_config(self):
        state = json.loads(self.store.path.read_text())
        self.assertEqual(state['content_digest'], self.store.content_digest)
        self.assertNotIn('prompt_version', state)
        self.assertIsNone(state['locale'])
        self.store.publish({'root': self.fixture['blocks']['root']})
        manifest = self.store.manifest()
        self.assertEqual(manifest['content_digest'], self.store.content_digest)
        self.assertEqual(manifest['structure_id'], self.store.structure_id)
        self.assertNotIn('prompt_version', manifest)

        different = ContentStore(
            self.workspace,
            self.fixture['hierarchy'],
            Path(self.tmp.name) / 'different.json',
            max_input_characters=12000,
        )
        self.assertNotEqual(self.store.content_digest, different.content_digest)
        self.assertNotEqual(self.store.instance_id, different.instance_id)

        stale = Path(self.tmp.name) / 'stale.json'
        stale_state = dict(state)
        stale_state.pop('content_digest')
        stale.write_text(json.dumps(stale_state))
        with self.assertRaisesRegex(ContentError, 'current prompt, schema and writing configuration'):
            ContentStore(self.workspace, self.fixture['hierarchy'], stale)
