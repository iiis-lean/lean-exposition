"""Pure graph foundation over validated declaration facts."""
from .graph import (
    MATHEMATICAL_EVIDENCE_KINDS,
    Boundary,
    Cycle,
    DependencyEdge,
    DependencyGraph,
    EvidenceOccurrence,
    Ordering,
    ProjectedEdge,
    Projection,
    RepositoryView,
)

__all__ = [
    "MATHEMATICAL_EVIDENCE_KINDS", "Boundary", "Cycle", "DependencyEdge",
    "DependencyGraph", "EvidenceOccurrence", "Ordering", "ProjectedEdge", "Projection", "RepositoryView",
]

from .helper import BuildConfig
from .hierarchy import Hierarchy, HierarchyCycleError, build_hierarchy, derive_narrative_order
from .order import (
    NarrativeOrder, OrderEdge, OrderEvidenceBundle, OrderProblem, OrderRelation,
    OrderSequence, OrderSubject, ScopeOrder, SubjectProjection,
    dependency_distance, material_subject_projections, project_order_evidence,
    solve_order,
)
from .source import (SourceOrder, SourceSequence, SourceSequenceSpec,
                     TexMaterialResult, derive_sequence_order_evidence,
                     derive_source_order, derive_tex_materials, source_order_from_lc_git)
from .regions import partition_regions
from .dependencies import (
    DependencyAnalysis, DependencyAnalysisPolicy, DependencyDecision,
    FoundationCatalog, FoundationEntry, ProviderFoundation, ProviderIdentity,
    analyze_dependencies, canonical_provider_name, generate_provider_foundation,
    load_foundation_catalog, merge_foundation_catalog,
)

__all__ += ["BuildConfig", "Hierarchy", "HierarchyCycleError", "build_hierarchy", "derive_narrative_order",
            "NarrativeOrder", "OrderEdge", "OrderEvidenceBundle", "OrderProblem",
            "OrderRelation", "OrderSequence", "OrderSubject", "ScopeOrder",
            "SubjectProjection", "dependency_distance", "material_subject_projections",
            "project_order_evidence", "solve_order",
            "SourceOrder", "SourceSequence", "SourceSequenceSpec", "derive_source_order",
            "TexMaterialResult", "derive_sequence_order_evidence", "derive_tex_materials",
            "source_order_from_lc_git", "partition_regions"]
__all__ += ["DependencyAnalysis", "DependencyAnalysisPolicy", "DependencyDecision",
            "FoundationCatalog", "FoundationEntry", "ProviderFoundation",
            "ProviderIdentity", "analyze_dependencies", "canonical_provider_name",
            "generate_provider_foundation", "load_foundation_catalog",
            "merge_foundation_catalog"]
