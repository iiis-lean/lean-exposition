import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx2 as httpx
from agents import Agent, function_tool
from lean_exposition.agent_runtime import RuntimeConfig, SdkRuntime, RunLimits
from test_runtime import Answer, response


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, protocol, *, reasoning=None, limit=16):
        sent, invoked = [], []
        @function_tool(failure_error_function=None)
        def lookup(value: str) -> str:
            invoked.append(value)
            return 'evidence'

        async def transport(request):
            body = json.loads(await request.aread())
            sent.append((str(request.url), body))
            self.assertEqual(request.headers['authorization'], 'Bearer custom-test-key')
            if protocol == 'responses':
                return httpx.Response(200, json=response())
            if len(sent) == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call_1', 'type': 'function', 'function': {
                        'name': 'lookup', 'arguments': '{"value":"query"}'}}]}
                finish = 'tool_calls'
            else:
                message = {'role': 'assistant', 'content': '{"answer":"ok"}'}
                finish = 'stop'
            return httpx.Response(200, json={'id':'chat_1','object':'chat.completion',
                'created':1,'model':'custom-model','choices':[{'index':0,'message':message,'finish_reason':finish}],
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CUSTOM_API_KEY':'custom-test-key'}):
            cfg = RuntimeConfig(base_url='https://compatible.example/v1', model='custom-model',
                credential_env='CUSTOM_API_KEY', protocol=protocol, reasoning=reasoning)
            async with SdkRuntime(cfg, evidence_dir=tmp, transport=httpx.MockTransport(transport)) as rt:
                agent = Agent(name='custom', model=rt.model, output_type=Answer,
                              tools=[lookup] if protocol == 'chat_completions' else [])
                if limit == 0:
                    with self.assertRaises(Exception):
                        await rt.run(agent, 'answer', limits=RunLimits(max_tool_calls=0))
                    self.assertEqual(invoked, [])
                    self.assertEqual(len(sent), 1)
                    receipt = json.loads(next(Path(tmp).glob('*/receipt.json')).read_text())
                    self.assertEqual(receipt['status'], 'tool_budget')
                else:
                    result = await rt.run(agent, 'answer')
                    self.assertEqual(result.final_output.answer, 'ok')
            for url, body in sent:
                self.assertEqual(url, 'https://compatible.example/v1/'+('responses' if protocol=='responses' else 'chat/completions'))
                self.assertEqual(body['model'], 'custom-model')
                for key in ['max_tokens','max_output_tokens','max_completion_tokens']:
                    self.assertNotIn(key, body)
                if reasoning is None:
                    self.assertNotIn('reasoning', body)
                    self.assertNotIn('reasoning_effort', body)
                else:
                    self.assertEqual(body['reasoning_effort'], reasoning)
            if protocol == 'chat_completions' and limit:
                self.assertEqual(invoked, ['query'])
                self.assertTrue(any(m.get('role')=='tool' and m['content']=='evidence' for m in sent[1][1]['messages']))
                self.assertEqual(sent[0][1]['response_format']['type'], 'json_schema')

    async def test_custom_responses_without_reasoning(self):
        await self.exercise('responses')

    async def test_chat_structured_tool_loop(self):
        await self.exercise('chat_completions')

    async def test_chat_reasoning_configuration(self):
        await self.exercise('chat_completions', reasoning='low')

    async def test_chat_tool_budget_prevents_side_effect(self):
        await self.exercise('chat_completions', limit=0)

    def test_invalid_protocol_and_endpoint(self):
        for kwargs in [{'protocol':'auto'}, {'base_url':'file:///tmp/key'},
                       {'base_url':'https://user:secret@example.com'}, {'model':''}]:
            with self.assertRaises(ValueError):
                RuntimeConfig(**kwargs)
