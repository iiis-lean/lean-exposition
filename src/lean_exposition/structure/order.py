"""Deterministic narrative order over prepared sibling atoms.

This module knows neither Workspace facts nor Regions.  It solves a fixed DAG of
post-helper atoms, records the exact input identity, and exposes strict JSON for
later hierarchy construction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path


ORDER_IMPLEMENTATION = {
    "constraints": "real_dependencies_then_acyclic_protected_sequences",
    "edge_weight": "distinct_base_declaration_pairs",
    "starts": ["source_kahn", "reverse_frontier"],
    "objective": "weighted_atom_distance",
    "improvement": "strict_best_legal_insertion",
    "tie_break": "source_displacement_then_stable_identity",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


ORDER_IMPLEMENTATION_DIGEST = digest(ORDER_IMPLEMENTATION)


def _is_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _tupleize(value):
    if isinstance(value, list):
        return tuple(_tupleize(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, _tupleize(item)) for key, item in sorted(value.items()))
    return value


@dataclass(frozen=True)
class OrderEdge:
    provider: str
    consumer: str
    weight: int

    def __post_init__(self):
        if not self.provider or not self.consumer or self.provider == self.consumer:
            raise ValueError("order edges need distinct nonempty endpoints")
        if type(self.weight) is not int or self.weight < 1:
            raise ValueError("order edge weights must be positive integers")


@dataclass(frozen=True)
class OrderProblem:
    scope_id: str
    parent_id: str
    atoms: tuple[str, ...]
    edges: tuple[OrderEdge, ...]
    source_keys: dict[str, tuple]
    source_basis: dict[str, str]
    source_roles: dict[str, str | None]
    source_anchors: dict[str, dict | None]
    protected_sequences: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        atoms = set(self.atoms)
        if not self.scope_id or not self.parent_id or len(atoms) != len(self.atoms):
            raise ValueError("order problem needs unique atoms and identities")
        if (set(self.source_keys) != atoms or set(self.source_basis) != atoms or
                set(self.source_roles) != atoms or set(self.source_anchors) != atoms):
            raise ValueError("every atom needs source ordering evidence")
        if any(role not in {None, "primary", "supporting", "reference"}
               for role in self.source_roles.values()):
            raise ValueError("invalid atom source role")
        if any(edge.provider not in atoms or edge.consumer not in atoms for edge in self.edges):
            raise ValueError("order edge endpoint is outside the atom set")
        pairs = {(edge.provider, edge.consumer) for edge in self.edges}
        if len(pairs) != len(self.edges):
            raise ValueError("duplicate projected order edge")
        for sequence in self.protected_sequences:
            if len(sequence) != len(set(sequence)) or any(atom not in atoms for atom in sequence):
                raise ValueError("invalid protected source sequence")

    @property
    def atom_digest(self):
        return digest(sorted(self.atoms))

    @property
    def edge_digest(self):
        return digest([asdict(edge) for edge in sorted(
            self.edges, key=lambda edge: (edge.provider, edge.consumer))])


@dataclass(frozen=True)
class ScopeOrder:
    scope_id: str
    parent_id: str
    atom_digest: str
    edge_digest: str
    atoms: tuple[str, ...]
    order: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]
    metrics: dict
    source_basis: dict[str, str]
    source_roles: dict[str, str | None]
    source_anchors: dict[str, dict | None]
    diagnostics: tuple[dict, ...] = ()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        expected = {"scope_id", "parent_id", "atom_digest", "edge_digest", "atoms", "order",
                    "constraints", "metrics", "source_basis", "source_roles", "source_anchors",
                    "diagnostics"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("invalid scope order fields")
        if (not all(isinstance(data[name], list) for name in ("atoms", "order", "constraints", "diagnostics")) or
                not all(isinstance(data[name], dict) for name in
                        ("metrics", "source_basis", "source_roles", "source_anchors")) or
                any(not isinstance(item, dict) for item in data["diagnostics"]) or
                any(item is not None and not isinstance(item, dict)
                    for item in data["source_anchors"].values())):
            raise ValueError("invalid scope order field types")
        value = cls(data["scope_id"], data["parent_id"], data["atom_digest"], data["edge_digest"],
                    tuple(data["atoms"]), tuple(data["order"]),
                    tuple(tuple(pair) for pair in data["constraints"]), dict(data["metrics"]),
                    dict(data["source_basis"]), dict(data["source_roles"]),
                    {key: dict(item) if item is not None else None
                     for key, item in data["source_anchors"].items()},
                    tuple(dict(item) for item in data["diagnostics"]))
        if not _is_digest(value.atom_digest) or not _is_digest(value.edge_digest):
            raise ValueError("invalid scope order digest")
        if len(set(value.atoms)) != len(value.atoms) or set(value.order) != set(value.atoms):
            raise ValueError("scope order must be a permutation of unique atoms")
        if (set(value.source_basis) != set(value.atoms) or
                set(value.source_roles) != set(value.atoms) or
                set(value.source_anchors) != set(value.atoms)):
            raise ValueError("scope order source evidence does not cover atoms")
        if any(len(pair) != 2 or pair[0] not in value.atoms or pair[1] not in value.atoms
               for pair in value.constraints):
            raise ValueError("invalid scope order constraint")
        return value


@dataclass(frozen=True)
class NarrativeOrder:
    repo_key: str
    repository_revision: str | None
    repository_input_digest: str | None
    workspace_digest: str
    source_digest: str
    config_digest: str
    implementation_digest: str
    scopes: tuple[ScopeOrder, ...]
    diagnostics: tuple[dict, ...]
    narrative_order_id: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, *, repo_key, repository_revision, repository_input_digest,
               workspace_digest, source_digest, config_digest, scopes, diagnostics=()):
        value = cls(repo_key, repository_revision, repository_input_digest, workspace_digest,
                    source_digest, config_digest, ORDER_IMPLEMENTATION_DIGEST,
                    tuple(sorted(scopes, key=lambda scope: (scope.scope_id, scope.parent_id))),
                    tuple(diagnostics), "")
        return cls(**{**value.to_dict(), "scopes": value.scopes, "diagnostics": value.diagnostics,
                      "narrative_order_id": _narrative_order_id(value)})

    @classmethod
    def from_dict(cls, data):
        expected = {"repo_key", "repository_revision", "repository_input_digest", "workspace_digest",
                    "source_digest", "config_digest", "implementation_digest", "scopes",
                    "diagnostics", "narrative_order_id"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("invalid narrative order fields")
        if (not isinstance(data["scopes"], list) or not isinstance(data["diagnostics"], list) or
                any(not isinstance(item, dict) for item in data["diagnostics"])):
            raise ValueError("invalid narrative order field types")
        value = cls(data["repo_key"], data["repository_revision"], data["repository_input_digest"],
                    data["workspace_digest"], data["source_digest"], data["config_digest"],
                    data["implementation_digest"], tuple(ScopeOrder.from_dict(scope) for scope in data["scopes"]),
                    tuple(dict(item) for item in data["diagnostics"]), data["narrative_order_id"])
        for name in ("workspace_digest", "source_digest", "config_digest", "implementation_digest"):
            if not _is_digest(getattr(value, name)):
                raise ValueError(f"invalid narrative order {name}")
        if (not isinstance(value.repo_key, str) or not value.repo_key or
                value.repository_revision is not None and not isinstance(value.repository_revision, str) or
                value.repository_input_digest is not None and not _is_digest(value.repository_input_digest) or
                not isinstance(value.narrative_order_id, str)):
            raise ValueError("invalid narrative order repository identity")
        identities = [(scope.scope_id, scope.parent_id) for scope in value.scopes]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate narrative scope order")
        if identities != sorted(identities):
            raise ValueError("narrative scope orders are not canonical")
        if value.implementation_digest != ORDER_IMPLEMENTATION_DIGEST:
            raise ValueError("narrative order implementation digest mismatch")
        if value.narrative_order_id != _narrative_order_id(value):
            raise ValueError("narrative order identity mismatch")
        return value

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    @classmethod
    def load(cls, path):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result
        return cls.from_dict(json.loads(Path(path).read_text(), object_pairs_hook=unique))


def _narrative_order_id(value):
    payload = value.to_dict()
    payload.pop("narrative_order_id", None)
    return "narrative-order:" + digest(payload)[:24]


def _path_exists(successors, start, target):
    pending, seen = [start], set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        pending.extend(successors[node] - seen)
    return False


def _protected_constraints(problem):
    successors = {atom: set() for atom in problem.atoms}
    for edge in problem.edges:
        successors[edge.provider].add(edge.consumer)
    accepted, diagnostics = [], []
    for sequence_index, sequence in enumerate(problem.protected_sequences):
        for earlier, later in zip(sequence, sequence[1:]):
            if earlier == later or later in successors[earlier]:
                continue
            if _path_exists(successors, later, earlier):
                diagnostics.append({"code": "dependency_overrides_source_order",
                                    "scope_id": problem.scope_id, "earlier": earlier,
                                    "later": later, "sequence": sequence_index})
                continue
            successors[earlier].add(later)
            accepted.append((earlier, later))
    return tuple(accepted), tuple(diagnostics)


def _graph(problem, constraints):
    successors = {atom: set() for atom in problem.atoms}
    predecessors = {atom: set() for atom in problem.atoms}
    for provider, consumer in [*((edge.provider, edge.consumer) for edge in problem.edges), *constraints]:
        successors[provider].add(consumer)
        predecessors[consumer].add(provider)
    return successors, predecessors


def _source_order(problem, constraints):
    successors, predecessors = _graph(problem, constraints)
    degree = {atom: len(predecessors[atom]) for atom in problem.atoms}
    ready = {atom for atom in problem.atoms if not degree[atom]}
    result = []
    while ready:
        atom = min(ready, key=lambda value: (_tupleize(problem.source_keys[value]), value))
        ready.remove(atom)
        result.append(atom)
        for child in successors[atom]:
            degree[child] -= 1
            if degree[child] == 0:
                ready.add(child)
    if len(result) != len(problem.atoms):
        raise ValueError(f"cyclic order problem at {problem.scope_id}")
    return tuple(result)


def _reverse_frontier(problem, constraints):
    successors, predecessors = _graph(problem, constraints)
    remaining_successors = {atom: len(successors[atom]) for atom in problem.atoms}
    ready = {atom for atom in problem.atoms if not remaining_successors[atom]}
    outgoing = {atom: 0 for atom in problem.atoms}
    incoming = {atom: 0 for atom in problem.atoms}
    for edge in problem.edges:
        outgoing[edge.provider] += edge.weight
        incoming[edge.consumer] += edge.weight
    reverse = []
    while ready:
        atom = max(ready, key=lambda value: (outgoing[value] - incoming[value],
                                             _tupleize(problem.source_keys[value]), value))
        ready.remove(atom)
        reverse.append(atom)
        for parent in predecessors[atom]:
            remaining_successors[parent] -= 1
            if remaining_successors[parent] == 0:
                ready.add(parent)
    if len(reverse) != len(problem.atoms):
        raise ValueError(f"cyclic order problem at {problem.scope_id}")
    return tuple(reversed(reverse))


def dependency_distance(problem, order):
    positions = {atom: index for index, atom in enumerate(order)}
    return sum(edge.weight * (positions[edge.consumer] - positions[edge.provider]) for edge in problem.edges)


def _projected_edge_distance(problem, order):
    positions = {atom: index for index, atom in enumerate(order)}
    return sum(positions[edge.consumer] - positions[edge.provider] for edge in problem.edges)


def _source_displacement(problem, order):
    baseline = sorted(problem.atoms, key=lambda atom: (_tupleize(problem.source_keys[atom]), atom))
    wanted = {atom: index for index, atom in enumerate(baseline)}
    return sum(abs(index - wanted[atom]) for index, atom in enumerate(order))


def _legal(order, successors):
    positions = {atom: index for index, atom in enumerate(order)}
    return all(positions[parent] < positions[child]
               for parent, children in successors.items() for child in children)


def _improve(problem, constraints, initial):
    successors, predecessors = _graph(problem, constraints)
    coefficients = {atom: 0 for atom in problem.atoms}
    for edge in problem.edges:
        coefficients[edge.provider] -= edge.weight
        coefficients[edge.consumer] += edge.weight
    wanted_order = sorted(problem.atoms, key=lambda atom: (_tupleize(problem.source_keys[atom]), atom))
    wanted = {atom: index for index, atom in enumerate(wanted_order)}
    current = tuple(initial)
    current_distance = dependency_distance(problem, current)
    current_displacement = _source_displacement(problem, current)
    while True:
        positions = {atom: index for index, atom in enumerate(current)}
        coefficient_prefix = [0]
        shift_right_prefix = [0]
        shift_left_prefix = [0]
        for index, atom in enumerate(current):
            coefficient_prefix.append(coefficient_prefix[-1] + coefficients[atom])
            shift_right_prefix.append(shift_right_prefix[-1] +
                                      abs(index + 1 - wanted[atom]) - abs(index - wanted[atom]))
            shift_left_prefix.append(shift_left_prefix[-1] +
                                     abs(index - 1 - wanted[atom]) - abs(index - wanted[atom]))
        best = None
        for old_index, atom in enumerate(current):
            lower = max((positions[parent] + 1 for parent in predecessors[atom]), default=0)
            upper = min((positions[child] - 1 for child in successors[atom]), default=len(current) - 1)
            for new_index in range(lower, upper + 1):
                if new_index == old_index:
                    continue
                if new_index < old_index:
                    coefficient_delta = (coefficients[atom] * (new_index - old_index) +
                                         coefficient_prefix[old_index] - coefficient_prefix[new_index])
                    displacement_delta = (abs(new_index - wanted[atom]) - abs(old_index - wanted[atom]) +
                                          shift_right_prefix[old_index] - shift_right_prefix[new_index])
                else:
                    coefficient_delta = (coefficients[atom] * (new_index - old_index) -
                                         (coefficient_prefix[new_index + 1] - coefficient_prefix[old_index + 1]))
                    displacement_delta = (abs(new_index - wanted[atom]) - abs(old_index - wanted[atom]) +
                                          shift_left_prefix[new_index + 1] - shift_left_prefix[old_index + 1])
                distance = current_distance + coefficient_delta
                if distance >= current_distance:
                    continue
                choice = (distance, current_displacement + displacement_delta, atom, new_index)
                if best is None or choice < best:
                    best = choice
        if best is None:
            return current
        current_distance, current_displacement, atom, new_index = best
        old_index = current.index(atom)
        changed = list(current)
        changed.pop(old_index)
        changed.insert(new_index, atom)
        current = tuple(changed)


def _weighted_quantile(values, quantile):
    total = sum(weight for _, weight in values)
    if not total:
        return 0
    threshold = max(1, math.ceil(total * quantile))
    cumulative = 0
    for value, weight in sorted(values):
        cumulative += weight
        if cumulative >= threshold:
            return value
    raise AssertionError("weighted quantile has no value")


def _ready_stats(problem, constraints):
    successors, predecessors = _graph(problem, constraints)
    degree = {atom: len(predecessors[atom]) for atom in problem.atoms}
    ready = {atom for atom in problem.atoms if not degree[atom]}
    ambiguous_steps = 0
    maximum = 0
    while ready:
        ambiguous_steps += len(ready) > 1
        maximum = max(maximum, len(ready))
        atom = min(ready, key=lambda value: (_tupleize(problem.source_keys[value]), value))
        ready.remove(atom)
        for child in successors[atom]:
            degree[child] -= 1
            if not degree[child]:
                ready.add(child)
    return ambiguous_steps, maximum


def _role_metrics(problem, order):
    positions = {atom: index for index, atom in enumerate(order)}
    result = {}
    for role in ("primary", "supporting", "reference"):
        members = [atom for atom in problem.atoms if problem.source_roles[atom] == role]
        source_members = sorted(members, key=lambda atom: (_tupleize(problem.source_keys[atom]), atom))
        rank = {atom: index for index, atom in enumerate(source_members)}
        final_members = sorted(members, key=positions.get)
        inversions = sum(rank[a] > rank[b] for index, a in enumerate(final_members)
                         for b in final_members[index + 1:])
        result[role] = {
            "atoms": len(members),
            "source_rank_displacement": sum(abs(index - rank[atom])
                                            for index, atom in enumerate(final_members)),
            "source_inversions": inversions,
        }
    return result


def _metrics(problem, constraints, source_start, reverse_start, order):
    positions = {atom: index for index, atom in enumerate(order)}
    spans = [(positions[edge.consumer] - positions[edge.provider], edge.weight) for edge in problem.edges]
    frontier = []
    for cut in range(max(0, len(order) - 1)):
        frontier.append(sum(edge.weight for edge in problem.edges
                            if positions[edge.provider] <= cut < positions[edge.consumer]))
    last_use = []
    for atom in order:
        consumers = [positions[edge.consumer] for edge in problem.edges if edge.provider == atom]
        if consumers:
            last_use.append(max(consumers) - positions[atom])
    final_distance = dependency_distance(problem, order)
    if sum(frontier) != final_distance:
        raise AssertionError("frontier area does not equal dependency distance")
    ambiguous_steps, maximum_ready = _ready_stats(problem, constraints)
    basis_coverage = {basis: sum(value == basis for value in problem.source_basis.values())
                      for basis in sorted(set(problem.source_basis.values()))}
    return {
        "projected_edge_count": len(problem.edges),
        "edge_weight": sum(edge.weight for edge in problem.edges),
        "dependency_distance": final_distance,
        "source_start_distance": dependency_distance(problem, source_start),
        "reverse_start_distance": dependency_distance(problem, reverse_start),
        "projected_edge_distance": _projected_edge_distance(problem, order),
        "source_start_projected_edge_distance": _projected_edge_distance(problem, source_start),
        "reverse_start_projected_edge_distance": _projected_edge_distance(problem, reverse_start),
        "mean_dependency_span": final_distance / sum(edge.weight for edge in problem.edges) if problem.edges else 0,
        "dependency_span_p50": _weighted_quantile(spans, 0.50),
        "dependency_span_p90": _weighted_quantile(spans, 0.90),
        "max_dependency_span": max((value for value, _ in spans), default=0),
        "frontier_area": sum(frontier),
        "frontier_peak": max(frontier, default=0),
        "frontier_mean": sum(frontier) / len(frontier) if frontier else 0,
        "last_use_sum": sum(last_use),
        "last_use_max": max(last_use, default=0),
        "source_displacement": _source_displacement(problem, order),
        "changed_atoms_from_source_start": sum(a != b for a, b in zip(order, source_start)),
        "ready_ambiguous_steps": ambiguous_steps,
        "ready_maximum_width": maximum_ready,
        "source_basis_coverage": basis_coverage,
        "source_role_order": _role_metrics(problem, order),
    }


def solve_order(problem):
    constraints, diagnostics = _protected_constraints(problem)
    source_start = _source_order(problem, constraints)
    reverse_start = _reverse_frontier(problem, constraints)
    starts = (source_start, reverse_start)
    initial = min(starts, key=lambda order: (dependency_distance(problem, order),
                                             _source_displacement(problem, order), order))
    order = _improve(problem, constraints, initial)
    result = ScopeOrder(problem.scope_id, problem.parent_id, problem.atom_digest, problem.edge_digest,
                        tuple(sorted(problem.atoms)), order, constraints,
                        _metrics(problem, constraints, source_start, reverse_start, order),
                        dict(sorted(problem.source_basis.items())),
                        dict(sorted(problem.source_roles.items())),
                        dict(sorted(problem.source_anchors.items())), diagnostics)
    validate_scope_order(problem, result)
    return result


def validate_scope_order(problem, result):
    if (result.scope_id, result.parent_id) != (problem.scope_id, problem.parent_id):
        raise ValueError("narrative scope identity mismatch")
    if result.atom_digest != problem.atom_digest or result.edge_digest != problem.edge_digest:
        raise ValueError("stale narrative scope order")
    if set(result.atoms) != set(problem.atoms) or set(result.order) != set(problem.atoms):
        raise ValueError("narrative scope order atom mismatch")
    if (result.source_basis != problem.source_basis or result.source_roles != problem.source_roles or
            result.source_anchors != problem.source_anchors):
        raise ValueError("narrative scope source evidence mismatch")
    constraints, diagnostics = _protected_constraints(problem)
    if result.constraints != constraints or result.diagnostics != diagnostics:
        raise ValueError("narrative scope protected constraints mismatch")
    successors, _ = _graph(problem, constraints)
    if not _legal(result.order, successors):
        raise ValueError("narrative scope order violates constraints")
    source_start = _source_order(problem, constraints)
    reverse_start = _reverse_frontier(problem, constraints)
    if result.metrics != _metrics(problem, constraints, source_start, reverse_start, result.order):
        raise ValueError("narrative scope metrics mismatch")
    return result.order
