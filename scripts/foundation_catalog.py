#!/usr/bin/env python3
"""Generate or check exact provider-bound foundation catalogs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lean_exposition.models import Workspace
from lean_exposition.structure.dependencies import (
    FoundationCatalog, FoundationEntry, generate_provider_foundation,
    merge_foundation_catalog,
)


def _implementation_digest() -> str:
    module = Path(__file__).parents[1] / "src/lean_exposition/structure/dependencies.py"
    return hashlib.sha256(module.read_bytes()).hexdigest()


def _reviewed_entries(path: Path | None) -> tuple[FoundationEntry, ...]:
    if path is None:
        return ()
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    data = json.loads(path.read_text(), object_pairs_hook=unique_keys)
    if not isinstance(data, list):
        raise ValueError("reviewed entries must be a JSON array")
    allowed = {"local_id", "classification", "parts", "reason", "source"}
    values = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError(f"reviewed entry {index} must have exactly {sorted(allowed)}")
        entry = FoundationEntry(item["local_id"], item["classification"],
                                tuple(item["parts"]), item["reason"], item["source"])
        entry.validate()
        values.append(entry)
    return tuple(values)


def generate(args) -> dict:
    workspace = Workspace.from_json(args.workspace.read_text())
    catalog = (FoundationCatalog.from_json(args.catalog.read_text())
               if args.catalog.exists() else FoundationCatalog(()))
    provider = generate_provider_foundation(
        workspace, args.repo_key,
        implementation_digest=_implementation_digest(),
        reviewed_entries=_reviewed_entries(args.reviewed),
    )
    result = merge_foundation_catalog(catalog, provider)
    args.catalog.parent.mkdir(parents=True, exist_ok=True)
    args.catalog.write_text(result.to_json())
    return {"catalog_digest": result.digest(),
            "provider": provider.identity.provider_name,
            "entry_count": len(provider.entries)}


def check(args) -> dict:
    workspace = Workspace.from_json(args.workspace.read_text())
    catalog = FoundationCatalog.from_json(args.catalog.read_text())
    repositories = ([repo for repo in workspace.manifest.repositories
                     if repo.repo_key == args.repo_key] if args.repo_key else
                    list(workspace.manifest.repositories))
    if args.repo_key and not repositories:
        raise ValueError(f"unknown repository: {args.repo_key}")
    matches = []
    for repository in repositories:
        provider = catalog.match(repository) if repository.version_status == "fixed" else None
        matches.append({"repo_key": repository.repo_key,
                        "matched": provider is not None,
                        "entry_count": len(provider.entries) if provider else 0})
    return {"catalog_digest": catalog.digest(), "providers": matches}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("--workspace", required=True, type=Path)
    create.add_argument("--repo-key", required=True)
    create.add_argument("--catalog", required=True, type=Path)
    create.add_argument("--reviewed", type=Path,
                        help="global reviewed entry array; reviewed: source is mandatory")
    create.set_defaults(handler=generate)
    verify = commands.add_parser("check")
    verify.add_argument("--workspace", required=True, type=Path)
    verify.add_argument("--catalog", required=True, type=Path)
    verify.add_argument("--repo-key")
    verify.set_defaults(handler=check)
    return value


def main() -> None:
    args = parser().parse_args()
    print(json.dumps(args.handler(args), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
