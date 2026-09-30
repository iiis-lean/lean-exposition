import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('export_cli', Path(__file__).resolve().parents[3] / 'scripts/export_compiled.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class QueueTests(unittest.TestCase):
    def test_paused_queue_does_not_start_later_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'projects': [dict(name=name, root='/source', lean='/lean', modules=['A']) for name in ['first', 'second']]}))
            with patch.object(cli, 'export_compiled', return_value=dict(status='paused', pause_reason='shared_rss_limit', completed={'A': {}}, failed={}, last_run_seconds=1)) as export:
                report = cli.queue(plan, root / 'output', 100)
            self.assertEqual(export.call_count, 1)
            self.assertEqual(report['status'], 'paused')
            self.assertEqual(report['reason'], 'shared_rss_limit')
            self.assertEqual(len(report['projects']), 1)

    def test_failed_project_is_recorded_and_does_not_erase_other_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'projects': [dict(name=name, root='/source', lean='/lean', modules=['A']) for name in ['bad', 'good']]}))
            results = [ValueError('missing dependency'), dict(status='complete', completed={'A': {}}, failed={}, last_run_seconds=1)]
            with patch.object(cli, 'export_compiled', side_effect=results):
                report = cli.queue(plan, root / 'output', 100)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual([row['status'] for row in report['projects']], ['failed', 'complete'])
            self.assertIn('missing dependency', report['projects'][0]['error'])

    def test_changed_plan_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'projects': []}))
            cli.queue(plan, root / 'output', 100)
            plan.write_text(json.dumps({'projects': [], 'changed': True}))
            with patch.object(cli, 'export_compiled', side_effect=AssertionError('changed plan')):
                with self.assertRaisesRegex(ValueError, 'plan or frozen exporter changed'):
                    cli.queue(plan, root / 'output', 100)
