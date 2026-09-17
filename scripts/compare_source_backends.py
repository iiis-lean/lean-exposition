#!/usr/bin/env python3
"""Compare source backends against one fixed compiled export; never build."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from lean_exposition.lean import extract_modules
from lean_exposition.lean.tools import extract_sources
from lean_exposition.importers.native import normalize_native


def compare(root, modules, output, *, local_repl_path=None, repl_rev=None, timeout=180):
    root, output = Path(root).resolve(), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    semantic = extract_modules(root, tuple(modules), include_source=False, timeout=timeout)
    result = {'root': str(root), 'modules': modules, 'toolchain': semantic['toolchain'],
              'source_digests': semantic['source_digests'],
              'semantic_seconds': time.perf_counter() - started, 'backends': {}}
    (output / 'semantic.json').write_text(json.dumps(semantic, ensure_ascii=False))
    workspaces = {}
    for backend in ('toolkit_text_ast', 'lean_interact'):
        start = time.perf_counter()
        try:
            source = extract_sources(root, tuple(modules), source_backend=backend, timeout=timeout,
                                     local_repl_path=local_repl_path, repl_rev=repl_rev)
            elapsed = time.perf_counter() - start
            payload = dict(semantic, source=source, source_backend=backend)
            (output / (backend + '.json')).write_text(json.dumps(payload, ensure_ascii=False))
            ws = normalize_native(root, repo_key='comparison', modules=tuple(modules), payload=payload)
            workspaces[backend] = {d.lean_name: d for d in ws.declarations}
            result['backends'][backend] = {
                'source_seconds': elapsed, 'source_commands': sum(len(v['declarations']) for v in source.values()),
                'compiled_declarations': len(ws.declarations),
                'mapped': sum(d.extraction_status.state == 'extracted' for d in ws.declarations),
                'statement_formal': sum(d.statement.formal.text is not None for d in ws.declarations),
                'proof_formal': sum(d.proof is not None and d.proof.formal.text is not None for d in ws.declarations),
                'docstrings': sum(d.statement.nl.text is not None for d in ws.declarations),
            }
        except Exception as exc:
            result['backends'][backend] = {'error': str(exc), 'seconds': time.perf_counter() - start}
    if len(workspaces) == 2:
        left, right = workspaces.values()
        differences = []
        def projection(d):
            return {'kind': d.kind, 'mapped': d.extraction_status.state == 'extracted',
                    'statement': d.statement.formal.text, 'proof': d.proof.formal.text if d.proof else None,
                    'docstring': d.statement.nl.text,
                    'scope': [t.text for t in d.source_context if any(p.method.endswith('_scope') for p in t.provenance)],
                    'deps': [(part, dep.provider.repo_key, dep.provider.local_id, dep.evidence_kind)
                             for part, c in [('statement', d.statement), ('proof', d.proof)] if c for dep in c.deps]}
        for name in sorted(set(left) | set(right)):
            a, b = projection(left[name]), projection(right[name])
            diff = {key: {'toolkit': a[key], 'lean_interact': b[key]} for key in a if a[key] != b[key]}
            if diff:
                differences.append({'name': name, 'fields': diff})
        result['different_declarations'] = len(differences)
        result['different_fields'] = {key: sum(key in d['fields'] for d in differences)
                                      for key in ('kind', 'mapped', 'statement', 'proof', 'docstring', 'scope', 'deps')}
        (output / 'differences.json').write_text(json.dumps(differences, ensure_ascii=False, indent=2))
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--module', action='append', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--local-repl-path')
    parser.add_argument('--repl-rev')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    print(json.dumps(compare(args.root, args.module, args.output_dir,
          local_repl_path=args.local_repl_path, repl_rev=args.repl_rev, timeout=args.timeout), ensure_ascii=False))
