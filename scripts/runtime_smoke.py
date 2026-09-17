"""Run one explicit structured-output smoke against an API, Codex, or Pi.

This command is live by design. Credentials are read only from a named
environment variable, optionally populated from ``--env-file``. The saved
record contains the credential variable name, never its value.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from lean_exposition.runtime import (
    ApiConfig,
    CodexAgentConfig,
    CodexAgentExecutor,
    PiAgentConfig,
    PiAgentExecutor,
    StructuredExecutor,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["title", "description"],
    "additionalProperties": False,
}
PROMPT = (
    "Name a mathematical region containing the lemmas Nat.add_comm and "
    "Nat.add_assoc. Return a brief English title and one-sentence description."
)


def load_env_file(path: Path | None) -> None:
    """Populate missing environment variables from a simple shell-style file."""

    if path is None:
        return
    for line_number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"invalid env assignment at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"empty env name at line {line_number}")
        os.environ.setdefault(key, value.strip().strip("\"'"))


def validate_deepseek_model(
    model: str, *, provider: str | None = None, base_url: str | None = None
) -> None:
    host = urlparse(base_url).hostname if base_url else None
    is_deepseek = (
        "deepseek" in model.casefold()
        or (provider is not None and provider.casefold() == "deepseek")
        or (host is not None and host.casefold().endswith("deepseek.com"))
    )
    if is_deepseek and model.casefold() != "deepseek-flash":
        raise ValueError("DeepSeek smoke runs only permit the deepseek-flash model")


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


def agent_config_from_args(args: argparse.Namespace):
    if args.backend == "codex":
        validate_deepseek_model(args.model)
        return CodexAgentConfig(
            model=args.model,
            cwd=str(args.cwd.resolve()),
            reasoning=args.reasoning,
            timeout=args.timeout,
            codex_home=str(args.codex_home.resolve()) if args.codex_home else None,
            auth_path=str(args.auth_path.resolve()) if args.auth_path else None,
            codex_bin=args.codex_bin,
            sdk_python_root=(
                str(args.sdk_python_root.resolve()) if args.sdk_python_root else None
            ),
        )
    validate_deepseek_model(args.model, provider=args.provider)
    return PiAgentConfig(
        model=args.model,
        provider=args.provider,
        cwd=str(args.cwd.resolve()),
        session_dir=str(args.session_dir.resolve()),
        reasoning=args.reasoning,
        timeout=args.timeout,
        executable=args.executable,
        credential_env=args.credential_env,
        provider_credential_env=args.provider_credential_env,
        tools=(),
    )


def _write_record(output: Path, backend: str, record: dict[str, Any]) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{backend}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return target


def run_api(
    args: argparse.Namespace,
    *,
    executor_factory: Callable[[ApiConfig], Any] = StructuredExecutor,
) -> dict[str, Any]:
    load_env_file(args.env_file)
    config = api_config_from_args(args)
    result = executor_factory(config).execute(
        PROMPT, SCHEMA, trace_label="runtime.smoke"
    )
    record = {
        "backend": "api",
        "model": config.model,
        "base_url": config.base_url,
        "protocol": config.protocol,
        "credential_env": config.credential_env,
        "prompt": PROMPT,
        "schema": SCHEMA,
        "result": asdict(result),
    }
    _write_record(args.output, "api", record)
    return record


def run_agent(
    args: argparse.Namespace,
    *,
    codex_factory: Callable[[CodexAgentConfig], Any] = CodexAgentExecutor,
    pi_factory: Callable[[PiAgentConfig], Any] = PiAgentExecutor,
) -> dict[str, Any]:
    load_env_file(args.env_file)
    config = agent_config_from_args(args)
    executor = codex_factory(config) if args.backend == "codex" else pi_factory(config)
    result = executor.result(executor.start(PROMPT, SCHEMA), config.timeout + 10)
    record = {
        "backend": args.backend,
        "model": config.model,
        "credential_env": getattr(config, "credential_env", None),
        "prompt": PROMPT,
        "schema": SCHEMA,
        "result": asdict(result),
    }
    _write_record(args.output, args.backend, record)
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="backend", required=True)

    api = subparsers.add_parser("api", help="OpenAI-compatible structured API")
    api.add_argument("--model", default="deepseek-flash")
    api.add_argument("--base-url", default="https://api.deepseek.com")
    api.add_argument("--credential-env", default="DEEPSEEK_API_KEY")
    api.add_argument(
        "--protocol", choices=["responses", "chat_completions"], default="responses"
    )
    api.add_argument("--reasoning-effort")
    api.add_argument("--max-output-tokens", type=int)
    api.add_argument("--prompt-cache-key", default="lean-exposition-runtime-smoke")
    api.add_argument("--timeout", type=float, default=180)
    api.add_argument("--env-file", type=Path)
    api.add_argument("--output", type=Path, default=Path("data/research/runtime_smoke"))

    codex = subparsers.add_parser("codex", help="stateful Codex Agent")
    codex.add_argument("--model", default="gpt-5.6-sol")
    codex.add_argument("--reasoning", default="high")
    codex.add_argument("--timeout", type=float, default=900)
    codex.add_argument("--cwd", type=Path, default=Path.cwd())
    codex.add_argument("--codex-home", type=Path)
    codex.add_argument("--auth-path", type=Path)
    codex.add_argument("--codex-bin")
    codex.add_argument("--sdk-python-root", type=Path)
    codex.add_argument("--env-file", type=Path)
    codex.add_argument(
        "--output", type=Path, default=Path("data/research/runtime_smoke")
    )

    pi = subparsers.add_parser("pi", help="stateful Pi Agent")
    pi.add_argument("--model", default="deepseek-flash")
    pi.add_argument("--provider", default="deepseek")
    pi.add_argument("--reasoning", default="high")
    pi.add_argument("--timeout", type=float, default=900)
    pi.add_argument("--cwd", type=Path, default=Path.cwd())
    pi.add_argument(
        "--session-dir",
        type=Path,
        default=Path("data/research/runtime_smoke/pi_sessions"),
    )
    pi.add_argument("--executable", default="pi")
    pi.add_argument("--credential-env")
    pi.add_argument("--provider-credential-env")
    pi.add_argument("--env-file", type=Path)
    pi.add_argument("--output", type=Path, default=Path("data/research/runtime_smoke"))
    return parser


def _error_kind_from_record(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    return error.get("kind") if isinstance(error, dict) else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = run_api(args) if args.backend == "api" else run_agent(args)
    result = record["result"]
    summary = {
        "backend": record["backend"],
        "model": record["model"],
        "status": result["status"],
        "error": _error_kind_from_record(result),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
