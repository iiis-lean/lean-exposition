import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lean_exposition.runtime import (
    ApiConfig,
    ApiToolExecutor,
    FunctionTool,
    RuntimeFailure,
    StructuredExecutor,
    canonical_json,
    stable_prompt,
)


SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}
TOOL = FunctionTool(
    "read",
    "Read one page",
    {
        "type": "object",
        "properties": {"page": {"type": "integer"}},
        "required": ["page"],
        "additionalProperties": False,
    },
)


class Item(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {
            key: value
            for key, value in vars(self).items()
            if not callable(value)
        }


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.delay = 0
        self.queue = []
        self.responses = SimpleNamespace(create=self.create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        time.sleep(self.delay)
        if self.queue:
            return self.queue.pop(0)
        return response_result()

    def close(self):
        self.kwargs["http_client"].close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def response_result(
    text='{"title":"test"}',
    *,
    status="completed",
    incomplete_details=None,
    usage=None,
    error=None,
):
    return Item(
        id="resp_1",
        output_text=text,
        output=[],
        model="returned-model",
        status=status,
        incomplete_details=incomplete_details,
        usage=usage,
        error=error,
        choices=[
            Item(
                finish_reason="stop",
                message=Item(content=text, tool_calls=[]),
            )
        ],
    )


def tool_response(protocol, *, arguments='{"page":1}', name="read"):
    function = Item(name=name, arguments=arguments)
    if protocol == "responses":
        return Item(
            id="resp_tool",
            output_text="",
            output=[Item(type="function_call", call_id="call_1", **vars(function))],
            model="returned-model",
            status="completed",
            incomplete_details=None,
            usage=None,
        )
    message = Item(content=None, tool_calls=[Item(id="call_1", function=function)])
    return Item(
        id="chat_tool",
        model="returned-model",
        usage=None,
        choices=[Item(finish_reason="tool_calls", message=message)],
    )


class ExecutorFixture:
    def setUp(self):
        self.env = patch.dict(os.environ, {"TEST_API_KEY": "fake-secret"})
        self.env.start()
        self.client = FakeClient()

        def factory(**kwargs):
            self.client.kwargs = kwargs
            return self.client

        self.factory = factory

    def tearDown(self):
        self.env.stop()

    def config(self, **kwargs):
        return ApiConfig(model="model", credential_env="TEST_API_KEY", **kwargs)

    def executor(self, **kwargs):
        return StructuredExecutor(self.config(**kwargs), client_factory=self.factory)


class StructuredExecutorTests(ExecutorFixture, unittest.TestCase):
    def test_official_deepseek_endpoint_accepts_only_flash(self):
        with self.assertRaisesRegex(ValueError, "restricted to deepseek-flash"):
            ApiConfig(
                model="deepseek-v4-pro",
                credential_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com/v1",
            )
        config = ApiConfig(
            model="deepseek-flash",
            credential_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com",
        )
        self.assertEqual(config.model, "deepseek-flash")

    def test_responses_contract_and_normalized_usage(self):
        usage = Item(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            input_tokens_details={"cached_tokens": 64},
            output_tokens_details={"reasoning_tokens": 7},
        )
        self.client.queue.append(response_result(usage=usage))
        result = self.executor(reasoning={"effort": "high"}).execute(
            "prompt", SCHEMA, trace_label="region-name"
        )
        self.assertEqual((result.status, result.data), ("succeeded", {"title": "test"}))
        self.assertEqual(result.provider_status, "completed")
        self.assertEqual(result.response_model, "returned-model")
        self.assertEqual(result.trace_label, "region-name")
        self.assertEqual(len(result.input_digest), 64)
        self.assertEqual(result.usage.cached_tokens, 64)
        self.assertEqual(result.usage.reasoning_tokens, 7)
        call = self.client.calls[0]
        self.assertEqual(call["text"]["format"]["schema"], SCHEMA)
        self.assertEqual(call["reasoning"], {"effort": "high"})
        self.assertNotIn("previous_response_id", call)
        self.assertEqual(self.client.kwargs["max_retries"], 0)
        self.assertFalse(self.client.kwargs["http_client"].follow_redirects)
        self.assertFalse(self.client.kwargs["http_client"].trust_env)

    def test_chat_contract_is_explicit_and_strict(self):
        result = self.executor(
            protocol="chat_completions", reasoning={"effort": "high"}
        ).execute("prompt", SCHEMA)
        self.assertEqual(result.status, "succeeded")
        call = self.client.calls[0]
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertEqual(call["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(call["reasoning_effort"], "high")

    def test_output_limit_can_be_omitted_for_both_protocols(self):
        self.assertIsNone(self.config().max_output_tokens)
        self.executor().execute("prompt", SCHEMA)
        self.assertNotIn("max_output_tokens", self.client.calls[-1])

        self.executor(
            protocol="chat_completions", max_output_tokens=None
        ).execute("prompt", SCHEMA)
        self.assertNotIn("max_tokens", self.client.calls[-1])

    def test_failure_categories_are_stable(self):
        cases = [
            ("", "empty_output"),
            ("not json", "invalid_json"),
            ('{"title":"ok"} noise', "trailing_output"),
            ('{"title":4}', "schema_validation"),
        ]
        for raw, expected in cases:
            with self.subTest(expected):
                self.client.queue.append(response_result(raw))
                result = self.executor().execute("prompt", SCHEMA)
                self.assertEqual((result.status, result.error.kind), ("failed", expected))

    def test_length_and_incomplete_details_are_preserved(self):
        self.client.queue.append(
            response_result(
                "",
                status="incomplete",
                incomplete_details=Item(reason="max_output_tokens"),
            )
        )
        result = self.executor().execute("prompt", SCHEMA)
        self.assertEqual(result.error.kind, "length")
        self.assertEqual(result.incomplete_details, {"reason": "max_output_tokens"})

    def test_terminal_provider_failure_preserves_only_code(self):
        self.client.queue.append(
            response_result("", status="failed", error=Item(code="upstream_failed", message="secret"))
        )
        result = self.executor().execute("prompt", SCHEMA)
        self.assertEqual(result.error.kind, "provider_failed")
        self.assertEqual(result.error.provider_code, "upstream_failed")
        self.assertNotIn("secret", repr(result))

    def test_provider_error_is_redacted(self):
        class SecretError(Exception):
            status_code = 429
            code = "rate_limit"

        self.client.responses.create = lambda **kwargs: (_ for _ in ()).throw(
            SecretError("secret prompt and token")
        )
        result = self.executor().execute("prompt", SCHEMA)
        self.assertEqual(result.error.kind, "provider_error")
        self.assertEqual(result.error.exception_type, "SecretError")
        self.assertEqual(result.error.status_code, 429)
        self.assertEqual(result.error.provider_code, "rate_limit")
        self.assertNotIn("secret", repr(result))

    def test_missing_credential_and_sync_failure(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.executor().execute("prompt", SCHEMA)
            self.assertEqual(result.error.kind, "missing_credential")
            with self.assertRaises(RuntimeFailure) as caught:
                self.executor().run_json("prompt", SCHEMA)
            self.assertEqual(caught.exception.result.error.kind, "missing_credential")

    def test_job_cancel_and_timeout_are_terminal(self):
        self.client.delay = 0.2
        executor = self.executor()
        handle = executor.start("prompt", SCHEMA)
        self.assertTrue(executor.cancel(handle))
        time.sleep(0.3)
        self.assertEqual(executor.status(handle).status, "cancelled")
        self.assertFalse(executor.cancel(handle))

        self.client = FakeClient()
        self.client.delay = 0.2
        timed = self.executor(timeout=0.05)
        handle = timed.start("prompt", SCHEMA)
        self.assertEqual(timed.result(handle, 1).error.kind, "timeout")
        time.sleep(0.25)
        self.assertEqual(timed.status(handle).error.kind, "timeout")

    def test_configuration_and_stable_serialization(self):
        with self.assertRaises(ValueError):
            self.config(protocol="automatic")
        with self.assertRaises(ValueError):
            self.config(transport="websocket")
        with self.assertRaises(ValueError):
            self.config(protocol="chat_completions", reasoning={"mode": "advanced"})
        with self.assertRaises(ValueError):
            self.config(max_output_tokens=0)
        self.assertEqual(canonical_json({"b": 1, "a": "中"}), '{"a":"中","b":1}')
        self.assertEqual(
            stable_prompt("fixed  ", {"b": 1, "a": 2}),
            'fixed\n\nINPUT\n{"a":2,"b":1}',
        )


class ToolExecutorTests(ExecutorFixture, unittest.TestCase):
    def run_loop(self, protocol="responses", *, first=None, max_steps=2, handler=None):
        self.client.queue[:] = [
            first or tool_response(protocol),
            response_result('{"title":"read"}'),
        ]
        calls = []
        executor = ApiToolExecutor(
            self.config(protocol=protocol), client_factory=self.factory
        )
        result = executor.execute(
            "read",
            SCHEMA,
            tools=[TOOL],
            handlers={"read": handler or (lambda page: calls.append(page) or {"text": "page"})},
            max_steps=max_steps,
        )
        return result, calls

    def test_responses_uses_client_history(self):
        result, calls = self.run_loop()
        self.assertEqual((result.status, calls), ("succeeded", [1]))
        history = self.client.calls[1]["input"]
        self.assertEqual(history[-1]["type"], "function_call_output")
        self.assertNotIn("previous_response_id", self.client.calls[1])
        self.assertEqual(result.tool_events[0].name, "read")

    def test_chat_uses_client_history(self):
        result, calls = self.run_loop("chat_completions")
        self.assertEqual((result.status, calls), ("succeeded", [1]))
        history = self.client.calls[1]["messages"]
        self.assertEqual(history[-1]["role"], "tool")

    def test_invalid_arguments_and_unknown_tools_do_not_dispatch(self):
        result, calls = self.run_loop(first=tool_response("responses", arguments='{"page":"bad"}'))
        self.assertEqual((result.error.kind, calls), ("invalid_tool_arguments", []))
        result, calls = self.run_loop(first=tool_response("responses", name="unknown"))
        self.assertEqual((result.error.kind, calls), ("unknown_tool", []))

    def test_handler_failure_is_redacted(self):
        result, _ = self.run_loop(
            handler=lambda page: (_ for _ in ()).throw(ValueError("sensitive data"))
        )
        self.assertEqual(result.error.kind, "tool_handler_error")
        self.assertEqual(result.error.provider_code, "ValueError")
        self.assertNotIn("sensitive", repr(result))

    def test_step_limit_is_distinct(self):
        self.client.queue[:] = [tool_response("responses"), tool_response("responses")]
        executor = ApiToolExecutor(self.config(), client_factory=self.factory)
        result = executor.execute(
            "read", SCHEMA, tools=[TOOL], handlers={"read": lambda page: {}}, max_steps=2
        )
        self.assertEqual(result.error.kind, "tool_step_limit")
        self.assertEqual(len(result.tool_events), 1)


if __name__ == "__main__":
    unittest.main()
