import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2 as httpx
import jsonschema
from agents import Agent, RunResult, function_tool
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from pydantic import BaseModel, ConfigDict

from lean_exposition.agent_runtime import RunLimits, RuntimeConfig, SdkRuntime, UnsupportedAgent


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


class Dynamic(AgentOutputSchemaBase):
    def is_plain_text(self): return False
    def name(self): return "dynamic_answer"
    def json_schema(self): return Answer.model_json_schema()
    def is_strict_json_schema(self): return True
    def validate_json(self, text):
        try:
            data = json.loads(text)
            jsonschema.validate(data, self.json_schema())
            return data
        except (ValueError, jsonschema.ValidationError) as exc:
            raise ModelBehaviorError("Invalid dynamic answer") from exc


def response(output=None, *, text='{"answer":"ok"}'):
    if output is None:
        output = [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": text, "annotations": []}]}]
    return {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed",
            "model": "deepseek-flash", "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25,
                      "input_tokens_details": {"cached_tokens": 3},
                      "output_tokens_details": {"reasoning_tokens": 2}}}


def call(name="work", arguments=None, index=1):
    return {"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
            "name": name, "arguments": json.dumps(arguments or {"value": "x"}), "status": "completed"}


class RuntimeFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = "test-private-credential-unique"
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": self.key})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sent = []
        self.raw = []

    def receipts(self):
        return [json.loads(p.read_text()) for p in Path(self.tmp.name).glob("*/receipt.json")]

    def runtime(self, bodies=None, handler=None):
        bodies = list(bodies or [response()])
        async def replay(request):
            self.raw.append(await request.aread())
            self.sent.append(json.loads(self.raw[-1]))
            if handler:
                return await handler(request)
            body = bodies.pop(0)
            return httpx.Response(200, json=body, headers={"x-secret-test": self.key})
        return SdkRuntime(RuntimeConfig(), evidence_dir=self.tmp.name, transport=httpx.MockTransport(replay))

    def agent(self, runtime, **kwargs):
        return Agent(name="test", model=runtime.model, output_type=Answer, **kwargs)


class RuntimeTests(RuntimeFixture):
    async def test_typed_wire_and_numeric_secret_safe_evidence(self):
        async with self.runtime() as rt:
            result = await rt.run(self.agent(rt), "中文 fixture")
            self.assertIsInstance(result, RunResult)
            self.assertIsInstance(result.final_output, Answer)
        self.assertEqual(len(self.sent), 1)
        wire = self.sent[0]
        self.assertEqual(wire["model"], "deepseek-flash")
        self.assertEqual(wire["reasoning"], {"effort": "high"})
        self.assertNotIn("max_output_tokens", wire)
        self.assertFalse(wire["text"]["format"]["schema"]["additionalProperties"])
        receipt, = self.receipts()
        self.assertEqual(receipt["usage"]["input_tokens_details"]["cached_tokens"], 3)
        self.assertEqual(receipt["usage"]["output_tokens_details"]["reasoning_tokens"], 2)
        self.assertEqual(receipt["usage"]["total_tokens"], 25)
        self.assertNotEqual(receipt["contract_digest"], receipt["exchanges"][0]["wire_digest"])
        saved = json.dumps(receipt)
        self.assertNotIn(self.key, saved)
        self.assertNotIn("x-secret-test", saved)
        self.assertNotIn("Authorization", saved)

    async def test_dynamic_schema_valid_and_invalid(self):
        async with self.runtime([response(), response(text='{"wrong":1}')]) as rt:
            agent = Agent(name="dynamic", model=rt.model, output_type=Dynamic())
            self.assertEqual((await rt.run(agent, "valid")).final_output, {"answer": "ok"})
            with self.assertRaises(ModelBehaviorError):
                await rt.run(agent, "invalid")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({r["status"] for r in self.receipts()}, {"succeeded", "schema_error"})

    async def test_open_output_rejected_without_transport(self):
        class Open(BaseModel):
            payload: dict
        async with self.runtime() as rt:
            with self.assertRaises(UnsupportedAgent):
                await rt.run(Agent(name="open", model=rt.model, output_type=Open), "x")
        self.assertFalse(self.sent)

    async def test_first_wire_budget_includes_schema_and_tools(self):
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            return value
        async with self.runtime() as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "x", limits=RunLimits(max_input_characters=100))
        self.assertFalse(self.sent)
        r, = self.receipts()
        self.assertEqual(r["status"], "input_budget")
        self.assertGreater(r["exchanges"][0]["characters"], 100)
        self.assertFalse(r["exchanges"][0]["transport_allowed"])
        self.assertTrue(r["exchanges"][0]["request"]["tools"])

    async def test_wire_character_boundary_uses_actual_utf8_request(self):
        async with self.runtime([response(), response()]) as rt:
            agent = self.agent(rt)
            await rt.run(agent, "中文 boundary")
            size = len(self.raw[0].decode("utf-8"))
            await rt.run(agent, "中文 boundary", limits=RunLimits(max_input_characters=size))
            with self.assertRaises(Exception):
                await rt.run(agent, "中文 boundary", limits=RunLimits(max_input_characters=size-1))
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(r["exchanges"][0]["characters"] == size for r in self.receipts()))

    async def test_continuation_wire_budget_before_transport(self):
        executed = []
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            executed.append(value)
            return "Z" * 4000
        async with self.runtime([response([call()])]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "x", limits=RunLimits(max_input_characters=2000))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(executed, ["x"])
        r, = self.receipts()
        self.assertEqual(r["status"], "input_budget")
        self.assertEqual(len(r["exchanges"]), 2)
        self.assertFalse(r["exchanges"][1]["transport_allowed"])

    async def test_zero_and_same_turn_multiple_tool_budget(self):
        for budget in (0, 1, 2):
            executed = []
            @function_tool(failure_error_function=None)
            async def work(value: str) -> str:
                executed.append(value)
                return value
            async with self.runtime([response([call(arguments={"value":str(i)}, index=i) for i in range(3)])]) as rt:
                with self.assertRaises(Exception):
                    await rt.run(self.agent(rt, tools=[work]), str(budget), limits=RunLimits(max_tool_calls=budget))
            self.assertEqual(executed, [str(i) for i in range(budget)])
        self.assertEqual(len(self.sent), 3)
        self.assertEqual({r["status"] for r in self.receipts()}, {"tool_budget"})

    async def test_oversized_result_does_not_continue_and_records_side_effect(self):
        executed = []
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            executed.append(value)
            return "z" * 11
        async with self.runtime([response([call()])]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "x", limits=RunLimits(max_tool_result_characters=10))
        self.assertEqual(executed, ["x"])
        self.assertEqual(len(self.sent), 1)
        r, = self.receipts()
        self.assertEqual(r["status"], "tool_result_budget")
        self.assertEqual(r["tool_events"][-1]["event"], "completed")

    async def test_cancel_one_concurrent_run_keeps_other_history_and_client(self):
        started = asyncio.Event()
        proceed = asyncio.Event()
        async def handler(request):
            body = json.loads(await request.aread())
            if body["input"][0]["content"] == "cancel-me":
                started.set()
                await asyncio.Event().wait()
            await proceed.wait()
            return httpx.Response(200, json=response())
        async with self.runtime(handler=handler) as rt:
            agent = self.agent(rt)
            cancelled = asyncio.create_task(rt.run(agent, "cancel-me"))
            await asyncio.wait_for(started.wait(), 2)
            survivor = asyncio.create_task(rt.run(agent, "survivor"))
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            proceed.set()
            self.assertEqual((await survivor).final_output.answer, "ok")
            self.assertEqual((await rt.run(agent, "after")).final_output.answer, "ok")
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(all(len(w["input"]) == 1 for w in self.sent))
        self.assertEqual(sorted(r["status"] for r in self.receipts()), ["cancelled", "succeeded", "succeeded"])
        self.assertEqual(len({r["run_id"] for r in self.receipts()}), 3)

    async def test_concurrent_tool_reservations_and_context_are_independent(self):
        both = asyncio.Event()
        entered = []
        async def handler(request):
            body = json.loads(await request.aread())
            marker = body["input"][0]["content"]
            if len(body["input"]) == 1:
                entered.append(marker)
                if len(entered) == 2:
                    both.set()
                await both.wait()
                return httpx.Response(200, json=response([call(arguments={"value": marker}, index=i) for i in (1,2)]))
            return httpx.Response(200, json=response())
        from agents import RunContextWrapper
        @function_tool(failure_error_function=None)
        async def work(ctx: RunContextWrapper[list], value: str) -> str:
            ctx.context.append(value)
            return value
        left, right = [], []
        async with self.runtime(handler=handler) as rt:
            agent = self.agent(rt, tools=[work])
            results = await asyncio.gather(
                rt.run(agent, "left", context=left, limits=RunLimits(max_tool_calls=1)),
                rt.run(agent, "right", context=right, limits=RunLimits(max_tool_calls=2)), return_exceptions=True)
        self.assertIsInstance(results[0], Exception)
        self.assertIsInstance(results[1], RunResult)
        self.assertEqual(left, ["left"])
        self.assertEqual(right, ["right", "right"])
        for r in self.receipts():
            self.assertEqual(r["tool_calls_reserved"], 1 if r["input"] == "left" else 2)
            self.assertTrue(all(e["request"]["input"][0]["content"] == r["input"] for e in r["exchanges"]))

    async def test_expected_error_is_tool_result_unexpected_error_terminates(self):
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            return "Expected business error: not found"
        async with self.runtime([response([call()]), response()]) as rt:
            await rt.run(self.agent(rt, tools=[work]), "expected")
        self.assertIn("Expected business error", json.dumps(self.sent[-1]))
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            raise OSError("fixture infrastructure failure")
        async with self.runtime([response([call()])]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "unexpected")
        self.assertEqual(len(self.sent), 3)
        self.assertIn("tool_error", [r["status"] for r in self.receipts()])

    async def test_invalid_arguments_unknown_tool_and_typed_output_fail_no_retry(self):
        executed = []
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            executed.append(value)
            return value
        for body in [response([call(arguments={"wrong":1})]), response([call(name="missing")]), response(text="bad JSON")]:
            async with self.runtime([body]) as rt:
                with self.assertRaises(Exception):
                    await rt.run(self.agent(rt, tools=[work]), "fail")
        self.assertEqual(len(self.sent), 3)
        self.assertFalse(executed)
        self.assertTrue(all(r["exchanges"][0]["response"] for r in self.receipts()))
        self.assertEqual({r["status"] for r in self.receipts()}, {"tool_arguments_error", "tool_selection_error", "schema_error"})

    async def test_provider_429_500_no_retry_or_fallback(self):
        for status in (429, 500):
            async def handler(request):
                return httpx.Response(status, json={"error": {"message": "fixture refusal", "type": "test"}})
            async with self.runtime(handler=handler) as rt:
                with self.assertRaises(Exception):
                    await rt.run(self.agent(rt), "x")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({r["status"] for r in self.receipts()}, {"provider_error"})

    async def test_timeouts_and_max_turns(self):
        async def blocked(request):
            await asyncio.Event().wait()
        async with self.runtime(handler=blocked) as rt:
            with self.assertRaises(TimeoutError):
                await rt.run(self.agent(rt), "timeout", limits=RunLimits(timeout=.03))
        async def transport_timeout(request):
            raise httpx.ReadTimeout("fixture transport timeout", request=request)
        async with self.runtime(handler=transport_timeout) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt), "transport")
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            return value
        async with self.runtime([response([call()])]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "turns", limits=RunLimits(max_turns=1))
        self.assertEqual({r["status"] for r in self.receipts()}, {"run_timeout", "transport_timeout", "max_turns"})
        self.assertEqual(len(self.sent), 3)

    async def test_handoff_nested_unbound_and_default_error_policy_rejected(self):
        async with self.runtime() as rt:
            child = self.agent(rt)
            @function_tool
            async def unsafe(value: str) -> str:
                return value
            agents = [self.agent(rt, handoffs=[child]), self.agent(rt, tools=[child.as_tool("child", "Nested agent")]),
                      Agent(name="unbound"), self.agent(rt, tools=[unsafe])]
            for agent in agents:
                with self.assertRaises(UnsupportedAgent):
                    await rt.run(agent, "x")
        self.assertFalse(self.sent)

    async def test_strict_default_is_required_and_sdk_preserves_zero(self):
        observed = []
        @function_tool(failure_error_function=None)
        async def work(value: str, offset: int = 0) -> str:
            observed.append(offset)
            return value
        self.assertIn("offset", work.params_json_schema["required"])
        async with self.runtime([response([call(arguments={"value":"x", "offset":0})]), response()]) as rt:
            await rt.run(self.agent(rt, tools=[work]), "x")
        self.assertEqual(observed, [0])
        self.assertTrue(self.sent[0]["tools"][0]["strict"])

    async def test_secret_rejected_in_input_tool_schema_and_tool_result(self):
        async with self.runtime() as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt), self.key)
            @function_tool(failure_error_function=None)
            async def work(value: str) -> str:
                return value
            work.description = self.key
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "x")
        self.assertFalse(self.sent)
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            return self.key
        async with self.runtime([response([call()])]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt, tools=[work]), "x")
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(all(self.key not in p.read_text() for p in Path(self.tmp.name).glob("*/receipt.json")))
        self.assertEqual({r["status"] for r in self.receipts()}, {"secret_input"})

    async def test_provider_refusal_has_distinct_terminal_receipt(self):
        refusal = response([{"type":"message", "id":"msg_refusal", "role":"assistant", "status":"completed",
                             "content":[{"type":"refusal", "refusal":"fixture refusal"}]}])
        async with self.runtime([refusal]) as rt:
            with self.assertRaises(Exception):
                await rt.run(self.agent(rt), "x")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.receipts()[0]["status"], "provider_refusal")

    async def test_cancel_during_tool_does_not_execute_queued_tool(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        calls = []
        @function_tool(failure_error_function=None)
        async def work(value: str) -> str:
            calls.append(value)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async with self.runtime([response([call(index=1),call(index=2)]), response()]) as rt:
            task = asyncio.create_task(rt.run(self.agent(rt, tools=[work]), "cancel"))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 2)
            self.assertEqual((await rt.run(self.agent(rt), "survivor")).final_output.answer, "ok")
        self.assertEqual(calls, ["x"])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({r["status"] for r in self.receipts()}, {"cancelled", "succeeded"})

    async def test_contract_identity_changes_with_schema_and_instructions(self):
        class Different(Dynamic):
            def json_schema(self):
                schema = super().json_schema()
                schema["properties"]["answer"]["minLength"] = 1
                return schema
        async with self.runtime([response(),response(),response()]) as rt:
            await rt.run(self.agent(rt, instructions="first"), "x")
            await rt.run(self.agent(rt, instructions="second"), "x")
            await rt.run(Agent(name="test",model=rt.model,output_type=Different(),instructions="second"), "x")
        self.assertEqual(len({r["contract_digest"] for r in self.receipts()}), 3)

    async def test_schema_keyword_as_property_name_is_valid(self):
        class KeywordAnswer(BaseModel):
            model_config = ConfigDict(extra="forbid")
            additionalProperties: str
        async with self.runtime([response(text='{"additionalProperties":"valid field"}')]) as rt:
            result = await rt.run(Agent(name="keyword",model=rt.model,output_type=KeywordAnswer), "x")
        self.assertEqual(result.final_output.additionalProperties, "valid field")

    async def test_dynamic_open_object_rejected_without_rewriting(self):
        class OpenDynamic(Dynamic):
            def json_schema(self): return {"type":"object", "properties":{"answer":{"type":"string"}}}
        async with self.runtime() as rt:
            with self.assertRaises(UnsupportedAgent):
                await rt.run(Agent(name="open",model=rt.model,output_type=OpenDynamic()), "x")
        self.assertFalse(self.sent)

    async def test_explicit_environment_proxy_initializes_without_mutating_environment(self):
        with patch.dict(os.environ, {"ALL_PROXY":"socks5://127.0.0.1:9", "HTTPS_PROXY":"socks5://127.0.0.1:9"}):
            async with SdkRuntime(RuntimeConfig(proxy="environment"), evidence_dir=self.tmp.name) as rt:
                self.assertIsNotNone(rt.model)
            self.assertEqual(os.environ["HTTPS_PROXY"], "socks5://127.0.0.1:9")

    async def test_proxy_environment_does_not_construct_trace_exporter(self):
        from agents.tracing import processors
        with patch.dict(os.environ, {"ALL_PROXY":"socks5://127.0.0.1:9", "HTTPS_PROXY":"socks5://127.0.0.1:9"}):
            with patch.object(processors, "default_processor", side_effect=AssertionError("trace exporter constructed")):
                async with self.runtime() as rt:
                    await rt.run(self.agent(rt), "x")
            self.assertEqual(os.environ["ALL_PROXY"], "socks5://127.0.0.1:9")


if __name__ == "__main__":
    unittest.main()
