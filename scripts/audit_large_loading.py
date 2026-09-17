#!/usr/bin/env python3
"""Measure real loading and HDG stages without builds or text generation."""
import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path
import resource
import time

from lean_exposition.importers import load_project
from lean_exposition.structure import build_hierarchy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--slice')
    parser.add_argument('--source-root', action='append')
    parser.add_argument('--compiled-module', action='append', default=[])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = {'root': args.root, 'profile': args.profile, 'stages': {},
              'implementation': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (Path(__file__), Path('src/lean_exposition/importers/project.py'),
                   Path('src/lean_exposition/models/facts.py'), Path('src/lean_exposition/construction/profiles.py'))}}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save(stage, values):
        result['stages'][stage] = values
        result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print(stage, json.dumps({k:v for k,v in values.items() if k != 'diagnostics'}, ensure_ascii=False), flush=True)

    import lean_exposition.importers.project as project
    for name in ('add_text_references', 'merge_adapters', '_profile_materials', 'build_repository'):
        original = getattr(project, name)
        def measured(*a, _fn=original, _name=name, **kw):
            began = time.monotonic()
            value = _fn(*a, **kw)
            print('phase', _name, time.monotonic()-began, flush=True)
            return value
        setattr(project, name, measured)
    start = time.monotonic()
    bundle = load_project(args.root, profile=args.profile, target_slice=args.slice,
                          source_roots=args.source_root, compiled_modules=args.compiled_module,
                          build=False, timeout=120, memory_limit_mb=4096)
    ws = bundle.workspace
    refs = {d.ref for d in ws.declarations}
    bindings = [b for m in bundle.materials for b in m.bindings]
    missing = [str(b.target.ref) for b in bindings if b.target.ref and b.target.ref not in refs]
    save('load', {'seconds': time.monotonic()-start, 'declarations': len(refs),
        'modules': len({d.module for d in ws.declarations}), 'units': len(ws.units),
        'statement_nl': sum(bool(d.statement.nl.text) for d in ws.declarations),
        'formal_statement': sum(bool(d.statement.formal.text) for d in ws.declarations),
        'formal_proof': sum(bool(d.proof and d.proof.formal.text) for d in ws.declarations),
        'dependencies': sum(len(d.statement.deps)+(len(d.proof.deps) if d.proof else 0) for d in ws.declarations),
        'material_records': sum(len(m.records) for m in bundle.materials),
        'material_bindings': len(bindings), 'bindings_by_status': dict(Counter(b.status for b in bindings)),
        'missing_binding_targets': missing, 'diagnostics': list(bundle.diagnostics)})
    assert not missing, missing[:5]
    start = time.monotonic()
    repo = next(r.repo_key for r in ws.manifest.repositories if r.root_scope is not None)
    hierarchy = build_hierarchy(bundle, repo)
    save('hdg', {'seconds': time.monotonic()-start, 'nodes': len(hierarchy.nodes)})


if __name__ == '__main__':
    main()
