"""A short-lived MCP endpoint bound to one mathematical writing job."""
from contextlib import contextmanager
import socket
import threading
import time
import uuid

from .writing import mathematical_prompt


@contextmanager
def writing_mcp(store, job_id, trace):
    """Serve only this job's operations in the owning process, sharing its lock."""
    from mcp.server.fastmcp import FastMCP
    import uvicorn

    path = "/writing-" + uuid.uuid4().hex
    mcp = FastMCP("Mathematical Writing", log_level="ERROR", stateless_http=True, json_response=True, streamable_http_path=path)

    def call(name, arguments, operation):
        try:
            result = operation()
            trace.append({"tool": name, "arguments": arguments, "result": result})
            return result
        except Exception as exc:
            trace.append({"tool": name, "arguments": arguments, "error": str(exc)})
            raise

    @mcp.tool()
    def get_step() -> dict:
        """Read the current child, source material, draft schema and canonical context."""
        return call("get_step", {}, lambda: store.get_step(job_id))

    @mcp.tool()
    def submit_draft(payload: dict) -> dict:
        """Validate or revise the current draft and preview it; does not advance."""
        return call("submit_draft", {"payload": payload}, lambda: store.submit_draft(job_id, payload))

    @mcp.tool()
    def accept_draft(draft_id: str) -> dict:
        """Accept exactly the latest draft; final acceptance atomically publishes the group."""
        return call("accept_draft", {"draft_id": draft_id}, lambda: store.accept_draft(job_id, draft_id))

    @mcp.tool()
    def query_decl(repo_key: str, local_id: str, offset: int = 0, limit: int = 12000) -> dict:
        """Page a related declaration's full statement and proof, within the job boundary."""
        arguments = dict(decl_ref=dict(repo_key=repo_key, local_id=local_id), offset=offset, limit=limit)
        return call("query_decl", arguments, lambda: store.query_job(job_id, "decl", **arguments))

    @mcp.tool()
    def query_scope(node_id: str, offset: int = 0, limit: int = 12000) -> dict:
        """Page a bound scope's source relations and declaration cards."""
        arguments = dict(node_id=node_id, offset=offset, limit=limit)
        return call("query_scope", arguments, lambda: store.query_job(job_id, "scope", **arguments))

    @mcp.tool()
    def query_path(node_id: str, offset: int = 0, limit: int = 12000) -> dict:
        """Page canonical ancestor introductions and preceding fixed outcomes."""
        arguments = dict(node_id=node_id, offset=offset, limit=limit)
        return call("query_path", arguments, lambda: store.query_job(job_id, "path", **arguments))

    @mcp.tool()
    def query_dependency_path(provider_repo: str, provider_id: str, consumer_repo: str, consumer_id: str,
                              offset: int = 0, limit: int = 12000) -> dict:
        """Find a provider-to-consumer dependency chain in the bound source evidence."""
        arguments = dict(provider_ref=dict(repo_key=provider_repo, local_id=provider_id),
                         consumer_ref=dict(repo_key=consumer_repo, local_id=consumer_id), offset=offset, limit=limit)
        return call("query_dependency_path", arguments, lambda: store.query_job(job_id, "dependency_path", **arguments))

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Writing MCP failed to start.")
            time.sleep(.01)
        yield "http://127.0.0.1:" + str(port) + path
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def run_agent_job(store, job_id, executor_factory, *, trace=None, record_request=None):
    """Run a stateful Agent through the one-job writing MCP.

    ``executor_factory`` receives the ephemeral MCP URL and returns an
    ``AgentExecutor``. A final model response is never publication evidence;
    the bound content store remains authoritative.
    """
    step = store.get_step(job_id)
    if step["status"] != "active":
        raise ValueError("Agent writing requires an active job.")
    trace = [] if trace is None else trace
    with writing_mcp(store, job_id, trace) as url:
        prompt = mathematical_prompt(store.locale, {}, {}) + (
            "\nYou are the writer for job " + job_id + ". Call get_step first. For every current step, read the material, "
            "call at least one bound source query to verify the interface; call query_decl/scope/path when additional source is needed, then submit_draft. Inspect the returned continuous "
            "preview for mathematical correctness, notation and transitions. Revise drafts when the preview or diagnostics reveal a concrete issue; do not add claims merely to make a revision. "
            "Keep terminal mathematical titles concise. Compiler-only source-missing entries must honestly explain the absence of an authored source or proof; never invent a mathematical lemma. Only accept the latest draft_id after reviewing the preview and any length warnings; essential hypotheses take priority over soft targets. Continue until status published. "
            "For source queries keep the default limit of 12000 characters unless a smaller page is specifically useful; "
            "follow next_offset only when more source is needed, and do not repeat an already-read page. "
            "Do not call any tools other than this writing MCP. Return the actual job_id and manifest_id after publication."
        )
        schema = {"type": "object", "properties": {"job_id": {"type": "string"}, "manifest_id": {"type": "string"}},
                  "required": ["job_id", "manifest_id"], "additionalProperties": False}
        executor = executor_factory(url)
        if record_request is not None:
            record_request({"prompt": prompt, "schema": schema,
                            "executor": type(executor).__name__})
        result = executor.result(executor.start(prompt, schema))
    status = store.get_step(job_id)
    return {"result": result, "trace": trace, "job": status, "prompt": prompt,
            "published": status["status"] == "published",
            "desktop_visibility": "unverified; this call uses isolated SDK App Server stdio"}
