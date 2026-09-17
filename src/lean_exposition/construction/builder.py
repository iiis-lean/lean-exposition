"""Authority-aware assembly of repository contributions into fixed facts."""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
import hashlib
import json
import re

from lean_exposition.models import (
    DeclContent, DeclRef, DeclUnit, Dependency, Provenance, RawDecl, Repository,
    Scope, SourceRange, Status, TextContent, ValidationError, Workspace,
    WorkspaceManifest,
)
from .contracts import CoverageEntry, DependencyCoverage, RepositoryBuildBundle, StructurePolicy
from .contributions import (
    COVERAGE_DOMAINS, COVERAGE_STATES, CONTRIBUTION_STATES,
    CanonicalDeclLocator, CoverageContribution, DeclarationContribution,
    FieldContribution, DeclUnitSeed, RepositoryAdapterResult, UNIT_AGGREGATIONS,
    UnresolvedDeclLocator,
)


_FIELDS = {
    "lean_name", "module", "native_scope", "kind",
    "statement.nl", "statement.formal", "statement.deps",
    "extraction_status", "provenance", "completion_status",
    "proof.nl", "proof.formal", "proof.deps",
    "kernel_kind", "source_refs", "source_context", "local_public", "generated_from",
}
_REQUIRED = {
    "lean_name", "module", "native_scope", "kind", "statement.nl", "statement.formal",
    "extraction_status", "provenance",
}
_UNION_FIELDS = {"statement.deps", "proof.deps", "provenance", "source_refs", "source_context"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _merge_provenance(left: tuple[Provenance, ...], right: tuple[Provenance, ...]):
    return _unique((*left, *right))


def _semantic(value):
    """Compare an observed value while treating provenance as evidence, not payload."""
    if is_dataclass(value):
        return tuple((field.name, _semantic(getattr(value, field.name)))
                     for field in fields(value) if field.name != "provenance")
    if isinstance(value, tuple):
        return tuple(_semantic(item) for item in value)
    return value


def _merge_equal(left, right):
    if _semantic(left) != _semantic(right):
        return None
    if isinstance(left, TextContent):
        check = left.check
        if check is not None and right.check is not None:
            check = _merge_equal(check, right.check)
        return replace(left, provenance=_merge_provenance(left.provenance, right.provenance), check=check)
    if isinstance(left, Status):
        return replace(left, provenance=_merge_provenance(left.provenance, right.provenance))
    if isinstance(left, Dependency):
        return replace(left, provenance=_merge_provenance(left.provenance, right.provenance))
    return left


def _authority(field: str, authority: str) -> int:
    if authority == "lean_source":
        return 110
    if authority == "lc_catalog":
        return 100
    if authority == "lean_environment":
        return 100 if field in {
            "lean_name", "module", "kind", "kernel_kind", "generated_from",
            "statement.deps", "proof.deps",
        } else 20
    if authority in {"source", "text_ast"}:
        return 100 if field in {
            "module", "native_scope", "statement.formal", "proof.formal",
            "source_refs", "source_context",
        } else 30
    if authority in {"project_metadata", "project_profile"}:
        return 80 if field in {"native_scope", "local_public", "completion_status"} else 10
    if authority == "generated":
        return 0
    return 50


def _validate_provenance(values: tuple[Provenance, ...], label: str) -> None:
    _require(bool(values), f"{label} provenance is required")
    for value in values:
        _require(bool(value.method.strip()) and bool(value.source_ref.strip()),
                 f"{label} provenance must be nonempty")


def _validate_field(value: FieldContribution) -> None:
    _require(value.field in _FIELDS, f"unsupported declaration field: {value.field}")
    _require(value.state in CONTRIBUTION_STATES, "invalid field contribution state")
    _require((value.value is not None) == (value.state == "present"),
             "field contribution value/state mismatch")
    _require(bool(value.authority.strip()) and bool(value.backend.strip()) and bool(value.method.strip()),
             "field contribution authority/backend/method must be nonempty")
    _validate_provenance(value.provenance, "field contribution")
    if value.input_digest is not None:
        _require(re.fullmatch(r"[0-9a-f]{64}", value.input_digest) is not None,
                 "field contribution input_digest must be lowercase SHA256")


def _merge_dependency_values(values) -> tuple[Dependency, ...]:
    merged: list[Dependency] = []
    for value in values:
        _require(isinstance(value, tuple) and all(isinstance(item, Dependency) for item in value),
                 "dependency fields must be tuples of Dependency")
        for dependency in value:
            position = next((i for i, current in enumerate(merged)
                             if (current.provider, current.evidence_kind) ==
                             (dependency.provider, dependency.evidence_kind)), None)
            if position is None:
                merged.append(dependency)
            else:
                merged[position] = replace(
                    merged[position],
                    provenance=_merge_provenance(merged[position].provenance, dependency.provenance),
                )
    return tuple(merged)


def _merge_field(field: str, contributions: list[FieldContribution], diagnostics=None, exact_authorities=()):
    present = [value for value in contributions if value.state == "present"]
    if present and any(value.state == "not_applicable" for value in contributions):
        if diagnostics is not None:
            diagnostics.append(f"field_conflict:{field}:present_vs_not_applicable")
    if not present:
        return None
    if field in {"statement.deps", "proof.deps"}:
        # Complete compiler/catalog contributions (including confirmed empty)
        # replace approximate references in the same declaration part.
        exact = [v for v in present if v.authority in exact_authorities]
        return _merge_dependency_values([value.value for value in (exact or present)])
    available = [v for v in present if not isinstance(v.value, TextContent) or v.value.text is not None]
    present = available or present
    if field in _UNION_FIELDS:
        tuples = [value.value for value in present]
        _require(all(isinstance(value, tuple) for value in tuples), f"{field} must be a tuple")
        return _unique(item for value in tuples for item in value)
    best = max(_authority(field, value.authority) for value in present)
    selected = sorted((value for value in present if _authority(field, value.authority) == best),
                      key=lambda value: (value.backend, value.method, repr(value.value)))
    result = selected[0].value
    for value in selected[1:]:
        merged = _merge_equal(result, value.value)
        if merged is None:
            if diagnostics is not None:
                diagnostics.append(f"field_conflict:{field}:{selected[0].backend}:{value.backend}:"
                                   f"selected={result!r}:alternative={value.value!r}")
        else:
            result = merged
    return result


def _fields_for(contributions: list[DeclarationContribution], diagnostics=None, complete_domains=None) -> dict[str, object]:
    grouped: dict[str, list[FieldContribution]] = {}
    for contribution in contributions:
        seen: set[str] = set()
        for value in contribution.fields:
            _validate_field(value)
            _require(value.field not in seen, f"duplicate field in one declaration contribution: {value.field}")
            seen.add(value.field)
            grouped.setdefault(value.field, []).append(value)
    complete_domains = complete_domains or set()
    result = {}
    for field, values in grouped.items():
        part = field.split('.')[0]
        exact = set()
        if (part, 'lc_declared') in complete_domains:
            exact.add('lc_catalog')
        needed = {'lean_type'} if part == 'statement' else {'lean_value'}
        if part == 'statement' and not any(k.startswith('proof.') for k in grouped):
            needed.add('lean_value')
        if all((part, domain) in complete_domains for domain in needed):
            exact.add('lean_environment')
        merged = _merge_field(field, values, diagnostics, exact)
        if merged is not None:
            result[field] = merged
    return result


def _range_matches(expected: SourceRange | None, fields_by_ref: dict[DeclRef, dict[str, object]], ref: DeclRef):
    if expected is None:
        return True
    return expected in fields_by_ref[ref].get("source_refs", ())


def _raw_decl(ref: DeclRef, values: dict[str, object]) -> RawDecl:
    missing = sorted(_REQUIRED - values.keys())
    _require(not missing, f"declaration {ref} is missing required fields: {missing}")
    proof_keys = {key for key in values if key.startswith("proof.")}
    if proof_keys:
        _require({"proof.nl", "proof.formal"} <= proof_keys,
                 f"declaration {ref} has incomplete proof content")
        proof = DeclContent(values["proof.nl"], values["proof.formal"], values.get("proof.deps", ()))
    else:
        proof = None
    return RawDecl(
        ref=ref,
        lean_name=values["lean_name"],
        module=values["module"],
        native_scope=values["native_scope"],
        kind=values["kind"],
        statement=DeclContent(values["statement.nl"], values["statement.formal"],
                              values.get("statement.deps", ())),
        extraction_status=values["extraction_status"],
        provenance=values["provenance"],
        completion_status=values.get("completion_status"),
        proof=proof,
        kernel_kind=values.get("kernel_kind"),
        source_refs=values.get("source_refs", ()),
        source_context=values.get("source_context", ()),
        local_public=values.get("local_public", False),
        generated_from=values.get("generated_from"),
    )


def _coverage(adapter: RepositoryAdapterResult, workspace: Workspace) -> DependencyCoverage:
    grouped: dict[tuple[DeclRef, str, str], list[CoverageContribution]] = {}
    decls = {decl.ref: decl for decl in workspace.declarations}
    for value in adapter.coverage:
        _require(value.ref in decls, f"coverage contribution references unknown declaration: {value.ref}")
        _require(value.part in {"statement", "proof"}, "invalid coverage contribution part")
        _require(value.part == "statement" or decls[value.ref].proof is not None,
                 "proof coverage contribution references declaration without proof")
        _require(value.evidence_domain in COVERAGE_DOMAINS, "invalid coverage contribution domain")
        _require(value.status in COVERAGE_STATES, "invalid coverage contribution status")
        _validate_provenance(value.provenance, "coverage contribution")
        grouped.setdefault((value.ref, value.part, value.evidence_domain), []).append(value)
    implicit = (Provenance("repository_builder", "implicit-unknown-coverage"),)
    entries = []
    rank = {"unknown": 0, "partial": 1, "complete": 2}
    for decl in workspace.declarations:
        for part in (("statement", "proof") if decl.proof is not None else ("statement",)):
            for domain in COVERAGE_DOMAINS:
                values = grouped.get((decl.ref, part, domain), ())
                if not values:
                    entries.append(CoverageEntry(decl.ref, part, domain, "unknown", implicit))
                    continue
                statuses = {value.status for value in values}
                if "not_applicable" in statuses:
                    _require(statuses <= {"not_applicable", "unknown"},
                             "not_applicable coverage conflicts with observed coverage")
                    status = "not_applicable"
                else:
                    status = max(statuses, key=rank.__getitem__)
                provenance = _unique(p for value in values for p in value.provenance)
                entries.append(CoverageEntry(decl.ref, part, domain, status, provenance))
    result = DependencyCoverage(workspace.digest(), tuple(entries))
    result.validate(workspace)
    return result


def build_repository(adapter: RepositoryAdapterResult) -> RepositoryBuildBundle:
    """Build and strictly validate one repository adapter result."""
    _require(adapter.unit_aggregation in UNIT_AGGREGATIONS, "invalid unit aggregation policy")
    asset_ids = {asset.asset_id for asset in adapter.assets}

    def adapter_provenance(values: tuple[Provenance, ...], label: str) -> None:
        _validate_provenance(values, label)
        for provenance in values:
            for location in provenance.ranges:
                _require(location.asset_id in asset_ids,
                         f"{label} provenance references unknown asset")

    for seed in adapter.units:
        adapter_provenance(seed.provenance, "unit seed")
    for contribution in adapter.declarations:
        for value in contribution.fields:
            adapter_provenance(value.provenance, "field contribution")
    canonical: dict[DeclRef, list[DeclarationContribution]] = {}
    unresolved: list[DeclarationContribution] = []
    order: list[DeclRef] = []
    for contribution in adapter.declarations:
        _require(bool(contribution.fields), "declaration contribution needs at least one field")
        if isinstance(contribution.locator, CanonicalDeclLocator):
            ref = contribution.locator.ref
            if ref not in canonical:
                order.append(ref)
            canonical.setdefault(ref, []).append(contribution)
        elif isinstance(contribution.locator, UnresolvedDeclLocator):
            unresolved.append(contribution)
        else:
            raise ValidationError("unknown declaration locator")

    diagnostics = list(adapter.diagnostics)
    complete_domains = {}
    for item in adapter.coverage:
        if item.status in {'complete', 'not_applicable'}:
            complete_domains.setdefault(item.ref, set()).add((item.part, item.evidence_domain))
    fields_by_ref = {ref: _fields_for(values, diagnostics, complete_domains.get(ref)) for ref, values in canonical.items()}
    unresolved_locators: list[UnresolvedDeclLocator] = []
    fallback_units = []
    for contribution in unresolved:
        locator = contribution.locator
        if locator.authoritative_ref is not None:
            candidates = [locator.authoritative_ref] if locator.authoritative_ref in canonical else []
        else:
            candidates = [
                ref for ref, values in fields_by_ref.items()
                if ref.repo_key == locator.repo_key
                and values.get("module") == locator.module
                and values.get("lean_name") == locator.raw_name
                and _range_matches(locator.source_range, fields_by_ref, ref)
            ]
        if len(candidates) != 1:
            unresolved_locators.append(locator)
            values = _fields_for([contribution], diagnostics)
            if _REQUIRED <= values.keys():
                identity = "source:" + hashlib.sha256(json.dumps(asdict(locator), sort_keys=True).encode()).hexdigest()
                ref = DeclRef(locator.repo_key, identity)
                scopes = {s.scope_id for s in adapter.scopes}
                if values['native_scope'] not in scopes:
                    root = next((r.root_scope for r in adapter.repositories if r.repo_key == locator.repo_key), None)
                    if root is None:
                        continue
                    values['native_scope'] = root
                if ref not in fields_by_ref:
                    order.append(ref)
                    fields_by_ref[ref] = values
                    fallback_units.append(DeclUnitSeed(json.dumps([ref.repo_key, ref.local_id], separators=(',', ':')),
                                                      ref, (), contribution.fields[0].provenance))
                    diagnostics.append(f"source_identity_retained:{locator.module}:{locator.raw_name}")
            continue
        ref = candidates[0]
        canonical[ref].append(contribution)
        fields_by_ref[ref] = _fields_for(canonical[ref], diagnostics, complete_domains.get(ref))

    declarations = tuple(_raw_decl(ref, fields_by_ref[ref]) for ref in order)
    repositories = tuple(adapter.repositories)
    known_repos = {repository.repo_key for repository in repositories}
    referenced = {
        dependency.provider.repo_key
        for decl in declarations
        for part in (decl.statement, decl.proof) if part is not None
        for dependency in part.deps
    }
    repositories += tuple(
        Repository(key, None, None, version_status="unresolved",
                   unresolved_reason="Referenced repository was not supplied by the source adapter.")
        for key in sorted(referenced - known_repos)
    )
    workspace = Workspace(
        WorkspaceManifest(repositories, adapter.assets, adapter.dependency_locks),
        declarations,
        tuple(Scope(seed.scope_id, seed.repo_key, seed.kind, seed.name,
                    seed.provenance, seed.parent) for seed in adapter.scopes),
        tuple(DeclUnit(seed.unit_id, seed.representative, seed.members) for seed in (*adapter.units, *fallback_units)),
    )
    _require(not declarations or bool(workspace.units), "builder requires explicit complete unit seeds")
    workspace.validate()
    digest = workspace.digest()
    policy = StructurePolicy(
        digest, adapter.unit_aggregation,
        (Provenance("repository_adapter", f"unit-aggregation:{adapter.unit_aggregation}"),),
    )
    bundle = RepositoryBuildBundle(
        workspace=workspace,
        structure_policy=policy,
        dependency_coverage=_coverage(adapter, workspace),
        source_texts=adapter.source_texts,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        unresolved_locators=tuple(unresolved_locators),
        materials=adapter.materials,
    )
    bundle.validate()
    return bundle
