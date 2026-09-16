"""Small hand-authored demonstration, independent of model credentials."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, Provenance, RawDecl, Repository, Scope,
    Status, TextContent, Workspace, WorkspaceManifest,
)
from lean_exposition.exposition import ContentStore


def demo_fixture():
    provenance = (Provenance("handwritten_fixture", "reader-demo"),)
    def text(value):
        return TextContent(value, "present", provenance)
    refs = {name: DeclRef("demo", name) for name in ("definition", "bound", "result")}
    decls = []
    for name, kind, nl, formal in [
        ("definition", "def", "Let f(n) = n + 1.", "def f (n : Nat) := n + 1"),
        ("bound", "theorem", "For n ≥ 0, n ≤ f(n).", "theorem bound (n : Nat) : n ≤ f n := by omega"),
        ("result", "theorem", "For every natural n, n < f(n).", "theorem result (n : Nat) : n < f n := by omega"),
    ]:
        deps = () if name == "definition" else (Dependency(refs["definition"], "lean_type", provenance),)
        decls.append(RawDecl(refs[name], name, "Main", "scope", kind,
                             DeclContent(text(nl), text(formal), deps), Status("fixture", provenance), provenance,
                             proof=DeclContent(text("The claim follows by arithmetic."), text("by omega")) if kind == "theorem" else None))
    workspace = Workspace(WorkspaceManifest((
        Repository("demo", "leanprover/lean4:v4.28.0", "scope", revision="a" * 40,
                   primary_outcomes=(refs["result"],)),), ()), tuple(decls),
        (Scope("scope", "demo", "repository", "Demo", provenance),))
    def node(id, kind, parent, children, names, title):
        return {"id": id, "kind": kind, "parent": parent, "children": children,
                "decl_refs": [asdict(refs[n]) for n in names], "title": title,
                "representative": asdict(refs[names[0]]) if kind == "unit" else None, "source_scope": "scope"}
    config = {"origin": "handwritten_fixture"}
    encoded = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    hierarchy = {"hierarchy_id": "", "workspace_digest": workspace.digest(),
        "source_digest": hashlib.sha256(encoded({"origin": "handwritten_fixture", "repo_key": "demo"})).hexdigest(),
        "config_digest": hashlib.sha256(encoded(config)).hexdigest(), "repo_key": "demo", "root_id": "root",
        "config": config, "diagnostics": [], "nodes": [
            node("root", "repo", None, ["setup", "conclusion"], list(refs), "A simple successor argument"),
            node("setup", "region", "root", ["definition", "bound"], ["definition", "bound"], "The successor and its bound"),
            node("conclusion", "scope", "root", ["result"], ["result"], "Strict growth"),
            node("definition", "unit", "setup", [], ["definition"], "Successor"),
            node("bound", "unit", "setup", [], ["bound"], "Weak bound"),
            node("result", "unit", "conclusion", [], ["result"], "Strict bound")],
        "edges": [{"id": "edge-" + name, "provider_node": "definition", "consumer_node": name,
                   "provider_decl": asdict(refs["definition"]), "consumer_decl": asdict(refs[name])} for name in ("bound", "result")],
        "external_refs": []}
    identity = dict(hierarchy)
    identity.pop("hierarchy_id")
    hierarchy["hierarchy_id"] = "hierarchy:" + hashlib.sha256(encoded(identity)).hexdigest()[:24]
    blocks = {
        "root": {"lead_in": "Consider the natural numbers.", "synopsis": "The successor construction gives a strict increase.",
                 "lead_out": "Thus the natural numbers admit a strictly increasing successor.", "anchors": []},
        "setup": {"lead_in": "", "synopsis": "Define the successor and establish its weak bound.", "lead_out": "The successor never decreases its input.", "anchors": []},
        "conclusion": {"lead_in": "We now strengthen the comparison.", "synopsis": "The increase is strict.", "lead_out": "", "anchors": []},
        "definition": {"content": "Define $f(n)=n+1$ for $n\\in\\mathbb{N}$.", "anchors": [{"part": "content", "targets": [{"decl_ref": asdict(refs["definition"])}]}]},
        "bound": {"statement": "For every $n\\in\\mathbb{N}$, $n\\le f(n)$.", "proof": "Adding one cannot decrease a natural number.", "anchors": []},
        "result": {"statement": "For every $n\\in\\mathbb{N}$, $n<f(n)$.", "proof": "Since $f(n)=n+1$, the inequality is immediate.", "anchors": []},
    }
    return {"workspace": json.loads(workspace.to_json()), "hierarchy": hierarchy, "blocks": blocks}


def create_demo(directory):
    fixture = demo_fixture()
    store = ContentStore(Workspace.from_json(json.dumps(fixture["workspace"])), fixture["hierarchy"], Path(directory) / "content.json")
    store.publish(fixture["blocks"])
    return store
