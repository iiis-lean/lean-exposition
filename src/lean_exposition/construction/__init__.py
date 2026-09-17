"""Unified repository construction contracts and authority-aware builder."""

from .builder import build_repository
from .contracts import CoverageEntry, DependencyCoverage, RepositoryBuildBundle, StructurePolicy
from .contributions import (
    COVERAGE_DOMAINS, COVERAGE_STATES, CONTRIBUTION_STATES, UNIT_AGGREGATIONS,
    CanonicalDeclLocator, CoverageContribution, DeclarationContribution,
    DeclLocator, DeclUnitSeed, FieldContribution, RepositoryAdapter,
    RepositoryAdapterResult, RepositoryContext, ScopeSeed, SourceTextContribution,
    UnresolvedDeclLocator,
)
from .materials import MaterialBinding, MaterialBundle, MaterialRecord, MaterialTarget
from .profiles import (
    ArtifactContributions, ContributorSpec, DeclarationLocatorSpec,
    MaterialAssetSpec, OrderHint, ProfileDiagnostic, ProfileRunPlan,
    PublishedDependency, RepositoryIdentity, RepositoryProfile, ScopeHint,
    TargetSlice, UnitHint, parse_formalization_yaml,
    parse_proof_path_markdown, parse_published_html_shard,
    parse_published_site_bundle,
)

__all__ = [
    "COVERAGE_DOMAINS", "COVERAGE_STATES", "CONTRIBUTION_STATES", "UNIT_AGGREGATIONS",
    "CanonicalDeclLocator", "CoverageContribution", "CoverageEntry",
    "DeclarationContribution", "DeclLocator", "DeclUnitSeed", "DependencyCoverage",
    "FieldContribution", "RepositoryAdapter", "RepositoryAdapterResult",
    "RepositoryBuildBundle", "RepositoryContext", "ScopeSeed", "SourceTextContribution",
    "StructurePolicy", "UnresolvedDeclLocator", "build_repository",
    "MaterialBinding", "MaterialBundle", "MaterialRecord", "MaterialTarget",
    "ArtifactContributions", "ContributorSpec", "DeclarationLocatorSpec",
    "MaterialAssetSpec", "OrderHint", "ProfileDiagnostic", "ProfileRunPlan",
    "PublishedDependency", "RepositoryIdentity", "RepositoryProfile", "ScopeHint",
    "TargetSlice", "UnitHint", "parse_formalization_yaml",
    "parse_proof_path_markdown", "parse_published_html_shard",
    "parse_published_site_bundle",
]
