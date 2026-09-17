"""Shared current HTTP and MCP reader contracts."""
S = {"type": "string"}
REF = {"anyOf": [S, {"type": "object", "properties": {"repo_key": S, "local_id": S},
                       "required": ["repo_key", "local_id"], "additionalProperties": False}]}
BUDGET = {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]}
LOCALE = {"type": "string", "enum": ["zh", "en"]}
DEPENDENCY_VIEW = {"type": "string", "enum": ["analysis", "full"], "default": "analysis"}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TOOL_SCHEMAS = {
    "open_reader": schema({"instance_id": S, "locale": LOCALE, "recommendations": {"type": "boolean", "default": True}, "budget_codepoints": BUDGET}, ["instance_id"]),
    "get_overview": schema({"reader_id": S, "view_id": S, "scope": S, "cursor": S, "limit": LIMIT}, ["reader_id"]),
    "read_text": schema({"reader_id": S, "view_id": S, "anchor": S, "start_line": {"type": "integer", "minimum": 1},
                         "cursor": S, "limit": LIMIT}, ["reader_id"]),
    "inspect": schema({"reader_id": S, "view_id": S, "ref": REF,
                       "detail": {"type": "string", "enum": ["summary", "interfaces", "members", "nl", "lean", "sources", "job"], "default": "summary"},
                       "dependency_view": DEPENDENCY_VIEW,
                       "cursor": S, "limit": LIMIT}, ["reader_id", "ref"]),
    "locate": schema({"reader_id": S, "view_id": S, "ref": REF}, ["reader_id", "ref"]),
    "recommend": schema({"reader_id": S, "limit": {**LIMIT, "default": 5}}, ["reader_id"]),
    "apply_action": schema({"reader_id": S, "expected_view": S,
                            "action": {"type": "string", "enum": ["expand", "collapse", "reset", "cancel", "set_budget", "switch_locale"]}, "target": S, "budget_codepoints": BUDGET, "locale": LOCALE},
                           ["reader_id", "expected_view", "action", "target"]),
}
CONTRACT = {
    "transport": {"http": "POST /api/{tool}", "mcp": "/mcp", "instances": "GET /api/instances"},
    "tools": TOOL_SCHEMAS,
    "result": {"always": ["ok", "view_id"], "error": {"code": "string", "message": "string", "latest_view": "optional string", "job_id": "optional string"}},
    "errors": ["stale_view", "validation_error", "not_found", "not_expandable", "generation_failed", "budget_exceeded", "locale_unavailable"],
    "job": {"job_id": "string", "status": "queued|drafting|stitching|validating|published|failed|cancelled",
            "generation_strategy": "sequential|concurrent",
            "applied": "boolean", "completed_children": "integer", "total_children": "integer",
            "result_view": "optional string", "latest_view": "string", "changed_anchor": "optional string", "error": "optional object"},
    "overview": {"nodes": "flat visible node objects: id,kind,title,parent,state,can_expand,anchor",
                 "edges": "projected provider→consumer: id,provider_node,consumer_node,evidence_ids",
                 "reading_order": "frontier node ids", "next_cursor": "view-bound token or null"},
    "text": {"text": "Markdown", "start_line": "1-based inclusive", "end_line": "inclusive",
             "anchors": "anchor_id,node_id,part,start_line,end_line,targets", "next_cursor": "view-bound token or null"},
}
