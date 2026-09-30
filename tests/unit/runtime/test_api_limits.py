import unittest

from test_api import ExecutorFixture, TOOL, SCHEMA, FunctionTool, tool_response
from lean_exposition.runtime import ApiToolExecutor


class ToolLimitTests(ExecutorFixture, unittest.TestCase):
    def run_loop(self, **limits):
        self.invoked = []
        executor = ApiToolExecutor(self.config(), client_factory=self.factory)
        return executor.execute('Read the evidence.', SCHEMA, tools=[TOOL],
            handlers={'read': lambda page: self.invoked.append(page) or {'page': page}}, **limits)

    def test_multiple_calls_in_one_turn_obey_total_quota(self):
        response = tool_response('responses')
        response.output.append(tool_response('responses', arguments='{"page":2}').output[0])
        self.client.queue = [response]
        result = self.run_loop(max_tool_calls=1)
        self.assertEqual(result.error.kind, 'tool_call_limit')
        self.assertEqual(self.invoked, [1])
        self.assertEqual(len(result.tool_events), 1)

    def test_zero_quota_and_input_budget_prevent_handlers(self):
        self.client.queue = [tool_response('responses')]
        self.assertEqual(self.run_loop(max_tool_calls=0).error.kind, 'tool_call_limit')
        self.assertEqual(self.invoked, [])
        self.client.calls.clear()
        self.assertEqual(self.run_loop(max_input_characters=1).error.kind, 'input_budget')
        self.assertEqual(self.client.calls, [])

    def test_large_result_stops_before_next_model_call(self):
        self.client.queue = [tool_response('responses')]
        result = self.run_loop(max_tool_result_characters=1)
        self.assertEqual(result.error.kind, 'tool_result_budget')
        self.assertEqual(len(self.client.calls), 1)

    def test_history_growth_is_bounded(self):
        from lean_exposition.runtime.api import _request_arguments, canonical_json
        budget = len(canonical_json(_request_arguments(self.config(),
            [{'role': 'user', 'content': 'Read the evidence.'}], SCHEMA, tools=[TOOL])))
        self.client.queue = [tool_response('responses')]
        result = self.run_loop(max_input_characters=budget)
        self.assertEqual(result.error.kind, 'input_budget')
        self.assertEqual(len(self.client.calls), 1)

    def test_identity_covers_tools_and_budgets(self):
        from lean_exposition.runtime import request_digest
        baseline = request_digest('p', SCHEMA, self.config(), tools=[TOOL], limits={'max_tool_calls': 1})
        for tools, limit in (([], 1), ([TOOL], 2), ([FunctionTool('read', 'Different meaning', TOOL.parameters)], 1)):
            self.assertNotEqual(baseline, request_digest('p', SCHEMA, self.config(), tools=tools, limits={'max_tool_calls': limit}))

    def test_optional_tools_explicitly_disable_strict_and_keep_local_defaults(self):
        from lean_exposition.runtime.api import _request_arguments
        optional = FunctionTool('inspect', 'Inspect evidence', {
            'type': 'object', 'properties': {'ref': {'type': 'string'}, 'limit': {'type': 'integer'}},
            'required': ['ref'], 'additionalProperties': False})
        for protocol in ('responses', 'chat_completions'):
            args = _request_arguments(self.config(protocol=protocol), 'p', SCHEMA, tools=[optional, TOOL])
            definitions = args['tools'] if protocol == 'responses' else [t['function'] for t in args['tools']]
            self.assertFalse(definitions[0]['strict'])
            self.assertTrue(definitions[1]['strict'])
            self.assertEqual(definitions[0]['parameters']['required'], ['ref'])
