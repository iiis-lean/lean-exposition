"""Fixed exposition construction and source-grounded writing views."""
from .content import ContentError, ContentStore, METADATA_SCHEMA, submission_schema
from .render import render
from .views import decl_card, scope_view, writing_view

__all__ = ["ContentError", "ContentStore", "METADATA_SCHEMA", "submission_schema", "render", "decl_card", "scope_view", "writing_view"]

from .agent import run_agent_job, writing_mcp
__all__ += ["run_agent_job", "writing_mcp"]
