import asyncio
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from lean_exposition.app.demo import create_demo
from lean_exposition.app.bilingual_demo import create_bilingual_demo
from lean_exposition.interfaces.server import create_app
from lean_exposition.interfaces.schema import TOOL_SCHEMAS
from lean_exposition.reading import ReaderService


class TransportTests(unittest.TestCase):
    def test_real_loopback_mcp_and_http_share_contract_and_action_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_demo(directory)
            service = ReaderService([store, *create_bilingual_demo(Path(directory) / 'languages')], Path(directory) / 'readers.json')
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(service), log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not server.started and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                url = f'http://127.0.0.1:{port}'
                with httpx.Client(base_url=url, trust_env=False) as client:
                    instances = client.get('/api/instances').json()
                    self.assertEqual(instances['instances'][0]['instance_id'], store.instance_id)
                    invalid = client.post('/api/apply_action', json={'action': 'expand'}).json()
                    self.assertEqual(invalid['error']['code'], 'validation_error')
                    async def run_mcp():
                        async with httpx.AsyncClient(trust_env=False) as transport, streamable_http_client(url + '/mcp', http_client=transport) as (read, write, _):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                tools = (await session.list_tools()).tools
                                self.assertEqual({t.name for t in tools}, set(TOOL_SCHEMAS))
                                for tool in tools:
                                    self.assertEqual(tool.inputSchema, TOOL_SCHEMAS[tool.name])
                                async def mcp_call(tool, args):
                                    result = await session.call_tool(tool, args)
                                    self.assertFalse(result.isError, result)
                                    return json.loads(result.content[0].text)
                                http_open = client.post('/api/open_reader', json={'instance_id': store.instance_id}).json()
                                mcp_open = await mcp_call('open_reader', {'instance_id': store.instance_id})
                                http_view, mcp_view = http_open['view_id'], mcp_open['view_id']
                                for action, target in [('expand', 'root'), ('expand', 'setup'), ('collapse', 'root'), ('expand', 'root')]:
                                    h = client.post('/api/apply_action', json={'reader_id': http_open['reader_id'], 'expected_view': http_view, 'action': action, 'target': target}).json()
                                    m = await mcp_call('apply_action', {'reader_id': mcp_open['reader_id'], 'expected_view': mcp_view, 'action': action, 'target': target})
                                    self.assertTrue(h['ok'], h)
                                    self.assertTrue(m['ok'], m)
                                    http_view, mcp_view = h['view_id'], m['view_id']
                                    ht = client.post('/api/read_text', json={'reader_id': http_open['reader_id']}).json()
                                    mt = await mcp_call('read_text', {'reader_id': mcp_open['reader_id']})
                                    self.assertEqual(ht['text'], mt['text'])
                                    self.assertEqual(ht['anchors'], mt['anchors'])
                                    hg = client.post('/api/get_overview', json={'reader_id': http_open['reader_id']}).json()
                                    mg = await mcp_call('get_overview', {'reader_id': mcp_open['reader_id']})
                                    self.assertEqual(hg['nodes'], mg['nodes'])
                                    self.assertEqual(hg['edges'], mg['edges'])
                                self.assertEqual({n['id'] for n in hg['nodes'] if n['is_frontier']}, {'setup', 'conclusion'})
                                self.assertNotIn('definition', {n['id'] for n in hg['nodes']})
                                for tool, extra in [('inspect', {'ref': 'definition', 'detail': 'summary'}), ('locate', {'ref': 'result'}), ('recommend', {})]:
                                    result = await mcp_call(tool, {'reader_id': mcp_open['reader_id'], **extra})
                                    self.assertTrue(result['ok'], result)
                                for locale in ('zh', 'en'):
                                    h = client.post('/api/apply_action', json={'reader_id':http_open['reader_id'],'expected_view':http_view,'action':'switch_locale','target':'root','locale':locale}).json()
                                    m = await mcp_call('apply_action', {'reader_id':mcp_open['reader_id'],'expected_view':mcp_view,'action':'switch_locale','target':'root','locale':locale})
                                    self.assertTrue(h['ok'], h)
                                    self.assertTrue(m['ok'], m)
                                    self.assertEqual(h['locale'], locale)
                                    self.assertEqual(m['locale'], locale)
                                    http_view, mcp_view = h['view_id'], m['view_id']
                                    ht = client.post('/api/read_text', json={'reader_id':http_open['reader_id']}).json()
                                    mt = await mcp_call('read_text', {'reader_id':mcp_open['reader_id']})
                                    self.assertEqual(ht['text'], mt['text'])
                                    self.assertEqual(ht['anchors'], mt['anchors'])
                    asyncio.run(run_mcp())
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                service.close()
                sock.close()
