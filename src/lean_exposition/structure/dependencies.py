"""Conservative dependency filtering for mathematical analysis views.

The complete dependency graph remains the source of truth.  This module only
records version-bound foundation decisions and derives keep/hide decisions for
an analysis view.  It deliberately has no effect on structure construction or
narrative ordering.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import Path
import re
from types import UnionType
from typing import Iterable, Union, get_args, get_origin, get_type_hints

from lean_exposition.models import DeclRef, Repository, ValidationError, Workspace


AMBIENT_PROVIDERS = frozenset({"lean", "batteries", "mathlib"})
FOUNDATION_CLASSIFICATIONS = frozenset({
    "automatic_ambient", "reviewed_ambient", "reviewed_keep",
})
DEPENDENCY_PARTS = frozenset({"statement", "proof"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def canonical_provider_name(repo_key: str) -> str:
    """Normalize LC and native-loader aliases to one global package identity."""
    _require(isinstance(repo_key, str) and bool(repo_key.strip()),
             "provider repo_key must be nonempty")
    if "/dependency/" in repo_key:
        return repo_key.rsplit("/dependency/", 1)[1].lower()
    if repo_key.lower().endswith("/lean"):
        return "lean"
    return repo_key.lower()


@dataclass(frozen=True)
class ProviderIdentity:
    """Exact provider identity, independent of a consumer-local repo alias."""

    provider_name: str
    revision: str | None
    input_digest: str | None
    toolchain: str

    @classmethod
    def from_repository(cls, repository: Repository) -> "ProviderIdentity":
        _require(repository.version_status == "fixed",
                 "foundation provider must have fixed identity")
        identity = cls(canonical_provider_name(repository.repo_key), repository.revision,
                       repository.input_digest, repository.toolchain or "")
        identity.validate()
        return identity

    def validate(self) -> None:
        _require(bool(self.provider_name.strip()), "provider_name must be nonempty")
        _require(self.provider_name == self.provider_name.lower(),
                 "provider_name must be lowercase canonical identity")
        _require(bool(self.toolchain.strip()), "provider toolchain must be nonempty")
        _require(self.revision is not None or self.input_digest is not None,
                 "provider identity needs revision or input_digest")
        if self.revision is not None:
            _require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.revision) is not None,
                     "provider revision must be a full lowercase Git object ID")
        if self.input_digest is not None:
            _require(_SHA256.fullmatch(self.input_digest) is not None,
                     "provider input_digest must be lowercase SHA256")


@dataclass(frozen=True)
class FoundationEntry:
    """One global reviewed or mechanically generated declaration decision."""

    local_id: str
    classification: str
    parts: tuple[str, ...]
    reason: str
    source: str

    def validate(self) -> None:
        _require(bool(self.local_id.strip()), "foundation local_id must be nonempty")
        _require(self.classification in FOUNDATION_CLASSIFICATIONS,
                 "invalid foundation classification")
        _require(bool(self.parts) and set(self.parts) <= DEPENDENCY_PARTS,
                 "foundation entry parts must contain statement or proof")
        _require(len(self.parts) == len(set(self.parts)) and
                 self.parts == tuple(sorted(self.parts)),
                 "foundation entry parts must be unique and sorted")
        _require(bool(self.reason.strip()) and bool(self.source.strip()),
                 "foundation reason and source must be nonempty")
        if self.classification == "automatic_ambient":
            _require(self.parts == ("proof",),
                     "automatic ambient entries may hide proof-only use only")
            _require(self.source.startswith("automatic:"),
                     "automatic ambient entry needs automatic source")
        else:
            _require(self.source.startswith("reviewed:"),
                     "semantic foundation entries need reviewed source")


@dataclass(frozen=True)
class ProviderFoundation:
    identity: ProviderIdentity
    generator_implementation_digest: str
    generator_config_digest: str
    entries: tuple[FoundationEntry, ...]

    def validate(self) -> None:
        self.identity.validate()
        for value, label in (
            (self.generator_implementation_digest, "generator implementation digest"),
            (self.generator_config_digest, "generator config digest"),
        ):
            _require(_SHA256.fullmatch(value) is not None,
                     f"{label} must be lowercase SHA256")
        seen = set()
        for entry in self.entries:
            entry.validate()
            _require(entry.local_id not in seen,
                     f"duplicate foundation declaration: {entry.local_id}")
            seen.add(entry.local_id)
        _require(tuple(sorted(self.entries, key=lambda entry: entry.local_id)) == self.entries,
                 "foundation entries must be sorted by local_id")


@dataclass(frozen=True)
class FoundationCatalog:
    """Global catalog supporting multiple exact revisions of each provider."""

    providers: tuple[ProviderFoundation, ...]

    def validate(self) -> None:
        identities = set()
        for provider in self.providers:
            provider.validate()
            _require(provider.identity not in identities,
                     "duplicate provider identity in foundation catalog")
            identities.add(provider.identity)
        expected = tuple(sorted(self.providers, key=lambda item: (
            item.identity.provider_name, item.identity.toolchain,
            item.identity.revision or "", item.identity.input_digest or "",
        )))
        _require(self.providers == expected, "foundation providers must be canonically sorted")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "FoundationCatalog":
        value = _load_json(cls, text, "foundation_catalog")
        value.validate()
        return value

    def digest(self) -> str:
        self.validate()
        return _digest(asdict(self))

    def match(self, repository: Repository) -> ProviderFoundation | None:
        """Return one unambiguous match on every identity field known at runtime.

        A consumer lock commonly records only a provider revision, while a
        catalog generated from that provider also knows its content digest.  In
        that case the fixed revision and toolchain are sufficient.  If the
        consumer knows both values, both must match; multiple compatible catalog
        records are treated as unknown rather than chosen heuristically.
        """
        if repository.version_status != "fixed":
            return None
        identity = ProviderIdentity.from_repository(repository)
        matches = [provider for provider in self.providers
                   if provider.identity.provider_name == identity.provider_name
                   and provider.identity.toolchain == identity.toolchain
                   and (identity.revision is None or
                        provider.identity.revision == identity.revision)
                   and (identity.input_digest is None or
                        provider.identity.input_digest == identity.input_digest)]
        return matches[0] if len(matches) == 1 else None

    def has_provider_name(self, repository: Repository) -> bool:
        name = canonical_provider_name(repository.repo_key)
        return any(provider.identity.provider_name == name for provider in self.providers)


@dataclass(frozen=True)
class DependencyDecision:
    provider: DeclRef
    consumer: DeclRef
    keep: bool
    reason: str
    classification: str | None = None


@dataclass(frozen=True)
class DependencyAnalysis:
    """Pair decisions derived from, but never replacing, a complete edge set."""

    target_repo_key: str
    catalog_digest: str
    decisions: tuple[DependencyDecision, ...]

    @property
    def kept_pairs(self) -> frozenset[tuple[DeclRef, DeclRef]]:
        return frozenset((item.provider, item.consumer) for item in self.decisions if item.keep)

    @property
    def hidden_pairs(self) -> frozenset[tuple[DeclRef, DeclRef]]:
        return frozenset((item.provider, item.consumer) for item in self.decisions if not item.keep)

    def keeps(self, provider: DeclRef, consumer: DeclRef) -> bool:
        match = next((item for item in self.decisions
                      if item.provider == provider and item.consumer == consumer), None)
        if match is None:
            raise KeyError((provider, consumer))
        return match.keep


class DependencyAnalysisPolicy:
    """Apply the global catalog conservatively to one target repository."""

    def __init__(self, catalog: FoundationCatalog):
        catalog.validate()
        self.catalog = catalog

    def decide(self, workspace: Workspace, target_repo_key: str, edge) -> DependencyDecision:
        provider, consumer = edge.provider, edge.consumer
        if consumer.repo_key != target_repo_key:
            return DependencyDecision(provider, consumer, True, "outside_target_repository")
        if provider.repo_key == target_repo_key:
            return DependencyDecision(provider, consumer, True, "target_internal")
        provider_name = canonical_provider_name(provider.repo_key)
        if provider_name not in AMBIENT_PROVIDERS:
            return DependencyDecision(provider, consumer, True, "other_project")
        repositories = {repo.repo_key: repo for repo in workspace.manifest.repositories}
        repository = repositories.get(provider.repo_key)
        if repository is None or repository.version_status != "fixed":
            return DependencyDecision(provider, consumer, True, "provider_unresolved")
        foundation = self.catalog.match(repository)
        if foundation is None:
            reason = ("catalog_identity_mismatch" if self.catalog.has_provider_name(repository)
                      else "catalog_missing")
            return DependencyDecision(provider, consumer, True, reason)
        entry = next((item for item in foundation.entries
                      if item.local_id == provider.local_id), None)
        if entry is None:
            return DependencyDecision(provider, consumer, True, "foundation_unknown")
        if entry.classification == "reviewed_keep":
            return DependencyDecision(provider, consumer, True, "reviewed_keep",
                                      entry.classification)
        occurrence_parts = frozenset(occurrence.part for occurrence in edge.occurrences)
        if not occurrence_parts or not occurrence_parts <= set(entry.parts):
            return DependencyDecision(provider, consumer, True,
                                      "foundation_not_applicable_to_occurrence",
                                      entry.classification)
        return DependencyDecision(provider, consumer, False, "ambient_foundation",
                                  entry.classification)

    def analyze(self, workspace: Workspace, target_repo_key: str,
                edges: Iterable[object]) -> DependencyAnalysis:
        workspace.validate()
        _require(any(repo.repo_key == target_repo_key and repo.root_scope is not None
                     for repo in workspace.manifest.repositories),
                 "analysis target must be a loaded repository")
        decisions = tuple(self.decide(workspace, target_repo_key, edge) for edge in edges)
        pairs = [(item.provider, item.consumer) for item in decisions]
        _require(len(pairs) == len(set(pairs)), "dependency analysis needs unique edge pairs")
        return DependencyAnalysis(target_repo_key, self.catalog.digest(), decisions)


def load_foundation_catalog(path: str | Path | None = None) -> FoundationCatalog:
    """Load the shipped global catalog or an explicit replacement."""
    catalog_path = (Path(path) if path is not None else
                    Path(__file__).with_name("foundation_catalog.json"))
    return FoundationCatalog.from_json(catalog_path.read_text())


def analyze_dependencies(workspace: Workspace, target_repo_key: str, *,
                         catalog: FoundationCatalog | None = None,
                         edges: Iterable[object] | None = None) -> DependencyAnalysis:
    """Build the default mathematical analysis view for one loaded repository."""
    if edges is None:
        from .graph import DependencyGraph
        edges = DependencyGraph.from_workspace(workspace).edges
    return DependencyAnalysisPolicy(catalog or load_foundation_catalog()).analyze(
        workspace, target_repo_key, edges,
    )


_AUTOMATIC_RULES = {
    "compiler_generated_support": (
        "compiler_only generated definition, theorem, or recursor with explicit owner; proof use only"
    ),
}
_SAFE_GENERATED_KINDS = frozenset({"definition", "recursor", "theorem"})


def automatic_generator_config_digest() -> str:
    return _digest(_AUTOMATIC_RULES)


def generate_provider_foundation(workspace: Workspace, repo_key: str, *,
                                 implementation_digest: str,
                                 reviewed_entries: Iterable[FoundationEntry] = ()) -> ProviderFoundation:
    """Generate mechanical candidates and merge explicit global review decisions.

    Automatic rules intentionally produce proof-only entries.  The function
    never infers semantic ambient status from namespaces or declaration names.
    """
    workspace.validate()
    _require(_SHA256.fullmatch(implementation_digest) is not None,
             "implementation_digest must be lowercase SHA256")
    repository = next((repo for repo in workspace.manifest.repositories
                       if repo.repo_key == repo_key), None)
    _require(repository is not None, f"unknown foundation provider: {repo_key}")
    identity = ProviderIdentity.from_repository(repository)
    entries: dict[str, FoundationEntry] = {}
    for decl in workspace.declarations:
        if decl.ref.repo_key != repo_key or decl.extraction_status.state != "compiler_only":
            continue
        generated_kind = decl.kernel_kind or decl.kind
        if decl.generated_from is not None and generated_kind in _SAFE_GENERATED_KINDS:
            entries[decl.ref.local_id] = FoundationEntry(
                decl.ref.local_id, "automatic_ambient", ("proof",),
                "Compiler-generated support declaration with an explicit owner; proof-only uses are ambient.",
                "automatic:compiler_generated_support",
            )
    reviewed_ids = set()
    for entry in reviewed_entries:
        entry.validate()
        _require(entry.classification != "automatic_ambient",
                 "reviewed_entries cannot impersonate automatic classifications")
        _require(entry.local_id not in reviewed_ids,
                 f"duplicate reviewed foundation declaration: {entry.local_id}")
        reviewed_ids.add(entry.local_id)
        entries[entry.local_id] = entry
    result = ProviderFoundation(identity, implementation_digest,
                                automatic_generator_config_digest(),
                                tuple(sorted(entries.values(), key=lambda item: item.local_id)))
    result.validate()
    return result


def merge_foundation_catalog(catalog: FoundationCatalog,
                             provider: ProviderFoundation) -> FoundationCatalog:
    """Replace one exact provider identity and preserve all other revisions."""
    catalog.validate()
    provider.validate()
    values = [item for item in catalog.providers if item.identity != provider.identity]
    values.append(provider)
    result = FoundationCatalog(tuple(sorted(values, key=lambda item: (
        item.identity.provider_name, item.identity.toolchain,
        item.identity.revision or "", item.identity.input_digest or "",
    ))))
    result.validate()
    return result


def _load_json(expected, text: str, path: str):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        raw = json.loads(text, object_pairs_hook=unique_keys)
        return _decode(expected, raw, path)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(str(exc)) from exc


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
            args = get_args(expected)
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(_decode(args[0], item, f"{path}[{i}]")
                             for i, item in enumerate(value))
    elif expected in (str, bool, int):
        if type(value) is expected:
            return value
    elif hasattr(expected, "__dataclass_fields__"):
        if isinstance(value, dict):
            known = {field.name for field in fields(expected)}
            unknown = value.keys() - known
            if unknown:
                raise ValidationError(f"{path}: unknown fields {sorted(unknown)}")
            hints = get_type_hints(expected)
            try:
                return expected(**{
                    key: _decode(hints[key], item, f"{path}.{key}")
                    for key, item in value.items()
                })
            except TypeError as exc:
                raise ValidationError(f"{path}: {exc}") from exc
    raise ValidationError(f"{path}: expected {expected}")
