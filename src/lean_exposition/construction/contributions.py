"""Controlled inputs accepted by :mod:`lean_exposition.construction.builder`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lean_exposition.models import (
    DeclRef, DependencyLock, Provenance, Repository, SourceAsset, SourceRange,
)


COVERAGE_DOMAINS = (
    "lc_declared", "lean_type", "lean_value", "published", "text_reference",
)
COVERAGE_STATES = ("complete", "partial", "unknown", "not_applicable")
CONTRIBUTION_STATES = ("present", "missing", "unknown", "not_applicable")
UNIT_AGGREGATIONS = ("preserve", "native_helpers")


@dataclass(frozen=True)
class CanonicalDeclLocator:
    """A declaration identity already fixed by an authoritative source."""

    ref: DeclRef


@dataclass(frozen=True)
class UnresolvedDeclLocator:
    """A source declaration awaiting a unique canonical identity match."""

    repo_key: str
    module: str
    raw_name: str
    source_range: SourceRange | None = None
    authoritative_ref: DeclRef | None = None


DeclLocator = CanonicalDeclLocator | UnresolvedDeclLocator


@dataclass(frozen=True)
class FieldContribution:
    """One field observation with explicit absence and authority semantics.

    ``value`` is deliberately an in-memory value.  Adapters are not durable
    artifacts; the validated build bundle is.
    """

    field: str
    state: str
    value: object | None
    authority: str
    backend: str
    method: str
    provenance: tuple[Provenance, ...]
    input_digest: str | None = None


@dataclass(frozen=True)
class DeclarationContribution:
    locator: DeclLocator
    fields: tuple[FieldContribution, ...]


@dataclass(frozen=True)
class ScopeSeed:
    scope_id: str
    repo_key: str
    kind: str
    name: str
    provenance: tuple[Provenance, ...]
    parent: str | None = None


@dataclass(frozen=True)
class DeclUnitSeed:
    unit_id: str
    representative: DeclRef
    members: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True)
class CoverageContribution:
    ref: DeclRef
    part: str
    evidence_domain: str
    status: str
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class SourceTextContribution:
    """Immutable author/project text used to initialize the exposition store."""

    ref: DeclRef
    text_kind: str
    locale: str
    text: str
    provenance: tuple[Provenance, ...]
    input_digest: str


@dataclass(frozen=True)
class RepositoryAdapterResult:
    repositories: tuple[Repository, ...]
    assets: tuple[SourceAsset, ...]
    declarations: tuple[DeclarationContribution, ...]
    scopes: tuple[ScopeSeed, ...]
    units: tuple[DeclUnitSeed, ...]
    unit_aggregation: str
    dependency_locks: tuple[DependencyLock, ...] = ()
    coverage: tuple[CoverageContribution, ...] = ()
    source_texts: tuple[SourceTextContribution, ...] = ()
    diagnostics: tuple[str, ...] = ()
    production_structure: bool = True


@dataclass(frozen=True)
class RepositoryContext:
    repository_path: str
    revision: str | None = None


class RepositoryAdapter(Protocol):
    def collect(self, context: RepositoryContext) -> RepositoryAdapterResult: ...
