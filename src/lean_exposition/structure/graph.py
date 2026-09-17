"""Deterministic indexes and graph queries over immutable workspace facts.

Edges point from provider to consumer. Unloaded providers remain references;
ordering and projection only use loaded declarations. No reading aggregation is
performed here, and source asset manifest order has no narrative significance.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
import heapq
from types import MappingProxyType
from typing import Mapping, Iterable, TypeVar, Generic

from lean_exposition.models.facts import DeclRef, Provenance, RawDecl, Scope, Workspace

MATHEMATICAL_EVIDENCE_KINDS = frozenset({"lc_declared", "lean_type", "lean_value"})
T = TypeVar("T")


def _ref_key(ref: DeclRef):
    return ref.repo_key, ref.local_id


def _freeze(mapping):
    return MappingProxyType({key: mapping[key] for key in sorted(
        mapping, key=lambda key: _ref_key(key) if isinstance(key, DeclRef) else key)})


@dataclass(frozen=True)
class EvidenceOccurrence:
    part: str
    evidence_kind: str
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class DependencyEdge:
    provider: DeclRef
    consumer: DeclRef
    occurrences: tuple[EvidenceOccurrence, ...]


@dataclass(frozen=True)
class Boundary:
    coverage: frozenset[DeclRef]
    incoming: tuple[DependencyEdge, ...]
    outgoing: tuple[DependencyEdge, ...]
    internal: tuple[DependencyEdge, ...]
    input_decls: frozenset[DeclRef]
    output_decls: frozenset[DeclRef]

    @property
    def incoming_pair_count(self):
        return len(self.incoming)

    @property
    def incoming_provider_count(self):
        return len(self.input_decls)

    @property
    def outgoing_pair_count(self):
        return len(self.outgoing)

    @property
    def outgoing_provider_count(self):
        return len({edge.provider for edge in self.outgoing})


@dataclass(frozen=True)
class Cycle(Generic[T]):
    nodes: tuple[T, ...]
    witness: tuple[T, ...]  # closed directed walk, first node repeated last


@dataclass(frozen=True)
class Ordering(Generic[T]):
    ordered: tuple[T, ...]
    cycles: tuple[Cycle[T], ...]
    blocked: tuple[T, ...]  # downstream of cycles, excluding cyclic nodes

    @property
    def is_acyclic(self):
        return not self.cycles


def _order(nodes, pairs, stable_key, source_priority):
    """Kahn order plus iterative Kosaraju SCCs and actual cycle witnesses."""
    nodes = set(nodes)
    if set(source_priority) - nodes:
        raise ValueError("source priority contains unknown nodes")
    def key(node):
        return (0, source_priority[node], stable_key(node)) if node in source_priority else (1, 0, stable_key(node))
    successors = {node: set() for node in nodes}
    predecessors = {node: set() for node in nodes}
    for provider, consumer in pairs:
        successors[provider].add(consumer)
        predecessors[consumer].add(provider)
    degree = {node: len(predecessors[node]) for node in nodes}
    # Stable integer tie breakers avoid comparing non-orderable DeclRef records.
    rank = {node: i for i, node in enumerate(sorted(nodes, key=stable_key))}
    ready = [(key(node), rank[node], node) for node in nodes if not degree[node]]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, _, node = heapq.heappop(ready)
        ordered.append(node)
        for child in successors[node]:
            degree[child] -= 1
            if not degree[child]:
                heapq.heappush(ready, (key(child), rank[child], child))
    remaining = nodes - set(ordered)
    seen, finished = set(), []
    for start in sorted(remaining, key=stable_key):
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(sorted(successors[start] & remaining, key=stable_key)))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(successors[child] & remaining, key=stable_key))))
    seen, cycles, cyclic = set(), [], set()
    for start in reversed(finished):
        if start in seen:
            continue
        component, pending = set(), [start]
        seen.add(start)
        while pending:
            node = pending.pop()
            component.add(node)
            for parent in predecessors[node] & remaining:
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        if len(component) == 1 and start not in successors[start]:
            continue
        members = tuple(sorted(component, key=stable_key))
        cyclic.update(component)
        # Choose the first internal edge, then a deterministic path back.
        first = members[0]
        second = min(successors[first] & component, key=stable_key)
        parents = {second: None}
        pending = [second]
        for node in pending:
            if node == first:
                break
            for child in sorted(successors[node] & component, key=stable_key):
                if child not in parents:
                    parents[child] = node
                    pending.append(child)
        path, node = [], first
        while node is not None:
            path.append(node)
            node = parents[node]
        cycles.append(Cycle(members, (first, *reversed(path))))
    cycles.sort(key=lambda cycle: stable_key(cycle.nodes[0]))
    return Ordering(tuple(ordered), tuple(cycles), tuple(sorted(remaining - cyclic, key=key)))


@dataclass(frozen=True)
class ProjectedEdge:
    provider: str
    consumer: str
    base_edges: tuple[DependencyEdge, ...]

    @property
    def pair_count(self):
        return len(self.base_edges)

    @property
    def provider_count(self):
        return len({edge.provider for edge in self.base_edges})


@dataclass(frozen=True)
class Projection:
    groups: Mapping[str, frozenset[DeclRef]]
    edges: tuple[ProjectedEdge, ...]
    internal_edges: Mapping[str, tuple[DependencyEdge, ...]]
    uncovered: frozenset[DeclRef]
    boundary: Boundary
    source_keys: Mapping[str, tuple]

    def order(self, source_priority: Mapping[str, int] | None = None) -> Ordering[str]:
        return _order(self.groups, ((edge.provider, edge.consumer) for edge in self.edges),
                      lambda node: self.source_keys[node], source_priority or {})


@dataclass(frozen=True)
class RepositoryView:
    """One repository's presentation boundary; other repos only resolve refs.

    External means outside this target repository, including already loaded
    providers. The view does not create an EET or aggregate reading units.
    """
    repo_key: str
    root_scope: str
    coverage: frozenset[DeclRef]
    scopes: Mapping[str, Scope]
    boundary: Boundary
    external_refs: frozenset[DeclRef]
    unloaded_local_refs: frozenset[DeclRef]
    external_declarations: Mapping[DeclRef, RawDecl | None]
    _graph: DependencyGraph = field(repr=False, compare=False)

    def external_decl(self, ref: DeclRef) -> RawDecl | None:
        """Resolve a referenced external provider; unrelated refs raise KeyError."""
        return self.external_declarations[ref]

    def order(self, source_priority: Mapping[DeclRef, int] | None = None) -> Ordering[DeclRef]:
        return self._graph.order(source_priority, refs=self.coverage)

    def scope_children(self, scope_id: str) -> Projection:
        """Project a target Scope; uncovered declarations stay target-relative."""
        if scope_id not in self.scopes:
            raise ValueError("scope is outside the target repository")
        projection = self._graph.scope_children(scope_id)
        return replace(projection, uncovered=self.coverage - projection.boundary.coverage)

    def root_projection(self) -> Projection:
        return self.scope_children(self.root_scope)


@dataclass(frozen=True)
class DependencyGraph:
    workspace: Workspace
    evidence_kinds: frozenset[str]
    edges: tuple[DependencyEdge, ...]
    incoming: Mapping[DeclRef, tuple[DependencyEdge, ...]]
    outgoing: Mapping[DeclRef, tuple[DependencyEdge, ...]]
    loaded_decls: frozenset[DeclRef]
    unloaded_refs: frozenset[DeclRef]
    representative_owner: Mapping[DeclRef, str]
    top_level_owner: Mapping[DeclRef, str]
    unit_coverages: Mapping[str, frozenset[DeclRef]]
    native_scope_coverages: Mapping[str, frozenset[DeclRef]]
    reading_scope_coverages: Mapping[str, frozenset[DeclRef]]
    repository_coverages: Mapping[str, frozenset[DeclRef]]
    source_keys: Mapping[DeclRef, tuple]

    @classmethod
    def from_workspace(cls, workspace: Workspace, *,
                       evidence_kinds: Iterable[str] = MATHEMATICAL_EVIDENCE_KINDS):
        workspace.validate()
        if isinstance(evidence_kinds, str):
            raise ValueError("evidence kinds must be an iterable of strings, not a string")
        kinds = frozenset(evidence_kinds)
        if any(not isinstance(kind, str) or not kind.strip() for kind in kinds):
            raise ValueError("evidence kinds must be nonempty strings")
        decls = {decl.ref: decl for decl in workspace.declarations}
        occurrences = defaultdict(list)
        for decl in workspace.declarations:
            for part in ("statement", "proof"):
                content = getattr(decl, part)
                if content is not None:
                    for dep in content.deps:
                        if dep.evidence_kind in kinds:
                            occurrences[dep.provider, decl.ref].append(
                                EvidenceOccurrence(part, dep.evidence_kind, dep.provenance))
        edges = tuple(DependencyEdge(provider, consumer, tuple(sorted(values, key=repr)))
                      for (provider, consumer), values in sorted(occurrences.items(),
                          key=lambda item: (_ref_key(item[0][0]), _ref_key(item[0][1]))))
        all_refs = set(decls) | {edge.provider for edge in edges}
        incoming, outgoing = {ref: [] for ref in all_refs}, {ref: [] for ref in all_refs}
        for edge in edges:
            incoming[edge.consumer].append(edge)
            outgoing[edge.provider].append(edge)
        units = {unit.unit_id: unit for unit in workspace.units}
        parents = {member: unit.unit_id for unit in workspace.units for member in unit.members}
        representative = {unit.representative: unit.unit_id for unit in workspace.units}
        top_owner, unit_coverage = {}, {}
        # A postorder avoids Python recursion depth limits for unit forests.
        pending = [(unit_id, False, unit_id) for unit_id in sorted(units) if unit_id not in parents]
        while pending:
            unit_id, visited, root = pending.pop()
            unit = units[unit_id]
            if visited:
                unit_coverage[unit_id] = frozenset({unit.representative}).union(
                    *(unit_coverage[member] for member in unit.members))
            else:
                top_owner[unit.representative] = root
                pending.append((unit_id, True, root))
                pending.extend((member, False, root) for member in unit.members)
        scopes = {scope.scope_id: scope for scope in workspace.scopes}
        native, reading = {scope: set() for scope in scopes}, {scope: set() for scope in scopes}
        for ref, decl in decls.items():
            scope = decl.native_scope
            while scope is not None:
                native[scope].add(ref)
                scope = scopes[scope].parent
        for unit_id in sorted(set(top_owner.values())):
            scope = decls[units[unit_id].representative].native_scope
            while scope is not None:
                reading[scope].update(unit_coverage[unit_id])
                scope = scopes[scope].parent
        repos = {repo.repo_key: frozenset(ref for ref in decls if ref.repo_key == repo.repo_key)
                 for repo in workspace.manifest.repositories}
        assets = {asset.asset_id: asset for asset in workspace.manifest.assets}
        source_keys = {}
        for ref, decl in decls.items():
            positions = [(assets[pos.asset_id].path, pos.start_line, pos.start_column or 0,
                          pos.end_line, pos.end_column or 0, pos.asset_id) for pos in decl.source_refs]
            source_keys[ref] = (0, min(positions), _ref_key(ref)) if positions else (1, (), _ref_key(ref))
        return cls(workspace, kinds, edges,
                   _freeze({ref: tuple(incoming[ref]) for ref in sorted(all_refs, key=_ref_key)}),
                   _freeze({ref: tuple(outgoing[ref]) for ref in sorted(all_refs, key=_ref_key)}),
                   frozenset(decls), frozenset(all_refs - set(decls)),
                   _freeze(representative), _freeze(top_owner), _freeze(unit_coverage),
                   _freeze({scope: frozenset(refs) for scope, refs in native.items()}),
                   _freeze({scope: frozenset(refs) for scope, refs in reading.items()}),
                   _freeze(repos), _freeze(source_keys))

    def analysis_view(self, analysis):
        """Return a filtered query view while preserving the complete graph."""
        if analysis.target_repo_key not in self.repository_coverages:
            raise ValueError("dependency analysis target is absent from the graph")
        pairs = {(edge.provider, edge.consumer) for edge in self.edges}
        decisions = {(item.provider, item.consumer) for item in analysis.decisions}
        if decisions != pairs:
            raise ValueError("dependency analysis does not match the complete graph")
        kept = tuple(edge for edge in self.edges
                     if (edge.provider, edge.consumer) in analysis.kept_pairs)
        all_refs = set(self.loaded_decls) | {edge.provider for edge in kept}
        incoming = {ref: [] for ref in all_refs}
        outgoing = {ref: [] for ref in all_refs}
        for edge in kept:
            incoming[edge.consumer].append(edge)
            outgoing[edge.provider].append(edge)
        return replace(
            self, edges=kept,
            incoming=_freeze({ref: tuple(incoming[ref]) for ref in all_refs}),
            outgoing=_freeze({ref: tuple(outgoing[ref]) for ref in all_refs}),
            unloaded_refs=frozenset(all_refs - self.loaded_decls),
        )

    def repository_view(self, repo_key: str) -> RepositoryView:
        """Select one loaded repository for presentation, retaining reference context."""
        repo = next((repo for repo in self.workspace.manifest.repositories if repo.repo_key == repo_key), None)
        if repo is None or repo.root_scope is None:
            raise ValueError(f"repository needs a loaded root Scope: {repo_key}")
        coverage = self.repository_coverages[repo_key]
        boundary = self.boundary(coverage)
        external = frozenset(ref for ref in boundary.input_decls if ref.repo_key != repo_key)
        local = frozenset(ref for ref in boundary.input_decls if ref.repo_key == repo_key)
        declarations = {decl.ref: decl for decl in self.workspace.declarations}
        return RepositoryView(repo_key, repo.root_scope, coverage,
                              _freeze({scope.scope_id: scope for scope in self.workspace.scopes
                                       if scope.repo_key == repo_key}),
                              boundary, external, local,
                              _freeze({ref: declarations.get(ref) for ref in external}), self)

    def _coverage(self, refs):
        coverage = frozenset(refs)
        if coverage - self.loaded_decls:
            raise ValueError("coverage contains unloaded declarations")
        return coverage

    def boundary(self, refs: Iterable[DeclRef]) -> Boundary:
        # Outgoing usage is observed workspace usage, not unknown future consumers.
        coverage = self._coverage(refs)
        incoming, outgoing, internal = [], [], []
        for edge in self.edges:
            provider, consumer = edge.provider in coverage, edge.consumer in coverage
            if provider and consumer:
                internal.append(edge)
            elif provider:
                outgoing.append(edge)
            elif consumer:
                incoming.append(edge)
        outcomes = {ref for repo in self.workspace.manifest.repositories for ref in repo.primary_outcomes}
        return Boundary(coverage, tuple(incoming), tuple(outgoing), tuple(internal),
                        frozenset(edge.provider for edge in incoming),
                        frozenset(edge.provider for edge in outgoing) | (outcomes & coverage))

    @property
    def unloaded_primary_outcomes(self) -> frozenset[DeclRef]:
        return frozenset(ref for repo in self.workspace.manifest.repositories
                         for ref in repo.primary_outcomes if ref not in self.loaded_decls)

    def order(self, source_priority: Mapping[DeclRef, int] | None = None, *,
              refs: Iterable[DeclRef] | None = None) -> Ordering[DeclRef]:
        """Order loaded declarations; outside prerequisites remain boundary refs.

        source_priority is an explicitly trusted caller-supplied priority, with
        lower numbers first. It only breaks ties among dependency-ready nodes.
        """
        selected = self.loaded_decls if refs is None else self._coverage(refs)
        return _order(selected,
                      ((edge.provider, edge.consumer) for edge in self.edges
                       if edge.provider in selected and edge.consumer in selected),
                      lambda ref: self.source_keys[ref], source_priority or {})

    def project(self, groups: Mapping[str, Iterable[DeclRef]]) -> Projection:
        coverage, owner, normalized = set(), {}, {}
        if any(not isinstance(name, str) or not name.strip() for name in groups):
            raise ValueError("group names must be nonempty strings")
        for name in sorted(groups):
            refs = self._coverage(groups[name])
            if coverage & refs:
                raise ValueError("projection groups overlap")
            coverage.update(refs)
            normalized[name] = refs
            owner.update((ref, name) for ref in refs)
        cross, internal = defaultdict(list), {name: [] for name in normalized}
        for edge in self.edges:
            if edge.provider in owner and edge.consumer in owner:
                provider, consumer = owner[edge.provider], owner[edge.consumer]
                if provider == consumer:
                    internal[provider].append(edge)
                else:
                    cross[provider, consumer].append(edge)
        source_keys = {name: (0, min(self.source_keys[ref] for ref in refs), name) if refs else (1, (), name)
                       for name, refs in normalized.items()}
        return Projection(_freeze(normalized),
                          tuple(ProjectedEdge(provider, consumer, tuple(values))
                                for (provider, consumer), values in sorted(cross.items())),
                          _freeze({name: tuple(values) for name, values in internal.items()}),
                          self.loaded_decls - coverage, self.boundary(coverage), _freeze(source_keys))

    def scope_children(self, scope_id: str) -> Projection:
        """Project direct child scopes and direct top-level units, with exact cover.

        Native and reading coverage must coincide throughout this subtree. A
        cross-scope unit is a conflict even when its representative lies outside
        this subtree. Facts-only workspaces may use project() for explicit groups.
        """
        expected = self.native_scope_coverages[scope_id]
        scopes = {scope.scope_id: scope for scope in self.workspace.scopes}
        descendants, pending = set(), [scope_id]
        children = defaultdict(list)
        for scope in scopes.values():
            children[scope.parent].append(scope.scope_id)
        while pending:
            current = pending.pop()
            descendants.add(current)
            pending.extend(children[current])
        conflicts = sorted(scope for scope in descendants
                           if self.native_scope_coverages[scope] != self.reading_scope_coverages[scope])
        if conflicts:
            raise ValueError(f"native/reading scope coverage conflict (or absent unit forest): {conflicts}")
        groups = {f"scope:{child}": self.reading_scope_coverages[child] for child in children[scope_id]}
        decls = {decl.ref: decl for decl in self.workspace.declarations}
        for unit in self.workspace.units:
            if self.top_level_owner[unit.representative] == unit.unit_id and decls[unit.representative].native_scope == scope_id:
                groups[f"unit:{unit.unit_id}"] = self.unit_coverages[unit.unit_id]
        projection = self.project(groups)
        if projection.boundary.coverage != expected:
            raise ValueError("scope children do not exactly cover native declarations")
        return projection
