"""Single-writer persistent reader state shared by HTTP and MCP clients."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import threading
import uuid

import jsonschema

from lean_exposition.exposition import ContentError, render, decl_card, scope_view
from lean_exposition.exposition.content import atomic_json, digest
from lean_exposition.exposition.writing import PublicationControl
from lean_exposition.exposition.views import ref_key
from lean_exposition.interfaces.schema import TOOL_SCHEMAS
from lean_exposition.recommendation import random_policy


class ReaderError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


class ReaderService:
    """One process owns the state file; clients only use the seven reader methods."""
    ACTIVE_GENERATION_STATUSES = {"queued", "drafting", "stitching", "validating"}

    def __init__(self, stores, path, *, seed=0, recommendation_policy=None):
        self.stores = {store.instance_id: store for store in stores}
        self.path, self.seed = Path(path), seed
        self.recommendation_policy = recommendation_policy or (lambda context, candidates: random_policy(context, candidates, seed=seed))
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eet-reader")
        self.generation_controls = {}
        self.state = {"readers": {}, "views": {}, "jobs": {}, "exposures": []}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            for job in self.state["jobs"].values():
                if job["status"] in self.ACTIVE_GENERATION_STATUSES:
                    job.update(status="failed", error={"code": "generation_failed", "message": "Generation was interrupted by service restart; retry is available."})
        # Bind historical views before any reader can select another locale.
        for view in self.state["views"].values():
            reader = self.state["readers"][view["reader_id"]]
            view.setdefault("instance_id", reader["instance_id"])
        self._save()

    def close(self):
        self.executor.shutdown(wait=True)

    def _save(self):
        atomic_json(self.path, self.state)

    @staticmethod
    def _publication_info(store, manifest):
        return {"published_node_count": len(manifest["blocks"]), "node_count": len(store.nodes),
                "content_complete": set(manifest["blocks"]) == set(store.nodes)}

    def instances(self):
        instances = []
        for store in self.stores.values():
            manifest = store.manifest()
            root = store.nodes[store.hierarchy["root_id"]]
            instances.append({"instance_id": store.instance_id, "root_id": root["id"], "locale": store.locale,
                              "structure_id": store.structure_id,
                              "title": manifest["metadata"].get(root["id"], {}).get("title", root["title"]),
                              "workspace_digest": store.workspace.digest(),
                              "declaration_count": len({ref_key(ref) for ref in root["decl_refs"]}),
                              **self._publication_info(store, manifest)})
        return {"instances": instances}

    def call(self, tool, arguments):
        """The transport-independent schema and error boundary."""
        with self.lock:
            reader = self.state["readers"].get(arguments.get("reader_id")) if isinstance(arguments, dict) else None
            current = reader["current_view"] if reader else None
            try:
                if tool not in TOOL_SCHEMAS:
                    raise ReaderError("not_found", "Unknown reader tool.")
                jsonschema.validate(arguments, TOOL_SCHEMAS[tool])
                result = getattr(self, "_" + tool)(**arguments)
                result_view = self.state["views"].get(result.get("view_id"))
                if result_view:
                    result_store = self.stores[result_view["instance_id"]]
                    result.update(instance_id=result_view["instance_id"], locale=getattr(result_store, "locale", None),
                                  structure_id=getattr(result_store, "structure_id", result_store.hierarchy["hierarchy_id"]))
                return {"ok": True, **result}
            except ReaderError as exc:
                return {"ok": False, "view_id": current, "error": {"code": exc.code, "message": str(exc), **exc.details}}
            except (jsonschema.ValidationError, ContentError, ValueError, TypeError) as exc:
                message = exc.message if isinstance(exc, jsonschema.ValidationError) else str(exc)
                return {"ok": False, "view_id": current, "error": {"code": "validation_error", "message": message}}

    def _reader(self, reader_id):
        if reader_id not in self.state["readers"]:
            raise ReaderError("not_found", "Unknown reader.")
        reader = self.state["readers"][reader_id]
        return reader, self.stores.get(reader["instance_id"])

    def _view(self, reader_id, view_id=None):
        reader, store = self._reader(reader_id)
        key = view_id or reader["current_view"]
        view = self.state["views"].get(key)
        if view is None or view["reader_id"] != reader_id:
            raise ReaderError("not_found", "View does not belong to this reader.")
        store = self.stores.get(view["instance_id"])
        if store is None:
            raise ReaderError("not_found", "View instance is not loaded.")
        manifest = store.manifest(view["manifest_id"])
        return reader, store, view, render(store.hierarchy, manifest, set(view["expanded"]))

    def _new_view(self, reader_id, manifest_id, expanded):
        value = {"reader_id": reader_id, "instance_id": self.state["readers"][reader_id]["instance_id"], "manifest_id": manifest_id, "expanded": sorted(expanded),
                 "budget_codepoints": self.state["readers"][reader_id].get("budget_codepoints")}
        key = "view-" + digest(value)[:24]
        self.state["views"][key] = {"view_id": key, **value}
        self.state["readers"][reader_id]["current_view"] = key
        self._save()
        return key

    def _open_reader(self, instance_id, recommendations=True, budget_codepoints=None, locale=None):
        if instance_id not in self.stores:
            raise ReaderError("not_found", "Unknown instance. List GET /api/instances first.")
        store = self.stores[instance_id]
        if locale is not None:
            store = self._locale_store(store, locale)
            instance_id = store.instance_id
        manifest = store.manifest()
        reader_id = "reader-" + uuid.uuid4().hex
        self.state["readers"][reader_id] = {"instance_id": instance_id, "current_view": None,
                                          "recommendations": recommendations, "budget_codepoints": budget_codepoints}
        view_id = self._new_view(reader_id, manifest["manifest_id"], set())
        return {"reader_id": reader_id, "instance_id": instance_id, "view_id": view_id,
                "root_id": store.hierarchy["root_id"], "text": self._read_text(reader_id, limit=50),
                "overview": self._get_overview(reader_id, limit=50)}

    def _locale_store(self, store, locale):
        matches = [candidate for candidate in self.stores.values()
                   if getattr(candidate, "structure_id", None) == getattr(store, "structure_id", None)
                   and getattr(candidate, "locale", None) == locale]
        if len(matches) != 1:
            raise ReaderError("locale_unavailable", "Exactly one matching language package must be loaded.", locale=locale)
        return matches[0]

    @staticmethod
    def _cursor(view_id, kind, offset, context):
        return base64.urlsafe_b64encode(json.dumps([view_id, kind, offset, context], separators=(",", ":")).encode()).decode()

    def _offset(self, cursor, view_id, kind, context):
        if cursor is None:
            return 0
        try:
            version, cursor_kind, offset, saved_context = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if version != view_id or cursor_kind != kind or saved_context != context or type(offset) is not int or offset < 0:
                raise ValueError()
            return offset
        except Exception as exc:
            raise ReaderError("validation_error", "Pagination cursor does not match this view and query.") from exc

    @staticmethod
    def _raw_kind(store, node):
        representative = node.get("representative")
        if representative:
            declaration = next((d for d in store.workspace.declarations if ref_key(d.ref) == ref_key(representative)), None)
            if declaration:
                return declaration.kind
        return node.get("metadata", {}).get("raw_kind", node["kind"])

    def _project(self, store, view, rendered, *, all_evidence=False):
        frontier = set(rendered["frontier"])
        manifest = store.manifest(view["manifest_id"])
        result = []
        for node_id in rendered["visible"]:
            node = store.nodes[node_id]
            is_section = store.kind(node_id) == "section"
            result.append({"id": node_id, "kind": node["kind"], "raw_kind": self._raw_kind(store, node),
                           "title": manifest["metadata"].get(node_id, {}).get("title", node["title"]),
                           "description": manifest["metadata"].get(node_id, {}).get("short_description", ""),
                           "parent": node["parent"], "is_frontier": node_id in frontier,
                           "state": "expanded" if node_id in view["expanded"] and is_section else "collapsed" if is_section else "terminal",
                           "can_expand": is_section and bool(node["children"]) and node_id not in view["expanded"],
                           "can_collapse": is_section and node_id in view["expanded"],
                           "anchor": node_id + ":section"})
        def endpoint(node_id):
            current = node_id
            while current in store.nodes and current not in frontier:
                current = store.nodes[current]["parent"]
            return current if current is not None else node_id
        projected = {}
        externals = {e["id"]: e for e in store.hierarchy.get("external_refs", [])}
        groups = {}
        for external in externals.values():
            repo = external["ref"]["repo_key"]
            group_id = "external-repo-" + digest(repo)[:20]
            groups.setdefault(group_id, {"repo_key": repo, "refs": []})["refs"].append(external)
        group_for = {external["id"]: id for id, group in groups.items() for external in group["refs"]}
        used_external = set()
        for edge in store.hierarchy.get("edges", []):
            provider, consumer = endpoint(edge["provider_node"]), endpoint(edge["consumer_node"])
            if provider == consumer:
                continue
            if provider not in frontier and consumer not in frontier:
                continue
            used_external.update(e for e in (provider, consumer) if e in externals)
            pair = (group_for.get(provider, provider), group_for.get(consumer, consumer))
            projected.setdefault(pair, []).append(edge["id"])
        for node_id in sorted({group_for[id] for id in used_external}):
            group = groups[node_id]
            relevant = [e for e in group["refs"] if e["id"] in used_external]
            result.append({"id": node_id, "kind": "external", "title": group["repo_key"] + " interfaces",
                           "parent": None, "is_frontier": True, "state": "external", "can_expand": False, "can_collapse": False,
                           "anchor": None, "repo_key": group["repo_key"], "declaration_count": len(relevant),
                           "loaded_count": sum(bool(e.get("loaded")) for e in relevant),
                           "detail": "Inspect members for paginated declaration references."})
        edges = [{"id": "projected-" + digest(pair)[:20], "provider_node": pair[0], "consumer_node": pair[1],
                  "evidence_ids": sorted(evidence) if all_evidence else sorted(evidence)[:50],
                  "evidence_count": len(evidence)} for pair, evidence in sorted(projected.items())]
        return result, edges

    def _length(self, store, view, rendered):
        metadata = store.manifest(view["manifest_id"])["metadata"]
        text_length = len(rendered["text"])
        title_length = sum(len(metadata.get(id, {}).get("title", store.nodes[id]["title"])) for id in rendered["visible"])
        total = text_length + title_length
        budget = view.get("budget_codepoints")
        return {"text_codepoints": text_length, "title_codepoints": title_length,
                "total_codepoints": total, "budget_codepoints": budget,
                "over_budget": budget is not None and total > budget}

    def _check_budget(self, store, view, manifest_id, expanded):
        candidate = {**view, "manifest_id": manifest_id, "expanded": sorted(expanded)}
        length = self._length(store, candidate, render(store.hierarchy, store.manifest(manifest_id), expanded))
        if length["over_budget"]:
            raise ReaderError("budget_exceeded", "Expansion exceeds the configured display codepoint budget; generated content remains cached.",
                              latest_view=view["view_id"])

    def _get_overview(self, reader_id, view_id=None, scope=None, cursor=None, limit=50):
        _, store, view, rendered = self._view(reader_id, view_id)
        nodes, edges = self._project(store, view, rendered)
        if scope is not None:
            if scope not in rendered["visible"]:
                raise ReaderError("not_found", "Scope is not visible in this view.")
            def inside(node):
                current = node["id"]
                while current in store.nodes:
                    if current == scope:
                        return True
                    current = store.nodes[current]["parent"]
                return False
            nodes = [node for node in nodes if inside(node)]
        offset = self._offset(cursor, view["view_id"], "overview", scope)
        page = nodes[offset:offset + limit]
        selected = {node["id"] for node in page}
        selected_edges = [edge for edge in edges if edge["provider_node"] in selected or edge["consumer_node"] in selected]
        endpoints = {e[k] for e in selected_edges for k in ("provider_node", "consumer_node")}
        return {"view_id": view["view_id"], "nodes": page, "edges": selected_edges,
                "boundary_stubs": [{"id": id, "outside_page": True} for id in sorted(endpoints - selected)],
                "reading_order": [id for id in rendered["frontier"] if id in selected],
                "length": self._length(store, view, rendered), "total_nodes": len(nodes), "next_cursor": self._cursor(view["view_id"], "overview", offset + limit, scope) if offset + limit < len(nodes) else None}

    def _read_text(self, reader_id, view_id=None, anchor=None, start_line=None, cursor=None, limit=50):
        _, store, view, rendered = self._view(reader_id, view_id)
        lines = rendered["lines"]
        begin, stop = 0, len(lines)
        if anchor:
            match = next((a for a in rendered["anchors"] if a["anchor_id"] == anchor), None)
            if match is None:
                raise ReaderError("not_found", "Anchor is not visible; locate it to find the visible ancestor.")
            begin, stop = match["start_line"] - 1, match["end_line"]
        if start_line is not None and (anchor or cursor):
            raise ReaderError("validation_error", "start_line cannot be combined with anchor or cursor.")
        offset = self._offset(cursor, view["view_id"], "text", anchor)
        start = (start_line - 1) if start_line is not None else begin + offset
        end = min(start + limit, stop)
        return {"view_id": view["view_id"], "text": "\n".join(lines[start:end]),
                **self._publication_info(store, store.manifest(view["manifest_id"])),
                "start_line": start + 1, "end_line": max(start, end), "total_lines": len(lines),
                "length": self._length(store, view, rendered),
                "anchors": [a for a in rendered["anchors"] if a["start_line"] <= end and a["end_line"] >= start + 1],
                "next_cursor": self._cursor(view["view_id"], "text", end - begin, anchor) if end < stop else None}

    @staticmethod
    def _external_group(store, ref):
        if not isinstance(ref, str):
            return None
        for external in store.hierarchy.get("external_refs", []):
            repo = external["ref"]["repo_key"]
            if ref == "external-repo-" + digest(repo)[:20]:
                return {"repo_key": repo, "refs": [e["ref"] for e in store.hierarchy["external_refs"] if e["ref"]["repo_key"] == repo]}
        return None

    def _resolve(self, store, ref):
        if isinstance(ref, dict):
            key = ref_key(ref)
            matches = [n for n in store.nodes.values() if n["kind"] == "unit" and any(ref_key(r) == key for r in n["decl_refs"])]
            if matches:
                return matches[0]["id"], ref
            external = next((e for e in store.hierarchy.get("external_refs", []) if ref_key(e["ref"]) == key), None)
            if external:
                return external["id"], ref
            raise ReaderError("not_found", "Declaration is not in this instance's tree or external boundary.")
        aliases = store.nodes[store.hierarchy["root_id"]].get("metadata", {}).get("aliases", {})
        ref = aliases.get(ref, ref)
        if ref in store.nodes or self._external_group(store, ref):
            return ref, None
        external = next((e for e in store.hierarchy.get("external_refs", []) if e["id"] == ref), None)
        if external:
            return ref, external["ref"]
        for suffix in (":section", ":body", ":lead_in", ":synopsis", ":lead_out", ":statement", ":proof", ":content"):
            if ref.endswith(suffix) and ref[:-len(suffix)] in store.nodes:
                return ref[:-len(suffix)], None
        raise ReaderError("not_found", "Unknown node, declaration, or anchor.")

    def _locate(self, reader_id, ref, view_id=None):
        _, store, view, rendered = self._view(reader_id, view_id)
        edge = next((e for e in store.hierarchy.get("edges", []) if e["id"] == ref), None) if isinstance(ref, str) else None
        if edge is None and isinstance(ref, str):
            edge = next((e for e in self._project(store, view, rendered)[1] if e["id"] == ref), None)
        if edge:
            return {"view_id": view["view_id"], "edge_id": edge["id"],
                    "endpoints": [self._locate(reader_id, edge[k], view["view_id"]) for k in ("provider_node", "consumer_node")]}
        node_id, _ = self._resolve(store, ref)
        if node_id not in store.nodes:
            return {"view_id": view["view_id"], "node_id": node_id, "external": True, "visible_ancestor": None,
                    "anchor": None, "expand_path": []}
        current, hidden = node_id, []
        while current not in rendered["visible"]:
            hidden.append(current)
            current = store.nodes[current]["parent"]
        path = [current] + list(reversed(hidden[1:])) if hidden else []
        exact = next((a for a in rendered["anchors"] if a["anchor_id"] == ref), None)
        anchor_id = exact["anchor_id"] if exact else current + ":section"
        return {"view_id": view["view_id"], "node_id": node_id, "visible_ancestor": current,
                "anchor": anchor_id, "expand_path": path, "external": False}

    def _inspect(self, reader_id, ref, detail="summary", view_id=None, cursor=None, limit=50):
        reader, store, view, rendered = self._view(reader_id, view_id)
        if detail == "job":
            job = self.state["jobs"].get(ref) if isinstance(ref, str) else None
            if job is None or job["reader_id"] != reader_id:
                raise ReaderError("not_found", "Job does not belong to this reader.")
            return {"view_id": reader["current_view"], "job": self._job_view(job, reader)}
        edge = next((e for e in store.hierarchy.get("edges", []) if e["id"] == ref), None) if isinstance(ref, str) else None
        if edge is None and isinstance(ref, str):
            edge = next((e for e in self._project(store, view, rendered, all_evidence=True)[1] if e["id"] == ref), None)
        if edge:
            if "evidence_ids" in edge:
                offset = self._offset(cursor, view["view_id"], "edge", ref)
                evidence = edge["evidence_ids"]
                page = evidence[offset:offset + limit]
                return {"view_id": view["view_id"], "detail": detail,
                        "edge": {k: v for k, v in edge.items() if k != "evidence_ids"},
                        "items": [e for e in store.hierarchy.get("edges", []) if e["id"] in page],
                        "next_cursor": self._cursor(view["view_id"], "edge", offset + limit, ref) if offset + limit < len(evidence) else None}
            return {"view_id": view["view_id"], "detail": detail, "edge": deepcopy(edge)}
        node_id, declaration_ref = self._resolve(store, ref)
        node = store.nodes.get(node_id)
        group = self._external_group(store, node_id)
        refs = group["refs"] if group else [declaration_ref] if declaration_ref else node["decl_refs"]
        if detail == "summary":
            return {"view_id": view["view_id"], "node_id": node_id, "title": store.manifest(view["manifest_id"])["metadata"].get(node_id, {}).get("title", node["title"]) if node else group["repo_key"] if group else declaration_ref["local_id"],
                    "kind": node["kind"] if node else "external", "raw_kind": self._raw_kind(store, node) if node else "external",
                    "description": store.manifest(view["manifest_id"])["metadata"].get(node_id, {}).get("short_description", ""), "member_count": len(refs),
                    "location": self._locate(reader_id, ref, view["view_id"])}
        context = json.dumps([ref, detail], sort_keys=True)
        offset = self._offset(cursor, view["view_id"], "inspect", context)
        if detail == "interfaces" and not group:
            if node is None:
                card = decl_card(store.workspace, declaration_ref)
                items = [{"relation_kind": "external_declaration", "ref": declaration_ref,
                          "loaded": card["loaded"], "name": card.get("name")}]
            else:
                value = scope_view(store.workspace, store.hierarchy, node_id, limit=0)
                items = [{"relation_kind": kind, **edge} for kind in ("incoming", "outgoing", "internal") for edge in value[kind]]
                items += [{"relation_kind": "primary_outcome", "ref": ref} for ref in value["primary_outcomes"]]
        elif detail == "members" or (detail == "interfaces" and group):
            items = refs
        else:
            items = []
            for declaration_ref in refs:
                card = decl_card(store.workspace, declaration_ref, proof=True)
                if detail in {"nl", "lean"}:
                    field = "nl" if detail == "nl" else "formal"
                    for part in ("statement", "proof"):
                        content = card.get(part)
                        value = content.get(field) if content else None
                        text = value.get("text") if value else None
                        status = value["status"] if value else "missing"
                        reason = value.get("reason") if value else "No source content available."
                        # Source newlines, not JSON-escaped strings, define page rows.
                        source_lines = text.split("\n") if text is not None else [None]
                        items.extend({"ref": declaration_ref, "part": part, "status": status,
                                      "line": index + 1 if text is not None else None, "text": line,
                                      "reason": reason} for index, line in enumerate(source_lines))
                elif detail == "sources":
                    declaration = next((d for d in store.workspace.declarations if ref_key(d.ref) == ref_key(declaration_ref)), None)
                    items.append({"ref": declaration_ref, "ranges": card.get("source_refs", []),
                                  "provenance": [asdict(p) for p in declaration.provenance] if declaration else []})
        page = items[offset:offset + limit]
        result = {"view_id": view["view_id"], "detail": detail, "items": page, "total": len(items),
                  "offset": offset, "next_cursor": self._cursor(view["view_id"], "inspect", offset + limit, context) if offset + limit < len(items) else None}
        if detail in {"nl", "lean"}:
            result["text"] = "\n".join(item["text"] if item["text"] is not None else "[" + (item["reason"] or "Missing source") + "]" for item in page)
        if detail in {"nl", "lean", "sources"}:
            self.state["exposures"].append({"reader_id": reader_id, "view_id": view["view_id"], "detail": detail,
                                            "ref": ref, "offset": offset, "line_count": len(page), "digest": digest(page)})
            self._save()
        return result

    @staticmethod
    def _recommendation_components(components):
        if not {"structural_gain", "estimated_cost", "cost_basis", "feature_coverage", "target"} <= components.keys():
            return components
        target = components["target"]
        return {
            "structural_gain": {key: components["structural_gain"][key] for key in ("G_decl", "G_dep", "new_decl_pairs")},
            "estimated_cost": deepcopy(components["estimated_cost"]),
            "feature_coverage": deepcopy(components["feature_coverage"]),
            "target": {"basis": target["basis"], "count": target["count"], "unresolved_count": len(target.get("unresolved_refs", []))},
            "cost_basis": {key: components["cost_basis"][key] for key in ("config_digest", "synopsis_status", "uses_generated_after_text", "section_budget")},
        }

    def _recommend(self, reader_id, limit=5):
        reader, store, view, rendered = self._view(reader_id)
        candidates = [id for id in rendered["visible"] if store.nodes[id]["children"] and id not in view["expanded"]]
        if not reader["recommendations"]:
            return {"view_id": view["view_id"], "policy": "disabled", "policy_id": "disabled", "enabled": False, "recommendations": []}
        context = {"view_id": view["view_id"], "length": self._length(store, view, rendered),
                   "budget_codepoints": view.get("budget_codepoints"),
                   "expanded": list(view["expanded"]), "locale": getattr(store, "locale", None),
                   "structure_id": getattr(store, "structure_id", store.hierarchy["hierarchy_id"]),
                   "hierarchy_id": store.hierarchy["hierarchy_id"],
                   "synopsis_codepoints": {node_id: len(store.manifest(view["manifest_id"])["blocks"][node_id].get("synopsis", "")) for node_id in candidates}}
        result = self.recommendation_policy(deepcopy(context), list(candidates))
        jsonschema.validate(result, {"type": "object", "properties": {
            "policy_id": {"type": "string", "minLength": 1}, "recommendations": {"type": "array", "items": {
                "type": "object", "properties": {"target_id": {"type": "string"}, "rank": {"type": "integer", "minimum": 1},
                "reason": {"type": "string"}, "score": {"type": "number"}, "components": {"type": "object"}},
                "required": ["target_id", "rank", "reason"], "additionalProperties": False}}},
            "required": ["policy_id", "recommendations"], "additionalProperties": False})
        targets = [item["target_id"] for item in result["recommendations"]]
        ranks = [item["rank"] for item in result["recommendations"]]
        if any(target not in candidates for target in targets) or len(set(targets)) != len(targets) or len(set(ranks)) != len(ranks):
            raise ReaderError("validation_error", "Recommendation policy returned duplicate ranks/targets or an illegal expansion target.")
        recommendations = deepcopy(sorted(result["recommendations"], key=lambda item: item["rank"])[:limit])
        for item in recommendations:
            if "components" in item:
                item["components"] = self._recommendation_components(item["components"])
        return {"view_id": view["view_id"], "policy": result["policy_id"], "policy_id": result["policy_id"], "enabled": True,
                "total_candidates": len(candidates), "total_recommendations": len(result["recommendations"]),
                "recommendations": recommendations}

    @staticmethod
    def _job_view(job, reader):
        return {k: deepcopy(v) for k, v in {**job, "latest_view": reader["current_view"]}.items()
                if k in {"job_id", "status", "applied", "result_view", "changed_anchor", "error", "latest_view",
                         "completed_children", "total_children"}}

    def _apply_action(self, reader_id, expected_view, action, target, budget_codepoints=None, locale=None):
        reader, store, view, rendered = self._view(reader_id)
        if expected_view != reader["current_view"]:
            raise ReaderError("stale_view", "Action was based on an old view.", latest_view=reader["current_view"])
        if action == "switch_locale":
            if target != store.hierarchy["root_id"] or locale is None:
                raise ReaderError("validation_error", "Language switch requires the root and locale.")
            destination = self._locale_store(store, locale)
            manifest = destination.manifest()
            # Render first: an incomplete translation cannot replace a valid view.
            render(destination.hierarchy, manifest, set(view["expanded"]))
            reader["instance_id"] = destination.instance_id
            new_view = self._new_view(reader_id, manifest["manifest_id"], set(view["expanded"]))
            return {"view_id": new_view, "changed_anchor": None, "idempotent": new_view == view["view_id"]}
        if locale is not None:
            raise ReaderError("validation_error", "locale is only valid for switch_locale.")
        if action == "set_budget":
            if target != store.hierarchy["root_id"]:
                raise ReaderError("validation_error", "Budget target must be the root.")
            reader["budget_codepoints"] = budget_codepoints
            new_view = self._new_view(reader_id, view["manifest_id"], set(view["expanded"]))
            current = self.state["views"][new_view]
            return {"view_id": new_view, "changed_anchor": None, "length": self._length(store, current, rendered),
                    "idempotent": new_view == view["view_id"]}
        if budget_codepoints is not None:
            raise ReaderError("validation_error", "budget_codepoints is only valid for set_budget.")
        if action == "cancel":
            job = self.state["jobs"].get(target)
            if job is None or job["reader_id"] != reader_id:
                raise ReaderError("not_found", "Job does not belong to this reader.")
            if job["status"] in self.ACTIVE_GENERATION_STATUSES:
                control = self.generation_controls.get(target)
                if control is None or control.cancel():
                    job.update(status="cancelled", applied=False)
                    self._save()
            return {"view_id": reader["current_view"], "job": self._job_view(job, reader)}
        expanded = set(view["expanded"])
        if action == "reset":
            if target != store.hierarchy["root_id"]:
                raise ReaderError("validation_error", "Reset target must be the root.")
            expanded.clear()
        else:
            if target not in rendered["visible"]:
                raise ReaderError("not_found", "Action target is hidden or unknown in this view.")
            if store.kind(target) != "section" or not store.nodes[target]["children"]:
                raise ReaderError("not_expandable", "This entry is terminal.")
            if action == "collapse":
                pending = [target]
                while pending:
                    descendant = pending.pop()
                    expanded.discard(descendant)
                    pending.extend(store.nodes[descendant]["children"])
            elif target in expanded:
                return {"view_id": view["view_id"], "changed_anchor": None, "idempotent": True}
            else:
                latest = store.manifest()
                if all(child in latest["blocks"] for child in store.nodes[target]["children"]):
                    expanded.add(target)
                    self._check_budget(store, view, latest["manifest_id"], expanded)
                    new_view = self._new_view(reader_id, latest["manifest_id"], expanded)
                    return {"view_id": new_view, "changed_anchor": target + ":body", "idempotent": False}
                existing = next((j for j in self.state["jobs"].values() if j["reader_id"] == reader_id and j["target"] == target and
                                 j["expected_view"] == expected_view and j["status"] in self.ACTIVE_GENERATION_STATUSES), None)
                if existing:
                    return {"view_id": view["view_id"], "job": self._job_view(existing, reader), "idempotent": True}
                job_id = "job-" + uuid.uuid4().hex
                job = {"job_id": job_id, "reader_id": reader_id, "target": target, "expected_view": expected_view,
                       "status": "queued", "applied": False, "completed_children": 0,
                       "total_children": len(store.nodes[target]["children"])}
                self.state["jobs"][job_id] = job
                self.generation_controls[job_id] = PublicationControl()
                self._save()
                self.executor.submit(self._run_job, job_id)
                return {"view_id": view["view_id"], "job": self._job_view(job, reader), "idempotent": False}
        new_view = self._new_view(reader_id, view["manifest_id"], expanded)
        return {"view_id": new_view, "changed_anchor": target + ":body", "idempotent": new_view == view["view_id"]}

    def _run_job(self, job_id):
        with self.lock:
            job = self.state["jobs"][job_id]
            reader, store, _, _ = self._view(job["reader_id"], job["expected_view"])
            target = job["target"]
            control = self.generation_controls[job_id]
        def cancelled():
            return control.is_cancelled()
        def progress(status, completed, total):
            with self.lock:
                current = self.state["jobs"][job_id]
                if current["status"] == "cancelled":
                    return
                current.update(status=status, completed_children=completed, total_children=total)
                self._save()
        try:
            manifest_id = store.generate_children(
                target,
                cancelled=cancelled,
                progress=progress,
                publication_control=control,
            )
            with self.lock:
                if job["status"] == "cancelled":
                    return
                job.update(status="published", completed_children=job["total_children"])
                if reader["current_view"] == job["expected_view"]:
                    expanded = set(self.state["views"][reader["current_view"]]["expanded"])
                    expanded.add(target)
                    current_view = self.state["views"][reader["current_view"]]
                    try:
                        self._check_budget(store, current_view, manifest_id, expanded)
                    except ReaderError as exc:
                        job["error"] = {"code": exc.code, "message": str(exc), "job_id": job_id}
                    else:
                        job["result_view"] = self._new_view(job["reader_id"], manifest_id, expanded)
                        job["changed_anchor"] = target + ":body"
                        job["applied"] = True
                self._save()
        except Exception as exc:
            with self.lock:
                if job["status"] != "cancelled":
                    code = exc.code if isinstance(exc, ReaderError) else "generation_failed"
                    message = str(exc) if isinstance(exc, ReaderError) else "Content generation failed; the previous view is unchanged. Retry can reuse accepted drafts."
                    job.update(status="failed", error={"code": code, "message": message, "job_id": job_id})
                self._save()
