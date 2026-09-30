"""Automatic source-only project indexing through the public loading entrypoint."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.importers import load_project
from lean_exposition.structure import DependencyGraph
from lean_exposition.structure.dependencies import (
    DependencyAnalysisPolicy, FoundationCatalog, FoundationEntry,
    ProviderFoundation, ProviderIdentity, load_foundation_catalog,
)


class ProjectReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('lean-toolchain', 'leanprover/lean4:v4.28.0\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def manifest(self, packages=None, packages_dir='.lake/packages'):
        self.write('lake-manifest.json', json.dumps({'packagesDir': packages_dir, 'packages': packages or [
            {'name': 'mathlib', 'type': 'git', 'rev': 'b' * 40, 'subDir': None}]}))

    def load(self, **kwargs):
        kwargs.setdefault('modules', ('Main',))
        kwargs.setdefault('compiled_modules', ())
        with patch('subprocess.Popen', side_effect=AssertionError('source-only loading started a process')):
            return load_project(self.root, repo_key='r', **kwargs)

    def dependencies(self, bundle):
        return [dep for d in bundle.workspace.declarations for part in (d.statement, d.proof)
                if part for dep in part.deps]

    def test_import_closure_is_automatic_and_does_not_expand_export_scope(self):
        self.manifest()
        self.write('Main.lean', 'import Bridge\ndef use := Shared.item\n')
        self.write('Bridge.lean', 'import Library\ndef helper := 1\n')
        self.write('.lake/packages/mathlib/Library.lean', 'namespace Shared\ndef item := 1\nend Shared\n')
        result = self.load()
        self.assertEqual([d.lean_name for d in result.workspace.declarations], ['use'])
        self.assertEqual([(d.provider.repo_key, d.provider.local_id, d.provider_module)
                          for d in self.dependencies(result)], [('r/dependency/mathlib', 'Shared.item', 'Library')])
        provider = next(r for r in result.workspace.manifest.repositories if r.repo_key == 'r/dependency/mathlib')
        self.assertEqual(provider.revision, 'b' * 40)
        self.assertIsNone(provider.input_digest)
        self.assertTrue(any(lock.dependency_repo_key == provider.repo_key for lock in result.workspace.manifest.dependency_locks))
        assets = {a.path: a for a in result.workspace.manifest.assets}
        self.assertEqual(assets['lake-manifest.json'].sha256,
                         hashlib.sha256((self.root / 'lake-manifest.json').read_bytes()).hexdigest())
        self.assertTrue(all(e.status == 'partial' for e in result.dependency_coverage.entries
                            if e.evidence_domain == 'text_reference'))

    def test_normal_load_enables_module_filter_and_exact_revision_keep(self):
        self.manifest()
        self.write('Main.lean', 'import Mathlib.Data.Finset.Filter Mathlib.Analysis.Real.Sqrt\ndef Lean.custom := True\ndef use := (Finset.filter, Finset.special, Real.sqrt, Lean.custom)\n')
        self.write('.lake/packages/mathlib/Mathlib/Data/Finset/Filter.lean', 'namespace Finset\ndef filter := True\ndef special := True\nend Finset\n')
        self.write('.lake/packages/mathlib/Mathlib/Analysis/Real/Sqrt.lean', 'namespace Real\ndef sqrt := True\nend Real\n')
        result = self.load()
        provider = next(r for r in result.workspace.manifest.repositories if r.repo_key == 'r/dependency/mathlib')
        catalog = FoundationCatalog((ProviderFoundation(ProviderIdentity.from_repository(provider), 'a' * 64, 'b' * 64,
            (FoundationEntry('Finset.special', 'reviewed_keep', ('proof', 'statement'),
                             'Mathematical interface.', 'reviewed:fixture'),)),),
            load_foundation_catalog().module_rules)
        graph = DependencyGraph.from_workspace(result.workspace)
        analysis = DependencyAnalysisPolicy(catalog).analyze(result.workspace, 'r', graph.edges)
        decisions = {d.provider.local_id: d for d in analysis.decisions}
        self.assertEqual(len(decisions), 4)
        self.assertFalse(decisions['Finset.filter'].keep)
        self.assertEqual(decisions['Finset.special'].reason, 'reviewed_keep')
        # Extraction must retain explicit mathematics independently of whether
        # a downstream policy treats familiar background as summary context.
        self.assertIn('Real.sqrt', decisions)
        self.assertTrue(all(d.keep for d in analysis.decisions if d.provider.repo_key == 'r'))

    def test_missing_dependency_source_is_partial_and_diagnosed(self):
        self.manifest()
        self.write('Main.lean', 'import Library\ndef local := 1\ndef use := local + Shared.item\n')
        result = self.load()
        self.assertEqual(len(result.workspace.declarations), 2)
        self.assertEqual(len(self.dependencies(result)), 1)
        self.assertTrue(any('text_index_missing_dependency_source:mathlib' in d for d in result.diagnostics))
        self.assertTrue(any('text_index_missing_module:r:Main:Library' in d for d in result.diagnostics))

    def test_unfixed_dependency_is_never_assigned_a_guessed_revision(self):
        self.manifest([{'name': 'mathlib', 'type': 'git', 'rev': 'main'}])
        self.write('Main.lean', 'import Library\ndef use := Shared.item\n')
        self.write('.lake/packages/mathlib/Library.lean', 'def Shared.item := 1\n')
        result = self.load()
        self.assertFalse(self.dependencies(result))
        self.assertTrue(any('text_index_unfixed_dependency:mathlib' in d for d in result.diagnostics))
        self.assertFalse(any(r.repo_key.endswith('/mathlib') for r in result.workspace.manifest.repositories))

    def test_custom_packages_directory_and_subdirectory_follow_manifest(self):
        self.manifest([{'name': 'mathlib', 'type': 'git', 'rev': 'b' * 40, 'subDir': 'lean'}], '.vendor')
        self.write('Main.lean', 'import Library\ndef use := Shared.item\n')
        self.write('.vendor/mathlib/lean/Library.lean', 'def Shared.item := 1\n')
        result = self.load()
        self.assertEqual(self.dependencies(result)[0].provider.local_id, 'Shared.item')

    def test_unreadable_dependency_module_keeps_target_partial(self):
        self.manifest()
        self.write('Main.lean', 'import Library\ndef use := Shared.item\n')
        self.write('.lake/packages/mathlib/Library.lean', '')
        (self.root / '.lake/packages/mathlib/Library.lean').write_bytes(b'\xff')
        result = self.load()
        self.assertEqual(len(result.workspace.declarations), 1)
        self.assertFalse(self.dependencies(result))
        self.assertTrue(any(d.startswith('text_index_source_failed:') for d in result.diagnostics))

    def test_invalid_manifest_keeps_project_imports(self):
        self.write('lake-manifest.json', '[{}]')
        self.write('Main.lean', 'import Other\ndef use := shared\n')
        self.write('Other.lean', 'def shared := 1\n')
        result = self.load()
        self.assertEqual(self.dependencies(result)[0].provider_module, 'Other')
        self.assertTrue(any(d.startswith('text_index_manifest_failed:') for d in result.diagnostics))

    def test_manifest_source_path_cannot_escape_project_root(self):
        self.manifest([{'name': 'mathlib', 'type': 'git', 'rev': 'b' * 40,
                        'subDir': '../../outside'}], '../vendor')
        self.write('Main.lean', 'def use := 1\n')
        result = self.load()
        self.assertFalse(any(r.repo_key.endswith('/mathlib') for r in result.workspace.manifest.repositories))
        self.assertTrue(any('source path escapes project root' in d for d in result.diagnostics))

    def test_warm_load_reuses_index_and_dependency_edits_invalidate_bundle(self):
        self.manifest()
        self.write('Main.lean', 'import Library\ndef use := Shared.item\n')
        self.write('.lake/packages/mathlib/Library.lean', 'def Shared.item := 1\n')
        cache = self.root / '.cache'
        first = self.load(cache_dir=cache)
        with patch('lean_exposition.importers.source.consume_text_ast_json', side_effect=AssertionError('index cache miss')):
            second = self.load(cache_dir=cache)
        self.assertEqual(first.digest(), second.digest())
        self.write('.lake/packages/mathlib/Library.lean', 'def Shared.replaced := 1\n')
        third = self.load(cache_dir=cache)
        self.assertNotEqual(first.digest(), third.digest())
        self.assertFalse(self.dependencies(third))

    def test_selected_project_can_index_imports_without_a_git_revision(self):
        self.write('Main.lean', 'import Other\ndef use := shared\n')
        self.write('Other.lean', 'def shared := 1\n')
        result = self.load()
        self.assertEqual(len(result.workspace.declarations), 1)
        dep = self.dependencies(result)[0]
        self.assertEqual(dep.provider.repo_key, 'r')
        self.assertEqual(dep.provider_module, 'Other')
        self.assertTrue(dep.provider.local_id.startswith('source:'))
        target = result.workspace.manifest.repositories[0]
        self.assertIsNone(target.revision)
        self.assertIsNotNone(target.input_digest)


if __name__ == '__main__':
    unittest.main()
