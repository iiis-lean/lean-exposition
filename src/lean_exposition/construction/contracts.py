"""Durable, Workspace-bound construction contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from lean_exposition.models import DeclRef, Provenance, ValidationError, Workspace
from .contributions import (
    COVERAGE_DOMAINS, COVERAGE_STATES, SourceTextContribution,
    UNIT_AGGREGATIONS, UnresolvedDeclLocator,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _nonempty(value: str, label: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty")


def _validate_provenance(values: tuple[Provenance, ...], label: str) -> None:
    _require(bool(values), f"{label} provenance is required")
    for value in values:
        _nonempty(value.method, "provenance method")
        _nonempty(value.source_ref, "provenance source_ref")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class StructurePolicy:
    workspace_digest: str
    unit_aggregation: str
    provenance: tuple[Provenance, ...]
    production_structure: bool

    def validate(self) -> None:
        _require(re.fullmatch(r"[0-9a-f]{64}", self.workspace_digest) is not None,
                 "StructurePolicy workspace_digest must be lowercase SHA256")
        _require(self.unit_aggregation in UNIT_AGGREGATIONS, "invalid unit aggregation policy")
        _require(type(self.production_structure) is bool,
                 "StructurePolicy production_structure must be boolean")
        _validate_provenance(self.provenance, "StructurePolicy")

    def digest(self) -> str:
        self.validate()
        return _digest(asdict(self))

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "StructurePolicy":
        value = _load_json(cls, text, "structure_policy")
        value.validate()
        return value


@dataclass(frozen=True)
class CoverageEntry:
    ref: DeclRef
    part: str
    evidence_domain: str
    status: str
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class DependencyCoverage:
    workspace_digest: str
    entries: tuple[CoverageEntry, ...]

    def validate(self, workspace: Workspace | None = None) -> None:
        _require(re.fullmatch(r"[0-9a-f]{64}", self.workspace_digest) is not None,
                 "DependencyCoverage workspace_digest must be lowercase SHA256")
        if workspace is not None:
            _require(self.workspace_digest == workspace.digest(), "stale DependencyCoverage workspace digest")
        seen: set[tuple[DeclRef, str, str]] = set()
        decls = {decl.ref: decl for decl in workspace.declarations} if workspace is not None else None
        for entry in self.entries:
            key = (entry.ref, entry.part, entry.evidence_domain)
            _require(key not in seen, f"duplicate dependency coverage entry: {key}")
            seen.add(key)
            _require(entry.part in {"statement", "proof"}, "invalid dependency coverage part")
            _require(entry.evidence_domain in COVERAGE_DOMAINS, "invalid dependency coverage domain")
            _require(entry.status in COVERAGE_STATES, "invalid dependency coverage status")
            _validate_provenance(entry.provenance, "DependencyCoverage entry")
            if decls is not None:
                _require(entry.ref in decls, f"dependency coverage references unknown declaration: {entry.ref}")
                _require(entry.part == "statement" or decls[entry.ref].proof is not None,
                         "proof coverage references a declaration without proof content")
        if decls is not None:
            expected = {
                (decl.ref, part, domain)
                for decl in workspace.declarations
                for part in (("statement", "proof") if decl.proof is not None else ("statement",))
                for domain in COVERAGE_DOMAINS
            }
            _require(seen == expected, "DependencyCoverage must explicitly cover every declaration part/domain")

    def digest(self) -> str:
        self.validate()
        return _digest(asdict(self))

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "DependencyCoverage":
        value = _load_json(cls, text, "dependency_coverage")
        value.validate()
        return value


@dataclass(frozen=True)
class RepositoryBuildBundle:
    workspace: Workspace
    structure_policy: StructurePolicy
    dependency_coverage: DependencyCoverage
    source_texts: tuple[SourceTextContribution, ...] = ()
    diagnostics: tuple[str, ...] = ()
    unresolved_locators: tuple[UnresolvedDeclLocator, ...] = ()

    def validate(self) -> None:
        self.workspace.validate()
        digest = self.workspace.digest()
        self.structure_policy.validate()
        _require(self.structure_policy.workspace_digest == digest, "stale StructurePolicy workspace digest")
        self.dependency_coverage.validate(self.workspace)
        decls = {decl.ref for decl in self.workspace.declarations}
        assets = {asset.asset_id for asset in self.workspace.manifest.assets}

        def sidecar_provenance(values: tuple[Provenance, ...], label: str) -> None:
            _validate_provenance(values, label)
            for provenance in values:
                for location in provenance.ranges:
                    _require(location.asset_id in assets,
                             f"{label} provenance references unknown asset")

        sidecar_provenance(self.structure_policy.provenance, "StructurePolicy")
        for entry in self.dependency_coverage.entries:
            sidecar_provenance(entry.provenance, "DependencyCoverage entry")
        text_keys: set[tuple[DeclRef, str, str]] = set()
        for item in self.source_texts:
            _require(item.ref in decls, f"source text references unknown declaration: {item.ref}")
            _nonempty(item.text_kind, "source text kind")
            _nonempty(item.locale, "source text locale")
            _require(isinstance(item.text, str), "source text must be a string")
            sidecar_provenance(item.provenance, "source text")
            _require(re.fullmatch(r"[0-9a-f]{64}", item.input_digest) is not None,
                     "source text input_digest must be lowercase SHA256")
            key = (item.ref, item.text_kind, item.locale)
            _require(key not in text_keys, f"duplicate source text contribution: {key}")
            text_keys.add(key)
        for value in self.diagnostics:
            _nonempty(value, "diagnostic")
        for locator in self.unresolved_locators:
            _nonempty(locator.repo_key, "unresolved locator repo_key")
            _nonempty(locator.module, "unresolved locator module")
            _nonempty(locator.raw_name, "unresolved locator raw_name")
            if locator.source_range is not None:
                _require(locator.source_range.asset_id in assets,
                         "unresolved locator references unknown asset")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def digest(self) -> str:
        self.validate()
        return _digest(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "RepositoryBuildBundle":
        bundle = _load_json(cls, text, "bundle")
        bundle.validate()
        return bundle


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
                return tuple(_decode(args[0], item, f"{path}[{i}]") for i, item in enumerate(value))
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
