"""Real loopback MCP 1.26 service; model responses alone are replayed."""
import asyncio
import json
import socket
import threading
import unittest

import httpx as legacy_httpx
import httpx2 as httpx
from agents import Agent
from agents.mcp import MCPServerStreamableHttp
from mcp.server.fastmcp import FastMCP
import uvicorn

from test_runtime import RuntimeFixture, Answer, response, call
from lean_exposition.agent_runtime import RunLimits


class McpLifecycleTests(RuntimeFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.events = []
        self.fixture = FastMCP("read-only fixture", stateless_http=True, json_response=True)
        @self.fixture.tool()
        async def read_token(name: str, offset: int = 0) -> dict:
            self.events.append({"name": name, "offset": offset})
            return {"answer": "mcp-fixture-token"}
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/mcp"
        self.server = uvicorn.Server(uvicorn.Config(self.fixture.streamable_http_app(), log_level="error"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets":[self.sock]}, daemon=True)
        self.thread.start()
        async with asyncio.timeout(5):
            while not self.server.started:
                await asyncio.sleep(.01)
        self.clients = []

    async def asyncTearDown(self):
        self.server.should_exit = True
        await asyncio.to_thread(self.thread.join, 5)
        self.sock.close()
        self.assertFalse(self.thread.is_alive())

    def mcp(self):
        def factory(headers=None, timeout=None, auth=None):
            client = legacy_httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, trust_env=False)
            self.clients.append(client)
            return client
        return MCPServerStreamableHttp(name="fixture", params={"url":self.url, "httpx_client_factory":factory},
            max_retry_attempts=0, failure_error_function=None)

    async def test_mcp_real_list_call_schema_and_cleanup(self):
        mcp = self.mcp()
        async with mcp:
            tools = await mcp.list_tools()
            self.assertEqual([t.name for t in tools], ["read_token"])
            async with self.runtime([response([call("read_token", {"name":"record", "offset":0})]), response()]) as rt:
                agent = Agent(name="mcp", model=rt.model, output_type=Answer, mcp_servers=[mcp])
                await rt.run(agent, "x")
        self.assertEqual(self.events, [{"name":"record", "offset":0}])
        self.assertFalse(self.sent[0]["tools"][0]["strict"])
        self.assertEqual(self.sent[0]["tools"][0]["parameters"]["required"], ["name"])
        self.assertIn("mcp-fixture-token", json.dumps(self.sent[1]))
        self.assertIsNone(mcp.session)
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_mcp_zero_budget_failure_closes_without_call(self):
        mcp = self.mcp()
        with self.assertRaises(Exception):
            async with mcp:
                async with self.runtime([response([call("read_token", {"name":"record"})])]) as rt:
                    await rt.run(Agent(name="mcp", model=rt.model, mcp_servers=[mcp]), "x",
                                 limits=RunLimits(max_tool_calls=0))
        self.assertFalse(self.events)
        self.assertIsNone(mcp.session)
        self.assertTrue(all(c.is_closed for c in self.clients))
        self.assertEqual(self.receipts()[0]["status"], "tool_budget")

    async def test_mcp_cancel_closes_context_and_shared_runtime_survives(self):
        waiting = asyncio.Event()
        async def handler(request):
            body = json.loads(await request.aread())
            if body.get("tools"):
                waiting.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=response())
        mcp = self.mcp()
        async with self.runtime(handler=handler) as rt:
            async def owned_run():
                async with mcp:
                    await rt.run(Agent(name="mcp", model=rt.model, mcp_servers=[mcp]), "cancel")
            task = asyncio.create_task(owned_run())
            await asyncio.wait_for(waiting.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNone(mcp.session)
            self.assertTrue(all(c.is_closed for c in self.clients))
            self.assertEqual((await rt.run(self.agent(rt), "survive")).final_output.answer, "ok")
