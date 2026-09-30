import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('queue_runner', Path(__file__).resolve().parents[2] / 'scripts/native_build_queue.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class QueueTests(unittest.TestCase):
    def run_case(self, code, usage=0, task_limit=runner.GIB, timeout=3):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mem = root / 'memory'
            mem.write_text(str(usage))
            return runner.run_step({'name': 'test', 'argv': [sys.executable, '-c', code],
                                    'cwd': tmp, 'timeout_seconds': timeout},
                                   root / 'log', mem, task_limit, 2 * runner.GIB, poll=0.02)

    def test_success(self):
        self.assertEqual(self.run_case('print(42)')['status'], 'success')

    def test_failure(self):
        self.assertEqual(self.run_case('raise SystemExit(7)')['exit_code'], 7)

    def test_container_pressure_prevents_spawn(self):
        self.assertEqual(self.run_case('raise Exception()', usage=3 * runner.GIB)['status'],
                         'container_limit_before_start')

    def test_container_pressure_during_run(self):
        code = "from pathlib import Path; import time; Path('memory').write_text(str(3 * 1024**3)); time.sleep(3)"
        self.assertEqual(self.run_case(code)['status'], 'container_limit')

    def test_task_memory(self):
        self.assertEqual(self.run_case('import time; x=bytearray(20000000); time.sleep(3)', task_limit=10_000_000)['status'], 'task_limit')

    def test_timeout(self):
        self.assertEqual(self.run_case('import time; time.sleep(3)', timeout=0.1)['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()
