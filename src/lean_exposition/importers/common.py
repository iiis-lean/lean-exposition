"""Small shared assembly helpers for source adapters."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from lean_exposition.models.facts import (
    DeclUnit, DependencyLock, RawDecl, Repository, Scope, SourceAsset,
    Workspace, WorkspaceManifest,
)


def qualified_id(repo_key: str, local_id: str) -> str:
    """Encode two identity components without delimiter collisions."""
    return json.dumps([repo_key, local_id], ensure_ascii=False, separators=(",", ":"))


def asset_from_bytes(repo_key: str, path: str, data: bytes) -> SourceAsset:
    return SourceAsset(qualified_id(repo_key, path), repo_key, path,
                       hashlib.sha256(data).hexdigest())


def assemble_workspace(*, repositories: Iterable[Repository], assets: Iterable[SourceAsset],
                       declarations: Iterable[RawDecl], scopes: Iterable[Scope],
                       dependency_locks: Iterable[DependencyLock] = ()) -> Workspace:
    """Validate source facts and give each declaration one singleton reading unit.

    Missing dependency repositories are registered honestly as unresolved stubs;
    no dependency declaration or version is invented.
    """
    repositories = tuple(repositories)
    declarations = tuple(declarations)
    known = {r.repo_key for r in repositories}
    referenced = {dep.provider.repo_key for d in declarations
                  for part in (d.statement, d.proof) if part is not None for dep in part.deps}
    repositories += tuple(Repository(key, None, None, version_status="unresolved",
                                    unresolved_reason="Referenced repository was not supplied by the source adapter.")
                          for key in sorted(referenced - known))
    workspace = Workspace(
        WorkspaceManifest(repositories, tuple(assets), tuple(dependency_locks)),
        declarations, tuple(scopes),
        tuple(DeclUnit(qualified_id(d.ref.repo_key, d.ref.local_id), d.ref) for d in declarations),
    )
    workspace.validate()
    return workspace
