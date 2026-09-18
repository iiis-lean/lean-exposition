"""Material contributions shared by LC resources and native project profiles."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

from lean_exposition.construction import (
    CanonicalDeclLocator, DeclarationContribution, FieldContribution,
    MaterialBinding, MaterialBundle, MaterialRecord, MaterialTarget,
)
from lean_exposition.models import DeclRef, Provenance, SourceRange, TextContent

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


def lc_resources(adapter, reader):
    """Convert shared, verified LC sections and bindings to material bundles."""
    from lean_comprehend_bench.readers.lc import IMPLEMENTATION_DIGEST, LCError
    from .lc import _source_asset
    implementation = hashlib.sha256((IMPLEMENTATION + IMPLEMENTATION_DIGEST).encode()).hexdigest()
    bundles, diagnostics = [], list(adapter.diagnostics)
    for material in reader.list_materials():
        if material['kind'] != 'resource' or material['status'] != 'available':
            continue
        path, repo = material['path'], material['repo']
        try:
            shared_sections = reader.material_sections(path, repo=repo)
        except LCError as exc:
            diagnostics.append(f'lc_material_unavailable:{repo}:{path}:{exc}')
            continue
        asset = _source_asset(next(p for p in material['provenance'] if p['path'] == path))
        config = {'parser': material['entry']['readable_kind']}
        config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

        def record(section, *, excerpt=False):
            lines = section['text'].splitlines()
            start, end = section['start_line'], section['end_line']
            span = SourceRange(asset.asset_id, start, 1, end, len(lines[-1]) + 1)
            method = 'lc_resource' if excerpt else 'material_' + config['parser']
            prov = (Provenance(method, path, (span,)),)
            occurrence = f'origin:{start}:{end}' if excerpt else f'section:{start - 1}'
            return MaterialRecord.create(asset=asset, occurrence_id=occurrence,
                parser_implementation_digest=implementation, parser_config_digest=config_digest,
                source_range=span, heading=None if excerpt else lines[0],
                text='\n'.join(lines), provenance=prov)

        sections = tuple(record(section) for section in shared_sections)
        records = {r.record_id: r for r in sections}
        bindings = []
        for link in reader.material_bindings(path, repo=repo):
            if link['status'] != 'available':
                continue
            target = link['target']
            if 'id' in target:
                target = MaterialTarget('declaration', target['repo'], ref=DeclRef(target['repo'], target['id']))
            else:
                target = MaterialTarget('repository', target['repo'], target['repo'])
            if link['start_line'] is not None:
                selected = (record(reader.read_material(path, repo=repo,
                    start_line=link['start_line'], end_line=link['end_line']), excerpt=True),)
            else:
                selected = sections
            for item in selected:
                records[item.record_id] = item
                bindings.append(MaterialBinding(item.record_id, target, link['relation'], 'exact', item.provenance))
        bundles.append(MaterialBundle.create(repo_key=repo, assets=(asset,),
            records=tuple(sorted(records.values(), key=lambda r: r.record_id)),
            bindings=tuple(sorted(set(bindings), key=lambda b: (b.record_id, b.target.canonical_key(), b.relation, b.status))),
            parser_implementation_digest=implementation, parser_config=config,
            binder_implementation_digest=implementation, binder_config={}))
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
