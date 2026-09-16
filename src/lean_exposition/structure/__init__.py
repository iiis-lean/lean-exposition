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
from .order import NarrativeOrder, OrderEdge, OrderProblem, ScopeOrder, dependency_distance, solve_order
from .source import (SourceOrder, SourceSequence, SourceSequenceSpec,
                     derive_source_order, source_order_from_lc_git)
from .regions import partition_regions

__all__ += ["BuildConfig", "Hierarchy", "HierarchyCycleError", "build_hierarchy", "derive_narrative_order",
            "NarrativeOrder", "OrderEdge", "OrderProblem", "ScopeOrder", "dependency_distance", "solve_order",
            "SourceOrder", "SourceSequence", "SourceSequenceSpec", "derive_source_order",
            "source_order_from_lc_git", "partition_regions"]
