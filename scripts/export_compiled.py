#!/usr/bin/env python3
"""Export checkpointed compiled facts or replay them offline into a build bundle.

No command builds a project or invokes Lake. Queue plans freeze explicit module
lists and exclusions; a paused queue requires an intentional resume invocation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from lean_exposition.lean.tools import (
    _atomic_json, _sha, export_compiled, extract_sources, load_compiled,
)


def replay(project, package, output, repo_key):
    from lean_exposition.importers.native import NativeRepositoryAdapter
    from lean_exposition.construction import build_repository
    start = time.monotonic()
    payload = load_compiled(project, package)
    modules = tuple(payload['source_digests'])
    payload.update(source=extract_sources(project, modules), source_backend='toolkit_text_ast')
    bundle = build_repository(NativeRepositoryAdapter(project, repo_key=repo_key, modules=modules, payload=payload).collect())
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(bundle.to_json())
    return dict(status='complete', seconds=time.monotonic() - start, declarations=len(bundle.workspace.declarations),
                bundle_bytes=Path(output).stat().st_size)


def queue(plan, output, task_timeout):
    plan_path, output = Path(plan), Path(output)
    specification = json.loads(plan_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    # In a frozen run this fingerprint binds exactly the loaded exporter snapshot.
    import lean_exposition.lean.tools as exporter
    implementation = {str(path): _sha(path) for path in (
        Path(exporter.__file__), Path(exporter.__file__).with_name('environment.lean'), Path(__file__))}
    previous = output / 'queue.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        if old['plan_sha256'] != _sha(plan_path) or old['implementation'] != implementation:
            raise ValueError('queue plan or frozen exporter changed')
    report = dict(status='running', plan_sha256=_sha(plan_path), implementation=implementation, projects=[])
    _atomic_json(previous, report)
    started = time.monotonic()
    for project in specification['projects']:
        if time.monotonic() - started >= task_timeout:
            report.update(status='paused', reason='queue_task_timeout')
            break
        modules = tuple(project['modules'])
        try:
            result = export_compiled(project['root'], modules, output / project['name'],
                lean_binary=project['lean'], batch_size=project.get('batch_size', 1),
                timeout=project.get('timeout', 180), memory_limit_mb=specification.get('memory_limit_mb', 12288),
                shared_rss_limit_bytes=specification.get('shared_rss_limit_bytes', 100 * 1024**3),
                task_timeout=min(project.get('task_timeout', task_timeout), task_timeout - (time.monotonic() - started)),
                min_free_bytes=specification.get('min_free_bytes', 10 * 1024**3))
            item = dict(name=project['name'], status=result['status'], requested=len(modules),
                        completed=len(result['completed']), failed=result['failed'], excluded=project.get('excluded', []),
                        seconds=result['last_run_seconds'], manifest=str(output / project['name'] / 'manifest.json'))
        except Exception as exc:
            item = dict(name=project['name'], status='failed', requested=len(modules), completed=0,
                        error=f'{type(exc).__name__}: {exc}', excluded=project.get('excluded', []))
            result = {'status': 'failed'}
        report['projects'].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        _atomic_json(previous, report)
        if result['status'] == 'paused':
            report.update(status='paused', reason=result.get('pause_reason'))
            break
    if report['status'] != 'paused':
        report['status'] = 'complete' if all(p['status'] == 'complete' for p in report['projects']) else 'incomplete'
    report['seconds'] = time.monotonic() - started
    _atomic_json(previous, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--project', required=True)
    export.add_argument('--modules-json', required=True, help='JSON array of exact module names')
    export.add_argument('--output', required=True)
    export.add_argument('--lean', required=True, help='absolute matching Lean executable')
    export.add_argument('--batch-size', type=int, default=1)
    export.add_argument('--timeout', type=float, default=180)
    export.add_argument('--task-timeout', type=float, default=86400)
    export.add_argument('--memory-mb', type=int, default=12288)
    offline = commands.add_parser('replay')
    offline.add_argument('--project', required=True)
    offline.add_argument('--package', required=True)
    offline.add_argument('--output', required=True)
    offline.add_argument('--repo-key', required=True)
    batch = commands.add_parser('queue')
    batch.add_argument('--plan', required=True)
    batch.add_argument('--output', required=True)
    batch.add_argument('--task-timeout', type=float, default=86400)
    args = parser.parse_args()
    if args.command == 'export':
        result = export_compiled(args.project, tuple(json.loads(Path(args.modules_json).read_text())), args.output,
            lean_binary=args.lean, batch_size=args.batch_size, timeout=args.timeout,
            memory_limit_mb=args.memory_mb, task_timeout=args.task_timeout, shared_rss_limit_bytes=100 * 1024**3)
        print(json.dumps({k: result[k] for k in ('status', 'requested_modules', 'failed')}, indent=2))
    elif args.command == 'replay':
        result = replay(args.project, args.package, args.output, args.repo_key)
        print(json.dumps(result))
    else:
        result = queue(args.plan, args.output, args.task_timeout)
    return 0 if result['status'] == 'complete' else (75 if result['status'] == 'paused' else 2)


if __name__ == '__main__':
    sys.exit(main())
