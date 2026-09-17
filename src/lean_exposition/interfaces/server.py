"""Thin same-origin HTTP and standard MCP adapters for the shared reader service."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from .schema import CONTRACT


def create_app(service, *, static_dir=None):
    mcp = FastMCP("Lean Exposition Reader", stateless_http=True, json_response=True,
                  streamable_http_path="/mcp")

    @mcp.tool()
    def open_reader(instance_id: str, recommendations: bool = True, budget_codepoints: int | None = None, locale: str | None = None) -> dict:
        """Open an independent reader for one fixed mathematical instance."""
        return service.call("open_reader", _arguments(locals()))

    @mcp.tool()
    def get_overview(reader_id: str, view_id: str | None = None, scope: str | None = None,
                     cursor: str | None = None, limit: int = 50) -> dict:
        """Read a flat, paginated visible graph without revealing hidden descendants."""
        return service.call("get_overview", _arguments(locals()))

    @mcp.tool()
    def read_text(reader_id: str, view_id: str | None = None, anchor: str | None = None,
                  start_line: int | None = None, cursor: str | None = None, limit: int = 50) -> dict:
        """Read fixed Markdown logical lines and stable anchors from this view."""
        return service.call("read_text", _arguments(locals()))

    @mcp.tool()
    def inspect(reader_id: str, ref: str | dict, detail: str = "summary", view_id: str | None = None,
                dependency_view: str = "analysis", cursor: str | None = None,
                limit: int = 50) -> dict:
        """Inspect explicit material, or query a generation job owned by this reader."""
        return service.call("inspect", _arguments(locals()))

    @mcp.tool()
    def locate(reader_id: str, ref: str | dict, view_id: str | None = None) -> dict:
        """Find the visible ancestor and required expand path; do not change state."""
        return service.call("locate", _arguments(locals()))

    @mcp.tool()
    def recommend(reader_id: str, limit: int = 5) -> dict:
        """Return stable random-baseline legal expansion candidates."""
        return service.call("recommend", dict(reader_id=reader_id, limit=limit))

    @mcp.tool()
    def apply_action(reader_id: str, expected_view: str, action: str, target: str, budget_codepoints: int | None = None, locale: str | None = None) -> dict:
        """Expand/collapse/reset with stale-view protection, or cancel an owned job."""
        return service.call("apply_action", _arguments(locals()))

    # FastMCP annotations provide Python argument conversion; the canonical schema
    # narrows enums, references and limits identically for both transports.
    for tool in mcp._tool_manager.list_tools():
        tool.parameters = CONTRACT["tools"][tool.name]

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Lean Exposition", lifespan=lifespan)
    app.state.reader_service = service
    app.state.mcp = mcp

    @app.get("/api/schema")
    def api_schema():
        return CONTRACT

    @app.get("/api/instances")
    def instances():
        return service.instances()

    @app.post("/api/{tool}")
    async def invoke(tool: str, request: Request):
        try:
            arguments = await request.json()
        except ValueError:
            return {"ok": False, "view_id": None, "error": {"code": "validation_error", "message": "Request body must be JSON."}}
        return service.call(tool, arguments)

    static = Path(static_dir) if static_dir is not None else Path(__file__).resolve().parents[1] / "app" / "static"

    @app.get("/")
    def home():
        if (static / "index.html").exists():
            return FileResponse(static / "index.html")
        return HTMLResponse('<html><body><p>Reader service is ready.</p><a href="/api/instances">Available instances</a></body></html>')

    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/", mcp.streamable_http_app())
    return app


def _arguments(values):
    # Nested wrappers close over service, which is not a public argument.
    return {key: value for key, value in values.items() if value is not None and key != "service"}
