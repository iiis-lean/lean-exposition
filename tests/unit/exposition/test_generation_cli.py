import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from lean_exposition.runtime import ApiConfig, ExecutionResult


class GenerationEvidenceTests(unittest.TestCase):
    def test_math_reader_example_uses_official_flash_responses(self):
        path = Path(__file__).resolve().parents[3] / 'configs' / 'math-reader.api.example.json'
        config = ApiConfig(**json.loads(path.read_text()))
        self.assertEqual(config.base_url, 'https://api.deepseek.com')
        self.assertEqual(config.model, 'deepseek-flash')
        self.assertEqual(config.protocol, 'responses')
        self.assertEqual(config.max_output_tokens, 32768)

    def test_resume_retains_unfinished_request_number(self):
        path = Path(__file__).resolve().parents[3] / 'scripts' / 'math_reader_generate.py'
        spec = importlib.util.spec_from_file_location('math_reader_generation_cli', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'calls'
            directory.mkdir()
            (directory / 'call-0000-result.json').write_text('{}')
            pending = directory / 'call-0001-request.json'
            pending.write_text('{"interrupted": true}')
            runtime = module.RecordedRuntime(ApiConfig(model='test', credential_env='TEST_API_KEY'), directory)
            self.assertEqual(runtime.counter, 2)
            self.assertEqual(pending.read_text(), '{"interrupted": true}')

    def test_output_budget_failure_does_not_repeat_unchanged_request(self):
        from lean_exposition.runtime import ApiError, ExecutionResult
        from unittest.mock import Mock
        path = Path(__file__).resolve().parents[3] / 'scripts' / 'math_reader_generate.py'
        spec = importlib.util.spec_from_file_location('math_reader_generation_cli_length', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'calls'
            recorded = module.RecordedRuntime(ApiConfig(model='test', credential_env='TEST_API_KEY'), directory)
            recorded.executor = Mock()
            recorded.executor.execute.return_value = ExecutionResult('failed', raw_text='', error=ApiError('length'))
            with self.assertRaisesRegex(RuntimeError, 'output budget exhausted'):
                recorded('unchanged input', {})
            self.assertEqual(recorded.executor.execute.call_count, 1)
            self.assertTrue((directory / 'call-0000-result.json').exists())
            self.assertFalse((directory / 'call-0001-request.json').exists())

    def test_metadata_call_does_not_assume_content_prompt_payload(self):
        from unittest.mock import Mock
        path = Path(__file__).resolve().parents[3] / 'scripts' / 'math_reader_generate.py'
        spec = importlib.util.spec_from_file_location('math_reader_generation_cli_metadata', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            recorded = module.RecordedRuntime(
                ApiConfig(model='test', credential_env='TEST_API_KEY'),
                Path(temporary) / 'calls',
            )
            recorded.executor = Mock()
            recorded.executor.execute.return_value = ExecutionResult(
                'succeeded', data={'title': 'Finite sets'}
            )
            self.assertEqual(
                recorded('metadata prompt without an INPUT delimiter', {'type': 'object'}),
                {'title': 'Finite sets'},
            )

    def test_parallel_calls_keep_distinct_records_and_usage(self):
        from lean_exposition.runtime import ApiUsage
        from unittest.mock import Mock
        path = Path(__file__).resolve().parents[3] / 'scripts' / 'math_reader_generate.py'
        spec = importlib.util.spec_from_file_location('math_reader_generation_cli_parallel', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'calls'
            recorded = module.RecordedRuntime(
                ApiConfig(model='test', credential_env='TEST_API_KEY'), directory
            )
            recorded.executor = Mock()
            recorded.executor.execute.side_effect = lambda prompt, schema, trace_label=None: ExecutionResult(
                'succeeded', data={'value': trace_label}, usage=ApiUsage(input_tokens=7, cached_tokens=3),
                trace_label=trace_label,
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda node: recorded.execute('prompt ' + node, {'type': 'object'}, trace_label='eet.draft.' + node),
                    ('left', 'right'),
                ))
            self.assertEqual({result.data['value'] for result in results}, {'eet.draft.left', 'eet.draft.right'})
            self.assertEqual(len(list(directory.glob('call-*-request.json'))), 2)
            result_files = sorted(directory.glob('call-*-result.json'))
            self.assertEqual(len(result_files), 2)
            self.assertTrue(all(json.loads(file.read_text())['usage']['cached_tokens'] == 3 for file in result_files))
            provenance = json.loads((directory.parent / 'block_providers.json').read_text())
            self.assertEqual(set(provenance), {'left', 'right'})
