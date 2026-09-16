import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess

from lean_exposition.importers.native import normalize_native, slice_source, source_range


class NativeTests(unittest.TestCase):
    def test_unicode_columns(self):
        text = 'def «😀α» := 1\n'
        span = source_range('x', {'start': {'line': 1, 'column': 4},
                                  'finish': {'line': 1, 'column': 8}})
        self.assertEqual(slice_source(text, span), '«😀α»')

    def test_original_proof_definition_docstring_private_and_expr_edges(self):
        text = '/-- whole doc -/\ntheorem t : True := by trivial\nprivate def x := 7\n'
        def span(a, b, c, d):
            return {'start': {'line': a, 'column': b}, 'finish': {'line': c, 'column': d}}
        authors = [
            {'name': 't', 'fullName': 't', 'kind': 'theorem', 'range': span(1, 0, 2, 30),
             'modifiers': {'docString': {'content': '/-- whole doc -/', 'range': span(1, 0, 1, 16)}},
             'scope': {'varDecls': ['variable (α : Type)']},
             'value': {'range': span(2, 20, 2, 30)}},
            {'name': 'x', 'fullName': '_private.0.x', 'kind': 'definition', 'range': span(3, 0, 3, 18),
             'scope': {'currNamespace': ''}, 'modifiers': {}}]
        compiled = [
            {'name': 't', 'user_name': 't', 'module': 'M', 'kind': 'theorem', 'generator': None,
             'type': [{'name': 'True', 'module': 'Init.Prelude'}],
             'value': [{'name': 'True.intro', 'module': 'Init.Prelude'}]},
            {'name': '_private.M.0.x', 'user_name': 'x', 'module': 'M', 'kind': 'definition',
             'generator': None, 'type': [], 'value': []}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text(text)
            (root / 'lean-toolchain').write_text('leanprover/lean4:v4.28.0')
            payload = {'source': {'M': {'declarations': authors}}, 'compiled': compiled,
                       'toolchain': 'leanprover/lean4:v4.28.0',
                       'source_digests': {'M': hashlib.sha256(text.encode()).hexdigest()}}
            with patch('lean_exposition.importers.native.subprocess.run', return_value=
                       subprocess.CompletedProcess([], 0, str(root.parent), '')) as git:
                nested = normalize_native(root, repo_key='p', modules=('M',), payload=payload)
                self.assertIsNone(nested.manifest.repositories[0].revision)
                self.assertEqual(git.call_count, 1)
            workspace = normalize_native(root, repo_key='p', modules=('M',), payload=payload)
            theorem = next(d for d in workspace.declarations if d.lean_name == 't')
            definition = next(d for d in workspace.declarations if d.kind == 'definition')
            self.assertEqual(theorem.proof.formal.text, 'by trivial')
            self.assertEqual(theorem.statement.nl.text, '/-- whole doc -/')
            self.assertEqual(theorem.proof.nl.status, 'missing')
            self.assertEqual(theorem.proof.deps[0].evidence_kind, 'lean_value')
            self.assertEqual(theorem.statement.deps[0].evidence_kind, 'lean_type')
            self.assertIn('α', theorem.source_context[0].text)
            self.assertEqual(definition.statement.formal.text, 'private def x := 7')
            self.assertEqual(len(workspace.units), 2)
            authors[0]['range']['synthetic'] = True
            compiled[0]['range'] = span(1, 0, 2, 30)
            macro = normalize_native(root, repo_key='p', modules=('M',), payload=payload)
            macro_theorem = next(d for d in macro.declarations if d.lean_name == 't')
            self.assertEqual(macro_theorem.proof.formal.text, 'by trivial')
            self.assertTrue(macro_theorem.source_refs)
            # LeanInteract can report an unrelated fullName for an anonymous instance.
            authors[1]['name'] = '[anonymous]'
            authors[1]['fullName'] = 'Nat'
            compiled[1]['range'] = span(3, 0, 3, 18)
            remapped = normalize_native(root, repo_key='p', modules=('M',), payload=payload)
            self.assertEqual({d.lean_name for d in remapped.declarations}, {'t', '_private.M.0.x'})
            with self.assertRaisesRegex(ValueError, 'primary outcomes'):
                normalize_native(root, repo_key='p', modules=('M',), payload=payload, primary_outcomes=('absent',))
            (root / 'M.lean').write_text(text + '\n')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                normalize_native(root, repo_key='p', modules=('M',), payload=payload)


if __name__ == '__main__':
    unittest.main()
