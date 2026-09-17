"""Material contributions shared by LC resources and native project profiles."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from lean_exposition.construction import (
    CanonicalDeclLocator, DeclarationContribution, FieldContribution,
    MaterialBinding, MaterialBundle, MaterialRecord, MaterialTarget,
)
from lean_exposition.models import Provenance, SourceRange, TextContent

IMPLEMENTATION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def text_material(asset, text, *, bindings=(), parser='plain'):
    """Keep readable sections, explicit bindings, and original bytes' identity."""
    config = {'parser': parser}
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r'^#{1,6}\s|^\\(?:sub)*section\b', line)]
    starts = sorted(set([0, *starts])) if lines else [0]
    records, links = [], []
    for start, end in zip(starts, [*starts[1:], len(lines)]):
        body = '\n'.join(lines[start:end])
        span = SourceRange(asset.asset_id, start + 1, 1, max(start + 1, end),
                           len(lines[end - 1]) + 1 if end else 1)
        prov = (Provenance('material_' + parser, asset.path, (span,)),)
        record = MaterialRecord.create(asset=asset, occurrence_id=f'section:{start}',
            parser_implementation_digest=IMPLEMENTATION, parser_config_digest=config_digest,
            source_range=span, heading=lines[start] if start < len(lines) else None,
            text=body, provenance=prov)
        records.append(record)
        for ref in bindings:
            links.append(MaterialBinding(record.record_id, MaterialTarget('declaration', ref.repo_key, ref=ref),
                                         'explains', 'exact', prov))
    return MaterialBundle.create(repo_key=asset.repo_key, assets=(asset,), records=tuple(records),
        bindings=tuple(links), parser_implementation_digest=IMPLEMENTATION, parser_config=config,
        binder_implementation_digest=IMPLEMENTATION, binder_config={})


def attach_materials(adapter, bundles):
    """Attach exact local text for all readers; retain unbound records in bundle."""
    bundles = tuple(bundles)
    additions, assets = [], {a.asset_id: a for a in adapter.assets}
    existing = {d.locator.ref for d in adapter.declarations if isinstance(d.locator, CanonicalDeclLocator)}
    proof_refs = {d.locator.ref for d in adapter.declarations
                  if isinstance(d.locator, CanonicalDeclLocator) and any(f.field.startswith('proof.') for f in d.fields)}
    for bundle in bundles:
        bundle.validate()
        assets.update((a.asset_id, a) for a in bundle.assets)
        records = {r.record_id: r for r in bundle.records}
        for binding in bundle.bindings:
            if binding.status != 'exact' or binding.target.ref not in existing:
                continue
            if binding.relation == 'paper_label':
                continue  # Ordering anchors are not additional prose copies.
            record = records[binding.record_id]
            if not record.text:
                continue
            prov = (Provenance('material_binding', record.record_id,
                               (record.source_range,) if record.source_range else ()),) + binding.provenance
            text = TextContent(record.text, 'present', prov)
            fields = [FieldContribution('source_context', 'present', (text,), 'project_metadata',
                                        'materials', 'material_binding', prov)]
            # Only explicit statement/proof relationships fill core NL. A
            # general explanation or nearby paper paragraph stays additional.
            part = {'states': 'statement', 'proof_route': 'proof'}.get(binding.relation)
            if part and (part == 'statement' or binding.target.ref in proof_refs):
                fields.append(FieldContribution(part + '.nl', 'present', text, 'project_metadata',
                                                 'materials', 'material_binding', prov))
            additions.append(DeclarationContribution(CanonicalDeclLocator(binding.target.ref), tuple(fields)))
    return replace(adapter, assets=tuple(assets.values()), declarations=adapter.declarations + tuple(additions),
                   materials=adapter.materials + bundles)


def lc_resources(adapter, snapshots):
    """Read immutable LC resource manifests; no LC runtime or checkout writes."""
    bundles, diagnostics = [], list(adapter.diagnostics)
    for snapshot in snapshots:
        repo_key = snapshot.source.repo_key
        origins = {}
        for d in adapter.declarations:
            if d.locator.ref.repo_key != repo_key:
                continue
            for f in d.fields:
                if f.field not in {'statement.nl', 'proof.nl', 'statement.formal', 'proof.formal'}:
                    continue
                for prov in f.provenance:
                    if prov.method != 'lc_origin':
                        continue
                    origin = json.loads(prov.source_ref)
                    key = origin.get('resource_key')
                    if key:
                        origins.setdefault(key, []).append((d.locator.ref, origin))
        for path in sorted(snapshot.paths):
            if not path.startswith('.lean_constellation/resources/items/') or not path.endswith('/manifest.json'):
                continue
            base = path.rsplit('/', 1)[0]
            key = base.rsplit('/', 1)[1]
            try:
                manifest = snapshot.json(path)
                metadata_path = base + '/resource.json'
                if metadata_path in snapshot.paths:
                    key = snapshot.json(metadata_path).get('resource_key', key)
                for entry in manifest.get('files', []):
                    relative = PurePosixPath(entry['path'])
                    if relative.is_absolute() or '..' in relative.parts:
                        diagnostics.append(f'lc_resource_unsafe_path:{path}:{relative}')
                        continue
                    file_path = base + '/' + str(relative)
                    try:
                        raw = snapshot.read(file_path)
                        asset = snapshot.assets[file_path]
                        if asset.sha256 != entry['sha256'] or len(raw) != entry['size_bytes']:
                            diagnostics.append(f'lc_resource_digest_mismatch:{file_path}')
                            continue
                        if not entry.get('readable_kind') or relative.suffix.lower() in {'.eps', '.ps', '.svg'}:
                            continue
                        text = raw.decode('utf-8')
                        local = [(ref, origin) for ref, origin in origins.get(key, ())
                                 if (origin.get('source_path') == str(relative) or
                                     (not origin.get('source_path') and str(relative) == manifest.get('canonical_entry')))]
                        bundle = text_material(asset, text, parser=entry['readable_kind'])
                        links, extra_records = [], []
                        for ref, origin in local:
                            start, end = origin.get('start_line'), origin.get('end_line')
                            if start is not None and end is not None:
                                lines = text.splitlines()
                                if not 1 <= start <= end <= len(lines):
                                    diagnostics.append(f'lc_resource_invalid_range:{file_path}:{start}:{end}')
                                    continue
                                span = SourceRange(asset.asset_id, start, 1, end, len(lines[end - 1]) + 1)
                                prov = (Provenance('lc_resource', file_path, (span,)),)
                                record = MaterialRecord.create(asset=asset, occurrence_id=f'origin:{start}:{end}',
                                    parser_implementation_digest=IMPLEMENTATION,
                                    parser_config_digest=bundle.parser_config_digest, source_range=span,
                                    text='\n'.join(lines[start - 1:end]), provenance=prov)
                                extra_records.append(record)
                                selected = (record,)
                            else:
                                selected = bundle.records
                            for record in selected:
                                links.append(MaterialBinding(record.record_id, MaterialTarget('declaration', repo_key, ref=ref),
                                                             'explains', 'exact', record.provenance))
                        bundle = replace(bundle, records=tuple(sorted({r.record_id: r for r in (*bundle.records, *extra_records)}.values(),
                                                                     key=lambda r: r.record_id)))
                        if not links:
                            links = [MaterialBinding(r.record_id, MaterialTarget('repository', repo_key, repo_key),
                                                     'explains', 'exact', r.provenance) for r in bundle.records]
                        bundles.append(replace(bundle, bindings=tuple(sorted(set(links), key=lambda b: (b.record_id, b.target.canonical_key(), b.relation, b.status)))))
                    except (OSError, ValueError, KeyError, UnicodeError) as exc:
                        diagnostics.append(f'lc_resource_failed:{file_path}:{exc}')
            except (OSError, ValueError, KeyError) as exc:
                diagnostics.append(f'lc_resource_failed:{path}:{exc}')
    return attach_materials(replace(adapter, diagnostics=tuple(diagnostics)), bundles)


def bind_blueprint(bundle, declarations, label_declarations=None):
    """Bind explicit TeX ``\u005clean{...}``/label mappings to full math environments.

    No fuzzy theorem-name matching is attempted. Adjacent proof environments
    inherit the preceding statement's explicit targets within the same document.
    """
    mappings = label_declarations or {}
    config = {'source': bundle.digest(), 'labels': mappings}
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    records, bindings, mapped = [], [], {}
    by_id = {r.record_id: r for r in bundle.records}
    assets = {a.asset_id: a for a in bundle.assets}
    def copy(record):
        if record.record_id in mapped:
            return mapped[record.record_id]
        parent = copy(by_id[record.parent_record_id]).record_id if record.parent_record_id else None
        new = MaterialRecord.create(asset=assets[record.asset_id], occurrence_id=record.occurrence_id,
            parser_implementation_digest=IMPLEMENTATION, parser_config_digest=config_digest,
            source_range=record.source_range, heading=record.heading, label=record.label,
            parent_record_id=parent, text=record.text, payload=record.payload, provenance=record.provenance)
        mapped[record.record_id] = new
        records.append(new)
        return new
    for record in bundle.records:
        copy(record)
    groups = {}
    for record in bundle.records:
        tex = (record.payload or {}).get('tex')
        if tex:
            groups.setdefault(tex['root'], []).append(record)
    for root, rows in groups.items():
        rows.sort(key=lambda r: r.payload['tex']['logical_position'])
        active, environment, targets, previous = [], None, set(), set()
        for row in rows:
            text = row.text or ''
            start = re.search(r'\\begin\{(theorem|lemma|proposition|corollary|definition|proof)\}', text)
            if start:
                active, environment = [], start.group(1)
                targets = set(previous) if environment == 'proof' else set()
            if environment is None:
                continue
            active.append(row)
            if row.label in mappings:
                name = mappings[row.label]
                if name in declarations:
                    targets.add(declarations[name])
            for match in re.finditer(r'\\lean\{([^}]+)\}', text):
                targets.update(declarations[name] for name in re.split(r'[,\s]+', match.group(1).strip())
                               if name in declarations)
            if '\\end{' + environment + '}' not in text:
                continue
            if targets:
                first = active[0]
                prov = tuple(dict.fromkeys(p for r in active for p in r.provenance))
                record = MaterialRecord.create(asset=assets[first.asset_id],
                    occurrence_id=f'blueprint:{root}:{first.occurrence_id}',
                    parser_implementation_digest=IMPLEMENTATION, parser_config_digest=config_digest,
                    text='\n'.join(r.text or '' for r in active), payload={'blueprint_environment': environment},
                    provenance=prov)
                records.append(record)
                for ref in sorted(targets, key=lambda r: (r.repo_key, r.local_id)):
                    target = MaterialTarget('declaration', bundle.repo_key, ref=ref)
                    bindings.append(MaterialBinding(record.record_id, target,
                        'proof_route' if environment == 'proof' else 'states', 'exact', prov))
                    bindings.extend(MaterialBinding(mapped[r.record_id].record_id, target, 'paper_label', 'exact', r.provenance)
                                    for r in active)
            if environment != 'proof':
                previous = set(targets)
            environment, active = None, []
    return MaterialBundle.create(repo_key=bundle.repo_key, assets=bundle.assets, records=tuple(records),
        bindings=tuple(bindings), parser_implementation_digest=IMPLEMENTATION, parser_config=config,
        binder_implementation_digest=IMPLEMENTATION, binder_config={'labels': mappings}, diagnostics=bundle.diagnostics)
