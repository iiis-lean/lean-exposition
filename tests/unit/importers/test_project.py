"""End-to-end acquisition rules, independent of the availability of Lean."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.construction import (
    CanonicalDeclLocator, DeclarationContribution, FieldContribution, CoverageContribution,
    ContributorSpec, DeclarationLocatorSpec, MaterialAssetSpec, RepositoryIdentity,
    RepositoryProfile, RepositoryBuildBundle, build_repository,
)
from lean_exposition.exposition import ContentStore, decl_card
from lean_exposition.importers import load_project
from lean_exposition.importers.common import adapter_result_from_workspace, asset_from_bytes
from lean_exposition.importers.materials import attach_materials, text_material
from lean_exposition.importers.merge import merge_adapters
from lean_exposition.models import DeclRef, Dependency, Provenance, TextContent
from lean_exposition.structure import build_hierarchy


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def load(self, **kwargs):
        kwargs.setdefault('compiled_modules', ())
        return load_project(self.root, repo_key='r', **kwargs)

    def test_default_compiles_missing_and_reuses_existing_modules(self):
        self.write('A.lean', 'def a := 1\n')
        self.write('B.lean', 'def b := 2\n')
        self.write('.lake/build/lib/lean/A.olean', 'fixture')
        base = self.load()
        semantic = adapter_result_from_workspace(base.workspace, unit_aggregation='native_helpers',
                                                  authority='lean_environment', method='compiled')
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter') as adapter:
            adapter.return_value.collect.return_value = semantic
            result = load_project(self.root, repo_key='r')
        calls = {c.kwargs['modules'][0]: c.kwargs['build'] for c in adapter.call_args_list}
        self.assertEqual(calls, {'A': False, 'B': True})
        self.assertIn('compiled_acquisition:complete:2', result.diagnostics)

    def test_default_semantic_failure_is_explicit_and_retains_source(self):
        self.write('M.lean', 'def n := 1\n')
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter.collect', side_effect=RuntimeError('budget')):
            result = load_project(self.root, repo_key='r')
        self.assertIn('compiled_acquisition:incomplete:M', result.diagnostics)
        self.assertEqual(len(result.workspace.declarations), 1)

    def test_source_only_reaches_same_hierarchy_and_content_store(self):
        self.write('M.lean', '/-- A number. -/\ndef n : Nat := 1\ntheorem t : n = 1 := by rfl\n')
        result = self.load()
        declarations = {d.lean_name: d for d in result.workspace.declarations}
        self.assertIn('A number', declarations['n'].statement.nl.text)
        self.assertEqual(declarations['t'].statement.deps[0].provider, declarations['n'].ref)
        self.assertIn('rfl', declarations['t'].proof.formal.text)
        hierarchy = build_hierarchy(result, 'r')
        store = ContentStore(result.workspace, hierarchy, self.root / 'content')
        self.assertIn(hierarchy.root_id, store.nodes)
        self.assertEqual(RepositoryBuildBundle.from_json(result.to_json()), result)

    def test_scope_imports_comments_strings_and_local_binders(self):
        self.write('A.lean', 'namespace A\ndef n : Nat := 1\ndef h : Nat := 2\nend A\n')
        self.write('B.lean', 'namespace B\ndef n : Nat := 3\nend B\n')
        self.write('M.lean', '''import A
open A
variable (h : Nat)
def localUse (n : Nat) : Nat := n + h
def actual : Nat := A.n
def misleading : String := "A.h" -- A.h
def unavailable : Nat := B.n
''')
        result = self.load()
        by_name = {d.lean_name: d for d in result.workspace.declarations}
        self.assertFalse(by_name['localUse'].statement.deps)
        self.assertFalse(by_name['misleading'].statement.deps)
        self.assertFalse(by_name['unavailable'].statement.deps)
        self.assertEqual([d.provider for d in by_name['actual'].statement.deps], [by_name['A.n'].ref])

    def test_ambiguous_open_does_not_link_all_candidates(self):
        self.write('A.lean', 'namespace A\ndef n := 1\nend A\nnamespace B\ndef n := 2\nend B\n')
        self.write('M.lean', 'import A\nopen A B\ndef value := n\n')
        result = self.load()
        value = next(d for d in result.workspace.declarations if d.lean_name == 'value')
        self.assertFalse(value.statement.deps)
        self.assertTrue(any('text_reference_ambiguous:M:' in d for d in result.diagnostics))

    def test_failed_optional_compiler_keeps_source_and_eet(self):
        self.write('M.lean', 'def n := 1\n')
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter.collect', side_effect=RuntimeError('memory budget')):
            result = self.load(compiled_modules=('M',))
        self.assertEqual(len(result.workspace.declarations), 1)
        self.assertTrue(any('compiled_failed:M:memory budget' in d for d in result.diagnostics))
        self.assertTrue(build_hierarchy(result, 'r').nodes)

    def test_one_source_failure_preserves_other_files(self):
        self.write('A.lean', 'def n := 1\n')
        result = self.load(modules=('A', 'Missing'))
        self.assertEqual(len(result.workspace.declarations), 1)
        self.assertTrue(any('source_failed:Missing.lean' in d for d in result.diagnostics))

    def test_compiler_empty_dependencies_replace_text_edges(self):
        self.write('M.lean', 'def n := 1\ndef m := n\n')
        source_bundle = self.load()
        source = adapter_result_from_workspace(source_bundle.workspace, unit_aggregation='preserve',
                                                authority='text_ast', method='source')
        original = source_bundle.workspace.declarations[1]
        canonical = DeclRef('r', 'm')
        decl = replace(original, ref=canonical, statement=replace(original.statement, deps=()))
        from lean_exposition.importers.common import assemble_workspace
        workspace = assemble_workspace(repositories=source.repositories, assets=source.assets,
                                        scopes=source_bundle.workspace.scopes, declarations=(decl,))
        semantic = adapter_result_from_workspace(workspace, unit_aggregation='native_helpers',
                                                  authority='lean_environment', method='compiled', coverage=tuple(
                    CoverageContribution(canonical, 'statement', domain, 'complete', decl.provenance)
                    for domain in ('lean_type', 'lean_value')))
        result = build_repository(merge_adapters(source, semantic))
        self.assertEqual(len(result.workspace.declarations), 2)
        self.assertFalse(next(d for d in result.workspace.declarations if d.ref == canonical).statement.deps)
        self.assertTrue(next(d for d in result.workspace.declarations if d.lean_name == 'n').ref.local_id.startswith('source:'))

    def test_partial_semantic_dependencies_keep_source_fallback(self):
        self.write('M.lean', 'def n := 1\ndef m := n\n')
        bundle = self.load()
        source = adapter_result_from_workspace(bundle.workspace, unit_aggregation='preserve',
                                                authority='text_ast', method='source')
        m = next(d for d in bundle.workspace.declarations if d.lean_name == 'm')
        prov = m.provenance
        semantic = replace(source, declarations=(DeclarationContribution(CanonicalDeclLocator(m.ref), (
            FieldContribution('statement.deps', 'present', (), 'lean_environment', 'compiled', 'expr', prov),)),),
            coverage=(CoverageContribution(m.ref, 'statement', 'lean_type', 'complete', prov),
                      CoverageContribution(m.ref, 'statement', 'lean_value', 'unknown', prov)))
        result = build_repository(replace(source, declarations=source.declarations + semantic.declarations,
                                           coverage=semantic.coverage))
        self.assertTrue(next(d for d in result.workspace.declarations if d.ref == m.ref).statement.deps)

    def test_blueprint_explicit_statement_and_proof_fill_local_fields(self):
        from lean_exposition.structure.source import derive_tex_materials
        from lean_exposition.importers.materials import bind_blueprint
        self.write('M.lean', 'theorem t : True := by trivial\n')
        text = r"""\begin{theorem}
\label{main}
\lean{t}
Truth holds.
\end{theorem}
\begin{proof}
Use the unique constructor.
\end{proof}
"""
        def enrich(adapter, context):
            ref = adapter.declarations[0].locator.ref
            asset = asset_from_bytes('r', 'paper.tex', text.encode())
            material = derive_tex_materials(repo_key='r', assets=(asset,), files={'paper.tex': text},
                                             roots=('paper.tex',)).materials
            bound = bind_blueprint(material, {'t': ref})
            bound.validate()
            return attach_materials(adapter, (bound,))
        result = self.load(contributors=(enrich,))
        decl = result.workspace.declarations[0]
        self.assertIn('Truth holds', decl.statement.nl.text)
        self.assertIn('unique constructor', decl.proof.nl.text)
        self.assertTrue(build_hierarchy(result, 'r').nodes)

    def test_preexported_semantics_recovers_private_source_without_lean(self):
        from lean_exposition.importers.native import NativeRepositoryAdapter
        text = 'private def n : Nat := 1\n'
        self.write('M.lean', text)
        self.write('lean-toolchain', 'leanprover/lean4:v4.28.0')
        payload = {'source': {}, 'toolchain': 'leanprover/lean4:v4.28.0',
                   'source_digests': {'M': hashlib.sha256(text.encode()).hexdigest()},
                   'compiled': [{'name': '_private.M.0.n', 'user_name': 'n', 'module': 'M',
                                 'kind': 'definition', 'generator': None, 'type': [], 'value': []}]}
        with patch('lean_exposition.lean.extract_modules', side_effect=AssertionError('no Lean')):
            semantic = NativeRepositoryAdapter(self.root, repo_key='r', modules=('M',), payload=payload).collect()
        source = adapter_result_from_workspace(self.load(compiled_modules=()).workspace,
                                                unit_aggregation='preserve', authority='text_ast', method='source')
        result = build_repository(merge_adapters(source, semantic))
        self.assertEqual(len(result.workspace.declarations), 1)
        self.assertEqual(result.workspace.declarations[0].ref.local_id, '_private.M.0.n')
        self.assertIn('Nat := 1', result.workspace.declarations[0].statement.formal.text)

    def test_cyclic_references_retain_facts_and_allow_structure(self):
        self.write('M.lean', 'def a := b\ndef b := a\n')
        result = self.load()
        self.assertEqual(sum(len(d.statement.deps) for d in result.workspace.declarations), 2)
        hierarchy = build_hierarchy(result, 'r')
        self.assertTrue(hierarchy.nodes)
        self.assertTrue(any(d['code'] == 'cyclic_dependencies' for d in hierarchy.diagnostics))

    def test_custom_materials_and_core_fields_reach_reader(self):
        self.write('M.lean', 'theorem t : True := by trivial\n')
        def enrich(adapter, context):
            ref = adapter.declarations[0].locator.ref
            asset = asset_from_bytes('r', 'paper.md', b'# Argument\nThe proof is direct.')
            bundle = text_material(asset, '# Argument\nThe proof is direct.', bindings=(ref,), parser='markdown')
            adapter = attach_materials(adapter, (bundle,))
            prov = (Provenance('project_metadata', 'paper.md'),)
            field = FieldContribution('statement.nl', 'present', TextContent('Truth holds.', 'present', prov),
                                      'project_metadata', 'custom', 'read', prov)
            return replace(adapter, declarations=adapter.declarations + (DeclarationContribution(CanonicalDeclLocator(ref), (field,)),))
        result = self.load(contributors=(enrich,))
        decl = result.workspace.declarations[0]
        self.assertEqual(decl.statement.nl.text, 'Truth holds.')
        card = decl_card(result.workspace, decl.ref, proof=True)
        self.assertIn('proof is direct', card['additional_materials'][0]['text'])
        self.assertEqual(RepositoryBuildBundle.from_json(result.to_json()), result)
        hierarchy = build_hierarchy(result, 'r')
        self.assertTrue(hierarchy.nodes)

    def test_source_cache_skips_parser_and_invalidates_changes(self):
        self.write('M.lean', 'def n := 1\n')
        cache = self.root / '.cache'
        first = self.load(cache_dir=cache)
        second = self.load(cache_dir=cache)
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(len(list((cache / 'source').glob('*.json'))), 1)
        self.write('M.lean', 'def n := 2\n')
        third = self.load(cache_dir=cache)
        self.assertNotEqual(first.digest(), third.digest())
        self.assertEqual(len(list((cache / 'source').glob('*.json'))), 2)

    def test_profile_materials_and_missing_asset_are_local(self):
        # A profile consumes a pinned checkout; this test supplies a fixed Git snapshot.
        self.write('M.lean', 'def n := 1\n')
        self.write('paper.md', '# Result\nA useful number.\n')
        import subprocess
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        rev = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        text = (self.root / 'paper.md').read_bytes()
        profile = RepositoryProfile('p', RepositoryIdentity('r', rev), 'inventory',
            (ContributorSpec('source', 'source_inventory', True, {'source_roots': ['M.lean']}),),
            (MaterialAssetSpec('paper', 'paper.md', hashlib.sha256(text).hexdigest(), 'text/markdown',
                               'markdown', 'names', 'primary', {'declarations': ['n']}),
             MaterialAssetSpec('missing', 'absent.md', 'a'*64, 'text/plain', 'plain', 'names', 'supporting', {})),
            (DeclarationLocatorSpec('n'),), (), (), (), ())
        result = self.load(profile=profile)
        self.assertEqual(len(result.materials), 1)
        self.assertTrue(result.workspace.declarations[0].source_context)
        self.assertTrue(any('material_failed:absent.md' in d for d in result.diagnostics))
        self.assertTrue(build_hierarchy(result, 'r').nodes)


    def test_archive_published_metadata_and_edges_reach_shared_pipeline(self):
        def fnv(name):
            value = 2166136261
            for byte in name.encode():
                value = ((value ^ byte) * 16777619) & 0xffffffff
            return str(value % 256)
        name = 'Demo.Main'
        shard = fnv(name)
        texts = {
            'meta': 'window.FLT_META=' + json.dumps({'names': [name, 'Demo.Helper'], 'root': 0}) + ';',
            'edges': 'window.FLT_EDGES={off:[0,1,1],dst:[1]};',
            'titles': 'window.FLT_TITLES=["Main","Helper"];',
            'shard': 'FLT_SHARD_CB("' + shard + '",' + json.dumps({name: {
                'dc': 'theorem Demo.Main : True', 'en': {'statement_html': '<p>Truth.</p>',
                                                       'proof_html': '<p>Use the helper.</p>'}}}) + ');',
        }
        specs = []
        for key, text in texts.items():
            self.write(key + '.js', text)
            config = {'javascript_global': 'FLT_' + key.upper()} if key != 'shard' else {'callback': 'FLT_SHARD_CB', 'shard_id': shard}
            specs.append(MaterialAssetSpec(key, key + '.js', hashlib.sha256(text.encode()).hexdigest(),
                         'application/javascript', 'published_bundle', 'published', 'primary', config))
        profile = RepositoryProfile('p', RepositoryIdentity('r', 'a'*40), 'provisional',
            (ContributorSpec('published', 'published', True, {'selected_declarations': [name]}),),
            tuple(specs), (DeclarationLocatorSpec(name),), (), (), (), ())
        result = self.load(profile=profile)
        self.assertFalse([d for d in result.diagnostics if 'material_published_failed' in d])
        declaration = result.workspace.declarations[0]
        self.assertEqual(declaration.statement.nl.text, 'Truth.')
        self.assertEqual(declaration.proof.nl.text, 'Use the helper.')
        self.assertEqual(declaration.kind, 'theorem')
        hierarchy = build_hierarchy(result, 'r')
        self.assertEqual(declaration.statement.deps[0].evidence_kind, 'published')
        self.assertTrue(hierarchy.external_refs)
        ContentStore(result.workspace, hierarchy, self.root / 'content')

    def test_source_only_overrides_compiled_profile_and_preserves_order_hints(self):
        from lean_exposition.construction import OrderHint, UnitHint
        self.write('M.lean', 'def first := 1\ndef second := 2\n')
        profile = RepositoryProfile('p', RepositoryIdentity('r', 'a'*40), 'formal',
            (ContributorSpec('source', 'source_inventory', True, {'source_roots': ['M.lean']}),
             ContributorSpec('compiled', 'compiled', True, {'modules': ['M']})), (), (), (), (),
            (OrderHint('route', (DeclarationLocatorSpec('second'), DeclarationLocatorSpec('first')),
                       'protected', 'Author route'),), ())
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter.collect') as compiler:
            result = self.load(profile=profile, compiled_modules=())
            compiler.assert_not_called()
        hierarchy = build_hierarchy(result, 'r')
        self.assertTrue(result.materials)
        self.assertTrue(hierarchy.nodes)
        self.assertTrue(any('route' in str(n['metadata'].get('narrative_order', {})) for n in hierarchy.nodes))

    def test_bundle_cache_skips_semantic_normalization(self):
        self.write('M.lean', 'def n := 1\n')
        base = self.load(compiled_modules=())
        semantic = adapter_result_from_workspace(base.workspace, unit_aggregation='native_helpers',
                                                  authority='lean_environment', method='compiled')
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter.collect', return_value=semantic) as compiler:
            first = self.load(cache_dir=self.root / '.cache', compiled_modules=('M',), build=False)
            self.assertEqual(compiler.call_count, 1)
        with patch('lean_exposition.importers.project.NativeRepositoryAdapter.collect', side_effect=AssertionError('cache miss')):
            second = self.load(cache_dir=self.root / '.cache', compiled_modules=('M',), build=False)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.root / '.cache/projects').glob('*.json'))), 1)

    def test_invalid_cache_directory_does_not_masquerade_as_source_failure(self):
        self.write('M.lean', 'def n := 1\n')
        self.write('cache', 'a regular file')
        with self.assertRaisesRegex(ValueError, 'cache_dir'):
            self.load(cache_dir=self.root / 'cache')


if __name__ == '__main__':
    unittest.main()
