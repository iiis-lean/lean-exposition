"""Read current LC facts from immutable Git snapshots without loading LC runtime."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, DependencyLock, Provenance, RawDecl,
    Repository, Scope, SourceRange, Status, TextContent, ValidationError, Workspace,
)
from .common import assemble_workspace, asset_from_bytes, qualified_id


@dataclass(frozen=True)
class LCRepositoryInput:
    path: str | Path
    repo_key: str
    revision: str | None = None


class _Snapshot:
    def __init__(self, source: LCRepositoryInput, revision: str | None = None):
        self.source = source
        self.revision = self.git("rev-parse", "--verify", (revision or source.revision or "HEAD") + "^{commit}").decode().strip()
        self.paths = set(self.git("ls-tree", "-r", "--name-only", self.revision).decode().splitlines())
        self.assets = {}
        self.cache = {}

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source.path), *args], capture_output=True)
        if result.returncode:
            raise ValidationError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    def read(self, path):
        if path not in self.cache:
            data = self.git("show", f"{self.revision}:{path}")
            self.cache[path] = data
            self.assets[path] = asset_from_bytes(self.source.repo_key, path, data)
        return self.cache[path]

    def json(self, path):
        return json.loads(self.read(path))

    def provenance(self, path, field=""):
        return (Provenance("lc_current", f"git:{self.revision}:{path}#{field}"),)

    def packages(self):
        return self.json("lake-manifest.json").get("packages", []) if "lake-manifest.json" in self.paths else []


def _ref(repo_key, value):
    return DeclRef(value.get("repo") or repo_key, value["node"] + "/" + value["name"])


def load_lc_workspace(main: LCRepositoryInput, providers=()) -> Workspace:
    """Freeze main HEAD and read each declaration's current revision only.

    Provider inputs identify local object stores. A consumer Lake lock selects the
    provider commit even when its checkout HEAD differs. Explicit conflicting
    revisions and incompatible workspace locks are errors. Unprovided dependencies
    remain references, with fixed metadata when a Lake lock supplies it.
    """
    supplied = {main.repo_key: main}
    for provider in providers:
        if provider.repo_key in supplied:
            raise ValidationError(f"duplicate repository input: {provider.repo_key}")
        supplied[provider.repo_key] = provider
    snapshots = {main.repo_key: _Snapshot(main)}
    pending = list(providers)
    while pending:
        # Resolve providers connected to a snapshot first (also handles transitive locks).
        constraints = {}
        for snapshot in snapshots.values():
            for package in snapshot.packages():
                if package.get("rev"):
                    key = package["name"]
                    if key in constraints and constraints[key] != package["rev"]:
                        raise ValidationError(f"incompatible dependency versions: {key}")
                    constraints[key] = package["rev"]
        provider = next((p for p in pending if p.repo_key in constraints), pending[0])
        locked = constraints.get(provider.repo_key)
        if locked and provider.revision:
            explicit = _Snapshot(provider).revision
            if explicit != locked:
                raise ValidationError(f"provider revision conflicts with Lake lock: {provider.repo_key}")
        snapshots[provider.repo_key] = _Snapshot(provider, locked)
        pending.remove(provider)

    repositories, declarations, scopes, locks = [], [], [], []
    external = {}
    for key, snapshot in snapshots.items():
        toolchain = snapshot.read("lean-toolchain").decode().strip()
        for package in snapshot.packages():
            name, revision = package["name"], package.get("rev")
            if name == key:
                continue
            if name in snapshots and revision and snapshots[name].revision != revision:
                raise ValidationError(f"provider revision conflicts with Lake lock: {name}")
            if name not in snapshots and revision:
                repo = Repository(name, toolchain, None, revision=revision)
                if name in external and external[name].revision != revision:
                    raise ValidationError(f"incompatible dependency versions: {name}")
                external[name] = repo
            if name in snapshots or revision:
                locks.append(DependencyLock(key, name, snapshot.provenance("lake-manifest.json", name)))
        index_path = ".lean_constellation/index/nodes.json"
        index = snapshot.json(index_path)
        nodes = {n["path"]: n for n in index["entries"]
                 if n.get("active") and n.get("lifecycle", "active") == "active"}
        if "Main" not in nodes:
            raise ValidationError("LC snapshot has no active Main root")
        for path, node in nodes.items():
            scopes.append(Scope(qualified_id(key, path), key,
                                "repository" if path == "Main" else node["kind"],
                                key if path == "Main" else path.rsplit(".", 1)[-1],
                                snapshot.provenance(index_path, path),
                                None if path == "Main" else qualified_id(key, path.rsplit(".", 1)[0])))
        root = nodes["Main"]
        contract_path = f'.lean_constellation/nodes/{root["node_id"]}/contracts/{root["active_contract_version"]}.json'
        contract = snapshot.json(contract_path)
        if "exports" not in contract:
            raise ValidationError("Main contract has no exports field; primary outcomes cannot be inferred from interfaces")
        outcomes = tuple(_ref(key, value) for value in contract["exports"])
        repositories.append(Repository(key, toolchain, qualified_id(key, "Main"),
                                       revision=snapshot.revision, primary_outcomes=outcomes))
        corpus_path = ".lean_constellation/source_corpus/manifest.json"
        corpus = snapshot.json(corpus_path) if corpus_path in snapshot.paths else None
        active_ids = {node["node_id"] for node in nodes.values()}
        for path in sorted(snapshot.paths):
            if not path.endswith("/decl.json") or "/decl_graph/decls/" not in path:
                continue
            if path.split("/")[2] not in active_ids:
                continue
            meta = snapshot.json(path)
            if meta.get("lifecycle", "active") != "active":
                continue
            revision_path = path.rsplit("/", 1)[0] + f'/revisions/{meta["current_revision"]}.json'
            current = snapshot.json(revision_path)
            provenance = snapshot.provenance(revision_path)
            source_refs = []
            source_context = []
            projection = meta["module"].replace(".", "/") + ".lean"
            if projection in snapshot.paths:
                code = snapshot.read(projection).decode()
                source_context.append(TextContent(code, "present", snapshot.provenance(projection)))
                # The managed mathematical region may include helpers. It is not
                # falsely labelled as the exact named declaration range.
                marker = "-- lean-constellation: declaration-source-begin"
                lines = code.splitlines()
                if marker in lines and lines.index(marker) + 1 < len(lines):
                    source_refs.append(SourceRange(snapshot.assets[projection].asset_id,
                                                   lines.index(marker) + 2, None, len(lines), None))

            def content(part, label):
                def text(field, value_key):
                    value = part.get(field)
                    prov = snapshot.provenance(revision_path, f"{label}.{field}")
                    if value is None or value.get(value_key) is None:
                        return TextContent(None, "missing", prov, "Not present in current LC record.")
                    for origin in value.get("origin", []):
                        ranges = ()
                        source_path = origin.get("source_path")
                        if source_path and corpus:
                            asset_path = corpus["relpath"].rstrip("/") + "/" + source_path
                            if asset_path in snapshot.paths:
                                data = snapshot.read(asset_path)
                                entry = next((f for f in corpus["files"] if f["path"] == source_path), None)
                                if entry and snapshot.assets[asset_path].sha256 != entry["sha256"]:
                                    raise ValidationError(f"source corpus digest mismatch: {source_path}")
                                start, end = origin.get("start_line"), origin.get("end_line")
                                if start is not None and end is not None:
                                    if not 1 <= start <= end <= len(data.decode().splitlines()):
                                        raise ValidationError(f"source origin range outside asset: {source_path}")
                                    ranges = (SourceRange(snapshot.assets[asset_path].asset_id, start, None, end, None),)
                        prov += (Provenance("lc_origin", json.dumps(origin, ensure_ascii=False, sort_keys=True), ranges),)
                    check = value.get("check")
                    status = Status(check["status"], snapshot.provenance(revision_path, f"{label}.{field}.check")) if check and check.get("status") else None
                    return TextContent(value[value_key], "present", prov, check=status)
                deps = []
                for dep in part.get("deps", []):
                    if dep["kind"] == "repo_decl":
                        target = _ref(key, dep["ref"])
                    elif dep["kind"] == "mathlib_decl":
                        target = DeclRef("mathlib", dep["ref"]["name"])
                    else:
                        raise ValidationError(f'unsupported LC declaration dependency: {dep["kind"]}')
                    deps.append(Dependency(target, "lc_declared", (Provenance(
                        "lc_dependency", f"git:{snapshot.revision}:{revision_path}#{label}.deps:" +
                        json.dumps(dep, ensure_ascii=False, sort_keys=True)),)))
                return DeclContent(text("nl", "text"), text("formal", "code"), tuple(deps))

            declarations.append(RawDecl(
                DeclRef(key, meta["node_path"] + "/" + meta["name"]), current["lean_decl_name"],
                meta["module"], qualified_id(key, meta["node_path"]), meta["kind"],
                content(current["statement"], "statement"), Status("imported", provenance),
                snapshot.provenance(path) + provenance,
                Status(current["state"], provenance) if current.get("state") else None,
                content(current["proof"], "proof") if current.get("proof") is not None else None,
                source_refs=tuple(source_refs), source_context=tuple(source_context),
                local_public=meta.get("public", False),
            ))
    loaded_refs = {declaration.ref for declaration in declarations}
    for repository in repositories:
        for outcome in repository.primary_outcomes:
            if outcome not in loaded_refs:
                raise ValidationError(f"Main export does not resolve to a loaded declaration: {outcome}")
    for declaration in declarations:
        for part in (declaration.statement, declaration.proof):
            if part is not None:
                for dependency in part.deps:
                    if dependency.provider.repo_key in snapshots and dependency.provider not in loaded_refs:
                        raise ValidationError(f"Dependency in supplied repository does not resolve: {dependency.provider}")
    repositories.extend(external.values())
    return assemble_workspace(repositories=repositories, declarations=declarations, scopes=scopes,
                              assets=[asset for snapshot in snapshots.values() for asset in snapshot.assets.values()],
                              dependency_locks=locks)
