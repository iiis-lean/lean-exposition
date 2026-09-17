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
from typing import Mapping

from lean_exposition.models import DeclRef, Provenance, SourceRange


ORDER_IMPLEMENTATION = {
    "constraints": "real_dependencies_then_canonical_acyclic_protected_relations",
    "edge_weight": "distinct_base_declaration_pairs",
    "starts": ["source_kahn", "reverse_frontier"],
    "objective": "weighted_atom_distance",
    "improvement": "strict_best_legal_insertion",
    "tie_break": "soft_relations_then_source_displacement_then_stable_identity",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


ORDER_IMPLEMENTATION_DIGEST = digest(ORDER_IMPLEMENTATION)

ORDER_EVIDENCE_IMPLEMENTATION = {
    "manual_validation": "protected_manual_subgraph_must_be_acyclic",
    "projection": "exact_unique_direct_atom_only",
    "protected_conflicts": "canonical_acyclic_subset_after_dependencies",
    "tie_breaker": "ready_frontier_preference_only",
}
ORDER_EVIDENCE_IMPLEMENTATION_DIGEST = digest(ORDER_EVIDENCE_IMPLEMENTATION)

ORDER_SUBJECT_KINDS = ("material_record", "declaration", "scope", "module", "source_range")
ORDER_STRENGTHS = ("protected", "tie_breaker")
PRODUCER_AUTHORITY = {
    "manual_config": 0,
    "authoritative_route": 1,
    "lc_tex_document": 2,
    "ordered_files": 3,
    "lean_modules": 3,
    "automatic_tex": 4,
    "fuzzy_match": 9,
}


def _is_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _tupleize(value):
    if isinstance(value, list):
        return tuple(_tupleize(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, _tupleize(item)) for key, item in sorted(value.items()))
    return value


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")


def _validate_provenance(values):
    if not values:
        raise ValueError("order evidence provenance is required")
    for value in values:
        _nonempty(value.method, "order provenance method")
        _nonempty(value.source_ref, "order provenance source_ref")
        for location in value.ranges:
            _validate_source_range(location)


def _validate_source_range(value):
    if not isinstance(value, SourceRange):
        raise ValueError("invalid order source range")
    _nonempty(value.asset_id, "order source range asset_id")
    if (type(value.start_line) is not int or type(value.end_line) is not int or
            value.start_line < 1 or value.end_line < value.start_line):
        raise ValueError("invalid order source range lines")
    for column in (value.start_column, value.end_column):
        if column is not None and (type(column) is not int or column < 1):
            raise ValueError("invalid order source range column")


@dataclass(frozen=True)
class OrderSubject:
    kind: str
    identifier: str | None = None
    ref: DeclRef | None = None
    source_range: SourceRange | None = None

    def __post_init__(self):
        if self.kind not in ORDER_SUBJECT_KINDS:
            raise ValueError("invalid order subject kind")
        if self.kind == "declaration":
            if self.ref is None or self.identifier is not None or self.source_range is not None:
                raise ValueError("declaration order subject needs one DeclRef")
            _nonempty(self.ref.repo_key, "order declaration repo_key")
            _nonempty(self.ref.local_id, "order declaration local_id")
        elif self.kind == "source_range":
            if self.source_range is None or self.identifier is not None or self.ref is not None:
                raise ValueError("source range order subject needs one range")
            _validate_source_range(self.source_range)
        elif (not isinstance(self.identifier, str) or not self.identifier or
              self.ref is not None or self.source_range is not None):
            raise ValueError("named order subject needs one identifier")

    def canonical_key(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class OrderRelation:
    relation_id: str
    before: OrderSubject
    after: OrderSubject
    strength: str
    basis: str
    producer: str
    source_order: int
    provenance: tuple[Provenance, ...]

    def __post_init__(self):
        _nonempty(self.relation_id, "order relation_id")
        _nonempty(self.basis, "order relation basis")
        if self.before == self.after:
            raise ValueError("order relation endpoints must differ")
        if self.strength not in ORDER_STRENGTHS:
            raise ValueError("invalid order relation strength")
        if self.producer not in PRODUCER_AUTHORITY:
            raise ValueError("invalid order relation producer")
        if self.strength == "protected" and self.producer in {"automatic_tex", "fuzzy_match"}:
            raise ValueError("automatic or fuzzy order evidence cannot be protected")
        if type(self.source_order) is not int or self.source_order < 0:
            raise ValueError("order relation source_order must be nonnegative")
        _validate_provenance(self.provenance)

    def canonical_key(self):
        return (PRODUCER_AUTHORITY[self.producer], self.source_order, self.relation_id,
                self.before.canonical_key(), self.after.canonical_key())


@dataclass(frozen=True)
class OrderSequence:
    sequence_id: str
    members: tuple[OrderSubject, ...]
    strength: str
    basis: str
    producer: str
    source_order: int
    provenance: tuple[Provenance, ...]

    def __post_init__(self):
        _nonempty(self.sequence_id, "order sequence_id")
        _nonempty(self.basis, "order sequence basis")
        if len(self.members) < 2 or len(set(self.members)) != len(self.members):
            raise ValueError("order sequence needs distinct members")
        if self.strength not in ORDER_STRENGTHS or self.producer not in PRODUCER_AUTHORITY:
            raise ValueError("invalid order sequence policy")
        if self.strength == "protected" and self.producer in {"automatic_tex", "fuzzy_match"}:
            raise ValueError("automatic or fuzzy order evidence cannot be protected")
        if type(self.source_order) is not int or self.source_order < 0:
            raise ValueError("order sequence source_order must be nonnegative")
        _validate_provenance(self.provenance)

    def relations(self):
        return tuple(OrderRelation(
            f"{self.sequence_id}:{index}", before, after, self.strength, self.basis,
            self.producer, self.source_order + index, self.provenance,
        ) for index, (before, after) in enumerate(zip(self.members, self.members[1:])))


@dataclass(frozen=True)
class OrderEvidenceBundle:
    material_digest: str
    binding_digest: str
    relations: tuple[OrderRelation, ...] = ()
    sequences: tuple[OrderSequence, ...] = ()
    diagnostics: tuple[dict, ...] = ()
    implementation_digest: str = ORDER_EVIDENCE_IMPLEMENTATION_DIGEST

    def __post_init__(self):
        for label, value in (("material", self.material_digest), ("binding", self.binding_digest),
                             ("implementation", self.implementation_digest)):
            if not _is_digest(value):
                raise ValueError(f"invalid order evidence {label} digest")
        if self.implementation_digest != ORDER_EVIDENCE_IMPLEMENTATION_DIGEST:
            raise ValueError("order evidence implementation digest mismatch")
        relation_ids = [relation.relation_id for relation in self.expanded_relations()]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("duplicate order relation ID")
        sequence_ids = [sequence.sequence_id for sequence in self.sequences]
        if len(sequence_ids) != len(set(sequence_ids)):
            raise ValueError("duplicate order sequence ID")
        if tuple(sorted(self.relations, key=lambda item: item.canonical_key())) != self.relations:
            raise ValueError("order relations must be canonical")
        if tuple(sorted(self.sequences, key=lambda item: (
                PRODUCER_AUTHORITY[item.producer], item.source_order, item.sequence_id))) != self.sequences:
            raise ValueError("order sequences must be canonical")
        if any(not isinstance(item, dict) for item in self.diagnostics):
            raise ValueError("order diagnostics must be objects")
        digest(self.diagnostics)
        _validate_manual_dag(self.expanded_relations())

    @classmethod
    def create(cls, *, material_digest, binding_digest, relations=(), sequences=(), diagnostics=()):
        return cls(material_digest, binding_digest,
                   tuple(sorted(relations, key=lambda item: item.canonical_key())),
                   tuple(sorted(sequences, key=lambda item: (
                       PRODUCER_AUTHORITY[item.producer], item.source_order, item.sequence_id))),
                   tuple(diagnostics))

    def expanded_relations(self):
        return (*self.relations, *(relation for sequence in self.sequences
                                   for relation in sequence.relations()))

    def digest(self):
        return digest(self.to_dict())

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def save(self, path):
        Path(path).write_text(self.to_json())

    @classmethod
    def from_dict(cls, data):
        expected = {"material_digest", "binding_digest", "relations", "sequences",
                    "diagnostics", "implementation_digest"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("invalid order evidence fields")
        if not all(isinstance(data[name], list) for name in ("relations", "sequences", "diagnostics")):
            raise ValueError("invalid order evidence arrays")
        return cls(data["material_digest"], data["binding_digest"],
                   tuple(_relation_from_dict(item) for item in data["relations"]),
                   tuple(_sequence_from_dict(item) for item in data["sequences"]),
                   tuple(dict(item) for item in data["diagnostics"]), data["implementation_digest"])

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(_strict_json(text))

    @classmethod
    def load(cls, path):
        return cls.from_json(Path(path).read_text())


@dataclass(frozen=True)
class SubjectProjection:
    status: str
    atoms: tuple[str, ...] = ()

    def __post_init__(self):
        if self.status not in {"exact", "candidate", "ambiguous", "unresolved"}:
            raise ValueError("invalid order subject projection status")
        if any(not isinstance(atom, str) or not atom for atom in self.atoms):
            raise ValueError("invalid projected atom")


@dataclass(frozen=True)
class ProjectedOrderRelation:
    relation_id: str
    provider: str
    consumer: str
    strength: str
    basis: str
    producer: str
    source_order: int

    def __post_init__(self):
        _nonempty(self.relation_id, "projected order relation_id")
        if (not self.provider or not self.consumer or self.provider == self.consumer or
                self.strength not in ORDER_STRENGTHS or self.producer not in PRODUCER_AUTHORITY or
                type(self.source_order) is not int or self.source_order < 0):
            raise ValueError("invalid projected order relation")

    def canonical_key(self):
        return (PRODUCER_AUTHORITY[self.producer], self.source_order, self.relation_id,
                self.provider, self.consumer)


@dataclass(frozen=True)
class ProjectedOrderEvidence:
    protected: tuple[ProjectedOrderRelation, ...]
    tie_breakers: tuple[ProjectedOrderRelation, ...]
    diagnostics: tuple[dict, ...]
    evidence_digest: str


def project_order_evidence(evidence: OrderEvidenceBundle,
                           projections: Mapping[str, SubjectProjection]) -> ProjectedOrderEvidence:
    """Project evidence endpoints without ever forming a Cartesian hard edge."""
    protected, tie_breakers, diagnostics = [], [], []
    for relation in sorted(evidence.expanded_relations(), key=lambda item: item.canonical_key()):
        endpoints = []
        rejection = None
        for side, subject in (("before", relation.before), ("after", relation.after)):
            projection = projections.get(subject.canonical_key(), SubjectProjection("unresolved"))
            atoms = tuple(sorted(set(projection.atoms)))
            if projection.status != "exact":
                rejection = f"{side}_{projection.status}"
                break
            if len(atoms) != 1:
                rejection = f"{side}_non_unique_atom"
                break
            endpoints.append(atoms[0])
        if rejection is not None:
            diagnostics.append({"code": "order_relation_not_projected", "relation_id": relation.relation_id,
                                "reason": rejection})
            continue
        if endpoints[0] == endpoints[1]:
            diagnostics.append({"code": "order_relation_internalized", "relation_id": relation.relation_id,
                                "atom": endpoints[0]})
            continue
        projected = ProjectedOrderRelation(relation.relation_id, endpoints[0], endpoints[1],
                                           relation.strength, relation.basis, relation.producer,
                                           relation.source_order)
        (protected if relation.strength == "protected" else tie_breakers).append(projected)
    return ProjectedOrderEvidence(tuple(sorted(protected, key=lambda item: item.canonical_key())),
                                  tuple(sorted(tie_breakers, key=lambda item: item.canonical_key())),
                                  tuple(diagnostics), evidence.digest())


def material_subject_projections(materials, target_atoms) -> dict[str, SubjectProjection]:
    """Resolve material-record subjects through bindings into direct atoms.

    Multiple exact declaration bindings are valid when their union is still one
    direct atom.  Candidate, ambiguous and unresolved bindings never become an
    exact projection.
    """
    by_record = {record.record_id: [] for record in materials.records}
    for binding in materials.bindings:
        by_record[binding.record_id].append(binding)
    result = {}
    rank = {"exact": 0, "candidate": 1, "ambiguous": 2, "unresolved": 3}
    for record_id, bindings in by_record.items():
        exact = [binding for binding in bindings if binding.status == "exact"]
        if exact:
            atoms = sorted({atom for binding in exact
                            for atom in target_atoms.get(binding.target.canonical_key(), ())})
            status = "exact" if all(binding.target.canonical_key() in target_atoms
                                    for binding in exact) else "unresolved"
        elif bindings:
            status = max((binding.status for binding in bindings), key=lambda item: rank[item])
            atoms = []
        else:
            status, atoms = "unresolved", []
        subject = OrderSubject("material_record", record_id)
        result[subject.canonical_key()] = SubjectProjection(status, tuple(atoms))
    return result


def _validate_manual_dag(relations):
    manual = [item for item in relations
              if item.producer == "manual_config" and item.strength == "protected"]
    nodes = {item.before.canonical_key() for item in manual} | {
        item.after.canonical_key() for item in manual}
    successors = {node: set() for node in nodes}
    for item in manual:
        successors[item.before.canonical_key()].add(item.after.canonical_key())
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("manual protected order evidence is cyclic")
        if node in visited:
            return
        visiting.add(node)
        for child in successors[node]:
            visit(child)
        visiting.remove(node)
        visited.add(node)
    for node in sorted(nodes):
        visit(node)


def _strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def _exact_fields(data, fields, label):
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"invalid {label} fields")
    return data


def _range_from_dict(data):
    if data is None:
        return None
    data = _exact_fields(data, {"asset_id", "start_line", "start_column", "end_line", "end_column"},
                         "source range")
    return SourceRange(data["asset_id"], data["start_line"], data["start_column"],
                       data["end_line"], data["end_column"])


def _subject_from_dict(data):
    data = _exact_fields(data, {"kind", "identifier", "ref", "source_range"}, "order subject")
    ref = data["ref"]
    if ref is not None:
        ref = _exact_fields(ref, {"repo_key", "local_id"}, "DeclRef")
        ref = DeclRef(ref["repo_key"], ref["local_id"])
    return OrderSubject(data["kind"], data["identifier"], ref, _range_from_dict(data["source_range"]))


def _provenance_from_dict(data):
    data = _exact_fields(data, {"method", "source_ref", "ranges"}, "provenance")
    if not isinstance(data["ranges"], list):
        raise ValueError("provenance ranges must be an array")
    ranges = tuple(_range_from_dict(item) for item in data["ranges"])
    if any(item is None for item in ranges):
        raise ValueError("provenance ranges cannot contain null")
    return Provenance(data["method"], data["source_ref"], ranges)


def _relation_from_dict(data):
    fields = {"relation_id", "before", "after", "strength", "basis", "producer",
              "source_order", "provenance"}
    data = _exact_fields(data, fields, "order relation")
    if not isinstance(data["provenance"], list):
        raise ValueError("order relation provenance must be an array")
    return OrderRelation(data["relation_id"], _subject_from_dict(data["before"]),
                         _subject_from_dict(data["after"]), data["strength"], data["basis"],
                         data["producer"], data["source_order"],
                         tuple(_provenance_from_dict(item) for item in data["provenance"]))


def _sequence_from_dict(data):
    fields = {"sequence_id", "members", "strength", "basis", "producer", "source_order",
              "provenance"}
    data = _exact_fields(data, fields, "order sequence")
    if not isinstance(data["members"], list) or not isinstance(data["provenance"], list):
        raise ValueError("invalid order sequence arrays")
    return OrderSequence(data["sequence_id"], tuple(_subject_from_dict(item) for item in data["members"]),
                         data["strength"], data["basis"], data["producer"], data["source_order"],
                         tuple(_provenance_from_dict(item) for item in data["provenance"]))


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
    protected_relations: tuple[ProjectedOrderRelation, ...] = ()
    tie_breaker_relations: tuple[ProjectedOrderRelation, ...] = ()
    internalized_relation_ids: tuple[str, ...] = ()
    unprojected_relation_ids: tuple[str, ...] = ()
    projection_diagnostics: tuple[dict, ...] = ()

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
        relation_ids = []
        for relation in (*self.protected_relations, *self.tie_breaker_relations):
            if relation.provider not in atoms or relation.consumer not in atoms:
                raise ValueError("projected order relation endpoint is outside the atom set")
            if relation.provider == relation.consumer:
                raise ValueError("projected order relation endpoints must differ")
            relation_ids.append(relation.relation_id)
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("duplicate projected order relation ID")
        decision_ids = (*relation_ids, *self.internalized_relation_ids,
                        *self.unprojected_relation_ids)
        if (len(decision_ids) != len(set(decision_ids)) or
                any(not isinstance(item, str) or not item for item in decision_ids)):
            raise ValueError("duplicate or invalid order relation projection decision")
        if any(not isinstance(item, dict) for item in self.projection_diagnostics):
            raise ValueError("order projection diagnostics must be objects")
        if any(item.strength != "protected" for item in self.protected_relations):
            raise ValueError("protected relation has wrong strength")
        if any(item.strength != "tie_breaker" for item in self.tie_breaker_relations):
            raise ValueError("tie-breaker relation has wrong strength")

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
    accepted_relation_ids: tuple[str, ...] = ()
    rejected_relation_ids: tuple[str, ...] = ()
    diagnostics: tuple[dict, ...] = ()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        expected = {"scope_id", "parent_id", "atom_digest", "edge_digest", "atoms", "order",
                    "constraints", "metrics", "source_basis", "source_roles", "source_anchors",
                    "accepted_relation_ids", "rejected_relation_ids", "diagnostics"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("invalid scope order fields")
        if (not all(isinstance(data[name], list) for name in
                    ("atoms", "order", "constraints", "accepted_relation_ids",
                     "rejected_relation_ids", "diagnostics")) or
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
                    tuple(data["accepted_relation_ids"]), tuple(data["rejected_relation_ids"]),
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
        if (len(value.accepted_relation_ids) != len(set(value.accepted_relation_ids)) or
                len(value.rejected_relation_ids) != len(set(value.rejected_relation_ids)) or
                set(value.accepted_relation_ids) & set(value.rejected_relation_ids)):
            raise ValueError("invalid scope order relation decisions")
        if any(not isinstance(item, str) or not item
               for item in (*value.accepted_relation_ids, *value.rejected_relation_ids)):
            raise ValueError("invalid scope order relation ID")
        return value


@dataclass(frozen=True)
class NarrativeOrder:
    repo_key: str
    repository_revision: str | None
    repository_input_digest: str | None
    workspace_digest: str
    source_digest: str
    material_digest: str | None
    binding_digest: str | None
    order_evidence_digest: str | None
    config_digest: str
    implementation_digest: str
    scopes: tuple[ScopeOrder, ...]
    diagnostics: tuple[dict, ...]
    narrative_order_id: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, *, repo_key, repository_revision, repository_input_digest,
               workspace_digest, source_digest, config_digest, scopes,
               material_digest=None, binding_digest=None, order_evidence_digest=None):
        canonical_scopes = tuple(sorted(scopes, key=lambda scope: (
            scope.scope_id, scope.parent_id,
        )))
        diagnostics = tuple(
            diagnostic for scope in canonical_scopes for diagnostic in scope.diagnostics
        )
        value = cls(repo_key, repository_revision, repository_input_digest, workspace_digest,
                    source_digest, material_digest, binding_digest, order_evidence_digest,
                    config_digest, ORDER_IMPLEMENTATION_DIGEST,
                    canonical_scopes, diagnostics, "")
        return cls(**{**value.to_dict(), "scopes": value.scopes, "diagnostics": value.diagnostics,
                      "narrative_order_id": _narrative_order_id(value)})

    @classmethod
    def from_dict(cls, data):
        expected = {"repo_key", "repository_revision", "repository_input_digest", "workspace_digest",
                    "source_digest", "material_digest", "binding_digest", "order_evidence_digest",
                    "config_digest", "implementation_digest", "scopes",
                    "diagnostics", "narrative_order_id"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("invalid narrative order fields")
        if (not isinstance(data["scopes"], list) or not isinstance(data["diagnostics"], list) or
                any(not isinstance(item, dict) for item in data["diagnostics"])):
            raise ValueError("invalid narrative order field types")
        value = cls(data["repo_key"], data["repository_revision"], data["repository_input_digest"],
                    data["workspace_digest"], data["source_digest"], data["material_digest"],
                    data["binding_digest"], data["order_evidence_digest"], data["config_digest"],
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
        evidence_digests = (value.material_digest, value.binding_digest, value.order_evidence_digest)
        if (any(item is not None for item in evidence_digests) and
                not all(_is_digest(item) for item in evidence_digests)):
            raise ValueError("narrative order material evidence digests must be all present")
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
    dependency_successors = {atom: set() for atom in problem.atoms}
    for edge in problem.edges:
        successors[edge.provider].add(edge.consumer)
        dependency_successors[edge.provider].add(edge.consumer)
    accepted = []
    accepted_ids = list(problem.internalized_relation_ids)
    rejected_ids = list(problem.unprojected_relation_ids)
    diagnostics = list(problem.projection_diagnostics)
    for relation in sorted(problem.protected_relations, key=lambda item: item.canonical_key()):
        earlier, later = relation.provider, relation.consumer
        if later in successors[earlier]:
            accepted_ids.append(relation.relation_id)
            continue
        if _path_exists(successors, later, earlier):
            reason = ("dependency_cycle" if _path_exists(dependency_successors, later, earlier)
                      else "earlier_protected_relation_cycle")
            diagnostic_code = ("dependency_overrides_source_order"
                               if reason == "dependency_cycle" and relation.relation_id.startswith("source-sequence:")
                               else "dependency_overrides_narrative_order"
                               if reason == "dependency_cycle" else "protected_relation_rejected")
            diagnostics.append({"code": diagnostic_code,
                                "scope_id": problem.scope_id, "earlier": earlier,
                                "later": later, "relation_id": relation.relation_id,
                                "reason": reason})
            rejected_ids.append(relation.relation_id)
            continue
        successors[earlier].add(later)
        accepted.append((earlier, later))
        accepted_ids.append(relation.relation_id)
    # Legacy protected sequences remain an internal compatibility input until
    # hierarchy wiring has migrated.  Explicit evidence is processed first.
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
    return (tuple(accepted), tuple(accepted_ids), tuple(rejected_ids), tuple(diagnostics))


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
        atom = _ready_min(problem, ready)
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
        preferred = _tie_preferred(problem, ready, reverse=True)
        atom = max(preferred, key=lambda value: (outgoing[value] - incoming[value],
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


def _tie_preferred(problem, ready, *, reverse=False):
    """Use soft relations only to choose among nodes already legal to emit."""
    blocked = {atom: 0 for atom in ready}
    for relation in problem.tie_breaker_relations:
        if relation.provider in ready and relation.consumer in ready:
            blocked[relation.provider if reverse else relation.consumer] += 1
    preferred = {atom for atom, count in blocked.items() if count == 0}
    return preferred or set(ready)


def _ready_min(problem, ready):
    return min(_tie_preferred(problem, ready),
               key=lambda value: (_tupleize(problem.source_keys[value]), value))


def _tie_violations(problem, order):
    positions = {atom: index for index, atom in enumerate(order)}
    return sum(positions[item.provider] > positions[item.consumer]
               for item in problem.tie_breaker_relations)


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
        atom = _ready_min(problem, ready)
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
        "tie_breaker_violations": _tie_violations(problem, order),
        "changed_atoms_from_source_start": sum(a != b for a, b in zip(order, source_start)),
        "ready_ambiguous_steps": ambiguous_steps,
        "ready_maximum_width": maximum_ready,
        "source_basis_coverage": basis_coverage,
        "source_role_order": _role_metrics(problem, order),
    }


def solve_order(problem):
    constraints, accepted_ids, rejected_ids, diagnostics = _protected_constraints(problem)
    source_start = _source_order(problem, constraints)
    reverse_start = _reverse_frontier(problem, constraints)
    starts = (source_start, reverse_start)
    initial = min(starts, key=lambda order: (dependency_distance(problem, order),
                                             _tie_violations(problem, order),
                                             _source_displacement(problem, order), order))
    order = _improve(problem, constraints, initial)
    result = ScopeOrder(problem.scope_id, problem.parent_id, problem.atom_digest, problem.edge_digest,
                        tuple(sorted(problem.atoms)), order, constraints,
                        _metrics(problem, constraints, source_start, reverse_start, order),
                        dict(sorted(problem.source_basis.items())),
                        dict(sorted(problem.source_roles.items())),
                        dict(sorted(problem.source_anchors.items())), accepted_ids, rejected_ids,
                        diagnostics)
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
    constraints, accepted_ids, rejected_ids, diagnostics = _protected_constraints(problem)
    if (result.constraints != constraints or result.accepted_relation_ids != accepted_ids or
            result.rejected_relation_ids != rejected_ids or result.diagnostics != diagnostics):
        raise ValueError("narrative scope protected constraints mismatch")
    successors, _ = _graph(problem, constraints)
    if not _legal(result.order, successors):
        raise ValueError("narrative scope order violates constraints")
    source_start = _source_order(problem, constraints)
    reverse_start = _reverse_frontier(problem, constraints)
    if result.metrics != _metrics(problem, constraints, source_start, reverse_start, result.order):
        raise ValueError("narrative scope metrics mismatch")
    return result.order
