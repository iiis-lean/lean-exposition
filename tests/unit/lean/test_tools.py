from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.lean import extract_modules
from lean_exposition.lean.tools import export_compiled, load_compiled, _query, QueryFailure


FAKE_LEAN = r'''import json, pathlib, re, sys, time, subprocess, os
if '--version' in sys.argv:
    print('Lean (version 4.32.0, test)')
    raise SystemExit
sys.stdout = open(os.environ['LEAN_EXPOSITION_OUTPUT'], 'w')
query = pathlib.Path(sys.argv[-1]).read_text()
modules = json.loads('[' + re.search(r'let selected : Array String := #\[(.*?)\]', query)[1] + ']')
mode = pathlib.Path('mode').read_text() if pathlib.Path('mode').exists() else ''
if mode == 'conflict' and len(modules) > 1:
    print('conflicting imports', flush=True)
    raise SystemExit(1)
for i, module in enumerate(modules):
    print('LEAN_EXPOSITION_BEGIN ' + json.dumps(module), flush=True)
    fact = dict(name=module+'.x', user_name=module+'.x', module=module, kind='definition',
                generator=None, range=None, type=[], value=[], type_text='ℕ', docstring=None)
    line = 'LEAN_EXPOSITION_JSON ' + json.dumps(fact, ensure_ascii=False)
    print(line, flush=True)
    if mode == 'partial':
        raise SystemExit(1)
    print('LEAN_EXPOSITION_END ' + json.dumps(dict(module=module, records=1,
          bytes=len(line.encode())+1, elapsed_ms=1)), flush=True)
    if mode == 'timeout' and i == 0:
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        pathlib.Path('child-pid').write_text(str(child.pid))
        time.sleep(30)
'''


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        (self.root / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0')
        for module in ('A', 'B', 'Challenge', 'Solution'):
            (self.root / (module + '.lean')).write_text('def x := 1')
        self.compiler = self.base / 'lean'
        self.compiler.write_text('#!' + sys.executable + '\n' + FAKE_LEAN)
        self.compiler.chmod(0o755)
        self.package = self.base / 'facts'

    def export(self, modules=('A', 'B'), **kwargs):
        return export_compiled(self.root, modules, self.package, lean_binary=self.compiler,
                               min_free_bytes=0, **kwargs)

    def test_single_and_batch_match_with_separate_type_text_comparison(self):
        self.export(batch_size=1)
        single = load_compiled(self.root, self.package)
        self.package = self.base / 'batch'
        result = self.export(batch_size=2)
        batch = load_compiled(self.root, self.package)
        self.assertEqual(single['compiled'], batch['compiled'])
        self.assertEqual([f['type_text'] for f in single['compiled']], [f['type_text'] for f in batch['compiled']])
        self.assertEqual(len(result['attempts']), 1)
        self.assertNotIn('source', result['identity'])

    def test_conflicts_split_once_to_singletons(self):
        (self.root / 'mode').write_text('conflict')
        result = self.export(batch_size=2)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([r['requested'] for r in result['attempts']], [['A', 'B'], ['A'], ['B']])

    def test_challenge_solution_are_isolated(self):
        result = self.export(('Challenge', 'Solution'), batch_size=6)
        self.assertEqual([r['requested'] for r in result['attempts']], [['Challenge'], ['Solution']])

    def test_partial_output_never_publishes_or_hits(self):
        (self.root / 'mode').write_text('partial')
        result = self.export(('A',))
        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse((self.package / 'modules/A.json').exists())
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            load_compiled(self.root, self.package)
        (self.root / 'mode').unlink()
        self.assertEqual(self.export(('A',))['status'], 'complete')

    def test_flush_checkpoint_survives_timeout_and_resume_skips_it(self):
        (self.root / 'mode').write_text('timeout')
        # A completes and flushes, then the same process (and child) exceeds the task bound.
        result = self.export(batch_size=2, timeout=.5, task_timeout=.4)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(set(result['completed']), {'A'})
        pid = int((self.root / 'child-pid').read_text())
        status = Path(f'/proc/{pid}/status')
        self.assertTrue(not status.exists() or 'State:\tZ' in status.read_text())
        (self.root / 'mode').unlink()
        result = self.export(batch_size=2)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['attempts'][-1]['requested'], ['B'])
        self.assertNotIn('pause_reason', result)

    def test_rss_guard_kills_only_query_process_group(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.terminate)
        probe = self.base / 'query.lean'
        probe.write_text('let selected : Array String := #["A"]')
        (self.root / 'mode').write_text('timeout')
        with self.assertRaises(QueryFailure) as failure:
            _query([str(self.compiler), str(probe)], cwd=self.root, timeout=5, modules=('A',), rss_limit_bytes=1)
        self.assertEqual(failure.exception.reason, 'process_tree_rss_limit')
        self.assertIsNone(unrelated.poll())

    def test_corrupt_cache_is_rejected_not_silently_queried(self):
        self.export()
        path = self.package / 'modules/A.json'
        path.write_text(path.read_text().replace('ℕ', 'ℤ'))
        with patch('subprocess.Popen', side_effect=AssertionError('must not query')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                load_compiled(self.root, self.package)

    def test_offline_relocation_and_new_text_leave_raw_facts_unchanged(self):
        self.export()
        moved = self.base / 'moved'
        shutil.copytree(self.root, moved)
        before = (self.package / 'modules/A.json').read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('offline')):
            payload = load_compiled(moved, self.package)
            payload['source'] = {'A': {'declarations': ['new parser result']}}
            self.assertEqual(len(payload['compiled']), 2)
        self.assertEqual(before, (self.package / 'modules/A.json').read_bytes())

    def test_dependency_content_change_refuses_offline_reuse(self):
        package = self.root / '.lake/packages/dep'
        (package / '.git').mkdir(parents=True)
        (package / '.git/HEAD').write_text('a' * 40)
        (package / 'D.lean').write_text('def d := 1')
        (self.root / 'lake-manifest.json').write_text(json.dumps({'packages': [{'name': 'dep', 'rev': 'a' * 40}]}))
        self.export()
        (package / 'D.lean').write_text('def d := 2')
        with patch('subprocess.Popen', side_effect=AssertionError('offline')):
            with self.assertRaisesRegex(ValueError, 'sources changed'):
                load_compiled(self.root, self.package)

    def test_input_and_dependency_change_refuse_reuse(self):
        self.export()
        (self.root / 'A.lean').write_text('def x := 2')
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            load_compiled(self.root, self.package)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.export()

    def test_dependency_identity_mismatch_stops_before_compiler(self):
        package = self.root / '.lake/packages/dep'
        (package / '.git').mkdir(parents=True)
        (package / '.git/HEAD').write_text('b' * 40)
        (self.root / 'lake-manifest.json').write_text(json.dumps({'packages': [{'name': 'dep', 'rev': 'a' * 40}]}))
        with patch('subprocess.Popen', side_effect=AssertionError('preflight first')):
            with self.assertRaisesRegex(ValueError, 'dependency revision mismatch'):
                self.export()

    def test_toolchain_mismatch_rejected(self):
        (self.root / 'lean-toolchain').write_text('leanprover/lean4:v4.28.0')
        with self.assertRaisesRegex(ValueError, 'toolchain mismatch'):
            self.export()

    def test_disk_and_shared_memory_pause_without_starting_queries(self):
        with patch('lean_exposition.lean.tools._query', side_effect=AssertionError('guard first')):
            with patch('lean_exposition.lean.tools.shutil.disk_usage', return_value=type('Disk', (), {'free': -1})()):
                self.assertEqual(self.export()['pause_reason'], 'disk_space_limit')
            with patch('lean_exposition.lean.tools._shared_rss', return_value=100):
                self.assertEqual(self.export(shared_rss_limit_bytes=1)['pause_reason'], 'shared_rss_limit')

    def test_cache_invalidates_source_and_artifact_changes(self):
        options = dict(include_source=False, cache_dir=self.base / 'cache', lean_binary=self.compiler)
        extract_modules(self.root, ('A',), **options)
        with patch('subprocess.Popen', side_effect=AssertionError('cache must be offline')):
            extract_modules(self.root, ('A',), **options)
        artifact = self.root / '.lake/build/lib/lean/A.olean'
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b'changed')
        extract_modules(self.root, ('A',), **options)
        (self.root / 'A.lean').write_text('def x := 2')
        extract_modules(self.root, ('A',), **options)
        self.assertEqual(len(list((self.base / 'cache/compiled').iterdir())), 3)

    def test_lean_interact_selection_retains_semantic_query(self):
        source = {'A': {'declarations': []}}
        with patch('lean_exposition.lean.tools.extract_sources', return_value=source) as extract:
            result = extract_modules(self.root, ('A',), source_backend='lean_interact', repl_rev='test', lean_binary=self.compiler)
        self.assertEqual(result['source'], source)
        self.assertEqual(result['source_backend'], 'lean_interact')
        self.assertEqual(extract.call_args.kwargs['repl_rev'], 'test')

    def test_lean_interact_server_is_closed_on_source_errors(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from lean_exposition.lean.tools import extract_sources
        server = MagicMock()
        server.run.return_value.model_dump.return_value = {'messages': [{'severity': 'error', 'data': 'bad'}]}
        fake = SimpleNamespace(FileCommand=MagicMock(), LeanREPLConfig=MagicMock(), LeanServer=MagicMock(return_value=server))
        with patch.dict('sys.modules', {'lean_interact': fake,
                'lean_interact.project': SimpleNamespace(LocalProject=MagicMock())}):
            with self.assertRaisesRegex(RuntimeError, 'source extraction failed'):
                extract_sources(self.root, ('A',), source_backend='lean_interact')
        server.kill.assert_called_once()

    def test_empty_or_duplicate_selection_never_builds(self):
        with patch('lean_exposition.lean.tools._run') as run:
            for modules in ((), ('A', 'A')):
                with self.assertRaisesRegex(ValueError, 'nonempty'):
                    extract_modules('/unused', modules)
            run.assert_not_called()

    def test_build_is_explicit(self):
        with patch('lean_exposition.lean.tools._run', return_value=subprocess.CompletedProcess([], 0, '', '')) as build:
            extract_modules(self.root, ('A',), build=True, include_source=False, lean_binary=self.compiler)
        self.assertEqual(build.call_args.args[0], ['lake', 'build', '+A'])
