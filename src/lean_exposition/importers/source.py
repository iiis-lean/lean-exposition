"""Stream source-only Lean declaration inventories from Toolkit text AST output.

This module consumes the current ``declarations.extract`` JSON contract.  It
does not import or copy Toolkit's parser.  Source observations remain
unresolved contributions until a compiled declaration can be matched, or a
caller explicitly asks for a provisional source-only bundle.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping, TextIO

from lean_exposition.construction import (
    CanonicalDeclLocator, CoverageContribution, DeclarationContribution,
    DeclUnitSeed, FieldContribution, RepositoryAdapterResult, ScopeSeed,
    UnresolvedDeclLocator, build_repository,
)
from lean_exposition.models import (
    DeclRef, Provenance, Repository, SourceAsset, SourceRange, Status, TextContent,
    ValidationError,
)
from .common import asset_from_bytes, qualified_id


_RESPONSE_KEYS = {
    "success", "error_message", "total_declarations", "declarations",
    "source_diagnostics",
}
_DECLARATION_KEYS = {
    "name", "kind", "signature", "value", "full_declaration", "docstring",
    "decl_start_pos", "decl_end_pos", "doc_start_pos", "doc_end_pos",
}
_DIAGNOSTIC_KEYS = {
    "backend", "total_top_level_commands", "classified_top_level_commands",
    "classification_ratio", "unrecognized_commands",
}
_ISSUE_KEYS = {"line", "column", "head", "source"}
_POSITION_KEYS = {"line", "column"}
_ENVELOPE_KEYS = {
    "path", "module", "source_sha256", "chunk_index", "chunk_count", "result",
}
_THEOREM_KINDS = {"theorem", "lemma"}


@dataclass(frozen=True)
class SourceCommandIssue:
    line: int
    column: int
    head: str
    source: str


@dataclass(frozen=True)
class SourceCommandCoverage:
    total_top_level_commands: int
    classified_top_level_commands: int
    unrecognized_commands: tuple[SourceCommandIssue, ...]

    @property
    def classification_ratio(self) -> float:
        if self.total_top_level_commands == 0:
            return 1.0
        return self.classified_top_level_commands / self.total_top_level_commands


@dataclass(frozen=True)
class SourceInventoryFile:
    """One fixed source asset and its syntax-derived declaration observations."""

    repo_key: str
    path: str
    module: str
    asset: SourceAsset
    declarations: tuple[DeclarationContribution, ...]
    coverage: SourceCommandCoverage | None
    error_message: str | None = None
    chunk_index: int = 0
    chunk_count: int = 1

    def __post_init__(self) -> None:
        _require(self.chunk_count > 0, "source inventory chunk_count must be positive")
        _require(0 <= self.chunk_index < self.chunk_count,
                 "source inventory chunk_index lies outside chunk_count")

    @property
    def complete(self) -> bool:
        return self.error_message is None and self.coverage is not None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _strict_object(value: object, keys: set[str], label: str) -> dict[str, object]:
    _require(isinstance(value, dict), f"{label} must be an object")
    unknown = set(value) - keys
    missing = keys - set(value)
    _require(not unknown, f"{label} has unknown fields: {sorted(unknown)}")
    _require(not missing, f"{label} is missing fields: {sorted(missing)}")
    return value


def _text_or_none(value: object, label: str) -> str | None:
    _require(value is None or isinstance(value, str), f"{label} must be a string or null")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    _require(type(value) is int and value >= minimum, f"{label} must be an integer >= {minimum}")
    return value


def _position(value: object, label: str) -> tuple[int, int] | None:
    if value is None:
        return None
    raw = _strict_object(value, _POSITION_KEYS, label)
    return (_integer(raw["line"], f"{label}.line", minimum=1),
            _integer(raw["column"], f"{label}.column", minimum=0))


def _source_range(asset_id: str, start: tuple[int, int] | None,
                  end: tuple[int, int] | None, label: str) -> SourceRange | None:
    _require((start is None) == (end is None), f"{label} positions must both be present or absent")
    if start is None:
        return None
    # Toolkit text_ast columns are zero-based Unicode code-point offsets;
    # Lean Exposition stores one-based Unicode code-point columns.
    result = SourceRange(asset_id, start[0], start[1] + 1, end[0], end[1] + 1)
    _require((result.start_line, result.start_column) < (result.end_line, result.end_column),
             f"{label} must be nonempty and ordered")
    return result


def _slice_source(text: str, location: SourceRange, *,
                  lines: list[str] | None = None,
                  line_offsets: list[int] | None = None) -> str:
    lines = lines if lines is not None else text.splitlines(keepends=True)
    if line_offsets is None:
        line_offsets = [0]
        for line in lines:
            line_offsets.append(line_offsets[-1] + len(line))
    _require(1 <= location.start_line <= len(lines), "source range start lies outside source")
    _require(1 <= location.end_line <= len(lines), "source range end lies outside source")
    for line, column in ((location.start_line, location.start_column),
                         (location.end_line, location.end_column)):
        content = lines[line - 1].rstrip("\r\n")
        _require(column is not None and 1 <= column <= len(content) + 1,
                 "source range column lies outside source")
    start_offset = line_offsets[location.start_line - 1] + location.start_column - 1
    end_offset = line_offsets[location.end_line - 1] + location.end_column - 1
    return text[start_offset:end_offset]


def _field(name: str, value: object, provenance: tuple[Provenance, ...],
           input_digest: str, *, authority: str = "text_ast") -> FieldContribution:
    return FieldContribution(
        name, "present", value, authority, "toolkit_text_ast",
        "declarations.extract", provenance, input_digest,
    )


def _missing_text(reason: str, provenance: tuple[Provenance, ...]) -> TextContent:
    return TextContent(None, "missing", provenance, reason)


def _declaration_contribution(*, repo_key: str, module: str, path: str,
                              source_text: str, asset: SourceAsset,
                              raw: object, source_lines: list[str] | None = None,
                              line_offsets: list[int] | None = None) -> DeclarationContribution:
    value = _strict_object(raw, _DECLARATION_KEYS, "text_ast declaration")
    name = _text_or_none(value["name"], "declaration.name")
    kind = _text_or_none(value["kind"], "declaration.kind")
    _require(bool(name and name.strip()), "declaration.name must be nonempty")
    _require(bool(kind and kind.strip()), "declaration.kind must be nonempty")
    signature = _text_or_none(value["signature"], "declaration.signature")
    body = _text_or_none(value["value"], "declaration.value")
    full = _text_or_none(value["full_declaration"], "declaration.full_declaration")
    docstring = _text_or_none(value["docstring"], "declaration.docstring")
    decl_range = _source_range(
        asset.asset_id,
        _position(value["decl_start_pos"], "declaration.decl_start_pos"),
        _position(value["decl_end_pos"], "declaration.decl_end_pos"),
        "declaration range",
    )
    doc_range = _source_range(
        asset.asset_id,
        _position(value["doc_start_pos"], "declaration.doc_start_pos"),
        _position(value["doc_end_pos"], "declaration.doc_end_pos"),
        "docstring range",
    )
    _require(decl_range is not None, "text_ast declaration requires a source range")
    source_slice = _slice_source(source_text, decl_range, lines=source_lines,
                                 line_offsets=line_offsets)
    if full is not None:
        _require(source_slice.rstrip() == full.rstrip(),
                 f"text_ast full_declaration does not match fixed source: {path}:{name}")
    if docstring is not None:
        _require(doc_range is not None, "text_ast docstring requires a docstring range")
        _require(_slice_source(source_text, doc_range, lines=source_lines,
                              line_offsets=line_offsets).rstrip() == docstring.rstrip(),
                 f"text_ast docstring does not match fixed source: {path}:{name}")

    provenance = (Provenance("toolkit_text_ast", f"{path}:{name}", (decl_range,)),)
    doc_provenance = (
        (Provenance("toolkit_text_ast_docstring", f"{path}:{name}", (doc_range,)),)
        if doc_range is not None else provenance
    )
    nl = (TextContent(docstring, "present", doc_provenance) if docstring is not None
          else _missing_text("No declaration docstring was found by text_ast", provenance))
    if kind in _THEOREM_KINDS:
        statement_text = signature
        proof_text = body
    else:
        statement_text = full or signature
        proof_text = None
    formal = (TextContent(statement_text, "present", provenance) if statement_text is not None
              else _missing_text("text_ast did not expose a statement source slice", provenance))
    fields = [
        _field("lean_name", name, provenance, asset.sha256),
        _field("module", module, provenance, asset.sha256),
        _field("native_scope", qualified_id(repo_key, module), provenance, asset.sha256),
        _field("kind", kind, provenance, asset.sha256),
        _field("statement.nl", nl, provenance, asset.sha256,
               authority="text_ast" if docstring is not None else "generated"),
        _field("statement.formal", formal, provenance, asset.sha256,
               authority="text_ast" if statement_text is not None else "generated"),
        _field("extraction_status", Status("source_only", provenance), provenance,
               asset.sha256, authority="generated"),
        _field("provenance", provenance, provenance, asset.sha256),
        _field("source_refs", (decl_range,), provenance, asset.sha256),
    ]
    if kind in _THEOREM_KINDS:
        proof_formal = (
            TextContent(proof_text, "present", provenance) if proof_text is not None
            else _missing_text("text_ast did not expose a theorem value source slice", provenance)
        )
        fields.extend((
            _field("proof.nl", _missing_text(
                "The declaration docstring is retained on the statement", provenance),
                provenance, asset.sha256, authority="generated"),
            _field("proof.formal", proof_formal, provenance, asset.sha256,
                   authority="text_ast" if proof_text is not None else "generated"),
        ))
    return DeclarationContribution(
        UnresolvedDeclLocator(repo_key, module, name, decl_range), tuple(fields),
    )


def consume_text_ast_json(*, repo_key: str, path: str, module: str,
                          source: bytes, payload: str | bytes | Mapping[str, object],
                          expected_sha256: str | None = None) -> SourceInventoryFile:
    """Consume one current Toolkit text_ast response without building a Workspace."""
    _require(bool(repo_key.strip()), "repo_key must be nonempty")
    _validate_relative_path(path)
    _require(bool(module.strip()), "module must be nonempty")
    digest = hashlib.sha256(source).hexdigest()
    if expected_sha256 is not None:
        _require(digest == expected_sha256, f"source digest mismatch: {path}")
    if isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raw = _load_json(payload, f"text_ast result for {path}")
    try:
        source_text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"Lean source is not UTF-8: {path}") from exc
    response = _strict_object(raw, _RESPONSE_KEYS, "text_ast response")
    _require(type(response["success"]) is bool, "text_ast response.success must be boolean")
    error = _text_or_none(response["error_message"], "text_ast response.error_message")
    declarations_raw = response["declarations"]
    _require(isinstance(declarations_raw, list), "text_ast response.declarations must be a list")
    total = _integer(response["total_declarations"], "text_ast response.total_declarations", minimum=0)
    _require(total == len(declarations_raw), "text_ast declaration count mismatch")
    asset = asset_from_bytes(repo_key, path, source)
    if not response["success"]:
        _require(bool(error and error.strip()), "failed text_ast response requires error_message")
        _require(not declarations_raw, "failed text_ast response cannot contain declarations")
        _require(response["source_diagnostics"] is None,
                 "failed text_ast response cannot claim source diagnostics")
        return SourceInventoryFile(repo_key, path, module, asset, (), None, error)
    _require(error is None, "successful text_ast response cannot contain error_message")
    coverage = _coverage(response["source_diagnostics"])
    source_lines = source_text.splitlines(keepends=True)
    line_offsets = [0]
    for line in source_lines:
        line_offsets.append(line_offsets[-1] + len(line))
    declarations = tuple(
        _declaration_contribution(
            repo_key=repo_key, module=module, path=path, source_text=source_text,
            asset=asset, raw=item, source_lines=source_lines,
            line_offsets=line_offsets,
        )
        for item in declarations_raw
    )
    return SourceInventoryFile(repo_key, path, module, asset, declarations, coverage)


def _coverage(value: object) -> SourceCommandCoverage:
    raw = _strict_object(value, _DIAGNOSTIC_KEYS, "text_ast source_diagnostics")
    _require(raw["backend"] == "text_ast", "source_diagnostics backend must be text_ast")
    total = _integer(raw["total_top_level_commands"], "source_diagnostics.total", minimum=0)
    classified = _integer(raw["classified_top_level_commands"],
                          "source_diagnostics.classified", minimum=0)
    _require(classified <= total, "classified command count exceeds total")
    ratio = raw["classification_ratio"]
    _require(type(ratio) in {int, float}, "classification_ratio must be numeric")
    expected = 1.0 if total == 0 else classified / total
    _require(abs(float(ratio) - expected) <= 1e-12, "classification_ratio is inconsistent")
    issues_raw = raw["unrecognized_commands"]
    _require(isinstance(issues_raw, list), "unrecognized_commands must be a list")
    issues = []
    for index, item in enumerate(issues_raw):
        issue = _strict_object(item, _ISSUE_KEYS, f"unrecognized_commands[{index}]")
        line = _integer(issue["line"], f"unrecognized_commands[{index}].line", minimum=1)
        column = _integer(issue["column"], f"unrecognized_commands[{index}].column", minimum=0)
        head = _text_or_none(issue["head"], f"unrecognized_commands[{index}].head")
        source = _text_or_none(issue["source"], f"unrecognized_commands[{index}].source")
        _require(bool(head and head.strip()), "unrecognized command head must be nonempty")
        _require(source is not None, "unrecognized command source must be a string")
        issues.append(SourceCommandIssue(line, column, head, source))
    _require(len(issues) == total - classified,
             "unrecognized command count must equal the unclassified command count")
    return SourceCommandCoverage(total, classified, tuple(issues))


def iter_text_ast_jsonl(stream: TextIO, *, repo_key: str, source_root: str | Path,
                        start_record: int = 0) -> Iterator[SourceInventoryFile]:
    """Read independent file records line by line; ``start_record`` resumes a shard."""
    _require(start_record >= 0, "start_record must be nonnegative")
    root = Path(source_root).resolve()
    record_index = 0
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        if record_index < start_record:
            record_index += 1
            continue
        envelope = _strict_object(
            _load_json(line, f"text_ast JSONL line {line_number}"),
            _ENVELOPE_KEYS, f"text_ast JSONL line {line_number}",
        )
        path = _text_or_none(envelope["path"], "JSONL path")
        module = _text_or_none(envelope["module"], "JSONL module")
        digest = _text_or_none(envelope["source_sha256"], "JSONL source_sha256")
        chunk_index = _integer(envelope["chunk_index"], "JSONL chunk_index", minimum=0)
        chunk_count = _integer(envelope["chunk_count"], "JSONL chunk_count", minimum=1)
        _require(chunk_index < chunk_count, "JSONL chunk_index lies outside chunk_count")
        _require(bool(path and path.strip()), "JSONL path must be nonempty")
        _require(bool(module and module.strip()), "JSONL module must be nonempty")
        _require(isinstance(digest, str) and len(digest) == 64
                 and all(char in "0123456789abcdef" for char in digest),
                 "JSONL source_sha256 must be lowercase SHA256")
        relative = _validate_relative_path(path)
        source_path = (root / relative).resolve()
        _require(source_path.is_relative_to(root), "JSONL source path escapes source_root")
        _require(source_path.is_file(), f"JSONL source file does not exist: {path}")
        yield replace(consume_text_ast_json(
            repo_key=repo_key, path=path, module=module, source=source_path.read_bytes(),
            payload=envelope["result"], expected_sha256=digest,
        ), chunk_index=chunk_index, chunk_count=chunk_count)
        record_index += 1


def _validate_relative_path(path: str) -> Path:
    _require(isinstance(path, str) and bool(path.strip()), "source path must be nonempty")
    pure = PurePosixPath(path)
    _require(not pure.is_absolute() and ".." not in pure.parts and "." not in pure.parts,
             "source path must be a normalized relative POSIX path")
    return Path(*pure.parts)


def _load_json(value: str | bytes, label: str) -> dict[str, object]:
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            _require(key not in result, f"{label} has duplicate key: {key}")
            result[key] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid {label}: {exc}") from exc
    _require(isinstance(result, dict), f"{label} must be an object")
    return result


def _present_fields(contribution: DeclarationContribution) -> dict[str, object]:
    return {field.field: field.value for field in contribution.fields if field.state == "present"}


def _resolve_contribution(source: DeclarationContribution,
                          compiled: RepositoryAdapterResult) -> DeclarationContribution:
    locator = source.locator
    _require(isinstance(locator, UnresolvedDeclLocator),
             "source inventory contributions must start unresolved")
    candidates: list[tuple[DeclRef, dict[str, object]]] = []
    for contribution in compiled.declarations:
        if not isinstance(contribution.locator, CanonicalDeclLocator):
            continue
        fields = _present_fields(contribution)
        if (contribution.locator.ref.repo_key == locator.repo_key
                and fields.get("module") == locator.module):
            candidates.append((contribution.locator.ref, fields))
    name_matches = [item for item in candidates if item[1].get("lean_name") == locator.raw_name]
    range_matches = [item for item in candidates
                     if locator.source_range is not None
                     and locator.source_range in item[1].get("source_refs", ())]
    if locator.source_range is not None:
        exact = [item for item in name_matches if item in range_matches]
        matches = exact if exact else range_matches
    else:
        matches = name_matches
    if len(matches) != 1:
        return source
    return replace(source, locator=replace(locator, authoritative_ref=matches[0][0]))


def merge_source_inventory(compiled: RepositoryAdapterResult,
                           files: Iterable[SourceInventoryFile], *,
                           strict_identity: bool = False) -> RepositoryAdapterResult:
    """Append source fields to compiled facts after unique module/name/range matching."""
    assets = list(compiled.assets)
    asset_by_id = {asset.asset_id: asset for asset in assets}
    declarations = list(compiled.declarations)
    diagnostics = list(compiled.diagnostics)
    unresolved: list[UnresolvedDeclLocator] = []
    values = tuple(sorted(files, key=lambda item: (
        item.repo_key, item.path, item.module, item.chunk_index,
    )))
    _validate_inventory_chunks(values)
    for item in values:
        _require(item.repo_key in {repo.repo_key for repo in compiled.repositories},
                 f"source inventory repository is absent from compiled adapter: {item.repo_key}")
        existing = asset_by_id.get(item.asset.asset_id)
        _require(existing is None or existing == item.asset,
                 f"source asset conflicts with compiled asset: {item.path}")
        if existing is None:
            assets.append(item.asset)
            asset_by_id[item.asset.asset_id] = item.asset
        if item.error_message is not None:
            diagnostics.append(f"text_ast_error:{item.path}:{item.error_message}")
            continue
        if item.chunk_index == 0:
            diagnostics.extend(_coverage_diagnostics(item))
        for contribution in item.declarations:
            resolved = _resolve_contribution(contribution, compiled)
            if resolved.locator.authoritative_ref is None:
                unresolved.append(resolved.locator)
            declarations.append(resolved)
    if strict_identity and unresolved:
        raise ValidationError(
            "source declarations did not resolve uniquely: "
            + ", ".join(f"{item.module}:{item.raw_name}" for item in unresolved)
        )
    return replace(compiled, assets=tuple(assets), declarations=tuple(declarations),
                   diagnostics=tuple(diagnostics))


def _coverage_diagnostics(item: SourceInventoryFile) -> tuple[str, ...]:
    if item.coverage is None:
        return ()
    result = [
        f"text_ast_coverage:{item.path}:"
        f"{item.coverage.classified_top_level_commands}/"
        f"{item.coverage.total_top_level_commands}"
    ]
    result.extend(
        f"text_ast_unrecognized:{item.path}:{issue.line}:{issue.column}:{issue.head}"
        for issue in item.coverage.unrecognized_commands
    )
    return tuple(result)


def provisional_source_adapter(files: Iterable[SourceInventoryFile], *, repo_key: str,
                               toolchain: str | None = None, revision: str | None = None,
                               primary_outcome_names: tuple[str, ...] = ()) -> RepositoryAdapterResult:
    """Explicitly promote fixed source observations to provisional source identities."""
    values = tuple(sorted(files, key=lambda item: (
        item.repo_key, item.path, item.module, item.chunk_index,
    )))
    _require(bool(values), "provisional source adapter needs at least one inventory file")
    _require(all(item.repo_key == repo_key for item in values),
             "all source inventory files must belong to repo_key")
    _validate_inventory_chunks(values)
    failed = [item.path for item in values if not item.complete]
    _require(not failed, f"cannot promote failed source inventories: {failed}")
    input_digest = hashlib.sha256(json.dumps(
        [(item.path, item.module, item.asset.sha256, item.chunk_index, item.chunk_count)
         for item in values],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    root_id = qualified_id(repo_key, "/")
    root_provenance = (Provenance("source_inventory", input_digest),)
    scopes: dict[str, ScopeSeed] = {
        root_id: ScopeSeed(root_id, repo_key, "repository", repo_key, root_provenance),
    }
    assets: list[SourceAsset] = []
    declarations: list[DeclarationContribution] = []
    units: list[DeclUnitSeed] = []
    coverage: list[CoverageContribution] = []
    diagnostics = ["stage:provisional-source-only"]
    observed_names: dict[str, list[DeclRef]] = {}
    seen_assets: set[str] = set()
    for item in values:
        if item.asset.asset_id not in seen_assets:
            assets.append(item.asset)
            seen_assets.add(item.asset.asset_id)
        if item.chunk_index == 0:
            diagnostics.extend(_coverage_diagnostics(item))
        parent = root_id
        parts = item.module.split(".")
        for index in range(1, len(parts) + 1):
            name = ".".join(parts[:index])
            scope_id = qualified_id(repo_key, name)
            if scope_id not in scopes:
                scopes[scope_id] = ScopeSeed(
                    scope_id, repo_key, "module" if index == len(parts) else "directory",
                    parts[index - 1], root_provenance, parent,
                )
            parent = scope_id
        for source in item.declarations:
            locator = source.locator
            _require(isinstance(locator, UnresolvedDeclLocator) and locator.source_range is not None,
                     "provisional source identity requires an unresolved ranged locator")
            identity_material = json.dumps(
                [locator.module, locator.raw_name, locator.source_range.start_line,
                 locator.source_range.start_column, locator.source_range.end_line,
                 locator.source_range.end_column],
                ensure_ascii=False, separators=(",", ":"),
            )
            local_id = "source:" + hashlib.sha256(identity_material.encode()).hexdigest()
            ref = DeclRef(repo_key, local_id)
            observed_names.setdefault(locator.raw_name, []).append(ref)
            canonical = replace(source, locator=CanonicalDeclLocator(ref))
            declarations.append(canonical)
            unit_id = qualified_id(repo_key, local_id)
            units.append(DeclUnitSeed(unit_id, ref, (), source.fields[0].provenance))
            parts_to_cover = ("statement", "proof") if any(
                field.field.startswith("proof.") for field in source.fields
            ) else ("statement",)
            for part in parts_to_cover:
                for domain in ("lean_type", "lean_value"):
                    coverage.append(CoverageContribution(
                        ref, part, domain, "unknown", source.fields[0].provenance,
                    ))
    duplicate_names = sorted(name for name, refs in observed_names.items() if len(refs) > 1)
    diagnostics.extend(f"text_ast_ambiguous_observed_name:{name}" for name in duplicate_names)
    name_to_ref = {name: refs[0] for name, refs in observed_names.items() if len(refs) == 1}
    missing_outcomes = sorted(set(primary_outcome_names) - name_to_ref.keys())
    _require(not missing_outcomes, f"provisional primary outcomes are not uniquely observed: {missing_outcomes}")
    repository = Repository(
        repo_key, toolchain, root_id, revision=revision, input_digest=input_digest,
        primary_outcomes=tuple(name_to_ref[name] for name in primary_outcome_names),
    )
    return RepositoryAdapterResult(
        repositories=(repository,), assets=tuple(assets), declarations=tuple(declarations),
        scopes=tuple(scopes.values()), units=tuple(units), unit_aggregation="preserve",
        coverage=tuple(coverage), diagnostics=tuple(diagnostics),
        production_structure=False,
    )


def _validate_inventory_chunks(values: tuple[SourceInventoryFile, ...]) -> None:
    grouped: dict[tuple[str, str, str], list[SourceInventoryFile]] = {}
    for item in values:
        grouped.setdefault((item.repo_key, item.path, item.module), []).append(item)
    for (_, path, _), chunks in grouped.items():
        counts = {item.chunk_count for item in chunks}
        _require(len(counts) == 1, f"inconsistent source inventory chunk_count: {path}")
        count = counts.pop()
        indices = [item.chunk_index for item in chunks]
        _require(len(indices) == len(set(indices)),
                 f"duplicate source inventory chunk index: {path}")
        _require(set(indices) == set(range(count)),
                 f"incomplete source inventory chunks: {path}")
        assets = {item.asset for item in chunks}
        modules = {item.module for item in chunks}
        coverages = {item.coverage for item in chunks}
        errors = {item.error_message for item in chunks}
        _require(len(assets) == len(modules) == len(coverages) == len(errors) == 1,
                 f"source inventory chunks disagree on file metadata: {path}")


def build_provisional_source_bundle(files: Iterable[SourceInventoryFile], *, repo_key: str,
                                    toolchain: str | None = None,
                                    revision: str | None = None,
                                    primary_outcome_names: tuple[str, ...] = ()):
    """Build a diagnostic provisional bundle; this does not construct Regions."""
    return build_repository(provisional_source_adapter(
        files, repo_key=repo_key, toolchain=toolchain, revision=revision,
        primary_outcome_names=primary_outcome_names,
    ))


__all__ = [
    "SourceCommandCoverage", "SourceCommandIssue", "SourceInventoryFile",
    "build_provisional_source_bundle", "consume_text_ast_json", "iter_text_ast_jsonl",
    "merge_source_inventory", "provisional_source_adapter",
]
