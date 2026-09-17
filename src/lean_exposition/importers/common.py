"""Small shared assembly helpers for source adapters."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from lean_exposition.models.facts import (
    DeclUnit, DependencyLock, RawDecl, Repository, Scope, SourceAsset,
    Workspace, WorkspaceManifest,
)


def adapter_result_from_workspace(workspace: Workspace, *, unit_aggregation: str,
                                  authority: str, method: str, coverage=(),
                                  source_texts=(), diagnostics=()):
    """Translate already-normalized facts into the shared construction input.

    This is the migration seam for the existing exact LC and compiled-native
    normalizers.  It is intentionally lossless; discovery remains adapter-owned
    while validation and Workspace assembly become builder-owned.
    """
    from lean_exposition.construction import (
        CanonicalDeclLocator, DeclarationContribution, DeclUnitSeed,
        FieldContribution, RepositoryAdapterResult, ScopeSeed,
    )

    input_digest = workspace.digest()

    def field(name, value, provenance):
        return FieldContribution(name, "present", value, authority, method, method,
                                 provenance, input_digest)

    declarations = []
    for decl in workspace.declarations:
        provenance = decl.provenance
        values = [
            field("lean_name", decl.lean_name, provenance),
            field("module", decl.module, provenance),
            field("native_scope", decl.native_scope, provenance),
            field("kind", decl.kind, provenance),
            field("statement.nl", decl.statement.nl, decl.statement.nl.provenance),
            field("statement.formal", decl.statement.formal, decl.statement.formal.provenance),
            field("statement.deps", decl.statement.deps, provenance),
            field("extraction_status", decl.extraction_status, decl.extraction_status.provenance),
            field("provenance", decl.provenance, provenance),
            field("source_refs", decl.source_refs, provenance),
            field("source_context", decl.source_context, provenance),
            field("local_public", decl.local_public, provenance),
        ]
        for name, value in (
            ("completion_status", decl.completion_status),
            ("kernel_kind", decl.kernel_kind),
            ("generated_from", decl.generated_from),
        ):
            if value is not None:
                item_provenance = value.provenance if hasattr(value, "provenance") else provenance
                values.append(field(name, value, item_provenance))
        if decl.proof is not None:
            values.extend((
                field("proof.nl", decl.proof.nl, decl.proof.nl.provenance),
                field("proof.formal", decl.proof.formal, decl.proof.formal.provenance),
                field("proof.deps", decl.proof.deps, provenance),
            ))
        declarations.append(DeclarationContribution(CanonicalDeclLocator(decl.ref), tuple(values)))
    scopes = tuple(ScopeSeed(scope.scope_id, scope.repo_key, scope.kind, scope.name,
                             scope.provenance, scope.parent) for scope in workspace.scopes)
    units = tuple(DeclUnitSeed(unit.unit_id, unit.representative, unit.members,
                               (workspace.declarations[0].provenance[0],) if workspace.declarations
                               else workspace.scopes[0].provenance)
                  for unit in workspace.units)
    return RepositoryAdapterResult(
        repositories=workspace.manifest.repositories,
        assets=workspace.manifest.assets,
        declarations=tuple(declarations),
        scopes=scopes,
        units=units,
        unit_aggregation=unit_aggregation,
        dependency_locks=workspace.manifest.dependency_locks,
        coverage=tuple(coverage),
        source_texts=tuple(source_texts),
        diagnostics=tuple(diagnostics),
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
