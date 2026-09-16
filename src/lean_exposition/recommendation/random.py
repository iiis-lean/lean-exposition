"""A reproducible placeholder that makes no mathematical benefit claim."""
import hashlib


def rank(view_id, candidates, *, seed=0):
    ordered = sorted(candidates, key=lambda node: hashlib.sha256(f"{seed}:{view_id}:{node}".encode()).digest())
    return [{"target_id": node, "rank": index + 1, "reason": "Random baseline; no estimated mathematical benefit."}
            for index, node in enumerate(ordered)]


def random_policy(context, candidates, *, seed=0):
    """Policy callable(context, candidate_ids) -> policy_id and recommendations."""
    return {"policy_id": "random", "recommendations": rank(context['view_id'], candidates, seed=seed)}
