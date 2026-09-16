"""Explicit source order and range-union measurements.

TeX is scanned statically; unsupported constructs produce diagnostics. No TeX runs.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path

SOURCE_ORDER_IMPLEMENTATION = {
    "include_base": "root_document_directory",
    "occurrence_policy": "explicit_primary_supporting_then_document_source_reference",
    "missing_policy": "module_then_local_id",
    "measurement": "source_range_union_else_formal_sum",
}


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


SOURCE_ORDER_IMPLEMENTATION_DIGEST = canonical_digest(SOURCE_ORDER_IMPLEMENTATION)


def normalized(path):
    path = posixpath.normpath(path.replace("\\", "/"))
    if path.startswith("/") or path == ".." or path.startswith("../"):
        raise ValueError("source path escapes corpus")
    return path


@dataclass(frozen=True)
class SourceSequence:
    id: str
    kind: str
    role: str
    strength: str
    roots: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or self.kind not in {"tex_document", "ordered_files", "lean_modules"}:
            raise ValueError("invalid source sequence identity or kind")
        if self.role not in {"primary", "supporting", "reference"}:
            raise ValueError("invalid source sequence role")
        if self.strength not in {"protected", "tie_breaker"}:
            raise ValueError("invalid source sequence strength")
        values = {"tex_document": self.roots, "ordered_files": self.paths,
                  "lean_modules": self.modules}[self.kind]
        if (not values or len(values) != len(set(values)) or
                any(not isinstance(value, str) or not value for value in values)):
            raise ValueError("source sequence members must be unique and nonempty")
        if self.kind != "tex_document" and self.roots:
            raise ValueError("only tex_document accepts roots")
        if self.kind != "ordered_files" and self.paths:
            raise ValueError("only ordered_files accepts paths")
        if self.kind != "lean_modules" and self.modules:
            raise ValueError("only lean_modules accepts modules")


@dataclass(frozen=True)
class SourceSequenceSpec:
    repo_key: str
    revision: str | None = None
    input_digest: str | None = None
    sequences: tuple[SourceSequence, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.repo_key, str) or not self.repo_key or
                self.revision is not None and not isinstance(self.revision, str) or
                self.input_digest is not None and
                (not isinstance(self.input_digest, str) or len(self.input_digest) != 64 or
                 any(character not in "0123456789abcdef" for character in self.input_digest)) or
                len({sequence.id for sequence in self.sequences}) != len(self.sequences)):
            raise ValueError("source sequence spec needs a repository and unique sequence IDs")
        for sequence in self.sequences:
            if sequence.kind in {"tex_document", "ordered_files"}:
                for value in (*sequence.roots, *sequence.paths):
                    normalized(value)

    def to_dict(self):
        return asdict(self)

    def digest(self):
        return canonical_digest(self.to_dict())

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    @classmethod
    def from_dict(cls, data):
        expected = {"repo_key", "revision", "input_digest", "sequences"}
        if not isinstance(data, dict) or set(data) != expected or not isinstance(data["sequences"], list):
            raise ValueError("invalid source sequence spec fields")
        sequence_fields = {"id", "kind", "role", "strength", "roots", "paths", "modules"}
        sequences = []
        for record in data["sequences"]:
            if not isinstance(record, dict) or set(record) != sequence_fields:
                raise ValueError("invalid source sequence fields")
            if any(not isinstance(record[field], list) or
                   any(not isinstance(value, str) for value in record[field])
                   for field in ("roots", "paths", "modules")):
                raise ValueError("source sequence members must be string arrays")
            sequences.append(SourceSequence(record["id"], record["kind"], record["role"], record["strength"],
                                              tuple(record["roots"]), tuple(record["paths"]), tuple(record["modules"])))
        return cls(data["repo_key"], data["revision"], data["input_digest"], tuple(sequences))

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


@dataclass
class SourceOrder:
    records: dict
    assets: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    sequence_spec: SourceSequenceSpec | None = None

    def digest(self):
        records = [{"ref": asdict(ref), "record": record} for ref, record in sorted(
            self.records.items(), key=lambda item: (item[0].repo_key, item[0].local_id))]
        return canonical_digest({"records": records, "config": self.config, "diagnostics": self.diagnostics,
                                 "sequence_spec": self.sequence_spec.to_dict() if self.sequence_spec else None})

    def key(self, ref):
        return self.records[ref]["key"]

    def summary(self, refs):
        refs = set(refs)
        records = [self.records[ref] for ref in sorted(refs, key=lambda r: (r.repo_key, r.local_id))]
        domains = {}
        ranges = set()
        for record in records:
            for anchor in record["anchors"]:
                domains.setdefault(anchor["domain"], []).append(anchor["position"])
            for location in record["ranges"]:
                ranges.add(tuple(location.items()))
        return {"members": len(refs), "located": sum(bool(r["anchors"]) for r in records),
                "domains": {d: {"first": min(v), "last": max(v)} for d, v in sorted(domains.items())},
                "ranges": [dict(r) for r in sorted(ranges, key=repr)],
                "basis": sorted({r["basis"] for r in records})}

    def measure(self, declarations):
        declarations = list(declarations)
        intervals = {}
        for decl in declarations:
            locations = [r for p in (decl.statement, decl.proof) if p for prov in p.formal.provenance for r in prov.ranges]
            if not locations:
                locations = list(decl.source_refs)
            if not locations or any(r.asset_id not in self.assets for r in locations):
                return {"codepoints": sum(len(p.formal.text or "") for d in declarations for p in (d.statement, d.proof) if p),
                        "measurement": "conservative_formal_sum"}
            for r in locations:
                text = self.assets[r.asset_id]
                lines = text.splitlines(keepends=True)
                start = sum(map(len, lines[:r.start_line - 1])) + (r.start_column - 1 if r.start_column else 0)
                end = sum(map(len, lines[:r.end_line - 1])) + (r.end_column - 1 if r.end_column else len(lines[r.end_line - 1]))
                intervals.setdefault(r.asset_id, []).append((start, end))
        size = 0
        for values in intervals.values():
            end = -1
            for a, b in sorted(values):
                size += max(0, b - max(a, end))
                end = max(end, b)
        return {"codepoints": size, "measurement": "source_range_union"}


def scan_tex(files, roots):
    """Map each physical line to all logical document appearances."""
    files = {normalized(p): t for p, t in files.items()}
    occurrences, diagnostics = {}, []
    for root in roots:
        root = normalized(root)
        position = 0
        base_dir = posixpath.dirname(root)
        def visit(path, stack):
            nonlocal position
            if path in stack:
                diagnostics.append({"code": "include_cycle", "path": path, "stack": stack})
                return
            if path not in files:
                diagnostics.append({"code": "missing_include", "path": path})
                return
            verbatim = False
            for number, raw in enumerate(files[path].splitlines(), 1):
                line = re.split(r"(?<!\\)%", raw, maxsplit=1)[0]
                if re.search(r"\\begin\{(?:verbatim\*?|lstlisting|minted)\}", line):
                    verbatim = True
                includes = [] if verbatim else list(re.finditer(r"\\(?:input|include)\s*(?:\{([^}]+)\}|([^\s{}]+))", line))
                if includes:
                    diagnostics.append({"code": "ambiguous_include_line", "path": path, "line": number,
                                        "reason": "No fragment-level origin mapping across input commands"})
                else:
                    occurrences.setdefault((path, number), []).append((root, position))
                position += 1
                if not verbatim:
                    if re.search(r"\\(?:includeonly|if\w*|else|fi)\b", line):
                        diagnostics.append({"code": "unsupported_conditional", "path": path, "line": number})
                    for match in includes:
                        target = (match.group(1) or match.group(2)).strip()
                        if any(c in target for c in "\\#$"):
                            diagnostics.append({"code": "dynamic_include", "path": path, "line": number})
                            continue
                        try:
                            target = normalized(posixpath.join(base_dir, target))
                        except ValueError:
                            diagnostics.append({"code": "invalid_include_path", "path": path, "line": number, "target": target})
                            continue
                        if not posixpath.splitext(target)[1]:
                            target += ".tex"
                        visit(target, stack + [path])
                if re.search(r"\\end\{(?:verbatim\*?|lstlisting|minted)\}", line):
                    verbatim = False
        visit(root, [])
    return occurrences, diagnostics


def derive_source_order(workspace, repo_key, *, asset_texts=None, corpus_files=None, corpus_prefix="",
                        document_order=(), source_spec=None):
    if source_spec is not None and not isinstance(source_spec, SourceSequenceSpec):
        source_spec = SourceSequenceSpec.from_dict(source_spec)
    repo = next(r for r in workspace.manifest.repositories if r.repo_key == repo_key)
    if source_spec is not None:
        if source_spec.repo_key != repo_key or (source_spec.revision is not None and source_spec.revision != repo.revision) or \
                (source_spec.input_digest is not None and source_spec.input_digest != repo.input_digest):
            raise ValueError("source sequence spec does not match repository identity")
    assets = {a.asset_id: a for a in workspace.manifest.assets}
    texts = dict(asset_texts or {})
    for asset_id, text in texts.items():
        assets[asset_id].verify(text.encode())
    files = dict(corpus_files or {})
    explicit_roots = tuple(root for sequence in (source_spec.sequences if source_spec else ())
                           if sequence.kind == "tex_document" for root in sequence.roots)
    roots = tuple(document_order) or explicit_roots
    diagnostics = []
    if files and not roots:
        candidates = sorted(p for p, t in files.items() if p.endswith(".tex") and re.search(r"\\documentclass\b", t))
        if len(candidates) == 1:
            roots = tuple(candidates)
        else:
            diagnostics.append({"code": "document_path_fallback", "candidates": candidates})
    occurrences, scan_diagnostics = scan_tex(files, roots)
    diagnostics.extend(scan_diagnostics)
    ambiguous_lines = {(d["path"], d["line"]) for d in scan_diagnostics if d["code"] == "ambiguous_include_line"}
    relative_paths = set(files)
    loaded_modules = {decl.module for decl in workspace.declarations if decl.ref.repo_key == repo_key}
    for sequence in source_spec.sequences if source_spec else ():
        if sequence.kind == "tex_document" and any(root not in relative_paths for root in sequence.roots):
            raise ValueError(f"unknown TeX document root in source sequence {sequence.id}")
        if sequence.kind == "ordered_files" and any(path not in relative_paths and path not in
                {asset.path for asset in assets.values() if asset.repo_key == repo_key} for path in sequence.paths):
            raise ValueError(f"unknown ordered file in source sequence {sequence.id}")
        if sequence.kind == "lean_modules" and any(module not in loaded_modules for module in sequence.modules):
            raise ValueError(f"unknown Lean module in source sequence {sequence.id}")
    role_rank = {"primary": 0, "supporting": 1, "reference": 4}
    records = {}
    for decl in workspace.declarations:
        if decl.ref.repo_key != repo_key:
            continue
        provenance = list(decl.provenance) + [p for part in (decl.statement, decl.proof) if part for text in (part.nl, part.formal) for p in text.provenance]
        origins = [r for p in provenance if p.method == "lc_origin" for r in p.ranges]
        locations = list(dict.fromkeys(origins or decl.source_refs))
        anchors = []
        ambiguous = False
        for location in locations:
            path = assets[location.asset_id].path
            relative = path.removeprefix(corpus_prefix.rstrip("/") + "/") if corpus_prefix else path
            ambiguous |= (relative, location.start_line) in ambiguous_lines
            matches = occurrences.get((relative, location.start_line), [])
            if matches:
                anchors.extend({"domain": doc, "position": pos, "path": path, "line": location.start_line, "column": location.start_column or 0, "document_occurrence": True} for doc, pos in matches)
            else:
                anchors.append({"domain": path, "position": location.start_line, "path": path, "line": location.start_line, "column": location.start_column or 0, "document_occurrence": False})
        def anchor_key(a):
            domain = a["domain"]
            return (0, roots.index(domain), a["position"], a["column"], a["path"]) if a["document_occurrence"] else (1, domain, a["position"], a["column"], a["path"])
        candidates = []
        for sequence_index, sequence in enumerate(source_spec.sequences if source_spec else ()):
            members = sequence.roots if sequence.kind == "tex_document" else sequence.paths if sequence.kind == "ordered_files" else sequence.modules
            for member_index, member in enumerate(members):
                matched = []
                if sequence.kind == "tex_document":
                    matched = [anchor for anchor in anchors if anchor["document_occurrence"] and anchor["domain"] == member]
                elif sequence.kind == "ordered_files":
                    matched = [anchor for anchor in anchors if anchor["path"] == member or
                               anchor["path"].removeprefix(corpus_prefix.rstrip("/") + "/") == member]
                elif decl.module == member:
                    matched = anchors or [{"domain": "module:" + member, "position": 0, "path": member,
                                           "line": 0, "column": 0, "document_occurrence": False}]
                for anchor in matched:
                    candidates.append(((role_rank[sequence.role], sequence_index, member_index,
                                        anchor["position"], anchor["column"], anchor["path"], decl.ref.local_id),
                                       sequence, anchor))
        base_key = (1, *min(map(anchor_key, anchors)), decl.ref.local_id) if anchors else \
            (2, decl.module, 0, decl.ref.local_id)
        automatic = []
        for anchor in anchors:
            raw = anchor_key(anchor)
            category = 2 if anchor["document_occurrence"] else 3
            automatic.append(((category, *raw[1:], decl.ref.local_id), None, anchor))
        choices = candidates + automatic
        if choices:
            key, selected_sequence, selected_anchor = min(choices, key=lambda item: item[0])
        else:
            key, selected_sequence, selected_anchor = ((5, decl.module, 0, decl.ref.local_id), None, None)
        if selected_sequence is not None:
            basis = "explicit_" + selected_sequence.role
        elif selected_anchor is not None:
            basis = "document_occurrence" if selected_anchor["document_occurrence"] else "path_fallback_source_position"
        else:
            basis = "missing_source"
        records[decl.ref] = {"key": key, "base_key": base_key, "basis": basis, "anchors": anchors,
                             "selected_anchor": selected_anchor,
                             "selected_sequence": selected_sequence.id if selected_sequence else None,
                             "selected_role": selected_sequence.role if selected_sequence else None,
                             "selected_strength": selected_sequence.strength if selected_sequence else None,
                             "sequence_matches": sorted({sequence.id for _, sequence, _ in candidates}),
                             "ambiguous_occurrence": ambiguous or len(anchors) > len(locations),
                             "ranges": [asdict(r) for r in locations]}
    for decl in workspace.declarations:
        if decl.ref in records and not records[decl.ref]["anchors"] and decl.generated_from in records:
            records[decl.ref]["key"] = records[decl.generated_from]["key"]
            records[decl.ref]["base_key"] = records[decl.generated_from]["base_key"]
            records[decl.ref]["basis"] = "generated_owner_anchor"
            for field_name in ("selected_anchor", "selected_sequence", "selected_role", "selected_strength", "sequence_matches"):
                records[decl.ref][field_name] = records[decl.generated_from][field_name]
    config = {"implementation_digest": SOURCE_ORDER_IMPLEMENTATION_DIGEST,
              "include_base": "root_document_directory", "document_order": list(roots), "corpus_prefix": corpus_prefix,
              "corpus_digest": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
              "asset_digests": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sorted(texts.items())},
              "source_spec_digest": source_spec.digest() if source_spec else None}
    return SourceOrder(records, texts, config, diagnostics, source_spec)


def source_order_from_lc_git(workspace, repo_key, repository_path, *, document_order=(), source_spec=None):
    """Read only committed corpus blobs at the Workspace's full revision."""
    repo = next(r for r in workspace.manifest.repositories if r.repo_key == repo_key)
    if not repo.revision:
        raise ValueError("LC source requires a pinned Git revision")
    def read(path):
        return subprocess.check_output(["git", "-C", str(Path(repository_path)), "show", f"{repo.revision}:{path}"]).decode()
    manifest = json.loads(read(".lean_constellation/source_corpus/manifest.json"))
    prefix = manifest["relpath"].rstrip("/")
    files = {entry["path"]: read(prefix + "/" + entry["path"]) for entry in manifest["files"]}
    texts = {a.asset_id: read(a.path) for a in workspace.manifest.assets if a.repo_key == repo_key}
    return derive_source_order(workspace, repo_key, asset_texts=texts, corpus_files=files,
                               corpus_prefix=prefix, document_order=document_order, source_spec=source_spec)
