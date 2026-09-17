"""Fixed material records and declaration bindings.

Material bytes keep using :class:`~lean_exposition.models.SourceAsset`.  This
module only records parser output and binder decisions, each with an identity
that changes when its implementation or effective configuration changes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lean_exposition.models import DeclRef, Provenance, SourceAsset, SourceRange, ValidationError


BINDING_STATUSES = ("exact", "candidate", "ambiguous", "unresolved")
BINDING_RELATIONS = (
    "states", "explains", "proof_route", "paper_label", "citation", "published_summary",
)
TARGET_KINDS = ("repository", "scope", "declaration", "declaration_locator")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"material value is not canonical JSON: {exc}") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _nonempty(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be nonempty")


def _validate_range(value: SourceRange, assets: set[str]) -> None:
    if value.asset_id not in assets:
        raise ValidationError("material range references unknown asset")
    if (type(value.start_line) is not int or type(value.end_line) is not int or
            value.start_line < 1 or value.end_line < value.start_line):
        raise ValidationError("invalid material source range lines")
    for column in (value.start_column, value.end_column):
        if column is not None and (type(column) is not int or column < 1):
            raise ValidationError("invalid material source range column")


def _validate_provenance(values: tuple[Provenance, ...], assets: set[str]) -> None:
    if not values:
        raise ValidationError("material provenance is required")
    for value in values:
        _nonempty(value.method, "material provenance method")
        _nonempty(value.source_ref, "material provenance source_ref")
        for location in value.ranges:
            _validate_range(location, assets)


@dataclass(frozen=True)
class MaterialTarget:
    """A binding target; exactly one variant payload is present."""

    kind: str
    repo_key: str
    identifier: str | None = None
    ref: DeclRef | None = None

    def validate(self) -> None:
        if self.kind not in TARGET_KINDS:
            raise ValidationError("invalid material target kind")
        _nonempty(self.repo_key, "material target repo_key")
        if self.kind == "declaration":
            if self.ref is None or self.identifier is not None or self.ref.repo_key != self.repo_key:
                raise ValidationError("declaration target needs one matching DeclRef")
            _nonempty(self.ref.local_id, "material target declaration local_id")
        elif self.ref is not None or not isinstance(self.identifier, str) or not self.identifier:
            raise ValidationError("non-declaration target needs one identifier")

    def canonical_key(self) -> tuple[str, str, str]:
        self.validate()
        value = self.ref.local_id if self.ref is not None else self.identifier
        return self.kind, self.repo_key, value or ""


@dataclass(frozen=True)
class MaterialRecord:
    record_id: str
    asset_id: str
    occurrence_id: str
    source_range: SourceRange | None
    page: int | None = None
    url: str | None = None
    heading: str | None = None
    label: str | None = None
    parent_record_id: str | None = None
    text: str | None = None
    payload: dict[str, Any] | None = None
    provenance: tuple[Provenance, ...] = ()

    @classmethod
    def create(cls, *, asset: SourceAsset, occurrence_id: str,
               parser_implementation_digest: str, parser_config_digest: str,
               source_range: SourceRange | None = None, page: int | None = None,
               url: str | None = None, heading: str | None = None, label: str | None = None,
               parent_record_id: str | None = None, text: str | None = None,
               payload: dict[str, Any] | None = None,
               provenance: tuple[Provenance, ...]) -> "MaterialRecord":
        identity = _record_identity(
            asset, occurrence_id, source_range, page, url, heading, label, parent_record_id,
            parser_implementation_digest, parser_config_digest,
        )
        return cls("material:" + _digest(identity)[:32], asset.asset_id, occurrence_id,
                   source_range, page, url, heading, label, parent_record_id, text, payload,
                   provenance)


@dataclass(frozen=True)
class MaterialBinding:
    record_id: str
    target: MaterialTarget
    relation: str
    status: str
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class MaterialBundle:
    repo_key: str
    assets: tuple[SourceAsset, ...]
    records: tuple[MaterialRecord, ...]
    bindings: tuple[MaterialBinding, ...]
    parser_implementation_digest: str
    parser_config: dict[str, Any]
    binder_implementation_digest: str
    binder_config: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...] = ()

    def validate(self) -> None:
        _nonempty(self.repo_key, "material repo_key")
        if not _is_digest(self.parser_implementation_digest):
            raise ValidationError("parser implementation digest must be lowercase SHA256")
        if not _is_digest(self.binder_implementation_digest):
            raise ValidationError("binder implementation digest must be lowercase SHA256")
        _canonical_json(self.parser_config)
        _canonical_json(self.binder_config)
        _canonical_json(self.diagnostics)
        asset_ids: set[str] = set()
        assets: dict[str, SourceAsset] = {}
        for asset in self.assets:
            _nonempty(asset.asset_id, "material asset_id")
            if asset.asset_id in asset_ids:
                raise ValidationError("duplicate material asset_id")
            if asset.repo_key != self.repo_key or not _is_digest(asset.sha256):
                raise ValidationError("invalid material SourceAsset")
            _nonempty(asset.path, "material asset path")
            asset_ids.add(asset.asset_id)
            assets[asset.asset_id] = asset
        if tuple(sorted(self.assets, key=lambda item: item.asset_id)) != self.assets:
            raise ValidationError("material assets must be canonical")
        record_ids: set[str] = set()
        parser_config_digest = _digest(self.parser_config)
        for record in self.records:
            _nonempty(record.record_id, "material record_id")
            _nonempty(record.occurrence_id, "material occurrence_id")
            if record.record_id in record_ids:
                raise ValidationError("duplicate material record_id")
            if record.asset_id not in assets:
                raise ValidationError("material record references unknown asset")
            if record.source_range is not None:
                _validate_range(record.source_range, asset_ids)
                if record.source_range.asset_id != record.asset_id:
                    raise ValidationError("material record range must use its asset")
            if record.page is not None and (type(record.page) is not int or record.page < 1):
                raise ValidationError("material page must be a positive integer")
            if record.url is not None and not isinstance(record.url, str):
                raise ValidationError("material URL must be a string")
            for label, item in (("heading", record.heading), ("label", record.label),
                                ("parent_record_id", record.parent_record_id)):
                if item is not None and not isinstance(item, str):
                    raise ValidationError(f"material {label} must be a string")
            if record.text is not None and not isinstance(record.text, str):
                raise ValidationError("material text must be a string")
            if record.payload is not None:
                if not isinstance(record.payload, dict):
                    raise ValidationError("material payload must be a JSON object")
                _canonical_json(record.payload)
            _validate_provenance(record.provenance, asset_ids)
            expected = "material:" + _digest(_record_identity(
                assets[record.asset_id], record.occurrence_id, record.source_range, record.page,
                record.url, record.heading, record.label, record.parent_record_id,
                self.parser_implementation_digest, parser_config_digest,
            ))[:32]
            if record.record_id != expected:
                raise ValidationError("material record identity mismatch")
            record_ids.add(record.record_id)
        if tuple(sorted(self.records, key=lambda item: item.record_id)) != self.records:
            raise ValidationError("material records must be canonical")
        binding_keys: set[tuple] = set()
        for binding in self.bindings:
            if binding.record_id not in record_ids:
                raise ValidationError("material binding references unknown record")
            binding.target.validate()
            if binding.relation not in BINDING_RELATIONS:
                raise ValidationError("invalid material binding relation")
            if binding.status not in BINDING_STATUSES:
                raise ValidationError("invalid material binding status")
            _validate_provenance(binding.provenance, asset_ids)
            key = (binding.record_id, binding.target.canonical_key(), binding.relation)
            if key in binding_keys:
                raise ValidationError("duplicate material binding")
            binding_keys.add(key)
        if tuple(sorted(self.bindings, key=_binding_key)) != self.bindings:
            raise ValidationError("material bindings must be canonical")
        for record in self.records:
            if record.parent_record_id is not None and record.parent_record_id not in record_ids:
                raise ValidationError("material parent references unknown record")

    @classmethod
    def create(cls, *, repo_key: str, assets: tuple[SourceAsset, ...],
               records: tuple[MaterialRecord, ...], bindings: tuple[MaterialBinding, ...] = (),
               parser_implementation_digest: str, parser_config: dict[str, Any],
               binder_implementation_digest: str, binder_config: dict[str, Any],
               diagnostics: tuple[dict[str, Any], ...] = ()) -> "MaterialBundle":
        value = cls(repo_key, tuple(sorted(assets, key=lambda item: item.asset_id)),
                    tuple(sorted(records, key=lambda item: item.record_id)),
                    tuple(sorted(bindings, key=_binding_key)), parser_implementation_digest,
                    dict(parser_config), binder_implementation_digest, dict(binder_config),
                    tuple(diagnostics))
        value.validate()
        return value

    @property
    def parser_config_digest(self) -> str:
        return _digest(self.parser_config)

    @property
    def binder_config_digest(self) -> str:
        return _digest(self.binder_config)

    def material_digest(self) -> str:
        self.validate()
        return _digest({
            "repo_key": self.repo_key,
            "assets": [asdict(item) for item in self.assets],
            "records": [asdict(item) for item in self.records],
            "parser_implementation_digest": self.parser_implementation_digest,
            "parser_config": self.parser_config,
            "diagnostics": self.diagnostics,
        })

    def binding_digest(self) -> str:
        self.validate()
        return _digest({
            "material_digest": self.material_digest(),
            "bindings": [asdict(item) for item in self.bindings],
            "binder_implementation_digest": self.binder_implementation_digest,
            "binder_config": self.binder_config,
        })

    def digest(self) -> str:
        return _digest({"material_digest": self.material_digest(),
                        "binding_digest": self.binding_digest()})

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def from_dict(cls, data: object) -> "MaterialBundle":
        expected = {
            "repo_key", "assets", "records", "bindings", "parser_implementation_digest",
            "parser_config", "binder_implementation_digest", "binder_config", "diagnostics",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValidationError("invalid material bundle fields")
        if not all(isinstance(data[name], list) for name in ("assets", "records", "bindings", "diagnostics")):
            raise ValidationError("invalid material bundle array fields")
        if not isinstance(data["parser_config"], dict) or not isinstance(data["binder_config"], dict):
            raise ValidationError("invalid material configuration fields")
        assets = tuple(_source_asset(item) for item in data["assets"])
        records = tuple(_material_record(item) for item in data["records"])
        bindings = tuple(_material_binding(item) for item in data["bindings"])
        if any(not isinstance(item, dict) for item in data["diagnostics"]):
            raise ValidationError("material diagnostics must be objects")
        value = cls(data["repo_key"], assets, records, bindings,
                    data["parser_implementation_digest"], dict(data["parser_config"]),
                    data["binder_implementation_digest"], dict(data["binder_config"]),
                    tuple(dict(item) for item in data["diagnostics"]))
        value.validate()
        return value

    @classmethod
    def from_json(cls, text: str) -> "MaterialBundle":
        return cls.from_dict(_load_json(text))

    @classmethod
    def load(cls, path: str | Path) -> "MaterialBundle":
        return cls.from_json(Path(path).read_text())


def _record_identity(asset: SourceAsset, occurrence_id: str, source_range: SourceRange | None,
                     page: int | None, url: str | None, heading: str | None, label: str | None,
                     parent_record_id: str | None, parser_implementation_digest: str,
                     parser_config_digest: str) -> dict[str, Any]:
    return {
        "asset_sha256": asset.sha256,
        "occurrence_id": occurrence_id,
        "source_range": asdict(source_range) if source_range is not None else None,
        "page": page,
        "url": url,
        "heading": heading,
        "label": label,
        "parent_record_id": parent_record_id,
        "parser_implementation_digest": parser_implementation_digest,
        "parser_config_digest": parser_config_digest,
    }


def _binding_key(binding: MaterialBinding) -> tuple:
    return (binding.record_id, binding.target.canonical_key(), binding.relation, binding.status,
            _canonical_json(asdict(binding)))


def _load_json(text: str) -> object:
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


def _exact_fields(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"invalid {label} fields")
    return value


def _source_asset(value: object) -> SourceAsset:
    value = _exact_fields(value, {"asset_id", "repo_key", "path", "sha256"}, "SourceAsset")
    return SourceAsset(value["asset_id"], value["repo_key"], value["path"], value["sha256"])


def _source_range(value: object | None) -> SourceRange | None:
    if value is None:
        return None
    value = _exact_fields(value, {"asset_id", "start_line", "start_column", "end_line", "end_column"},
                          "SourceRange")
    return SourceRange(value["asset_id"], value["start_line"], value["start_column"],
                       value["end_line"], value["end_column"])


def _provenance(value: object) -> Provenance:
    value = _exact_fields(value, {"method", "source_ref", "ranges"}, "Provenance")
    if not isinstance(value["ranges"], list):
        raise ValidationError("provenance ranges must be an array")
    ranges = tuple(_source_range(item) for item in value["ranges"])
    if any(item is None for item in ranges):
        raise ValidationError("provenance ranges cannot contain null")
    return Provenance(value["method"], value["source_ref"], ranges)


def _decl_ref(value: object | None) -> DeclRef | None:
    if value is None:
        return None
    value = _exact_fields(value, {"repo_key", "local_id"}, "DeclRef")
    return DeclRef(value["repo_key"], value["local_id"])


def _material_target(value: object) -> MaterialTarget:
    value = _exact_fields(value, {"kind", "repo_key", "identifier", "ref"}, "material target")
    return MaterialTarget(value["kind"], value["repo_key"], value["identifier"],
                          _decl_ref(value["ref"]))


def _material_record(value: object) -> MaterialRecord:
    fields = {
        "record_id", "asset_id", "occurrence_id", "source_range", "page", "url", "heading",
        "label", "parent_record_id", "text", "payload", "provenance",
    }
    value = _exact_fields(value, fields, "material record")
    if not isinstance(value["provenance"], list):
        raise ValidationError("material record provenance must be an array")
    return MaterialRecord(value["record_id"], value["asset_id"], value["occurrence_id"],
                          _source_range(value["source_range"]), value["page"], value["url"],
                          value["heading"], value["label"], value["parent_record_id"],
                          value["text"], value["payload"],
                          tuple(_provenance(item) for item in value["provenance"]))


def _material_binding(value: object) -> MaterialBinding:
    value = _exact_fields(value, {"record_id", "target", "relation", "status", "provenance"},
                          "material binding")
    if not isinstance(value["provenance"], list):
        raise ValidationError("material binding provenance must be an array")
    return MaterialBinding(value["record_id"], _material_target(value["target"]),
                           value["relation"], value["status"],
                           tuple(_provenance(item) for item in value["provenance"]))
