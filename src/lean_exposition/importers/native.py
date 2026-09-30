"""Native Lean projects loaded into the common declaration fact model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, DependencyLock, Provenance, RawDecl,
    Repository, Scope, SourceRange, Status, TextContent,
)
from lean_exposition.construction import CoverageContribution, RepositoryContext, build_repository
from .common import adapter_result_from_workspace, assemble_workspace, asset_from_bytes, qualified_id


def source_range(asset_id, span):
    """Lean positions use one-based lines and zero-based Unicode scalar columns."""
    if not span or span.get("synthetic"):
        return None
    start, end = span["start"], span["finish"]
    if (start["line"], start["column"]) >= (end["line"], end["column"]):
        return None
    return SourceRange(asset_id, start["line"], start["column"] + 1,
                       end["line"], end["column"] + 1)


def slice_source(text, location):
    lines = text.splitlines(keepends=True)
    for line, column in ((location.start_line, location.start_column),
                         (location.end_line, location.end_column)):
        if line < 1 or line > len(lines) or column < 1 or column > len(lines[line - 1].rstrip('\r\n')) + 1:
            raise ValueError('source range lies outside source text')
    start = sum(map(len, lines[:location.start_line - 1])) + location.start_column - 1
    end = sum(map(len, lines[:location.end_line - 1])) + location.end_column - 1
    return text[start:end]


def _load_native_workspace(project: str | Path, *, repo_key: str, modules: tuple[str, ...],
                           primary_outcomes: tuple[str, ...] = (), timeout: int = 300,
                           repl_rev: str | None = None, local_repl_path: str | Path | None = None,
                           evidence_dir: str | Path | None = None, build: bool = False,
                           cache_dir: str | Path | None = None, payload: dict | None = None, _artifact_state=None, memory_limit_mb=None, source_backend="toolkit_text_ast"):
    """Load explicit built modules (include desired local import closure explicitly).

    Source commands are canonicalized against compiler names, including private
    names. Other selected-module constants retain compiler facts and missing source
    status. No helper aggregation or feature extraction is performed.
    """
    from lean_exposition.lean import extract_modules
    root = Path(project).resolve()
    if payload is None:
        payload = extract_modules(root, modules, timeout=timeout, repl_rev=repl_rev, local_repl_path=local_repl_path,
                                  evidence_dir=evidence_dir, build=build, cache_dir=cache_dir, _artifact_state=_artifact_state, memory_limit_mb=memory_limit_mb, source_backend=source_backend)
    elif not payload.get('source'):
        # A pre-exported environment can omit source author commands. Recover
        # them locally so private names and statement/proof slices still align.
        from lean_exposition.lean.tools import extract_sources
        payload = dict(payload, source=extract_sources(root, modules, source_backend=source_backend,
            timeout=timeout, repl_rev=repl_rev, local_repl_path=local_repl_path, cache_dir=cache_dir),
            source_backend=source_backend)
    return normalize_native(root, repo_key=repo_key, modules=modules, payload=payload,
                            primary_outcomes=primary_outcomes)


class NativeRepositoryAdapter:
    """Compiled-native adapter retaining the existing exact extraction path."""

    def __init__(self, project: str | Path, *, repo_key: str, modules: tuple[str, ...],
                 primary_outcomes: tuple[str, ...] = (), timeout: int = 300,
                 repl_rev: str | None = None, local_repl_path: str | Path | None = None,
                 evidence_dir: str | Path | None = None, build: bool = False,
                           cache_dir: str | Path | None = None, payload: dict | None = None, _artifact_state=None, memory_limit_mb=None, source_backend="toolkit_text_ast"):
        self.project = project
        self.repo_key = repo_key
        self.modules = tuple(modules)
        self.primary_outcomes = tuple(primary_outcomes)
        self.timeout = timeout
        self.repl_rev = repl_rev
        self.local_repl_path = local_repl_path
        self.evidence_dir = evidence_dir
        self.build = build
        self.cache_dir = cache_dir
        self.payload = payload
        self._artifact_state = _artifact_state
        self.memory_limit_mb = memory_limit_mb
        self.source_backend = source_backend

    def collect(self, context: RepositoryContext | None = None):
        workspace = _load_native_workspace(
            self.project, repo_key=self.repo_key, modules=self.modules,
            primary_outcomes=self.primary_outcomes, timeout=self.timeout,
            repl_rev=self.repl_rev, local_repl_path=self.local_repl_path,
            evidence_dir=self.evidence_dir, build=self.build, cache_dir=self.cache_dir, payload=self.payload, _artifact_state=self._artifact_state, memory_limit_mb=self.memory_limit_mb, source_backend=self.source_backend,
        )
        coverage = []
        for decl in workspace.declarations:
            coverage.append(CoverageContribution(
                decl.ref, "statement", "lean_type", "complete", decl.provenance,
            ))
            value_status = ("unknown" if any(p.method == "lean_value_unavailable" for p in decl.provenance) else "complete")
            if decl.kernel_kind == "axiom":
                value_status = "not_applicable"
            if decl.proof is None:
                coverage.append(CoverageContribution(
                    decl.ref, "statement", "lean_value", value_status, decl.provenance,
                ))
            else:
                coverage.append(CoverageContribution(
                    decl.ref, "proof", "lean_value", value_status, decl.provenance,
                ))
        adapter = adapter_result_from_workspace(
            workspace, unit_aggregation="native_helpers", authority="lean_environment",
            method="compiled_native", coverage=coverage,
        )

        # Lean-provided author slices must not be overwritten by the fallback
        # Toolkit contributor in a unified mixed bundle.
        from dataclasses import replace
        return replace(adapter, declarations=tuple(replace(d, fields=tuple(
            replace(f, authority='lean_source') if f.field in {
                'statement.formal', 'proof.formal', 'statement.nl', 'kind'} and
                any(p.method.startswith('lean_interact') for p in f.provenance)
            else f for f in d.fields)) for d in adapter.declarations))


def load_native(*args, **kwargs):
    """Load compiled Lean facts into the canonical construction bundle."""
    return build_repository(NativeRepositoryAdapter(*args, **kwargs).collect())


def normalize_native(project, *, repo_key, modules, payload, primary_outcomes=()):
    """Normalize a compiler response against the exact local source assets."""
    root = Path(project).resolve()
    for path, digest in payload.get('input_digests', {}).items():
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f'project input changed since extraction: {path}')
    for module in modules:
        actual = hashlib.sha256((root / (module.replace('.', '/') + '.lean')).read_bytes()).hexdigest()
        if payload.get('source_digests', {}).get(module) != actual:
            raise ValueError(f'source changed since extraction: {module}')
    if (root / 'lean-toolchain').read_text().strip() != payload['toolchain']:
        raise ValueError('toolchain changed since extraction')
    assets, scopes, declarations, locks = [], [], [], []
    root_id = qualified_id(repo_key, '/')
    base = (Provenance('native_project', str(root)),)
    scopes.append(Scope(root_id, repo_key, 'repository', root.name, base))
    sources = {}
    for module in modules:
        path = module.replace('.', '/') + '.lean'
        raw = (root / path).read_bytes()
        asset = asset_from_bytes(repo_key, path, raw)
        assets.append(asset)
        sources[module] = (raw.decode(), asset)
        parent = root_id
        parts = module.split('.')
        for i in range(1, len(parts) + 1):
            name = '.'.join(parts[:i])
            identity = qualified_id(repo_key, name)
            if not any(s.scope_id == identity for s in scopes):
                scopes.append(Scope(identity, repo_key, 'module' if name in modules else 'directory',
                                    parts[i - 1], base, parent))
            parent = identity
    toolchain = payload['toolchain']
    external = {}
    for filename in ('lean-toolchain', 'lake-manifest.json', 'lakefile.toml', 'lakefile.lean'):
        if (root / filename).is_file():
            assets.append(asset_from_bytes(repo_key, filename, (root / filename).read_bytes()))
    manifest = json.loads((root / 'lake-manifest.json').read_text()) if (root / 'lake-manifest.json').exists() else {}
    for package in manifest.get('packages', []):
        key = repo_key + '/dependency/' + package['name']
        rev = package.get('rev')
        external[package['name'].lower()] = Repository(key, toolchain, None, revision=rev,
            version_status='fixed' if rev else 'unresolved',
            unresolved_reason=None if rev else 'Lake package has no fixed revision')
        locks.append(DependencyLock(repo_key, key, base))
    lean_repo = Repository(repo_key + '/lean', toolchain, None,
                          input_digest=hashlib.sha256(toolchain.encode()).hexdigest())
    external['lean'] = lean_repo
    locks.append(DependencyLock(repo_key, lean_repo.repo_key, base))
    compiled = {d['name']: d for d in payload['compiled']}
    if set(primary_outcomes) - compiled.keys():
        raise ValueError('primary outcomes must name extracted declarations')
    module_repositories = {}
    for fact in compiled.values():
        for dep in fact['type'] + (fact.get('value') or []):
            module = dep.get('module')
            if not module or module in module_repositories:
                continue
            prefix = module.split('.')[0].lower()
            path = module.replace('.', '/') + '.lean'
            if (root / path).is_file():
                module_repositories[module] = repo_key
            else:
                for package in manifest.get('packages', []):
                    package_root = root / '.lake/packages' / package['name']
                    if (package_root / path).is_file():
                        module_repositories[module] = external[package['name'].lower()].repo_key
                        break
                if module not in module_repositories:
                    # Module ownership precedes standard-library prefixes: a
                    # project or package can define its own Lean.* module.
                    module_repositories[module] = (lean_repo.repo_key
                        if prefix in {'lean', 'init', 'std'} else repo_key + '/external/' + prefix)

    def dep_ref(dep):
        module = dep.get('module') or ''
        if module in module_repositories:
            return DeclRef(module_repositories[module], dep['name'])
        return DeclRef(repo_key + '/external/unknown', dep['name'])

    authors = {}
    for module, response in payload['source'].items():
        for author in response.get('declarations', []):
            name = author.get('fullName', author.get('full_name'))
            match = compiled.get(name)
            if match is not None and (match['module'] != module or
                (match.get('range') and (match['range']['start'] != author['range']['start'] or
                                         match['range']['finish'] != author['range']['finish']))):
                match = None
            if match is None:
                # User-visible private name is provided by Lean, never parsed from numeric name segments.
                namespace = author.get('scope', {}).get('currNamespace', '')
                user_name = '.'.join(filter(None, (namespace, author['name'])))
                candidates = [d for d in compiled.values() if d['module'] == module and d['user_name'] == user_name]
                if len(candidates) != 1:
                    source_span = author.get('range') or {}
                    candidates = [d for d in compiled.values() if d['module'] == module and d.get('range')
                                  and d['range']['start'] == source_span.get('start')
                                  and d['range']['finish'] == source_span.get('finish')]
                if len(candidates) != 1:
                    continue  # Unmatched source declarations survive in the unified source contributor.
                match = candidates[0]
            if match['name'] in authors:
                continue
            authors[match['name']] = author
    for name, fact in sorted(compiled.items()):
        module = fact['module']
        text, asset = sources[module]
        author = authors.get(name)
        span = source_range(asset.asset_id, author.get('range')) if author else None
        range_method = payload.get('source_backend', 'lean_interact') + '_source'
        if author and span is None and fact.get('range'):
            original = author.get('range') or {}
            if all(fact['range'][p] == original.get(p) for p in ('start', 'finish')):
                span = source_range(asset.asset_id, fact['range'])
                range_method = 'lean_compiler_range_matching_macro_source'
        origin = (Provenance('lean_compiler_expr', name),)
        if fact.get('value') is None:
            origin += (Provenance('lean_value_unavailable', name),)
        source_origin = (Provenance(range_method, name, (span,) if span else ()),)
        missing_nl = TextContent(None, 'missing', source_origin, 'No declaration docstring is available')
        nl = (TextContent(fact["docstring"], "present", origin) if fact.get("docstring") else missing_nl)
        if author:
            doc = author.get('modifiers', {}).get('docString')
            if doc:
                doc_range = source_range(asset.asset_id, doc.get('range'))
                nl = TextContent(slice_source(text, doc_range) if doc_range else doc['content'],
                                 'present', (Provenance('lean_docstring', name, (doc_range,) if doc_range else ()),))
        deps_type = tuple(Dependency(dep_ref(d), 'lean_type', origin, d.get('module') or None)
                          for d in fact['type'])
        deps_value = tuple(Dependency(dep_ref(d), 'lean_value', origin, d.get('module') or None)
                           for d in fact.get('value') or [])
        kind = author['kind'] if author else fact['kind']
        proof = None
        if span:
            formal = TextContent(slice_source(text, span), 'present', source_origin)
        else:
            formal = TextContent(None, 'missing', origin, 'Compiler constant has no author source command mapping')
        if author and kind in {'theorem', 'lemma'}:
            value = author.get('value')
            value_span = source_range(asset.asset_id, value.get('range')) if value else None
            if value_span and span:
                statement_span = SourceRange(span.asset_id, span.start_line, span.start_column,
                                             value_span.start_line, value_span.start_column)
                formal = TextContent(slice_source(text, statement_span), 'present',
                    (Provenance(payload.get('source_backend', 'lean_interact') + '_statement_before_value', name, (statement_span,)),))
                proof_formal = TextContent(slice_source(text, value_span), 'present',
                    (Provenance(payload.get('source_backend', 'lean_interact') + '_value', name, (value_span,)),))
            else:
                proof_formal = TextContent(None, 'missing', source_origin, 'No reliable proof source range')
            proof = DeclContent(TextContent(None, 'missing', source_origin,
                               'Docstring retained whole in statement; no separate proof NL'), proof_formal, deps_value)
        elif fact['kind'] == 'theorem':
            proof = DeclContent(missing_nl, TextContent(None, 'missing', origin,
                                'Generated theorem has no author proof mapping'), deps_value)
        context = ((TextContent(fact["type_text"], "present", (Provenance("lean_compiler_type", name),)),)
                   if fact.get("type_text") else ())
        if author:
            context += (TextContent(json.dumps(author.get('scope', {}), ensure_ascii=False, sort_keys=True),
                                   'present', (Provenance(payload.get('source_backend', 'lean_interact') + '_scope', name),)),)
        declarations.append(RawDecl(DeclRef(repo_key, name), name, module,
            qualified_id(repo_key, module), kind,
            DeclContent(nl, formal, deps_type if proof else deps_type + deps_value),
            Status('extracted' if author else 'compiler_only', origin), source_origin + origin,
            proof=proof, kernel_kind=fact['kind'], source_refs=(span,) if span else (),
            source_context=context, local_public=bool(author and author.get('modifiers', {}).get('visibility', 'regular') != 'private'),
            generated_from=DeclRef(repo_key, fact['generator']) if fact.get('generator') else None))
    revision = payload.get('project_revision')
    if 'project_revision' not in payload:
        git_root = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=root, text=True, capture_output=True)
        if git_root.returncode == 0 and Path(git_root.stdout.strip()).resolve() == root:
            revision_result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, capture_output=True)
            if revision_result.returncode == 0:
                revision = revision_result.stdout.strip()
    digest = hashlib.sha256(json.dumps(sorted((a.path, a.sha256) for a in assets)).encode()).hexdigest()
    repository = Repository(repo_key, toolchain, root_id, revision, digest,
                            tuple(DeclRef(repo_key, n) for n in primary_outcomes))
    return assemble_workspace(repositories=(repository, *external.values()), assets=assets,
        declarations=declarations, scopes=scopes, dependency_locks=locks)
