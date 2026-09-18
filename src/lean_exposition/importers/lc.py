"""Convert the shared LC reader's fixed source records to Exposition facts."""
from __future__ import annotations

from functools import cached_property
import json

from lean_comprehend_bench.readers.lc import LCError, LCReader, LCRepositoryInput

from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, DependencyLock, Provenance, RawDecl,
    Repository, Scope, SourceAsset, SourceRange, Status, TextContent, ValidationError,
)
from lean_exposition.construction import CoverageContribution, RepositoryContext, build_repository
from .common import adapter_result_from_workspace, assemble_workspace, qualified_id


def _source_asset(record):
    return SourceAsset(qualified_id(record['repo'], record['path']), record['repo'],
                       record['path'], record['sha256'])


def _provenance(record, field=None):
    field = record['field'] if field is None else field
    return (Provenance('lc_current', f"git:{record['revision']}:{record['path']}#{field}"),)


def _workspace(reader):
    """Map shared records to domain types; catalog interpretation stays in Bench."""
    info = reader.info()
    assets, repositories, scopes, declarations, locks, external = {}, [], [], [], [], {}
    loaded = {r['repo'] for r in info['repositories']}

    def register(record):
        asset = _source_asset(record)
        assets[asset.asset_id] = asset
        return asset

    for repo in info['repositories']:
        key, toolchain = repo['repo'], repo['lean_toolchain']
        for record in repo['provenance']:
            register(record)
        repositories.append(Repository(
            key, toolchain, qualified_id(key, 'Main'), revision=repo['revision'],
            primary_outcomes=tuple(DeclRef(e['target']['repo'], e['target']['id'])
                                   for e in repo['exports'] if e['resolution'] == 'loaded'),
        ))
        manifest = next((p for p in repo['provenance'] if p['path'] == 'lake-manifest.json'), None)
        for package in repo['lake_manifest'].get('packages', []):
            name, revision = package['name'], package.get('rev')
            if name == key:
                continue
            if name not in loaded and revision:
                external[name] = Repository(name, toolchain, None, revision=revision)
            if name in loaded or revision:
                locks.append(DependencyLock(key, name, _provenance(manifest, name)))
    for scope in reader.list_scopes():
        key, path = scope['repo'], scope['path']
        register(scope['provenance'][0])
        scopes.append(Scope(
            qualified_id(key, path), key, 'repository' if path == 'Main' else scope['node']['kind'],
            key if path == 'Main' else path.rsplit('.', 1)[-1], _provenance(scope['provenance'][0]),
            None if path == 'Main' else qualified_id(key, path.rsplit('.', 1)[0]),
        ))
    for material in reader.list_materials():
        if material['kind'] == 'source_corpus':
            register(material['provenance'][0])
    for record in reader.list_declarations():
        content = reader.declaration_content(record['id'], repo=record['repo'])
        for source in content['provenance']:
            register(source)
        current = content['provenance'][1]
        provenance = _provenance(current)
        source_refs, source_context = [], []
        if content['source'] is not None:
            source = content['source']
            asset = register(source['provenance'])
            source_context.append(TextContent(source['text'], 'present', _provenance(source['provenance'])))
            if source['managed_source_start_line'] is not None:
                source_refs.append(SourceRange(asset.asset_id, source['managed_source_start_line'], None,
                                               source['managed_source_end_line'], None))

        def part(label):
            data = content[label]
            if data is None:
                return None

            def text(field):
                value = data[field]
                prov = _provenance(value['provenance'])
                if value['status'] == 'missing':
                    return TextContent(None, 'missing', prov, 'Not present in current LC record.')
                for origin in content['origins']:
                    if origin['field'] != f'{label}.{field}':
                        continue
                    if origin['status'] in {'integrity_mismatch', 'invalid_range'}:
                        continue
                    ranges = ()
                    raw = origin['origin']
                    if origin['status'] == 'available':
                        material = next(p for p in origin['provenance'] if p['path'] == origin['path'])
                        asset = register(material)
                        if raw.get('start_line') is not None and raw.get('end_line') is not None:
                            ranges = (SourceRange(asset.asset_id, raw['start_line'], None, raw['end_line'], None),)
                    prov += (Provenance('lc_origin', json.dumps(raw, ensure_ascii=False, sort_keys=True), ranges),)
                check = value['check']
                status = Status(check['status'], _provenance(current, f'{label}.{field}.check')) if check and check.get('status') else None
                return TextContent(value['text'], 'present', prov, check=status)

            deps = []
            for edge in data['dependencies']:
                if edge['target'] is None:
                    raise ValidationError(f"unsupported LC declaration dependency: {edge['dependency']['kind']}")
                target = DeclRef(edge['target']['repo'], edge['target']['id'])
                deps.append(Dependency(target, 'lc_declared', (Provenance(
                    'lc_dependency', f"git:{current['revision']}:{current['path']}#{label}.deps:" +
                    json.dumps(edge['dependency'], ensure_ascii=False, sort_keys=True)),)))
            return DeclContent(text('nl'), text('formal'), tuple(deps))

        declarations.append(RawDecl(
            DeclRef(content['repo'], content['id']), content['lean_name'], content['module'],
            qualified_id(content['repo'], content['scope']), content['kind'],
            part('statement'), Status('imported', provenance),
            _provenance(content['provenance'][0]) + provenance,
            Status(content['state'], provenance) if content['state'] else None, part('proof'),
            source_refs=tuple(source_refs), source_context=tuple(source_context), local_public=content['public'],
        ))
    return assemble_workspace(repositories=[*repositories, *external.values()], declarations=declarations,
                              scopes=scopes, assets=assets.values(), dependency_locks=locks)


class LCRepositoryAdapter:
    """LC domain adapter over LeanComprehendBench; no source AST or LC runtime."""

    def __init__(self, main: LCRepositoryInput, providers=()):
        self.main = main
        self.providers = tuple(providers)

    @cached_property
    def reader(self):
        try:
            return LCReader(self.main.path, self.main.revision or 'HEAD',
                            repo_key=self.main.repo_key, providers=self.providers)
        except LCError as exc:
            raise ValidationError(str(exc)) from exc

    def read_source(self, path):
        """Read profile assets from the same snapshot as collected declarations."""
        try:
            return self.reader.read_source(path)
        except LCError as exc:
            raise ValidationError(str(exc)) from exc

    def collect(self, context: RepositoryContext | None = None):
        try:
            workspace = _workspace(self.reader)
            coverage = tuple(CoverageContribution(decl.ref, name, 'lc_declared', 'complete', decl.provenance)
                             for decl in workspace.declarations
                             for name, part in (('statement', decl.statement), ('proof', decl.proof))
                             if part is not None)
            diagnostics = tuple('lc_reader:' + json.dumps(d, ensure_ascii=False, sort_keys=True)
                                for d in self.reader.info()['diagnostics'])
            adapter = adapter_result_from_workspace(
                workspace, unit_aggregation='preserve', authority='lc_catalog', method='lc_catalog',
                coverage=coverage, source_texts=(), diagnostics=diagnostics,
            )
            from .materials import lc_resources
            return lc_resources(adapter, self.reader)
        except LCError as exc:
            raise ValidationError(str(exc)) from exc


def load_lc_workspace(main: LCRepositoryInput, providers=()):
    """Load current LC catalog facts into the canonical construction bundle."""
    return build_repository(LCRepositoryAdapter(main, providers).collect())
