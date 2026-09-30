"""Source signatures and import visibility for conservative text references."""
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import tempfile

from lean_exposition.models import DeclRef, Repository
from .common import qualified_id
from .text_scope import DECLARATIONS, NAME, SourceContext, declaration_head, module_contexts, name_parts, scan_scope, tokenize


@dataclass(frozen=True)
class ReferenceSignature:
    ref: DeclRef
    name: str
    module: str
    namespace: str
    parameters: tuple[tuple[str, str | None, bool], ...]
    source_sha256: str
    line: int = 1
    public: bool = True


@dataclass(frozen=True)
class FixedSourceRoot:
    repo_key: str
    root: Path
    revision: str | None
    toolchain: str | None = None


class ReferenceIndex:
    """An index can cover more declarations than the target adapter exports."""

    def __init__(self, signatures=(), imports=None, repositories=(), diagnostics=()):
        self.signatures = tuple(signatures)
        self.imports = dict(imports or {})
        self.repositories = tuple(repositories)
        self.diagnostics = tuple(diagnostics)
        self.by_name = defaultdict(list)
        self.namespace_modules = defaultdict(set)
        for signature in self.signatures:
            self.by_name[signature.name].append(signature)
            parts = name_parts(signature.name)
            for i in range(1, len(parts)):
                self.namespace_modules['.'.join(parts[:i])].add((signature.ref.repo_key, signature.module))
        self._closure = {}

    def visible_modules(self, repo_key, module):
        key = repo_key, module
        if key not in self._closure:
            reached, pending = set(), [key]
            while pending:
                current = pending.pop()
                if current in reached:
                    continue
                reached.add(current)
                pending.extend(self.imports.get(current, ()))
            self._closure[key] = reached
        return self._closure[key]

    def candidates(self, names, repo_key, module, line=None):
        visible = self.visible_modules(repo_key, module)
        return list({s.ref: s for name in names for s in self.by_name.get(name, ())
                     if (s.ref.repo_key, s.module) in visible
                     and (s.public or (s.ref.repo_key, s.module) == (repo_key, module))}.values())

    def resolve(self, token, *, repo_key, module, namespace='', opens=(), aliases=(), line=None):
        rooted = token.startswith('_root_.')
        name = token.removeprefix('_root_.')
        prefixes = name_parts(namespace)
        tiers = [[name]] if rooted else [[('.'.join(prefixes[:i]) + '.' if i else '') + name]
                                             for i in range(len(prefixes), -1, -1)]
        if not rooted:
            opened = []
            visible = self.visible_modules(repo_key, module)
            for item in opens:
                if isinstance(item, str):
                    opened.append(item)
                    continue
                at_namespace, opened_name = item
                if opened_name.startswith('_root_.'):
                    opened.append(opened_name.removeprefix('_root_.'))
                    continue
                components = name_parts(at_namespace)
                for i in range(len(components), -1, -1):
                    candidate = '.'.join(components[:i] + [opened_name])
                    if self.namespace_modules.get(candidate, set()) & visible:
                        opened.append(candidate)
                        break
            tiers.append([o + '.' + name for o in opened])
        alias_map = dict(aliases)
        for tier in tiers:
            result = self.candidates(tier, repo_key, module, line)
            for name in tier:
                if name in alias_map:
                    result += self.resolve(alias_map[name], repo_key=repo_key, module=module,
                        namespace='.'.join(name_parts(name)[:-1]), opens=opens, line=line)
            if result:
                return list({s.ref: s for s in result}.values())
        return []

    def type_heads(self, text, *, repo_key, module, namespace='', opens=(), line=None):
        if not text:
            return []
        # A function, relation, metavariable or coercion needs elaboration.
        scan = scan_scope(text)
        words = [t.text for t in scan.tokens]
        if not words or words[0] in {'(', '{', '_', '∀', 'fun'}:
            return []
        depth = 0
        for word in words:
            if word in {'(', '[', '{', '⟨'}:
                depth += 1
            elif word in {')', ']', '}', '⟩'}:
                depth -= 1
            elif depth == 0 and word in {'→', '->', '=', '≤', '≥', '<', '>', '∈', '∉', '≠', '↔', '∧', '∨'}:
                return []
        head = words[0]
        if not NAME.fullmatch(head):
            return []
        return self.resolve(head, repo_key=repo_key, module=module, namespace=namespace, opens=opens, line=line)

    def dot_candidates(self, receiver_type, method, *, repo_key, module, namespace='', opens=(), line=None):
        types = self.type_heads(receiver_type, repo_key=repo_key, module=module,
                                namespace=namespace, opens=opens, line=line)
        if len(types) != 1:
            return [], 'receiver_type_ambiguous' if types else 'receiver_type_unknown'
        typ = types[0]
        candidates = self.candidates([typ.name + '.' + method], repo_key, module, line)
        confirmed = []
        for candidate in candidates:
            for _, parameter_type, implicit in candidate.parameters:
                heads = self.type_heads(parameter_type, repo_key=candidate.ref.repo_key,
                                        module=candidate.module, namespace=candidate.namespace)
                if len(heads) == 1 and heads[0].ref == typ.ref:
                    confirmed.append(candidate)
                    break
        return confirmed, 'method_signature_unknown' if not confirmed else None


def adapter_reference_index(adapter, source_texts):
    module_states = {module: module_contexts(text) for module, text in source_texts.items()}
    module_lines = {module: text.splitlines(keepends=True) for module, text in source_texts.items()}
    repo_key = adapter.repositories[0].repo_key
    imports = {(repo_key, module): tuple((repo_key, dep) for dep in data[1])
               for module, data in module_states.items()}
    signatures = []
    for decl in adapter.declarations:
        values = {f.field: f.value for f in decl.fields if f.state == 'present'}
        module, name = values['module'], values['lean_name']
        ranges = values.get('source_refs', ())
        line = ranges[0].start_line if ranges else 1
        contexts = module_states.get(module, ((), ()))[0]
        context = contexts[min(line - 1, len(contexts) - 1)] if contexts else SourceContext()
        public = context.public
        if ranges and module in module_lines:
            source = ''.join(module_lines[module][line - 1:ranges[0].end_line])
            _, tokens = tokenize(source)
            head = declaration_head(tokens)
            if head is not None and head + 1 < len(tokens) and NAME.fullmatch(tokens[head + 1].text):
                # Keep Toolkit/source observations intact. Bind references by
                # the declared spelling under the actual command scope, also
                # when Toolkit did not close a compound namespace/section end.
                from lean_mcp_toolkit.backends.text_ast.namespace import qualify_name
                name = qualify_name(namespace_stack=(context.namespace,) if context.namespace else (),
                                    raw_name=tokens[head + 1].text).removeprefix('_root_.')
            modifiers = []
            for token in tokens:
                if token.text in DECLARATIONS:
                    break
                modifiers.append(token.text)
            if 'private' in modifiers:
                public = False
            elif 'public' in modifiers:
                public = True
        if name_parts(name)[-1].startswith('_anonymous_'):
            public = False
        formal = values.get('statement.formal')
        params = scan_scope(formal.text or '', declaration=True).parameters if formal else ()
        parameters = tuple((b.name, b.type_text, b.implicit) for b in params)
        # Section parameters become implicit arguments when used by a declaration.
        words = set(NAME.findall(formal.text or '')) if formal else set()
        proof = values.get('proof.formal')
        if proof and proof.text:
            words.update(NAME.findall(proof.text))
        parameters += tuple((n, typ, False) for n, typ in context.variables
                            if any(w == n or w.startswith(n + '.') for w in words))
        signatures.append(ReferenceSignature(decl.locator.ref, name, module, context.namespace,
                          parameters, hashlib.sha256(source_texts[module].encode()).hexdigest()
                          if module in source_texts else '', line, public))
    return ReferenceIndex(signatures, imports), module_states


def build_source_reference_index(roots, targets, *, cache_dir=None):
    """Index a fixed import closure without Lean; cache each file by bytes/parser.

    ``targets`` are (repo_key, module) pairs. Ambiguous cross-repository module
    ownership is diagnosed instead of assigning a provider. Missing imports
    stay visible as diagnostics. Dependency roots require fixed revisions;
    target roots may use the target adapter's existing source-asset identity.
    """
    from .source import consume_text_ast_json, provisional_source_adapter
    from .toolkit import source_response
    roots = tuple(roots)
    targets = tuple(targets)
    target_repos = {repo_key for repo_key, _ in targets}
    if len({r.repo_key for r in roots}) != len(roots) or any(
            not r.revision and r.repo_key not in target_repos for r in roots):
        raise ValueError('source roots require unique repo keys and fixed dependency revisions')
    by_repo = {r.repo_key: r for r in roots}
    signatures, imports, diagnostics = [], {}, []
    pending, visited = list(targets), set()
    implementation = reference_parser_fingerprint().encode()
    while pending:
        repo_key, module = pending.pop()
        if (repo_key, module) in visited:
            continue
        visited.add((repo_key, module))
        root = by_repo[repo_key]
        path = module.replace('.', '/') + '.lean'
        file = (Path(root.root) / path).resolve()
        if not file.is_relative_to(Path(root.root).resolve()):
            raise ValueError('module path escapes source root')
        if not file.is_file():
            diagnostics.append(f'text_index_missing_module:{repo_key}:{module}')
            continue
        try:
            source = file.read_bytes()
            text = source.decode()
            payload = source_response(text, module, cache_dir=cache_dir)
        except (OSError, ValueError, UnicodeError) as exc:
            diagnostics.append(f'text_index_source_failed:{repo_key}:{module}:{exc}')
            continue
        digest = hashlib.sha256(source).hexdigest()
        # source_response already fingerprints the complete Toolkit parser.
        key = hashlib.sha256(implementation + json.dumps([repo_key, root.revision, root.toolchain,
             module, digest, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cached = Path(cache_dir) / 'references' / (key + '.json') if cache_dir else None
        if cached and cached.exists():
            wire = json.loads(cached.read_text())
        else:
            inventory = consume_text_ast_json(repo_key=repo_key, path=path, module=module,
                                             source=source, payload=payload)
            adapter = provisional_source_adapter((inventory,), repo_key=repo_key)
            index, states = adapter_reference_index(adapter, {module: text})
            wire = {'imports': sorted(states[module][1]), 'diagnostics': list(adapter.diagnostics), 'signatures': [
                {'local_id': s.ref.local_id, 'name': s.name, 'namespace': s.namespace,
                 'parameters': s.parameters, 'line': s.line, 'public': s.public} for s in index.signatures]}
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w', dir=cached.parent, delete=False) as handle:
                    json.dump(wire, handle, ensure_ascii=False)
                    temporary = Path(handle.name)
                temporary.replace(cached)
        signatures.extend(ReferenceSignature(DeclRef(repo_key, s['local_id']), s['name'], module,
            s['namespace'], tuple(tuple(p) for p in s['parameters']), digest, s['line'], s['public']) for s in wire['signatures'])
        diagnostics.extend(wire['diagnostics'])
        linked = []
        for imported in wire['imports']:
            owners = [r.repo_key for r in roots if (Path(r.root) / (imported.replace('.', '/') + '.lean')).is_file()]
            if len(owners) == 1:
                linked.append((owners[0], imported))
                pending.append((owners[0], imported))
            else:
                kind = 'ambiguous' if owners else 'missing'
                diagnostics.append(f'text_index_{kind}_module:{repo_key}:{module}:{imported}')
        imports[repo_key, module] = tuple(linked)
    # A closure's file hashes identify these observations, not the provider's
    # entire repository. Keep them in signatures/cache and use the fixed
    # revision/toolchain as the provider identity used by exact catalogs.
    observed_repos = {s.ref.repo_key for s in signatures}
    repositories = tuple(Repository(r.repo_key, r.toolchain, qualified_id(r.repo_key, '/'), revision=r.revision)
                         for r in roots if r.repo_key in observed_repos and r.revision)
    return ReferenceIndex(signatures, imports, repositories, diagnostics)


def reference_parser_fingerprint():
    """Bind signature caches to the complete source parser and identity code."""
    from lean_mcp_toolkit.backends.text_ast.declarations import parse_declarations
    package = Path(inspect.getfile(parse_declarations)).parent
    paths = sorted(package.glob('*.py')) + [Path(__file__).with_name(name) for name in (
        'reference_index.py', 'text_scope.py', 'source.py', 'toolkit.py')]
    return hashlib.sha256(b''.join(p.name.encode() + p.read_bytes() for p in paths)).hexdigest()
