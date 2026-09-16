"""Contiguous balanced and constrained optimal interval trees."""
from functools import lru_cache
import math


def partition_regions(items, weights=None, *, k=6, algorithm="ordered_dp", dp_limit=120, burdens=None):
    """Return nested tuples, objective cost and actual algorithm.

    Leaves are input IDs; each tuple is a display container with at most k children.
    Weights map ordered input-index pairs to distinct declaration-pair counts.
    """
    items = tuple(items)
    if k < 2 or dp_limit < 1 or algorithm not in {"balanced", "ordered_dp"}:
        raise ValueError("invalid Region configuration")
    n = len(items)
    weights = weights or {}
    if burdens is not None and (len(burdens) != n or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in burdens)):
        raise ValueError("burdens require one finite nonnegative value per item")
    prefix = [0]
    for value in burdens or [0] * n:
        prefix.append(prefix[-1] + value)
    matrix = [[0] * (n + 1) for _ in range(n + 1)]
    for (a, b), weight in weights.items():
        if not 0 <= a < b < n or weight < 0:
            raise ValueError("weights require nonnegative forward index pairs")
        matrix[a + 1][b + 1] += weight
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            matrix[i][j] += matrix[i - 1][j] + matrix[i][j - 1] - matrix[i - 1][j - 1]
    def rectangle(a, b, c, d):
        return matrix[b][d] - matrix[a][d] - matrix[b][c] + matrix[a][c]
    actual = "balanced" if n > dp_limit or not weights else algorithm
    @lru_cache(None)
    def solve(i, j):
        m = j - i
        if m <= k:
            return m * rectangle(i, j, i, j), tuple(items[i:j]), 0
        candidates = range((m + 2) // 3, (2 * m) // 3 + 1)
        if actual == "balanced":
            candidates = [min(candidates, key=lambda left: (abs(m - 2 * left), left))]
        best = None
        for left in candidates:
            mid = i + left
            lc, lt, lb = solve(i, mid)
            rc, rt, rb = solve(mid, j)
            cost = lc + rc + m * rectangle(i, mid, mid, j)
            imbalance = lb + rb + abs(2 * prefix[mid] - prefix[i] - prefix[j])
            key = (cost, imbalance, abs(m - 2 * left), left) if burdens is not None and actual == "ordered_dp" else (cost, abs(m - 2 * left), left)
            if best is None or key < best[0]:
                best = key, (lt, rt), imbalance
        return best[0][0], best[1], best[2]
    cost, tree, _ = solve(0, n)
    return tree, cost, actual
