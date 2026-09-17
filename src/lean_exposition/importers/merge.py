"""Compose partial source and semantic observations before shared construction."""
from dataclasses import replace

from lean_exposition.construction import CanonicalDeclLocator, DeclUnitSeed
from lean_exposition.models import Dependency
from .common import qualified_id


def merge_adapters(source, semantic):
    """Preserve source-only declarations while canonicalizing unique matches."""
    def values(decl):
        return {f.field: f.value for f in decl.fields if f.state == 'present'}
    indexed, ranges = {}, {}
    for decl in semantic.declarations:
        v = values(decl)
        indexed.setdefault((v.get('module'), v.get('lean_name')), []).append(decl.locator.ref)
        for span in v.get('source_refs', ()):
            ranges.setdefault((v.get('module'), span), []).append(decl.locator.ref)
    mapping, diagnostics = {}, list(source.diagnostics) + list(semantic.diagnostics)
    source_names = {}
    for decl in source.declarations:
        name = values(decl).get('lean_name')
        if name:
            source_names.setdefault(name, set()).add(decl.locator.ref)
    for decl in source.declarations:
        v = values(decl)
        candidates = indexed.get((v.get('module'), v.get('lean_name')), [])
        if len(candidates) != 1:
            candidates = list(dict.fromkeys(r for span in v.get('source_refs', ())
                                           for r in ranges.get((v.get('module'), span), ())))
        if len(candidates) == 1:
            mapping[decl.locator.ref] = candidates[0]
    # Compiled consumers may point into modules with source-only coverage.
    # Resolve those providers to the existing source node, not an unloaded twin.
    semantic_names = {values(d).get('lean_name') for d in semantic.declarations}
    for name, refs in source_names.items():
        if len(refs) == 1 and name not in semantic_names:
            ref = next(iter(refs))
            mapping.setdefault(type(ref)(ref.repo_key, name), ref)
    def remap(ref):
        return mapping.get(ref, ref)
    declarations = []
    for decl in (*source.declarations, *semantic.declarations):
        fields = []
        for f in decl.fields:
            value = f.value
            if f.field.endswith('.deps'):
                value = tuple(replace(dep, provider=remap(dep.provider)) for dep in value)
            if f.field == 'generated_from':
                value = remap(value)
            fields.append(replace(f, value=value))
        declarations.append(replace(decl, locator=CanonicalDeclLocator(remap(decl.locator.ref)), fields=tuple(fields)))
    def unique(items, key):
        result = {}
        for item in items:
            identity = key(item)
            if identity not in result:
                result[identity] = item
        return tuple(result.values())
    repositories = [replace(repo, primary_outcomes=tuple(remap(r) for r in repo.primary_outcomes))
                    for repo in source.repositories]
    repositories.extend(semantic.repositories)
    refs = dict.fromkeys(d.locator.ref for d in declarations)
    origins = {d.locator.ref: d.fields[0].provenance for d in declarations}
    return replace(source,
        repositories=unique(repositories, lambda x: x.repo_key),
        assets=unique((*source.assets, *semantic.assets), lambda x: x.asset_id),
        declarations=tuple(declarations),
        scopes=unique((*source.scopes, *semantic.scopes), lambda x: x.scope_id),
        units=tuple(DeclUnitSeed(qualified_id(r.repo_key, r.local_id), r, (), origins[r]) for r in refs),
        unit_aggregation='native_helpers',
        coverage=tuple(replace(c, ref=remap(c.ref)) for c in (*source.coverage, *semantic.coverage)),
        dependency_locks=unique((*source.dependency_locks, *semantic.dependency_locks),
                                lambda x: (x.repo_key, x.dependency_repo_key)),
        diagnostics=tuple(diagnostics), materials=source.materials + semantic.materials)
