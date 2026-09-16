"""Run one bounded Reader workflow through an OpenAI-compatible API.

Only the seven fixed Reader operations are exposed. No filesystem, shell,
general HTTP, or credential tool is registered.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Any, Callable

from lean_exposition.app.demo import create_demo
from lean_exposition.reading import ReaderService
from lean_exposition.runtime import ApiConfig, ApiToolExecutor
from lean_exposition.workflows import reader_workflow
from runtime_smoke import load_env_file, validate_deepseek_model


ALLOWED_TOOLS = (
    "open_reader",
    "get_overview",
    "read_text",
    "inspect",
    "locate",
    "recommend",
    "apply_action",
)
SCHEMA = {
    "type": "object",
    "properties": {
        key: {"type": "string"}
        for key in (
            "reader_id",
            "initial_view",
            "expanded_view",
            "root_id",
            "before_text",
            "after_text",
            "summary",
        )
    },
    "required": [
        "reader_id",
        "initial_view",
        "expanded_view",
        "root_id",
        "before_text",
        "after_text",
        "summary",
    ],
    "additionalProperties": False,
}


class TracedReaderService:
    def __init__(self, service: ReaderService, trace: list[dict[str, Any]], path: Path):
        self.service = service
        self.trace = trace
        self.path = path

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.service.call(name, arguments)
        self.trace.append({"tool": name, "arguments": arguments, "result": result})
        self.path.write_text(
            json.dumps(self.trace, ensure_ascii=False, indent=2) + "\n"
        )
        return result


def api_config_from_args(args: argparse.Namespace) -> ApiConfig:
    validate_deepseek_model(args.model, base_url=args.base_url)
    reasoning = {"effort": args.reasoning_effort} if args.reasoning_effort else None
    return ApiConfig(
        model=args.model,
        credential_env=args.credential_env,
        base_url=args.base_url,
        protocol=args.protocol,
        timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
        reasoning=reasoning,
        prompt_cache_key=args.prompt_cache_key,
    )


def run(
    output: Path,
    env_file: Path | None,
    config: ApiConfig,
    *,
    executor_factory: Callable[[ApiConfig], Any] = ApiToolExecutor,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    load_env_file(env_file)
    store = create_demo(output / "demo")
    service = ReaderService([store], output / "readers.json")
    instance = service.instances()["instances"][0]["instance_id"]
    trace: list[dict[str, Any]] = []
    traced = TracedReaderService(service, trace, output / "tool_trace.json")
    workflow = reader_workflow(
        executor_factory(config),
        traced,
        allowed_tools=ALLOWED_TOOLS,
        max_steps=12,
    )
    task = {
        "instance_id": instance,
        "procedure": [
            "Open one reader for the fixed instance.",
            "Before changing it, call read_text and get_overview for the initial view.",
            "Call inspect with detail summary and locate for the root, then recommend once.",
            "Expand only the root using the initial expected_view.",
            "Call read_text and get_overview for the returned expanded view.",
            "Use all seven allowed tools and no others.",
        ],
        "output_rules": {
            "before_text": "copy the exact full text from the initial read_text result",
            "after_text": "copy the exact full text from the expanded read_text result",
            "summary": "short English mathematical summary based only on Reader results",
        },
        "page_limit": 50,
    }
    started = time.monotonic()
    try:
        call = workflow.run_reader_task(task=task, output_schema=SCHEMA)
        result = call.execution
        evidence = {
            "backend": "api",
            "model": config.model,
            "base_url": config.base_url,
            "protocol": config.protocol,
            "credential_env": config.credential_env,
            "allowed_tools": list(ALLOWED_TOOLS),
            "task": task,
            "schema": SCHEMA,
            "stage": call.stage,
            "prefix_digest": call.prefix_digest,
            "prompt_digest": call.prompt_digest,
            "result": asdict(result),
            "seconds": time.monotonic() - started,
            "fixture_only": True,
        }
        (output / "run.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
        )
        if result.status != "succeeded":
            kind = result.error.kind if result.error else result.status
            raise RuntimeError(f"Reader API smoke did not succeed: {kind}")
        _verify_trace(trace, result.data)
        report = {
            "passed": True,
            "model": result.response_model or config.model,
            "tools": [event["tool"] for event in trace],
            "view_changed": True,
            "exact_text_verified": True,
            "seconds": evidence["seconds"],
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
        return report
    finally:
        service.close()


def _verify_trace(trace: list[dict[str, Any]], data: dict[str, Any]) -> None:
    assert set(ALLOWED_TOOLS) <= {
        event["tool"] for event in trace
    }, "not all allowed Reader tools were exercised"
    assert {event["tool"] for event in trace} <= set(
        ALLOWED_TOOLS
    ), "an unapproved Reader tool was exercised"
    opens = [event for event in trace if event["tool"] == "open_reader"]
    assert len(opens) == 1 and opens[0]["result"]["reader_id"] == data["reader_id"]
    assert opens[0]["result"]["view_id"] == data["initial_view"]
    actions = [event for event in trace if event["tool"] == "apply_action"]
    assert len(actions) == 1 and actions[0]["arguments"]["action"] == "expand"
    assert actions[0]["arguments"]["target"] == data["root_id"]
    assert data["root_id"] == opens[0]["result"]["root_id"]
    assert actions[0]["result"]["view_id"] == data["expanded_view"]
    assert data["expanded_view"] != data["initial_view"]
    for key, view_key in (
        ("before_text", "initial_view"),
        ("after_text", "expanded_view"),
    ):
        reads = [
            event["result"]
            for event in trace
            if event["tool"] == "read_text"
            and event["result"].get("view_id") == data[view_key]
        ]
        assert reads and any(
            result["text"] == data[key] for result in reads
        ), f"{key} does not exactly match a Reader result"
    assert all(
        event["result"].get("ok") for event in trace
    ), "Reader tool returned an error"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/research/reader_delivery/api_smoke")
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--credential-env", default="DEEPSEEK_API_KEY")
    parser.add_argument(
        "--protocol", choices=["responses", "chat_completions"], default="responses"
    )
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--prompt-cache-key", default="lean-exposition-reader-smoke")
    parser.add_argument("--timeout", type=float, default=180)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run(args.output, args.env_file, api_config_from_args(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
