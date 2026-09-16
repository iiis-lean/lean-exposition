import asyncio
import json
import unittest

import test_writing as fixtures
from lean_exposition.exposition import writing_mcp


class WritingMCPTests(unittest.TestCase):
    setUp = fixtures.WritingTests.setUp
    make = fixtures.WritingTests.make

    def test_real_mcp_bound_protocol(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        trace = []
        job = self.store.create_writing_job()
        with writing_mcp(self.store, job, trace) as url:
            async def exercise():
                async with httpx.AsyncClient(trust_env=False) as client, streamable_http_client(url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertEqual(names, {'get_step', 'submit_draft', 'accept_draft', 'query_decl', 'query_scope', 'query_path', 'query_dependency_path'})
                        async def call(name, args):
                            result = await session.call_tool(name, args)
                            self.assertFalse(result.isError)
                            return json.loads(result.content[0].text)
                        step = await call('get_step', {})
                        self.assertEqual(step['node_id'], 'root')
                        source = await call('query_decl', {'repo_key': 'demo', 'local_id': 'definition'})
                        self.assertEqual(source['interface']['ref']['local_id'], 'definition')
                        draft = await call('submit_draft', {'payload': self.fixture['blocks']['root']})
                        self.assertIsNone(self.store.state['latest_manifest'])
                        published = await call('accept_draft', {'draft_id': draft['draft_id']})
                        self.assertEqual(published['status'], 'published')
            asyncio.run(exercise())
        self.assertEqual([item['tool'] for item in trace], ['get_step', 'query_decl', 'submit_draft', 'accept_draft'])
