"""Version-bound declaration facts and a separately represented unit forest."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints


class ValidationError(ValueError):
    """The fact package violates the schema or a cross-record invariant."""


@dataclass(frozen=True)
class DeclRef:
    repo_key: str
    local_id: str


@dataclass(frozen=True)
class SourceAsset:
    asset_id: str
    repo_key: str
    path: str
    sha256: str

    def verify(self, data: bytes) -> None:
        """Verify bytes supplied by the caller; this never opens source paths."""
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValidationError(f"asset digest mismatch: {self.asset_id}")


@dataclass(frozen=True)
class SourceRange:
    """One-based Unicode code-point columns, exclusive end; absent columns mean inclusive lines."""
    asset_id: str
    start_line: int
    start_column: int | None
    end_line: int
    end_column: int | None


@dataclass(frozen=True)
class Provenance:
    """Method and original identity/evidence, optionally tied to asset ranges."""
    method: str
    source_ref: str
    ranges: tuple[SourceRange, ...] = ()


@dataclass(frozen=True)
class Status:
    """Source-specific status, retained without claiming independent verification."""
    state: str
    provenance: tuple[Provenance, ...]
    reason: str | None = None


@dataclass(frozen=True)
class TextContent:
    text: str | None
    status: str  # present, missing, or not_applicable
    provenance: tuple[Provenance, ...]
    reason: str | None = None
    check: Status | None = None


@dataclass(frozen=True)
class Dependency:
    provider: DeclRef
    evidence_kind: str  # e.g. lc_declared, lean_type, lean_value, text_reference
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class DeclContent:
    nl: TextContent
    formal: TextContent
    deps: tuple[Dependency, ...] = ()


@dataclass(frozen=True)
class RawDecl:
    ref: DeclRef
    lean_name: str
    module: str
    native_scope: str
    kind: str
    statement: DeclContent
    extraction_status: Status
    provenance: tuple[Provenance, ...]
    completion_status: Status | None = None
    proof: DeclContent | None = None
    kernel_kind: str | None = None
    source_refs: tuple[SourceRange, ...] = ()
    source_context: tuple[TextContent, ...] = ()
    local_public: bool = False
    generated_from: DeclRef | None = None


@dataclass(frozen=True)
class DependencyLock:
    repo_key: str
    dependency_repo_key: str
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class Repository:
    repo_key: str
    toolchain: str | None
    root_scope: str | None
    revision: str | None = None
    input_digest: str | None = None
    primary_outcomes: tuple[DeclRef, ...] = ()
    version_status: str = "fixed"
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class WorkspaceManifest:
    repositories: tuple[Repository, ...]
    assets: tuple[SourceAsset, ...]
    dependency_locks: tuple[DependencyLock, ...] = ()


@dataclass(frozen=True)
class Scope:
    scope_id: str
    repo_key: str
    kind: str
    name: str
    provenance: tuple[Provenance, ...]
    parent: str | None = None


@dataclass(frozen=True)
class DeclUnit:
    unit_id: str
    representative: DeclRef
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workspace:
    manifest: WorkspaceManifest
    declarations: tuple[RawDecl, ...] = ()
    scopes: tuple[Scope, ...] = ()
    units: tuple[DeclUnit, ...] = ()

    def validate(self) -> None:
        """Validate types, frozen identities, references, and optional full unit coverage."""
        # The same strict shape checks apply to Python instances and loaded JSON.
        decoded = _decode(Workspace, asdict(self), "workspace")
        _require(decoded == self, "Python records must use schema dataclasses and tuples")
        _validate(self)

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def digest(self) -> str:
        """Return the identity of the complete validated current fact package."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, text: str) -> Workspace:
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValidationError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        try:
            data = json.loads(text, object_pairs_hook=unique_keys)
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        workspace = _decode(cls, data, "workspace")
        workspace.validate()
        return workspace

    def coverage(self, unit_id: str) -> frozenset[DeclRef]:
        """Return recursive coverage after checking the entire package."""
        self.validate()
        units = {unit.unit_id: unit for unit in self.units}
        if unit_id not in units:
            raise ValidationError(f"unknown unit: {unit_id}")
        pending = [unit_id]
        result = set()
        while pending:
            unit = units[pending.pop()]
            result.add(unit.representative)
            pending.extend(unit.members)
        return frozenset(result)


def _decode(expected, value, path):
    origin = get_origin(expected)
    if origin in (Union, UnionType):
        for option in get_args(expected):
            try:
                return _decode(option, value, path)
            except ValidationError:
                pass
        raise ValidationError(f"{path}: value does not match {expected}")
    if expected is type(None):
        if value is None:
            return None
    elif origin is tuple:
        if isinstance(value, (list, tuple)):
            return tuple(_decode(get_args(expected)[0], item, f"{path}[{i}]")
                         for i, item in enumerate(value))
    elif expected in (str, bool, int):
        if type(value) is expected:
            return value
    elif hasattr(expected, "__dataclass_fields__"):
        if isinstance(value, dict):
            known = {field.name for field in fields(expected)}
            if value.keys() - known:
                raise ValidationError(f"{path}: unknown fields {sorted(value.keys() - known)}")
            hints = get_type_hints(expected)
            try:
                return expected(**{key: _decode(hints[key], item, f"{path}.{key}")
                                   for key, item in value.items()})
            except TypeError as exc:
                raise ValidationError(f"{path}: {exc}") from exc
    raise ValidationError(f"{path}: expected {expected}")


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _nonempty(value, label):
    _require(bool(value.strip()), f"{label} must be nonempty")


def _index(values, key, label):
    result = {}
    for value in values:
        identity = key(value)
        _require(identity not in result, f"duplicate {label}: {identity}")
        result[identity] = value
    return result


def _acyclic(parents, label):
    finished = set()
    for start in parents:
        path = set()
        node = start
        while node is not None and node not in finished:
            _require(node not in path, f"{label} cycle at {node}")
            path.add(node)
            node = parents[node]
        finished.update(path)


def _validate(workspace):
    manifest = workspace.manifest
    repos = _index(manifest.repositories, lambda r: r.repo_key, "repository")
    _require(bool(repos), "manifest needs at least one repository")
    assets = _index(manifest.assets, lambda a: a.asset_id, "asset")
    scopes = _index(workspace.scopes, lambda s: s.scope_id, "scope")
    decls = _index(workspace.declarations, lambda d: d.ref, "declaration")
    units = _index(workspace.units, lambda u: u.unit_id, "unit")

    def ref(value):
        _require(value.repo_key in repos, f"unknown reference repository: {value.repo_key}")
        _nonempty(value.local_id, "local_id")

    def source_range(value):
        _require(value.asset_id in assets, f"unknown source asset: {value.asset_id}")
        _require(min(value.start_line, value.end_line) >= 1, "source lines must be positive")
        _require((value.start_column is None) == (value.end_column is None), "columns must both be known or absent")
        if value.start_column is None:
            _require(value.start_line <= value.end_line, "source lines must be ordered")
        else:
            _require(min(value.start_column, value.end_column) >= 1, "source columns must be positive")
            _require((value.start_line, value.start_column) < (value.end_line, value.end_column),
                     "source range must be nonempty and ordered")

    def provenance(values):
        _require(bool(values), "provenance is required")
        for value in values:
            _nonempty(value.method, "provenance method")
            _nonempty(value.source_ref, "provenance source_ref")
            for location in value.ranges:
                source_range(location)

    def status(value):
        _nonempty(value.state, "status state")
        provenance(value.provenance)

    def text(value):
        _require(value.status in {"present", "missing", "not_applicable"}, "invalid text status")
        _require((value.text is not None) == (value.status == "present"), "text/status mismatch")
        if value.status != "present":
            _require(value.reason is not None and bool(value.reason.strip()), "missing text needs reason")
        provenance(value.provenance)
        if value.check is not None:
            status(value.check)

    def content(value):
        text(value.nl)
        text(value.formal)
        for dependency in value.deps:
            ref(dependency.provider)
            _nonempty(dependency.evidence_kind, "dependency evidence_kind")
            provenance(dependency.provenance)

    for repo in repos.values():
        _nonempty(repo.repo_key, "repo_key")
        _require(repo.version_status in {"fixed", "unresolved"}, "invalid repository version status")
        if repo.version_status == "fixed":
            _require(repo.toolchain is not None, "fixed repository needs toolchain")
            _nonempty(repo.toolchain, "toolchain")
            _require(repo.revision is not None or repo.input_digest is not None,
                     f"repository has no fixed version: {repo.repo_key}")
        else:
            _require(repo.unresolved_reason is not None and bool(repo.unresolved_reason.strip()),
                     "unresolved repository needs reason")
            _require(repo.revision is None and repo.input_digest is None and repo.root_scope is None,
                     "unresolved repository is an external stub, not a loaded repository")
        if repo.revision is not None:
            _require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", repo.revision) is not None,
                     "revision must be a full lowercase Git object ID")
        if repo.input_digest is not None:
            _require(re.fullmatch(r"[0-9a-f]{64}", repo.input_digest) is not None,
                     "input_digest must be lowercase SHA256")
        if repo.root_scope is not None:
            _require(repo.root_scope in scopes, "unknown repository root_scope")
            root = scopes[repo.root_scope]
            _require(root.repo_key == repo.repo_key and root.parent is None, "invalid repository root_scope")
        _require(len(set(repo.primary_outcomes)) == len(repo.primary_outcomes), "duplicate primary outcome")
        for outcome in repo.primary_outcomes:
            ref(outcome)
            _require(outcome.repo_key == repo.repo_key, "primary outcome belongs to another repository")
    for asset in assets.values():
        _nonempty(asset.asset_id, "asset_id")
        _nonempty(asset.path, "asset path")
        _require(asset.repo_key in repos, "unknown asset repository")
        _require(re.fullmatch(r"[0-9a-f]{64}", asset.sha256) is not None, "asset needs SHA256")
    locks = set()
    for lock in manifest.dependency_locks:
        _require(lock.repo_key in repos and lock.dependency_repo_key in repos, "unknown lock repository")
        pair = (lock.repo_key, lock.dependency_repo_key)
        _require(pair not in locks and pair[0] != pair[1], "duplicate or self dependency lock")
        locks.add(pair)
        provenance(lock.provenance)
    for scope in scopes.values():
        _nonempty(scope.scope_id, "scope_id")
        _nonempty(scope.kind, "scope kind")
        _require(scope.repo_key in repos, "unknown scope repository")
        provenance(scope.provenance)
        if scope.parent is not None:
            _require(scope.parent in scopes and scopes[scope.parent].repo_key == scope.repo_key,
                     "scope parent must exist in the same repository")
        else:
            _require(repos[scope.repo_key].root_scope == scope.scope_id, "scope is not repository root")
    _acyclic({s.scope_id: s.parent for s in scopes.values()}, "scope")
    for decl in decls.values():
        ref(decl.ref)
        for value, label in ((decl.lean_name, "lean_name"), (decl.module, "module"), (decl.kind, "kind")):
            _nonempty(value, label)
        _require(decl.native_scope in scopes and scopes[decl.native_scope].repo_key == decl.ref.repo_key,
                 "declaration scope must exist in the same repository")
        provenance(decl.provenance)
        status(decl.extraction_status)
        if decl.completion_status is not None:
            status(decl.completion_status)
        content(decl.statement)
        if decl.proof is not None:
            content(decl.proof)
        for location in decl.source_refs:
            source_range(location)
        for context in decl.source_context:
            text(context)
        if decl.generated_from is not None:
            ref(decl.generated_from)
            _require(decl.generated_from != decl.ref, "generated declaration cannot own itself")
    parents = {unit_id: None for unit_id in units}
    representatives = set()
    for unit in units.values():
        _nonempty(unit.unit_id, "unit_id")
        _require(unit.representative in decls, "unit representative must be a loaded RawDecl")
        _require(unit.representative not in representatives, "duplicate declaration ownership")
        representatives.add(unit.representative)
        for member in unit.members:
            _require(member in units, "unknown unit member")
            _require(member != unit.unit_id, "unit cannot contain itself")
            _require(parents[member] is None, "duplicate unit ownership")
            parents[member] = unit.unit_id
    _acyclic(parents, "unit")
    if units:
        _require(representatives == set(decls), "unit forest must cover every loaded RawDecl exactly once")
