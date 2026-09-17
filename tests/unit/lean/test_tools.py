from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from lean_exposition.lean import extract_modules


class ToolBoundaryTests(unittest.TestCase):
    def test_lean_interact_selection_retains_semantic_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('leanprover/lean4:v4.28.0')
            source = {'M': {'declarations': []}}
            with patch('lean_exposition.lean.tools._query', return_value=[]) as query, patch(
                    'lean_exposition.lean.tools.extract_sources', return_value=source) as extract:
                result = extract_modules(root, ('M',), source_backend='lean_interact', repl_rev='test')
            self.assertEqual(query.call_count, 1)
            self.assertEqual(result['source'], source)
            self.assertEqual(result['source_backend'], 'lean_interact')
            self.assertEqual(extract.call_args.kwargs['source_backend'], 'lean_interact')
            self.assertEqual(extract.call_args.kwargs['repl_rev'], 'test')

    def test_lean_interact_server_is_closed_on_source_errors(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from lean_exposition.lean.tools import extract_sources
        server = MagicMock()
        server.run.return_value.model_dump.return_value = {'messages': [{'severity': 'error', 'data': 'bad'}]}
        fake = SimpleNamespace(FileCommand=MagicMock(), LeanREPLConfig=MagicMock(),
                               LeanServer=MagicMock(return_value=server))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'lean-toolchain').write_text('leanprover/lean4:v4.28.0')
            with patch.dict('sys.modules', {'lean_interact': fake,
                    'lean_interact.project': SimpleNamespace(LocalProject=MagicMock())}):
                with self.assertRaisesRegex(RuntimeError, 'source extraction failed'):
                    extract_sources(root, ('M',), source_backend='lean_interact')
        server.kill.assert_called_once()

    def test_empty_or_duplicate_selection_never_builds(self):
        with patch('lean_exposition.lean.tools._run') as run:
            for modules in ((), ('M', 'M')):
                with self.assertRaisesRegex(ValueError, 'nonempty'):
                    extract_modules('/unused', modules)
            run.assert_not_called()

    def test_artifact_query_does_not_require_repl_mapping_or_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('leanprover/lean4:v9.0.0')
            with patch('lean_exposition.lean.tools._run') as build, patch(
                    'lean_exposition.lean.tools._query', return_value=[]) as query:
                result = extract_modules(root, ('M',), include_source=False)
                build.assert_not_called()
                self.assertEqual(query.call_count, 1)
                self.assertFalse(result['build_requested'])

    def test_cache_invalidates_source_and_artifact_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('lean:v4.32')
            (root / '.lake').mkdir()
            artifact = root / '.lake/M.olean'
            artifact.write_bytes(b'one')
            with patch('lean_exposition.lean.tools._query', return_value=[]) as query:
                options = dict(include_source=False, cache_dir=root / 'cache')
                extract_modules(root, ('M',), **options)
                extract_modules(root, ('M',), **options)
                self.assertEqual(query.call_count, 1)
                artifact.write_bytes(b'changed')
                extract_modules(root, ('M',), **options)
                (root / 'M.lean').write_text('def x := 2')
                extract_modules(root, ('M',), **options)
                self.assertEqual(query.call_count, 3)

    def test_build_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('lean:v4.32')
            with patch('lean_exposition.lean.tools._run', return_value=subprocess.CompletedProcess([], 0, '', '')) as build, patch(
                    'lean_exposition.lean.tools._query', return_value=[]):
                extract_modules(root, ('M',), build=True, include_source=False)
                self.assertEqual(build.call_args.args[0], ['lake', 'build', '+M'])
