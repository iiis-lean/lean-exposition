#!/usr/bin/env python3
"""Run one project stage under the frozen resource-measurement contract.

Network checkout and downloads deliberately happen before this runner is invoked.
The measured interval starts immediately before spawning the child and ends after
that child exits.  Child output is discarded so stdout contains exactly one JSON
report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
from typing import Iterable, Mapping, Sequence

import psutil


SAMPLE_SECONDS = 0.05
TERM_GRACE_SECONDS = 1.0
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_path(path: Path) -> tuple[str, str]:
    """Return a deterministic content digest and the input kind."""
    if path.is_file():
        return _digest_file(path), "file"
    if not path.is_dir():
        raise ValueError(f"benchmark input does not exist: {path}")
    digest = hashlib.sha256()
    root = path.resolve()
    entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for entry in entries:
        relative = entry.relative_to(root).as_posix().encode("utf-8")
        if entry.is_symlink():
            digest.update(b"L\0" + relative + b"\0")
            digest.update(os.readlink(entry).encode("utf-8") + b"\0")
        elif entry.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(b"F\0" + relative + b"\0")
            digest.update(bytes.fromhex(_digest_file(entry)))
        else:
            raise ValueError(f"unsupported benchmark input entry: {entry}")
    return digest.hexdigest(), "directory"


def _input_records(paths: Iterable[Path]) -> tuple[list[dict[str, str]], str]:
    records = []
    combined = hashlib.sha256()
    for path in paths:
        resolved = path.resolve()
        digest, kind = _digest_path(resolved)
        record = {"path": str(resolved), "kind": kind, "digest": digest}
        records.append(record)
        combined.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
        combined.update(b"\0")
    if not records:
        raise ValueError("at least one --input is required")
    return records, combined.hexdigest()


def _runner_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _rss_tree(root: psutil.Process) -> int:
    try:
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        processes = [root]
    total = 0
    seen: set[int] = set()
    for process in processes:
        if process.pid in seen:
            continue
        seen.add(process.pid)
        try:
            total += process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, sig)
            return
        except ProcessLookupError:
            return
    try:
        process.send_signal(sig)
    except ProcessLookupError:
        pass


def _terminate_after_timeout(
    process: subprocess.Popen[bytes], root: psutil.Process, peak_rss: int
) -> tuple[int, str]:
    _signal_group(process, signal.SIGTERM)
    deadline = time.perf_counter() + TERM_GRACE_SECONDS
    while process.poll() is None and time.perf_counter() < deadline:
        peak_rss = max(peak_rss, _rss_tree(root))
        time.sleep(SAMPLE_SECONDS)
    if process.poll() is not None:
        return peak_rss, "SIGTERM"
    _signal_group(process, signal.SIGKILL)
    while process.poll() is None:
        peak_rss = max(peak_rss, _rss_tree(root))
        time.sleep(SAMPLE_SECONDS)
    return peak_rss, "SIGKILL"


def _environment(additions: Mapping[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    for name, value in additions.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name!r}")
        if "\0" in value:
            raise ValueError(f"environment variable {name!r} contains NUL")
        environment[name] = value
    return environment


def run_benchmark(
    *,
    command: Sequence[str],
    cwd: Path,
    inputs: Sequence[Path],
    cache_state: str,
    toolkit_commit: str,
    timeout_seconds: float,
    max_wall_seconds: float,
    max_rss_bytes: int,
    environment_additions: Mapping[str, str] | None = None,
) -> dict:
    """Run ``command`` and return the strict benchmark report."""
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must be a non-empty argv string array")
    if cache_state not in {"cold", "warm", "unknown"}:
        raise ValueError("cache_state must be cold, warm, or unknown")
    if timeout_seconds <= 0 or max_wall_seconds <= 0 or max_rss_bytes <= 0:
        raise ValueError("timeout and resource limits must be positive")
    if not toolkit_commit:
        raise ValueError("toolkit_commit must be recorded explicitly")
    resolved_cwd = cwd.resolve()
    if not resolved_cwd.is_dir():
        raise ValueError(f"working directory does not exist: {resolved_cwd}")
    input_records, input_digest = _input_records(inputs)
    additions = dict(environment_additions or {})
    environment = _environment(additions)

    started = time.perf_counter()
    process = subprocess.Popen(
        list(command),
        cwd=resolved_cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    root = psutil.Process(process.pid)
    peak_rss = _rss_tree(root)
    timed_out = False
    termination_signal: str | None = None
    while process.poll() is None:
        peak_rss = max(peak_rss, _rss_tree(root))
        if time.perf_counter() - started >= timeout_seconds:
            timed_out = True
            peak_rss, termination_signal = _terminate_after_timeout(process, root, peak_rss)
            break
        time.sleep(SAMPLE_SECONDS)
    exit_code = process.wait()
    wall_seconds = time.perf_counter() - started

    violations = []
    if timed_out:
        violations.append("timeout")
    elif exit_code != 0:
        violations.append("nonzero_exit")
    if wall_seconds > max_wall_seconds:
        violations.append("wall_seconds")
    if peak_rss > max_rss_bytes:
        violations.append("peak_rss_bytes")

    return {
        "command": list(command),
        "cwd": str(resolved_cwd),
        "inputs": input_records,
        "input_digest": input_digest,
        "environment_additions": sorted(additions),
        "platform": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "cpu": platform.processor() or platform.machine() or "unknown",
            "cpu_count": os.cpu_count(),
        },
        "toolkit_commit": toolkit_commit,
        "cache_state": cache_state,
        "runner_implementation_digest": _runner_digest(),
        "outcome": {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "termination_signal": termination_signal,
            "wall_seconds": wall_seconds,
            "peak_rss_bytes": peak_rss,
        },
        "limits": {
            "timeout_seconds": timeout_seconds,
            "max_wall_seconds": max_wall_seconds,
            "max_rss_bytes": max_rss_bytes,
        },
        "gate": {"passed": not violations, "violations": violations},
    }


def _env_pair(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("environment additions use NAME=VALUE")
    name, content = value.split("=", 1)
    if not _ENV_NAME.fullmatch(name):
        raise argparse.ArgumentTypeError(f"invalid environment variable name: {name!r}")
    return name, content


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--cwd", required=True, type=Path)
    value.add_argument("--input", required=True, action="append", type=Path, dest="inputs")
    value.add_argument("--cache-state", required=True, choices=("cold", "warm", "unknown"))
    value.add_argument("--toolkit-commit", required=True)
    value.add_argument("--timeout-seconds", required=True, type=float)
    value.add_argument("--max-wall-seconds", required=True, type=float)
    value.add_argument("--max-rss-bytes", required=True, type=int)
    value.add_argument("--env", action="append", default=[], type=_env_pair)
    value.add_argument("--report", type=Path)
    value.add_argument("command", nargs=argparse.REMAINDER)
    return value


def main() -> int:
    args = parser().parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    additions: dict[str, str] = {}
    for name, value in args.env:
        if name in additions:
            raise ValueError(f"duplicate --env name: {name}")
        additions[name] = value
    report = run_benchmark(
        command=command,
        cwd=args.cwd,
        inputs=args.inputs,
        cache_state=args.cache_state,
        toolkit_commit=args.toolkit_commit,
        timeout_seconds=args.timeout_seconds,
        max_wall_seconds=args.max_wall_seconds,
        max_rss_bytes=args.max_rss_bytes,
        environment_additions=additions,
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_name(args.report.name + ".tmp")
        temporary.write_text(rendered + "\n")
        temporary.replace(args.report)
    print(rendered)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
