"""Workspace-scoped immutable declaration text records and explicit generation."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import threading

import jsonschema

from lean_exposition.models import DeclRef, Workspace
from lean_exposition.runtime import stable_prompt


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _ref_key(ref):
    if isinstance(ref, DeclRef):
        return f"{ref.repo_key}\0{ref.local_id}"
    return f"{ref['repo_key']}\0{ref['local_id']}"


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


class DeclTextError(ValueError):
    pass


class DeclTextStore:
    """Append-only text companion for one immutable Workspace."""

    _STATE_KEYS = {"workspace_digest", "records", "requests", "source_active"}
    _RECORD_KEYS = {
        "record_id", "ref", "locale", "profile", "source_kind", "summary",
        "statement_nl", "proof_nl", "provenance", "declaration_digest",
        "input_digest", "prompt_digest", "config_digest", "implementation_digest",
        "content_digest",
    }

    def __init__(self, workspace: Workspace, path):
        workspace.validate()
        self.workspace = workspace
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "workspace_digest": workspace.digest(), "records": {},
            "requests": {}, "source_active": {},
        }
        self._validate()
        if not self.path.exists():
            _atomic(self.path, self.state)

    def _validate(self):
        if set(self.state) != self._STATE_KEYS:
            raise DeclTextError("Declaration text store has unknown or missing fields.")
        if self.state["workspace_digest"] != self.workspace.digest():
            raise DeclTextError("Declaration text store belongs to another Workspace.")
        refs = {_ref_key(decl.ref) for decl in self.workspace.declarations}
        for record_id, record in self.state["records"].items():
            if set(record) != self._RECORD_KEYS or record_id != record["record_id"]:
                raise DeclTextError("Invalid declaration text record shape.")
            if _ref_key(record["ref"]) not in refs:
                raise DeclTextError("Declaration text record references an unknown declaration.")
            payload = {key: value for key, value in record.items()
                       if key not in {"record_id", "content_digest"}}
            content_digest = _digest({
                "summary": record["summary"], "statement_nl": record["statement_nl"],
                "proof_nl": record["proof_nl"], "source_kind": record["source_kind"],
                "provenance": record["provenance"],
            })
            if record["content_digest"] != content_digest or record_id != "decl-text-" + _digest(payload)[:24]:
                raise DeclTextError("Declaration text record identity mismatch.")
        for mapping in (self.state["requests"], self.state["source_active"]):
            if not isinstance(mapping, dict) or any(value not in self.state["records"] for value in mapping.values()):
                raise DeclTextError("Declaration text active mapping is invalid.")

    def _declaration_digest(self, ref):
        declaration = next((decl for decl in self.workspace.declarations if decl.ref == ref), None)
        if declaration is None:
            raise DeclTextError(f"Unknown declaration: {ref}")
        return _digest(asdict(declaration))

    @staticmethod
    def request_key(ref, *, declaration_digest, input_digest, locale, profile,
                    prompt_digest, config_digest, implementation_digest):
        return "decl-text-request-" + _digest({
            "ref": asdict(ref), "declaration_digest": declaration_digest,
            "input_digest": input_digest, "locale": locale, "profile": profile,
            "prompt_digest": prompt_digest, "config_digest": config_digest,
            "implementation_digest": implementation_digest,
        })[:24]

    def _append(self, *, ref, locale, profile, source_kind, summary,
                statement_nl, proof_nl, provenance, input_digest,
                prompt_digest, config_digest, implementation_digest):
        declaration_digest = self._declaration_digest(ref)
        content_digest = _digest({
            "summary": summary, "statement_nl": statement_nl,
            "proof_nl": proof_nl, "source_kind": source_kind,
            "provenance": provenance,
        })
        payload = {
            "ref": asdict(ref), "locale": locale, "profile": profile,
            "source_kind": source_kind, "summary": summary,
            "statement_nl": statement_nl, "proof_nl": proof_nl,
            "provenance": provenance, "declaration_digest": declaration_digest,
            "input_digest": input_digest, "prompt_digest": prompt_digest,
            "config_digest": config_digest,
            "implementation_digest": implementation_digest,
        }
        record_id = "decl-text-" + _digest(payload)[:24]
        record = {"record_id": record_id, **payload, "content_digest": content_digest}
        existing = self.state["records"].get(record_id)
        if existing is not None and existing != record:
            raise DeclTextError("Declaration text record collision.")
        self.state["records"][record_id] = record
        return record_id

    def import_source(self, contributions, *, profile="default"):
        """Initialize exact source summaries; same-authority conflicts fail."""
        with self.lock:
            for item in contributions:
                if item.text_kind != "summary":
                    continue
                key = _canonical({"ref": asdict(item.ref), "locale": item.locale, "profile": profile})
                existing_id = self.state["source_active"].get(key)
                provenance = [asdict(value) for value in item.provenance]
                record_id = self._append(
                    ref=item.ref, locale=item.locale, profile=profile,
                    source_kind="lc_catalog", summary=item.text,
                    statement_nl=None, proof_nl=None, provenance=provenance,
                    input_digest=item.input_digest, prompt_digest=_digest("source"),
                    config_digest=_digest({"profile": profile}),
                    implementation_digest=_digest("source-contribution"),
                )
                if existing_id is not None and self.state["records"][existing_id]["content_digest"] != self.state["records"][record_id]["content_digest"]:
                    raise DeclTextError("Conflicting source declaration summary.")
                self.state["source_active"][key] = record_id
            self._validate()
            _atomic(self.path, self.state)

    def record(self, record_id):
        with self.lock:
            return json.loads(json.dumps(self.state["records"][record_id]))

    def pinned(self, record_id, ref, *, locale, profile="default"):
        record = self.record(record_id)
        if (_ref_key(record["ref"]) != _ref_key(ref) or record["locale"] != locale
                or record["profile"] != profile):
            raise DeclTextError("Pinned declaration text record does not match its view context.")
        return record

    def active(self, ref, *, locale, profile="default", request_key=None):
        """Read without model side effects; exact source text outranks generated."""
        with self.lock:
            source_key = _canonical({"ref": asdict(ref), "locale": locale, "profile": profile})
            record_id = self.state["source_active"].get(source_key)
            if record_id is None and request_key is not None:
                record_id = self.state["requests"].get(request_key)
            return None if record_id is None else self.record(record_id)

    def pin(self, refs, *, locale, profile="default", request_keys=None):
        request_keys = request_keys or {}
        result = {}
        for ref in refs:
            record = self.active(ref, locale=locale, profile=profile,
                                 request_key=request_keys.get(_ref_key(ref)))
            if record is not None:
                result[_ref_key(ref)] = record["record_id"]
        return result


OUTPUT_SCHEMA = {
    "type": "object", "properties": {"records": {"type": "array", "items": {
        "type": "object", "properties": {
            "ref": {"type": "object", "properties": {
                "repo_key": {"type": "string"}, "local_id": {"type": "string"}},
                "required": ["repo_key", "local_id"], "additionalProperties": False},
            "summary": {"type": "string", "minLength": 1},
            "statement_nl": {"type": ["string", "null"]},
            "proof_nl": {"type": ["string", "null"]},
        }, "required": ["ref", "summary", "statement_nl", "proof_nl"],
        "additionalProperties": False,
    }}}, "required": ["records"], "additionalProperties": False,
}


_LOCALE_INSTRUCTIONS = {
    "en": (
        "Write concise mathematical declaration summaries in English. Preserve every hypothesis and conclusion. "
        "Explain the mathematical meaning and only a source-supported proof route, without Lean tactics, file paths, "
        "raw internal identifiers, or invented details. Set statement_nl or proof_nl to null when its need flag is false. "
        "Style example: 'For finite sets A and B, inclusion–exclusion expresses the size of their union by subtracting "
        "the intersection counted twice. The proof compares two disjoint decompositions.' Return only the requested JSON."
    ),
    "zh": (
        "用中文为每个数学声明写简洁摘要，完整保留量词、假设与结论。只说明来源材料支持的数学意义和证明路线，"
        "不要罗列 Lean 策略、文件路径、内部标识符，也不要补造事实。need 标记为 false 时，对应的 "
        "statement_nl 或 proof_nl 必须为 null。文风示例：‘对有限集合 A、B，容斥公式从两集合大小之和中扣除"
        "被重复计算的交集，从而得到并集大小；证明比较两个不交分解。’只返回要求的 JSON。"
    ),
}


def _instructions(locale):
    if locale not in _LOCALE_INSTRUCTIONS:
        raise DeclTextError("locale must be en or zh")
    return _LOCALE_INSTRUCTIONS[locale]


def ensure_decl_texts(workspace, store, refs, *, locale, executor, profile="default",
                      config_digest=None, implementation_digest=None, batch_size=12):
    """Explicitly generate missing records in bounded sibling batches.

    Ordinary views never call this function. Identical requests are serialized by
    the store lock and resolve to the same accepted record mapping.
    """
    if not 1 <= batch_size <= 16:
        raise DeclTextError("batch_size must be between 1 and 16")
    config_digest = config_digest or _digest({"batch_size": batch_size})
    implementation_digest = implementation_digest or _digest("ensure-decl-texts")
    prompt_prefix = _instructions(locale)
    prompt_hash = _digest(prompt_prefix)
    declarations = {decl.ref: decl for decl in workspace.declarations}
    ordered = []
    seen = set()
    for ref in refs:
        if ref not in declarations:
            raise DeclTextError(f"Unknown declaration: {ref}")
        if ref not in seen:
            seen.add(ref); ordered.append(ref)
    generated, cached, failed = {}, {}, []
    with store.lock:
        pending = []
        for ref in ordered:
            declaration = declarations[ref]
            input_value = {
                "ref": asdict(ref), "name": declaration.lean_name,
                "kind": declaration.kind, "statement": asdict(declaration.statement),
                "proof": asdict(declaration.proof) if declaration.proof else None,
                "need_statement_nl": declaration.statement.nl.status != "present",
                "need_proof_nl": bool(declaration.proof and declaration.proof.nl.status != "present"),
            }
            input_digest = _digest(input_value)
            request_key = store.request_key(
                ref, declaration_digest=store._declaration_digest(ref),
                input_digest=input_digest, locale=locale, profile=profile,
                prompt_digest=prompt_hash, config_digest=config_digest,
                implementation_digest=implementation_digest,
            )
            active = store.active(ref, locale=locale, profile=profile, request_key=request_key)
            if active is not None:
                cached[_ref_key(ref)] = active["record_id"]
            else:
                pending.append((ref, input_value, input_digest, request_key))
        batches = []
        current = []
        current_group = None
        for item in pending:
            long = len(_canonical(item[1].get("proof"))) > 10000
            group = (item[0].repo_key,
                     bool(item[1]["need_statement_nl"] or item[1]["need_proof_nl"]))
            if long:
                if current: batches.append(current); current = []
                batches.append([item])
            else:
                if current and group != current_group:
                    batches.append(current); current = []
                current.append(item)
                current_group = group
                if len(current) == batch_size:
                    batches.append(current); current = []
        if current: batches.append(current)
        for batch in batches:
            prompt = stable_prompt(prompt_prefix, {
                "locale": locale, "profile": profile,
                "declarations": [item[1] for item in batch],
            })
            if hasattr(executor, "run_json"):
                response = executor.run_json(prompt, OUTPUT_SCHEMA, trace_label="decl-text")
            elif hasattr(executor, "execute"):
                result = executor.execute(prompt, OUTPUT_SCHEMA, trace_label="decl-text")
                if result.status != "succeeded":
                    failed.extend(_ref_key(item[0]) for item in batch)
                    continue
                response = result.data
            else:
                response = executor(prompt, OUTPUT_SCHEMA)
            jsonschema.validate(response, OUTPUT_SCHEMA)
            returned = {_ref_key(item["ref"]): item for item in response["records"]}
            expected = {_ref_key(item[0]): item for item in batch}
            if set(returned) - set(expected):
                raise DeclTextError("Model returned an unrequested declaration.")
            for key, (ref, _, input_digest, request_key) in expected.items():
                value = returned.get(key)
                if value is None:
                    failed.append(key); continue
                request_input = expected[key][1]
                if ((not request_input["need_statement_nl"] and value["statement_nl"] is not None) or
                        (not request_input["need_proof_nl"] and value["proof_nl"] is not None)):
                    raise DeclTextError("Model returned unrequested generated NL.")
                record_id = store._append(
                    ref=ref, locale=locale, profile=profile, source_kind="generated",
                    summary=value["summary"], statement_nl=value["statement_nl"],
                    proof_nl=value["proof_nl"], provenance=[{"method": "model", "source_ref": request_key, "ranges": []}],
                    input_digest=input_digest, prompt_digest=prompt_hash,
                    config_digest=config_digest, implementation_digest=implementation_digest,
                )
                store.state["requests"][request_key] = record_id
                generated[key] = record_id
        store._validate()
        _atomic(store.path, store.state)
    return {"generated": generated, "cached": cached, "failed": failed,
            "record_ids": {**cached, **generated}}
