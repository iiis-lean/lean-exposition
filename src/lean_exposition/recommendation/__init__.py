from .random import rank, random_policy

__all__ = ["rank", "random_policy"]

from .structural import RecommendationConfig, make_structural_policy

__all__ += ["RecommendationConfig", "make_structural_policy"]
