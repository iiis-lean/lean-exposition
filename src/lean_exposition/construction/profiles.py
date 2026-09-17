"""Declarative repository profiles and narrow published-artifact parsers.

Profiles describe inputs to the construction pipeline.  They deliberately do
not contain callbacks and cannot construct a Workspace, narrative order,
Region, or exposition.  Repository bytes remain fixed by the caller-provided
``SourceAsset`` and repository revision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
import re
from typing import Any, Callable, Mapping

from lean_exposition.construction.contributions import (
    CanonicalDeclLocator, DeclLocator, UnresolvedDeclLocator,
)
from lean_exposition.construction.materials import (
    MaterialBinding, MaterialBundle, MaterialRecord, MaterialTarget,
)
from lean_exposition.models import DeclRef, Provenance, SourceAsset, SourceRange, ValidationError


PROFILE_STAGES = ("inventory", "provisional", "verified_slice", "formal")
CONTRIBUTOR_KINDS = ("lc_catalog", "source_inventory", "compiled", "published")
PROFILE_CAPABILITIES = {
    "inventory": frozenset({"inventory", "materials"}),
    "provisional": frozenset({"inventory", "materials", "provisional_graph"}),
    "verified_slice": frozenset(
        {"inventory", "materials", "provisional_graph", "production_structure"}
    ),
    "formal": frozenset(
        {"inventory", "materials", "provisional_graph", "production_structure"}
    ),
}
MATERIAL_ROLES = ("primary", "supporting", "reference")
ORDER_STRENGTHS = ("protected", "tie_breaker")
PARSER_IDS = (
    "formalization_yaml", "proof_path_markdown", "published_html", "tex", "markdown",
    "published_bundle", "json", "pdf_pages", "plain",
)

PROFILE_IMPLEMENTATION = {
    "identity": "fixed_repository_revision_and_asset_digests",
    "composition": "declarative_contributors_materials_hints_and_target_slice",
    "stages": list(PROFILE_STAGES),
    "yaml": "PyYAML_safe_load_optional_material_extra",
    "proof_path": "markdown_headings_and_code_landmarks",
    "html": "selected_shard_data_attributes_only",
    "published_bundle": (
        "unique_fixed_meta_titles_csr_globals_and_fnv_selected_shards"
    ),
}
PROFILE_IMPLEMENTATION_DIGEST = hashlib.sha256(json.dumps(
    PROFILE_IMPLEMENTATION, ensure_ascii=False, sort_keys=True,
    separators=(",", ":"), allow_nan=False,
).encode()).hexdigest()
FORMALIZATION_YAML_DEPENDENCY = "PyYAML>=6,<7 (optional material-ingestion extra)"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"profile value is not canonical JSON: {exc}") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _nonempty(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be nonempty")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"invalid {label} fields")
    return value


def _strict_json(text: str) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(str(exc)) from exc


@dataclass(frozen=True)
class RepositoryIdentity:
    repo_key: str
    revision: str
    toolchain: str | None = None
    input_digest: str | None = None

    def validate(self) -> None:
        _nonempty(self.repo_key, "profile repo_key")
        _nonempty(self.revision, "profile revision")
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.revision) is None:
            raise ValidationError("profile revision must be a full lowercase Git object ID")
        if self.toolchain is not None:
            _nonempty(self.toolchain, "profile toolchain")
        if self.input_digest is not None and not _is_digest(self.input_digest):
            raise ValidationError("profile input_digest must be lowercase SHA256")


@dataclass(frozen=True)
class ContributorSpec:
    contributor_id: str
    kind: str
    required: bool
    config: dict[str, Any]

    def validate(self) -> None:
        _nonempty(self.contributor_id, "contributor_id")
        if self.kind not in CONTRIBUTOR_KINDS or type(self.required) is not bool:
            raise ValidationError("invalid contributor kind or required flag")
        if not isinstance(self.config, dict):
            raise ValidationError("contributor config must be an object")
        _canonical_json(self.config)


@dataclass(frozen=True)
class MaterialAssetSpec:
    asset_id: str
    path: str
    sha256: str
    media_type: str
    parser_id: str
    binder_id: str
    role: str
    config: dict[str, Any]

    def validate(self) -> None:
        for value, label in ((self.asset_id, "material asset_id"), (self.path, "material path"),
                             (self.media_type, "material media_type"),
                             (self.binder_id, "material binder_id")):
            _nonempty(value, label)
        if not _is_digest(self.sha256):
            raise ValidationError("profile material sha256 must be lowercase SHA256")
        if self.parser_id not in PARSER_IDS or self.role not in MATERIAL_ROLES:
            raise ValidationError("invalid material parser or role")
        if not isinstance(self.config, dict):
            raise ValidationError("material config must be an object")
        _canonical_json(self.config)


@dataclass(frozen=True)
class DeclarationLocatorSpec:
    name: str
    module: str | None = None

    def validate(self) -> None:
        _nonempty(self.name, "declaration locator name")
        if self.module is not None:
            _nonempty(self.module, "declaration locator module")


@dataclass(frozen=True)
class ScopeHint:
    scope_id: str
    kind: str
    name: str
    parent: str | None
    source_prefixes: tuple[str, ...]

    def validate(self) -> None:
        for value, label in ((self.scope_id, "scope hint ID"), (self.kind, "scope hint kind"),
                             (self.name, "scope hint name")):
            _nonempty(value, label)
        if self.parent is not None:
            _nonempty(self.parent, "scope hint parent")
        if not self.source_prefixes or any(not isinstance(item, str) or not item
                                           for item in self.source_prefixes):
            raise ValidationError("scope hint needs source prefixes")


@dataclass(frozen=True)
class UnitHint:
    unit_id: str
    representative: DeclarationLocatorSpec
    members: tuple[DeclarationLocatorSpec, ...]

    def validate(self) -> None:
        _nonempty(self.unit_id, "unit hint ID")
        self.representative.validate()
        for member in self.members:
            member.validate()
        if len({(item.module, item.name) for item in self.members}) != len(self.members):
            raise ValidationError("unit hint members must be unique")


@dataclass(frozen=True)
class OrderHint:
    hint_id: str
    members: tuple[DeclarationLocatorSpec, ...]
    strength: str
    basis: str

    def validate(self) -> None:
        _nonempty(self.hint_id, "order hint ID")
        _nonempty(self.basis, "order hint basis")
        if self.strength not in ORDER_STRENGTHS or len(self.members) < 2:
            raise ValidationError("invalid profile order hint")
        for member in self.members:
            member.validate()
        if len({(item.module, item.name) for item in self.members}) != len(self.members):
            raise ValidationError("order hint members must be distinct")


@dataclass(frozen=True)
class TargetSlice:
    slice_id: str
    source_roots: tuple[str, ...]
    primary_outcomes: tuple[DeclarationLocatorSpec, ...]
    compiled_roots: tuple[DeclarationLocatorSpec, ...]
    contributor_ids: tuple[str, ...]
    material_asset_ids: tuple[str, ...]
    scope_hint_ids: tuple[str, ...]
    unit_hint_ids: tuple[str, ...]
    order_hint_ids: tuple[str, ...]

    def validate(self) -> None:
        _nonempty(self.slice_id, "target slice ID")
        if not self.source_roots or any(not isinstance(item, str) or not item
                                        for item in self.source_roots):
            raise ValidationError("target slice needs source roots")
        for locator in (*self.primary_outcomes, *self.compiled_roots):
            locator.validate()
        for values, label in ((self.primary_outcomes, "slice primary outcomes"),
                              (self.compiled_roots, "slice compiled roots")):
            if len({(item.module, item.name) for item in values}) != len(values):
                raise ValidationError(f"{label} must be unique")
        for values, label in ((self.contributor_ids, "slice contributor IDs"),
                              (self.material_asset_ids, "slice material IDs"),
                              (self.scope_hint_ids, "slice scope hint IDs"),
                              (self.unit_hint_ids, "slice unit hint IDs"),
                              (self.order_hint_ids, "slice order hint IDs")):
            if len(values) != len(set(values)) or any(not isinstance(item, str) or not item
                                                       for item in values):
                raise ValidationError(f"invalid {label}")


@dataclass(frozen=True)
class ProfileRunPlan:
    profile_id: str
    identity: RepositoryIdentity
    stage: str
    target_slice: str | None
    source_roots: tuple[str, ...]
    contributors: tuple[ContributorSpec, ...]
    material_assets: tuple[MaterialAssetSpec, ...]
    primary_outcomes: tuple[DeclarationLocatorSpec, ...]
    compiled_roots: tuple[DeclarationLocatorSpec, ...]
    scope_hints: tuple[ScopeHint, ...]
    unit_hints: tuple[UnitHint, ...]
    order_hints: tuple[OrderHint, ...]

    def require(self, capability: str) -> None:
        if capability not in PROFILE_CAPABILITIES[self.stage]:
            raise ValidationError(
                f"profile stage {self.stage!r} does not permit capability {capability!r}"
            )


@dataclass(frozen=True)
class RepositoryProfile:
    profile_id: str
    identity: RepositoryIdentity
    stage: str
    contributors: tuple[ContributorSpec, ...]
    material_assets: tuple[MaterialAssetSpec, ...]
    primary_outcomes: tuple[DeclarationLocatorSpec, ...]
    scope_hints: tuple[ScopeHint, ...]
    unit_hints: tuple[UnitHint, ...]
    order_hints: tuple[OrderHint, ...]
    target_slices: tuple[TargetSlice, ...]

    def validate(self) -> None:
        _nonempty(self.profile_id, "profile_id")
        self.identity.validate()
        if self.stage not in PROFILE_STAGES:
            raise ValidationError("invalid repository profile stage")
        for values, key in ((self.contributors, lambda item: item.contributor_id),
                            (self.material_assets, lambda item: item.asset_id),
                            (self.scope_hints, lambda item: item.scope_id),
                            (self.unit_hints, lambda item: item.unit_id),
                            (self.order_hints, lambda item: item.hint_id),
                            (self.target_slices, lambda item: item.slice_id)):
            if len({key(item) for item in values}) != len(values):
                raise ValidationError("profile IDs must be unique within each collection")
            for item in values:
                item.validate()
        for outcome in self.primary_outcomes:
            outcome.validate()
        contributor_ids = {item.contributor_id for item in self.contributors}
        material_ids = {item.asset_id for item in self.material_assets}
        scope_ids = {item.scope_id for item in self.scope_hints}
        unit_ids = {item.unit_id for item in self.unit_hints}
        order_ids = {item.hint_id for item in self.order_hints}
        compiled_ids = {item.contributor_id for item in self.contributors if item.kind == "compiled"}
        for target in self.target_slices:
            if not set(target.contributor_ids) <= contributor_ids:
                raise ValidationError("target slice references unknown contributor")
            if not set(target.material_asset_ids) <= material_ids:
                raise ValidationError("target slice references unknown material asset")
            if not set(target.scope_hint_ids) <= scope_ids:
                raise ValidationError("target slice references unknown scope hint")
            if not set(target.unit_hint_ids) <= unit_ids:
                raise ValidationError("target slice references unknown unit hint")
            if not set(target.order_hint_ids) <= order_ids:
                raise ValidationError("target slice references unknown order hint")
        # A source file may belong to only one configured target slice.  This is
        # the mechanical isolation needed for the NS and Euler roots.
        roots: list[tuple[str, str]] = []
        for target in self.target_slices:
            for root in target.source_roots:
                normalized = root.strip("/")
                if not normalized:
                    raise ValidationError("target slice source root cannot be repository root")
                for other, owner in roots:
                    if (normalized == other or normalized.startswith(other + "/") or
                            other.startswith(normalized + "/")):
                        raise ValidationError(
                            f"target slices {owner!r} and {target.slice_id!r} overlap at source root"
                        )
                roots.append((normalized, target.slice_id))
        if len(self.target_slices) > 1 and self.primary_outcomes:
            raise ValidationError("multi-slice profiles require slice-local primary outcomes")
        seen_targets: dict[tuple[str | None, str], str] = {}
        for target in self.target_slices:
            for locator in (*target.primary_outcomes, *target.compiled_roots):
                key = (locator.module, locator.name)
                owner = seen_targets.get(key)
                if owner is not None and owner != target.slice_id:
                    raise ValidationError("target slices share a declaration root")
                seen_targets[key] = target.slice_id
        if self.stage == "verified_slice":
            if not any(item.kind == "compiled" for item in self.contributors):
                raise ValidationError("verified_slice profile requires a compiled contributor")
            if not self.target_slices or any(not item.compiled_roots for item in self.target_slices):
                raise ValidationError("verified_slice targets require compiled roots")
            if any(not set(item.contributor_ids) & compiled_ids for item in self.target_slices):
                raise ValidationError("verified_slice targets must select a compiled contributor")
        if self.stage == "formal" and not any(item.kind in {"compiled", "lc_catalog"}
                                               for item in self.contributors):
            raise ValidationError("formal profile requires an authoritative contributor")

    def plan(self, target_slice: str | None = None) -> ProfileRunPlan:
        self.validate()
        if self.target_slices:
            if target_slice is None:
                if len(self.target_slices) != 1:
                    raise ValidationError("profile with multiple target slices requires a selection")
                selected = self.target_slices[0]
            else:
                selected = next((item for item in self.target_slices
                                 if item.slice_id == target_slice), None)
                if selected is None:
                    raise ValidationError(f"unknown target slice: {target_slice}")
            contributor_ids = set(selected.contributor_ids)
            asset_ids = set(selected.material_asset_ids)
            contributors = tuple(item for item in self.contributors
                                 if item.contributor_id in contributor_ids)
            assets = tuple(item for item in self.material_assets if item.asset_id in asset_ids)
            scope_ids = set(selected.scope_hint_ids)
            unit_ids = set(selected.unit_hint_ids)
            order_ids = set(selected.order_hint_ids)
            scopes = tuple(item for item in self.scope_hints if item.scope_id in scope_ids)
            units = tuple(item for item in self.unit_hints if item.unit_id in unit_ids)
            orders = tuple(item for item in self.order_hints if item.hint_id in order_ids)
            source_roots = selected.source_roots
            outcomes = (*self.primary_outcomes, *selected.primary_outcomes)
            compiled_roots = selected.compiled_roots
            selected_id = selected.slice_id
        else:
            if target_slice is not None:
                raise ValidationError("profile has no target slices")
            contributors, assets = self.contributors, self.material_assets
            scopes, units, orders = self.scope_hints, self.unit_hints, self.order_hints
            source_roots, outcomes, compiled_roots, selected_id = (), self.primary_outcomes, (), None
        return ProfileRunPlan(
            self.profile_id, self.identity, self.stage, selected_id, source_roots,
            contributors, assets, tuple(outcomes), compiled_roots,
            scopes, units, orders,
        )

    def digest(self) -> str:
        self.validate()
        return _digest({"implementation": PROFILE_IMPLEMENTATION_DIGEST, "profile": asdict(self)})

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "RepositoryProfile":
        return cls.from_dict(_strict_json(text))

    @classmethod
    def from_dict(cls, value: object) -> "RepositoryProfile":
        data = _exact(value, {
            "profile_id", "identity", "stage", "contributors", "material_assets",
            "primary_outcomes", "scope_hints", "unit_hints", "order_hints", "target_slices",
        }, "repository profile")
        identity = RepositoryIdentity(**_exact(data["identity"], {
            "repo_key", "revision", "toolchain", "input_digest",
        }, "repository identity"))
        contributors = tuple(ContributorSpec(**_exact(item, {
            "contributor_id", "kind", "required", "config",
        }, "contributor")) for item in _array(data["contributors"], "contributors"))
        assets = tuple(MaterialAssetSpec(**_exact(item, {
            "asset_id", "path", "sha256", "media_type", "parser_id", "binder_id", "role", "config",
        }, "material asset")) for item in _array(data["material_assets"], "material_assets"))
        outcomes = tuple(_locator(item) for item in _array(data["primary_outcomes"], "primary_outcomes"))
        scopes_list = []
        for item in _array(data["scope_hints"], "scope_hints"):
            scope = _exact(item, {"scope_id", "kind", "name", "parent", "source_prefixes"},
                           "scope hint")
            prefixes = _array(scope["source_prefixes"], "scope source_prefixes")
            scopes_list.append(ScopeHint(scope["scope_id"], scope["kind"], scope["name"],
                                         scope["parent"], tuple(prefixes)))
        scopes = tuple(scopes_list)
        units = tuple(_unit_hint(item) for item in _array(data["unit_hints"], "unit_hints"))
        orders = tuple(_order_hint(item) for item in _array(data["order_hints"], "order_hints"))
        slices = tuple(_target_slice(item) for item in _array(data["target_slices"], "target_slices"))
        result = cls(data["profile_id"], identity, data["stage"], contributors, assets,
                     outcomes, scopes, units, orders, slices)
        result.validate()
        return result


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    return value


def _locator(value: object) -> DeclarationLocatorSpec:
    return DeclarationLocatorSpec(**_exact(value, {"name", "module"}, "declaration locator"))


def _unit_hint(value: object) -> UnitHint:
    data = _exact(value, {"unit_id", "representative", "members"}, "unit hint")
    return UnitHint(data["unit_id"], _locator(data["representative"]),
                    tuple(_locator(item) for item in _array(data["members"], "unit members")))


def _order_hint(value: object) -> OrderHint:
    data = _exact(value, {"hint_id", "members", "strength", "basis"}, "order hint")
    return OrderHint(data["hint_id"],
                     tuple(_locator(item) for item in _array(data["members"], "order members")),
                     data["strength"], data["basis"])


def _target_slice(value: object) -> TargetSlice:
    data = _exact(value, {
        "slice_id", "source_roots", "primary_outcomes", "compiled_roots",
        "contributor_ids", "material_asset_ids", "scope_hint_ids", "unit_hint_ids",
        "order_hint_ids",
    }, "target slice")
    for name in ("source_roots", "primary_outcomes", "compiled_roots", "contributor_ids",
                 "material_asset_ids", "scope_hint_ids", "unit_hint_ids", "order_hint_ids"):
        _array(data[name], f"target slice {name}")
    return TargetSlice(
        data["slice_id"], tuple(data["source_roots"]),
        tuple(_locator(item) for item in data["primary_outcomes"]),
        tuple(_locator(item) for item in data["compiled_roots"]),
        tuple(data["contributor_ids"]), tuple(data["material_asset_ids"]),
        tuple(data["scope_hint_ids"]), tuple(data["unit_hint_ids"]),
        tuple(data["order_hint_ids"]),
    )


@dataclass(frozen=True)
class ProfileDiagnostic:
    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class PublishedDependency:
    dependent: DeclLocator
    provider: DeclLocator
    provenance: tuple[Provenance, ...]
    evidence_kind: str = "published"


@dataclass(frozen=True)
class ArtifactContributions:
    materials: MaterialBundle
    primary_outcomes: tuple[DeclLocator, ...] = ()
    order_landmarks: tuple[DeclLocator, ...] = ()
    published_dependencies: tuple[PublishedDependency, ...] = ()
    diagnostics: tuple[ProfileDiagnostic, ...] = ()


def _asset_for_plan(plan: ProfileRunPlan, asset: SourceAsset, text: str,
                    parser_id: str) -> MaterialAssetSpec:
    plan.require("materials")
    if asset.repo_key != plan.identity.repo_key:
        raise ValidationError("profile material asset belongs to another repository")
    spec = next((item for item in plan.material_assets if item.asset_id == asset.asset_id), None)
    if spec is None:
        raise ValidationError("material asset is not selected by this profile run")
    if spec.parser_id != parser_id or spec.path != asset.path or spec.sha256 != asset.sha256:
        raise ValidationError("material asset does not match selected profile specification")
    asset.verify(text.encode())
    return spec


def _provenance(method: str, asset: SourceAsset, source_range: SourceRange | None = None):
    return (Provenance(method, asset.path, (source_range,) if source_range else ()),)


def _source_range(asset: SourceAsset, lines: list[str], start: int, end: int) -> SourceRange:
    return SourceRange(asset.asset_id, start, 1, end, len(lines[end - 1]) + 1)


def _resolve(name: str, repo_key: str, declarations: Mapping[str, DeclRef],
             module: str | None = None) -> DeclLocator:
    ref = declarations.get(name)
    if ref is None and module and "." not in name:
        ref = declarations.get(f"{module}.{name}")
    if ref is not None:
        if ref.repo_key != repo_key:
            raise ValidationError("declaration index crosses repository identity")
        return CanonicalDeclLocator(ref)
    return UnresolvedDeclLocator(repo_key, module or "", name)


def _target(locator: DeclLocator) -> MaterialTarget:
    if isinstance(locator, CanonicalDeclLocator):
        return MaterialTarget("declaration", locator.ref.repo_key, ref=locator.ref)
    return MaterialTarget("declaration_locator", locator.repo_key,
                          identifier=(f"{locator.module}:{locator.raw_name}"
                                      if locator.module else locator.raw_name))


def _status(locator: DeclLocator) -> str:
    return "exact" if isinstance(locator, CanonicalDeclLocator) else "unresolved"


def _bundle(plan: ProfileRunPlan, asset: SourceAsset, spec: MaterialAssetSpec,
            records: list[MaterialRecord], bindings: list[MaterialBinding],
            diagnostics: list[ProfileDiagnostic], parser_contract: dict[str, Any]) -> MaterialBundle:
    parser_config = {"profile_digest": _digest(asdict(plan)), "asset_config": spec.config,
                     "contract": parser_contract}
    return MaterialBundle.create(
        repo_key=plan.identity.repo_key, assets=(asset,), records=tuple(records),
        bindings=tuple(bindings), parser_implementation_digest=PROFILE_IMPLEMENTATION_DIGEST,
        parser_config=parser_config, binder_implementation_digest=PROFILE_IMPLEMENTATION_DIGEST,
        binder_config={"binder_id": spec.binder_id},
        diagnostics=tuple(asdict(item) for item in diagnostics),
    )


def _bundle_many(plan: ProfileRunPlan, assets: tuple[SourceAsset, ...],
                 specs: tuple[MaterialAssetSpec, ...], records: list[MaterialRecord],
                 bindings: list[MaterialBinding], diagnostics: list[ProfileDiagnostic],
                 parser_contract: dict[str, Any]) -> MaterialBundle:
    parser_config = _many_parser_config(plan, specs, parser_contract)
    return MaterialBundle.create(
        repo_key=plan.identity.repo_key, assets=assets, records=tuple(records),
        bindings=tuple(bindings), parser_implementation_digest=PROFILE_IMPLEMENTATION_DIGEST,
        parser_config=parser_config, binder_implementation_digest=PROFILE_IMPLEMENTATION_DIGEST,
        binder_config={"binder_ids": sorted({spec.binder_id for spec in specs})},
        diagnostics=tuple(asdict(item) for item in diagnostics),
    )


def _many_parser_config(plan: ProfileRunPlan, specs: tuple[MaterialAssetSpec, ...],
                        parser_contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_digest": _digest(asdict(plan)),
        "asset_configs": {spec.asset_id: spec.config for spec in specs},
        "contract": parser_contract,
    }


def _record(asset: SourceAsset, spec: MaterialAssetSpec, plan: ProfileRunPlan, *,
            occurrence_id: str, source_range: SourceRange, heading: str | None,
            text: str | None, payload: dict[str, Any], method: str,
            parser_contract: dict[str, Any],
            parser_config: dict[str, Any] | None = None) -> MaterialRecord:
    parser_config = parser_config or {
        "profile_digest": _digest(asdict(plan)), "asset_config": spec.config,
        "contract": parser_contract,
    }
    return MaterialRecord.create(
        asset=asset, occurrence_id=occurrence_id,
        parser_implementation_digest=PROFILE_IMPLEMENTATION_DIGEST,
        parser_config_digest=_digest(parser_config), source_range=source_range,
        heading=heading, text=text, payload=payload,
        provenance=_provenance(method, asset, source_range),
    )


def parse_formalization_yaml(*, plan: ProfileRunPlan, asset: SourceAsset, text: str,
                             declarations: Mapping[str, DeclRef],
                             yaml_loader: Callable[[str], object] | None = None) -> ArtifactContributions:
    """Read main-results/alignment entries with PyYAML's safe loader.

    PyYAML is an optional material-ingestion dependency rather than a core
    package dependency.  Callers that do not install it may inject an
    equivalent trusted loader for an embedding or test.  The default path
    always uses ``safe_load`` and never enables Python object constructors.
    """
    spec = _asset_for_plan(plan, asset, text, "formalization_yaml")
    allowed_config = spec.config.get("allowed_declarations")
    if (allowed_config is not None and
            (not isinstance(allowed_config, list)
             or any(not isinstance(item, str) or not item for item in allowed_config)
             or len(allowed_config) != len(set(allowed_config)))):
        raise ValidationError("formalization allowed_declarations must be a unique string array")
    allowed = set(allowed_config) if allowed_config is not None else None
    if yaml_loader is None:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("formalization.yaml ingestion requires the PyYAML material extra") from exc
        yaml_loader = yaml.safe_load
    try:
        root = yaml_loader(text)
    except Exception as exc:
        raise ValidationError(f"invalid formalization.yaml: {exc}") from exc
    if not isinstance(root, dict):
        raise ValidationError("formalization.yaml root must be a mapping")
    _canonical_json(root)
    contract = {"paths": ["status.main_results", "alignment.statements"],
                "declaration_keys": ["declaration", "lean_declaration", "formal", "lean"]}
    entries: list[tuple[str, int, dict[str, Any]]] = []
    for path in (("status", "main_results"), ("alignment", "statements")):
        value: object = root
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValidationError(f"formalization.yaml {'.'.join(path)} must be a list")
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValidationError(f"formalization.yaml {'.'.join(path)} entries must be mappings")
            entries.append((".".join(path), index, item))
    lines = text.splitlines() or [""]
    records: list[MaterialRecord] = []
    bindings: list[MaterialBinding] = []
    outcomes: list[DeclLocator] = []
    diagnostics: list[ProfileDiagnostic] = []
    for path, index, item in entries:
        raw_declaration = next((item.get(key) for key in contract["declaration_keys"]
                                if isinstance(item.get(key), str) and item.get(key).strip()), None)
        subject = f"{path}[{index}]"
        if raw_declaration is None:
            diagnostics.append(ProfileDiagnostic("unknown_formalization_entry", subject,
                                                 "entry has no supported declaration field"))
            continue
        # v0.4 alignment entries use a semicolon-separated ``lean`` field and
        # may append shorthand such as ``(+_cumulative)``.  The base names are
        # explicit bindings; shorthand variants remain in the raw payload.
        declarations_in_entry = tuple(value for value in (
            part.strip().split(maxsplit=1)[0]
            for part in raw_declaration.split(";")
        ) if _LEAN_NAME.fullmatch(value))
        if allowed is not None:
            declarations_in_entry = tuple(
                declaration for declaration in declarations_in_entry
                if declaration in allowed
            )
        if not declarations_in_entry:
            if allowed is None:
                diagnostics.append(ProfileDiagnostic("unknown_formalization_entry", subject,
                                                     "declaration field has no supported Lean name"))
            continue
        declaration = declarations_in_entry[0]
        line = next((number for number, value in enumerate(lines, 1)
                     if declaration in value), 1)
        source_range = _source_range(asset, lines, line, line)
        heading = next((item.get(key) for key in
                        ("name", "title", "statement", "source", "description")
                        if isinstance(item.get(key), str)), declaration)
        record = _record(
            asset, spec, plan, occurrence_id=f"formalization:{path}:{index}",
            source_range=source_range, heading=heading, text=heading,
            payload={"formalization": item}, method="formalization_yaml", parser_contract=contract,
        )
        provenance = _provenance("formalization_yaml", asset, source_range)
        records.append(record)
        module = item.get("module") if isinstance(item.get("module"), str) else None
        if module is None and isinstance(item.get("file"), str):
            module = item["file"].removesuffix(".lean").replace("/", ".")
        for declaration in declarations_in_entry:
            locator = _resolve(declaration, plan.identity.repo_key, declarations, module)
            relation = "states" if path == "status.main_results" else "paper_label"
            bindings.append(MaterialBinding(record.record_id, _target(locator), relation,
                                            _status(locator), provenance))
            if path == "status.main_results":
                outcomes.append(locator)
            if isinstance(locator, UnresolvedDeclLocator):
                diagnostics.append(ProfileDiagnostic(
                    "unbound_declaration", declaration,
                    "formalization entry did not match the declaration inventory",
                ))
    return ArtifactContributions(
        _bundle(plan, asset, spec, records, bindings, diagnostics, contract),
        tuple(outcomes), diagnostics=tuple(diagnostics),
    )


_CODE = re.compile(r"`([^`\n]+)`")
_LEAN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")


def parse_proof_path_markdown(*, plan: ProfileRunPlan, asset: SourceAsset, text: str,
                              declarations: Mapping[str, DeclRef]) -> ArtifactContributions:
    """Parse Markdown headings and explicit code-formatted Lean landmarks."""
    spec = _asset_for_plan(plan, asset, text, "proof_path_markdown")
    lines = text.splitlines() or [""]
    headings = [index for index, line in enumerate(lines, 1)
                if (re.match(r"^#{1,6}\s+\S", line) or
                    re.match(r"^\*\*\d+\.\s+\S", line))]
    starts = headings or [1]
    records: list[MaterialRecord] = []
    bindings: list[MaterialBinding] = []
    landmarks: list[DeclLocator] = []
    diagnostics: list[ProfileDiagnostic] = []
    contract = {"heading": "ATX", "landmark": "code_span_matching_lean_identifier"}
    for occurrence, start in enumerate(starts):
        end = (starts[occurrence + 1] - 1) if occurrence + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start - 1:end]).strip()
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", lines[start - 1])
        numbered_match = re.match(r"^\*\*(\d+\.\s+.+?)\*\*", lines[start - 1])
        heading = (heading_match.group(1) if heading_match else
                   numbered_match.group(1) if numbered_match else None)
        source_range = _source_range(asset, lines, start, end)
        names = []
        for name in _CODE.findall(block):
            value = name.strip()
            if _LEAN_NAME.fullmatch(value) and value not in names:
                names.append(value)
        record = _record(
            asset, spec, plan, occurrence_id=f"proof-path:{occurrence}",
            source_range=source_range, heading=heading, text=block,
            payload={"proof_path": {"landmarks": names}}, method="proof_path_markdown",
            parser_contract=contract,
        )
        records.append(record)
        for name in names:
            locator = _resolve(name, plan.identity.repo_key, declarations)
            landmarks.append(locator)
            provenance = _provenance("proof_path_markdown", asset, source_range)
            bindings.append(MaterialBinding(record.record_id, _target(locator), "proof_route",
                                            _status(locator), provenance))
            if isinstance(locator, UnresolvedDeclLocator):
                diagnostics.append(ProfileDiagnostic("unbound_landmark", name,
                                                     "PROOF-PATH landmark did not match the inventory"))
    return ArtifactContributions(
        _bundle(plan, asset, spec, records, bindings, diagnostics, contract),
        order_landmarks=tuple(landmarks), diagnostics=tuple(diagnostics),
    )


class _PublishedHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current: str | None = None
        self.rows: dict[str, dict[str, Any]] = {}
        self.stack: list[tuple[str, str | None, bool]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        previous = self.current
        declaration = values.get("data-lean-declaration")
        if declaration:
            self.current = declaration.strip()
            self.rows.setdefault(self.current, {"summary": [], "dependencies": []})
        if self.current and values.get("data-lean-dependency"):
            dependency = values["data-lean-dependency"].strip()
            if dependency and dependency not in self.rows[self.current]["dependencies"]:
                self.rows[self.current]["dependencies"].append(dependency)
        if self.current and values.get("data-lean-summary"):
            self.rows[self.current]["summary"].append(values["data-lean-summary"].strip())
        classes = set(values.get("class", "").split())
        capture = self.current is not None and "decl-summary" in classes
        self.stack.append((tag, previous, capture))

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        open_tag, previous, _ = self.stack.pop()
        if open_tag == tag and previous != self.current:
            self.current = previous

    def handle_data(self, data: str) -> None:
        if self.current and any(item[2] for item in self.stack):
            value = data.strip()
            if value:
                self.rows[self.current]["summary"].append(value)


def parse_published_html_shard(*, plan: ProfileRunPlan, asset: SourceAsset, text: str,
                               declarations: Mapping[str, DeclRef],
                               allowed_declarations: tuple[str, ...] = ()) -> ArtifactContributions:
    """Extract declarations, summaries, and published edges from one selected shard.

    The contract intentionally recognizes only ``data-lean-*`` attributes and
    ``decl-summary`` text.  It does not scrape arbitrary site presentation.
    """
    spec = _asset_for_plan(plan, asset, text, "published_html")
    parser = _PublishedHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise ValidationError(f"invalid published HTML shard: {exc}") from exc
    allowed = set(allowed_declarations)
    lines = text.splitlines() or [""]
    contract = {"declaration": "data-lean-declaration", "summary": "data-lean-summary|decl-summary",
                "dependency": "data-lean-dependency"}
    records: list[MaterialRecord] = []
    bindings: list[MaterialBinding] = []
    edges: list[PublishedDependency] = []
    diagnostics: list[ProfileDiagnostic] = []
    for index, (name, row) in enumerate(parser.rows.items()):
        if allowed and name not in allowed:
            diagnostics.append(ProfileDiagnostic("unselected_html_declaration", name,
                                                 "declaration is outside the selected shard contract"))
            continue
        line = next((number for number, value in enumerate(lines, 1) if name in value), 1)
        source_range = _source_range(asset, lines, line, line)
        summary = " ".join(row["summary"]).strip() or None
        record = _record(
            asset, spec, plan, occurrence_id=f"published-html:{index}:{name}",
            source_range=source_range, heading=name, text=summary,
            payload={"published_html": {"declaration": name,
                                        "dependencies": row["dependencies"]}},
            method="published_html", parser_contract=contract,
        )
        dependent = _resolve(name, plan.identity.repo_key, declarations)
        provenance = _provenance("published_html", asset, source_range)
        records.append(record)
        bindings.append(MaterialBinding(record.record_id, _target(dependent), "published_summary",
                                        _status(dependent), provenance))
        if isinstance(dependent, UnresolvedDeclLocator):
            diagnostics.append(ProfileDiagnostic("unbound_published_declaration", name,
                                                 "HTML declaration did not match the inventory"))
        for provider_name in row["dependencies"]:
            provider = _resolve(provider_name, plan.identity.repo_key, declarations)
            edges.append(PublishedDependency(dependent, provider, provenance))
            if isinstance(provider, UnresolvedDeclLocator):
                diagnostics.append(ProfileDiagnostic("unbound_published_dependency", provider_name,
                                                     f"provider referenced by {name} is unknown"))
    if not parser.rows:
        diagnostics.append(ProfileDiagnostic("no_published_declarations", asset.path,
                                             "selected HTML shard contained no contract declaration"))
    return ArtifactContributions(
        _bundle(plan, asset, spec, records, bindings, diagnostics, contract),
        published_dependencies=tuple(edges), diagnostics=tuple(diagnostics),
    )


def _javascript_assignment(text: str, prefix: str, label: str) -> object:
    if not text.startswith(prefix) or not re.fullmatch(
            re.escape(prefix) + r".*;?\s*", text, re.DOTALL):
        raise ValidationError(f"invalid published bundle {label} assignment")
    payload = text[len(prefix):].rstrip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return _strict_json(payload)


def _published_edges(text: str) -> tuple[list[int], list[int]]:
    match = re.fullmatch(
        r"window\.FLT_EDGES=\{off:\[([0-9,]*)\],dst:\[([0-9,]*)\]\};?\s*",
        text,
    )
    if match is None:
        raise ValidationError("invalid published bundle CSR assignment")
    return tuple(
        [int(item) for item in group.split(",") if item]
        for group in match.groups()
    )


def _published_shard(text: str) -> tuple[str, dict[str, Any]]:
    match = re.fullmatch(r'FLT_SHARD_CB\("([0-9]{3})",(\{.*\})\);?\s*',
                         text, re.DOTALL)
    if match is None:
        raise ValidationError("invalid published theorem shard callback")
    value = _strict_json(match.group(2))
    if not isinstance(value, dict) or any(not isinstance(name, str) or not isinstance(row, dict)
                                          for name, row in value.items()):
        raise ValidationError("published theorem shard must map names to records")
    return match.group(1), value


def _fnv_shard(name: str) -> int:
    value = 2166136261
    for byte in name.encode("utf-8"):
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value % 256


def _substring_range(asset: SourceAsset, text: str, needle: str) -> SourceRange:
    offset = text.find(needle)
    if offset < 0:
        raise ValidationError(f"published shard does not contain selected name: {needle}")
    prefix = text[:offset]
    line = prefix.count("\n") + 1
    column = offset - prefix.rfind("\n")
    return SourceRange(asset.asset_id, line, column, line, column + len(needle))


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _html_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _TextOnlyHTMLParser()
    parser.feed(value)
    parser.close()
    result = " ".join(parser.parts).strip()
    return result or None


def parse_published_site_bundle(
        *, plan: ProfileRunPlan, assets: Mapping[str, tuple[SourceAsset, str]],
        declarations: Mapping[str, DeclRef],
        allowed_declarations: tuple[str, ...]) -> ArtifactContributions:
    """Consume the fixed FLT-style published JavaScript bundle.

    The graph is publication evidence derived by the site and remains separate
    from compiled Lean dependencies.  Only declarations explicitly selected by
    ``allowed_declarations`` become records or dependency rows.
    """
    plan.require("materials")
    if not allowed_declarations or len(set(allowed_declarations)) != len(allowed_declarations):
        raise ValidationError("published bundle needs distinct selected declarations")
    selected_specs: list[MaterialAssetSpec] = []
    fixed_assets: list[SourceAsset] = []
    texts: dict[str, str] = {}
    for asset_id, pair in assets.items():
        if (not isinstance(asset_id, str) or not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(pair[0], SourceAsset) or not isinstance(pair[1], str)):
            raise ValidationError("published bundle assets must map IDs to (SourceAsset, text)")
        asset, text = pair
        if asset.asset_id != asset_id:
            raise ValidationError("published bundle asset key does not match SourceAsset")
        spec = _asset_for_plan(plan, asset, text, "published_bundle")
        selected_specs.append(spec)
        fixed_assets.append(asset)
        texts[asset_id] = text
    if len(fixed_assets) != len(set(asset.asset_id for asset in fixed_assets)):
        raise ValidationError("published bundle asset IDs must be unique")
    global_specs = [spec for spec in selected_specs
                    if isinstance(spec.config.get("javascript_global"), str)]
    by_global = {spec.config["javascript_global"]: spec for spec in global_specs}
    if len(by_global) != len(global_specs):
        raise ValidationError("published bundle JavaScript globals must be unique")
    if set(by_global) != {"FLT_META", "FLT_EDGES", "FLT_TITLES"}:
        raise ValidationError("published bundle needs exactly FLT_META, FLT_EDGES, and FLT_TITLES")
    shard_specs = [spec for spec in selected_specs if spec.config.get("callback") == "FLT_SHARD_CB"]
    if not shard_specs:
        raise ValidationError("published bundle needs at least one FLT_SHARD_CB asset")
    meta = _javascript_assignment(texts[by_global["FLT_META"].asset_id],
                                  "window.FLT_META=", "metadata")
    titles = _javascript_assignment(texts[by_global["FLT_TITLES"].asset_id],
                                    "window.FLT_TITLES=", "titles")
    offsets, destinations = _published_edges(texts[by_global["FLT_EDGES"].asset_id])
    if not isinstance(meta, dict) or not isinstance(meta.get("names"), list):
        raise ValidationError("published bundle metadata needs a names array")
    names = meta["names"]
    if (not names or any(not isinstance(name, str) or not name for name in names)
            or len(names) != len(set(names))):
        raise ValidationError("published bundle declaration names must be nonempty and unique")
    if (not isinstance(titles, list) or len(titles) != len(names)
            or any(not isinstance(title, str) for title in titles)):
        raise ValidationError("published bundle titles must parallel declaration names")
    if (len(offsets) != len(names) + 1 or not offsets or offsets[0] != 0
            or any(left > right for left, right in zip(offsets, offsets[1:]))
            or offsets[-1] != len(destinations)
            or any(index < 0 or index >= len(names) for index in destinations)):
        raise ValidationError("published bundle CSR arrays are inconsistent")
    root = meta.get("root")
    if type(root) is not int or not 0 <= root < len(names):
        raise ValidationError("published bundle root index is invalid")
    name_index = {name: index for index, name in enumerate(names)}
    shard_rows: dict[str, tuple[dict[str, Any], MaterialAssetSpec, SourceAsset, str]] = {}
    asset_by_id = {asset.asset_id: asset for asset in fixed_assets}
    for spec in shard_specs:
        shard_id, rows = _published_shard(texts[spec.asset_id])
        expected_id = spec.config.get("shard_id")
        if expected_id is not None and shard_id != str(expected_id):
            raise ValidationError("published shard ID differs from profile")
        for name, row in rows.items():
            if name not in name_index or _fnv_shard(name) != int(shard_id):
                raise ValidationError("published shard membership is inconsistent")
            if name in shard_rows:
                raise ValidationError("published declaration occurs in multiple shards")
            shard_rows[name] = (row, spec, asset_by_id[spec.asset_id], texts[spec.asset_id])
    missing = sorted(set(allowed_declarations) - set(shard_rows))
    if missing:
        raise ValidationError(f"selected published declarations are absent from shards: {missing}")

    contract = {
        "metadata": "window.FLT_META names/root",
        "edges": "window.FLT_EDGES compressed sparse rows",
        "titles": "window.FLT_TITLES",
        "shards": "FLT_SHARD_CB with FNV-1a membership",
        "dependency_semantics": "published",
    }
    records: list[MaterialRecord] = []
    bindings: list[MaterialBinding] = []
    edges: list[PublishedDependency] = []
    diagnostics: list[ProfileDiagnostic] = []
    parser_config = _many_parser_config(plan, tuple(selected_specs), contract)
    for name in allowed_declarations:
        row, spec, shard_asset, shard_text = shard_rows[name]
        index = name_index[name]
        source_range = _substring_range(shard_asset, shard_text, name)
        english = row.get("en")
        summary_parts = []
        if isinstance(english, dict):
            summary_parts = [_html_text(english.get(key)) for key in ("statement_html", "proof_html")]
        summary = "\n\n".join(item for item in summary_parts if item) or None
        statement = row.get("dc") if isinstance(row.get("dc"), str) else None
        record = _record(
            shard_asset, spec, plan, occurrence_id=f"published-bundle:{name}",
            source_range=source_range, heading=titles[index], text=summary or statement,
            payload={"published_bundle": {
                "declaration": name, "statement": statement,
                "proof_lines": row.get("proof_lines"), "english": english,
            }}, method="published_site_bundle", parser_contract=contract,
            parser_config=parser_config,
        )
        dependent = _resolve(name, plan.identity.repo_key, declarations)
        record_provenance = _provenance("published_site_bundle", shard_asset, source_range)
        records.append(record)
        bindings.append(MaterialBinding(record.record_id, _target(dependent),
                                        "published_summary", _status(dependent),
                                        record_provenance))
        if isinstance(dependent, UnresolvedDeclLocator):
            diagnostics.append(ProfileDiagnostic(
                "unbound_published_declaration", name,
                "published declaration did not match the inventory",
            ))
        edge_asset = asset_by_id[by_global["FLT_EDGES"].asset_id]
        edge_text = texts[edge_asset.asset_id]
        edge_range = SourceRange(edge_asset.asset_id, 1, 1,
                                 edge_text.count("\n") + 1,
                                 len(edge_text.rsplit("\n", 1)[-1]) + 1)
        edge_provenance = _provenance("published_site_bundle_csr", edge_asset, edge_range)
        for provider_index in destinations[offsets[index]:offsets[index + 1]]:
            provider_name = names[provider_index]
            provider = _resolve(provider_name, plan.identity.repo_key, declarations)
            edges.append(PublishedDependency(dependent, provider, edge_provenance))
            if isinstance(provider, UnresolvedDeclLocator):
                diagnostics.append(ProfileDiagnostic(
                    "unbound_published_dependency", provider_name,
                    f"provider referenced by {name} is unknown",
                ))
    return ArtifactContributions(
        _bundle_many(plan, tuple(fixed_assets), tuple(selected_specs), records,
                     bindings, diagnostics, contract),
        published_dependencies=tuple(edges), diagnostics=tuple(diagnostics),
    )
