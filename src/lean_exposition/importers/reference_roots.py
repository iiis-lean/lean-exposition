"""Assemble source-only reference roots from the project's existing Lake lock."""
from dataclasses import replace
import json
from pathlib import Path
import re

from lean_exposition.models import DependencyLock, Provenance
from .common import asset_from_bytes
from .reference_index import FixedSourceRoot, build_source_reference_index
from .references import add_text_references


def add_project_text_references(adapter, root, source_texts, *, cache_dir=None):
    """Resolve the selected sources against locally available, locked imports.

    The manifest supplies declared dependency revisions, as in the native
    adapter; file hashes independently identify the bytes actually indexed.
    No package fetching, lakefile evaluation or Lean process is needed.
    """
    root = Path(root)
    target = adapter.repositories[0]
    roots = [FixedSourceRoot(target.repo_key, root, target.revision, target.toolchain)]
    assets, diagnostics = [], []
    manifest = {}
    for name in ('lean-toolchain', 'lake-manifest.json'):
        path = root / name
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            assets.append(asset_from_bytes(target.repo_key, name, raw))
            if name == 'lake-manifest.json':
                manifest = json.loads(raw)
                if not isinstance(manifest, dict) or not isinstance(manifest.get('packages', []), list):
                    raise ValueError('expected a Lake manifest object with packages')
        except (OSError, ValueError) as exc:
            diagnostics.append(f'text_index_manifest_failed:{name}:{exc}')
            manifest = {}
    packages_dir = manifest.get('packagesDir', '.lake/packages')
    if not isinstance(packages_dir, str):
        diagnostics.append('text_index_manifest_failed:packagesDir:expected a path')
        manifest = {}
    for package in manifest.get('packages', []):
        if not isinstance(package, dict) or not isinstance(package.get('name'), str):
            diagnostics.append('text_index_manifest_failed:package:missing name')
            continue
        name, revision = package['name'], package.get('rev')
        if package.get('type') != 'git' or not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
            diagnostics.append(f'text_index_unfixed_dependency:{name}:requires locked Git revision')
            continue
        subdir = package.get('subDir') or '.'
        if not isinstance(subdir, str):
            diagnostics.append(f'text_index_manifest_failed:{name}:invalid subDir')
            continue
        path = (root / packages_dir / name / subdir).resolve()
        if not path.is_relative_to(root.resolve()):
            diagnostics.append(f'text_index_manifest_failed:{name}:source path escapes project root')
            continue
        if not path.is_dir():
            diagnostics.append(f'text_index_missing_dependency_source:{name}:{path}')
            continue
        key = target.repo_key + '/dependency/' + name
        if any(r.repo_key == key for r in roots):
            diagnostics.append(f'text_index_manifest_failed:{name}:duplicate package')
            continue
        roots.append(FixedSourceRoot(key, path, revision, target.toolchain))
    index = build_source_reference_index(roots, ((target.repo_key, module) for module in source_texts),
                                         cache_dir=cache_dir)
    indexed = add_text_references(adapter, source_texts, reference_index=index)
    lock_asset = next((a for a in assets if a.path == 'lake-manifest.json'), None)
    locks = tuple(DependencyLock(target.repo_key, r.repo_key,
        (Provenance('lake_manifest', f'{lock_asset.asset_id}:{lock_asset.sha256}'),))
        for r in index.repositories if r.repo_key != target.repo_key) if lock_asset else ()
    return replace(indexed, assets=tuple(dict.fromkeys(indexed.assets + tuple(assets))),
        dependency_locks=indexed.dependency_locks + locks,
        diagnostics=indexed.diagnostics + tuple(diagnostics))
