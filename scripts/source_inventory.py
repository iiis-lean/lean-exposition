#!/usr/bin/env python3
"""Stream Toolkit text-AST declaration results into validated JSONL shards."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lean_exposition.importers import consume_text_ast_json


def _module(path: Path) -> str:
    return ".".join(path.with_suffix("").parts)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_inventory(*, root: Path, repo_key: str, source_roots: tuple[str, ...],
                    output_dir: Path, max_shard_bytes: int = 16 * 1024 * 1024,
                    max_shard_records: int = 5000) -> dict:
    """Parse fixed source files; each envelope is validated before publication."""
    from lean_mcp_toolkit.interfaces.declarations.backends.text_ast_backend import (
        TextAstDeclarationsInterfaceBackend,
    )
    from lean_mcp_toolkit.interfaces.declarations.base import DeclarationsInterfaceRequest

    root = root.resolve()
    prefixes = tuple(Path(item) for item in source_roots)
    selected = []
    for prefix in prefixes:
        candidate = root / prefix
        selected.extend((candidate,) if candidate.is_file() and candidate.suffix == ".lean"
                        else candidate.rglob("*.lean"))
    files = sorted(set(selected))
    if not files:
        raise ValueError("source roots contain no Lean files")
    if max_shard_bytes <= 0 or max_shard_records <= 0:
        raise ValueError("shard limits must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("inventory-*.jsonl"):
        stale.unlink()

    backend = TextAstDeclarationsInterfaceBackend(include_value=True)
    shards: list[dict] = []
    stream = None
    shard_path = None
    shard_bytes = shard_records = 0
    totals = {"files": 0, "declarations": 0, "failed_files": 0,
              "top_level_commands": 0, "unrecognized_commands": 0}

    def close_shard() -> None:
        nonlocal stream, shard_path, shard_bytes, shard_records
        if stream is None:
            return
        stream.close()
        shards.append({"path": shard_path.name, "sha256": _sha(shard_path),
                       "bytes": shard_bytes, "records": shard_records})
        stream = shard_path = None
        shard_bytes = shard_records = 0

    try:
        for source_path in files:
            relative = source_path.relative_to(root)
            response = backend.extract(DeclarationsInterfaceRequest(
                project_root=root, target_rel_file=relative,
                module_dot=_module(relative), timeout_seconds=30,
            ))
            payload = {
                "success": response.success,
                "error_message": response.error_message,
                "total_declarations": len(response.declarations),
                "declarations": [item.to_dict() for item in response.declarations],
                "source_diagnostics": (response.source_diagnostics.to_dict()
                                       if response.source_diagnostics else None),
            }
            source = source_path.read_bytes()
            validated = consume_text_ast_json(
                repo_key=repo_key, path=relative.as_posix(), module=_module(relative),
                source=source, payload=payload,
            )
            declaration_rows = payload["declarations"]
            chunks = ([declaration_rows[index:index + 1000]
                       for index in range(0, len(declaration_rows), 1000)]
                      if declaration_rows else [[]])
            for chunk_index, rows in enumerate(chunks):
                chunk_payload = {**payload, "total_declarations": len(rows),
                                 "declarations": rows}
                envelope = {
                    "path": relative.as_posix(), "module": _module(relative),
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "chunk_index": chunk_index, "chunk_count": len(chunks),
                    "result": chunk_payload,
                }
                line = (json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode()
                if len(line) > max_shard_bytes:
                    raise ValueError(f"one inventory chunk exceeds shard byte limit: {relative}")
                if (stream is None or shard_records >= max_shard_records or
                        shard_bytes + len(line) > max_shard_bytes):
                    close_shard()
                    shard_path = output_dir / f"inventory-{len(shards):05d}.jsonl"
                    stream = shard_path.open("wb")
                stream.write(line)
                shard_bytes += len(line)
                shard_records += 1
            totals["files"] += 1
            totals["declarations"] += len(validated.declarations)
            totals["failed_files"] += int(not validated.complete)
            if validated.coverage is not None:
                totals["top_level_commands"] += validated.coverage.total_top_level_commands
                totals["unrecognized_commands"] += len(validated.coverage.unrecognized_commands)
    finally:
        close_shard()
    result = {
        "repo_key": repo_key, "root": str(root),
        "source_roots": list(source_roots), **totals,
        "classified_top_level_commands": (
            totals["top_level_commands"] - totals["unrecognized_commands"]
        ),
        "shards": shards,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo-key", required=True)
    parser.add_argument("--source-root", required=True, action="append")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-shard-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-shard-records", type=int, default=5000)
    args = parser.parse_args()
    result = build_inventory(
        root=args.root, repo_key=args.repo_key, source_roots=tuple(args.source_root),
        output_dir=args.output_dir, max_shard_bytes=args.max_shard_bytes,
        max_shard_records=args.max_shard_records,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
