#!/usr/bin/env python3
"""Load a fixed repository and construct the shared HDG without generating text."""
import argparse
import json
from pathlib import Path

from lean_exposition.importers import load_project
from lean_exposition.structure import build_hierarchy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--repo-key')
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--target-slice')
    parser.add_argument('--module', action='append')
    parser.add_argument('--source-root', action='append')
    parser.add_argument('--compiled-module', action='append')
    parser.add_argument('--source-backend', choices=('toolkit_text_ast', 'lean_interact'), default='toolkit_text_ast')
    parser.add_argument('--repl-rev')
    parser.add_argument('--local-repl-path', type=Path)
    parser.add_argument('--source-only', action='store_true')
    build_policy = parser.add_mutually_exclusive_group()
    build_policy.add_argument('--build', dest='build', action='store_true', default=None,
                              help='Run incremental builds even when artifacts exist')
    build_policy.add_argument('--no-build', dest='build', action='store_false',
                              help='Read existing artifacts only; report missing modules')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--memory-limit-mb', type=int)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if args.source_only and args.source_backend != 'toolkit_text_ast':
        parser.error('--source-only uses Toolkit; LeanInteract requires a Lean environment')
    if args.source_only and (args.build or args.compiled_module):
        parser.error('--source-only cannot be combined with --build or --compiled-module')
    bundle = load_project(args.root, repo_key=args.repo_key, profile=args.profile,
        target_slice=args.target_slice, modules=args.module, source_roots=args.source_root,
        compiled_modules=() if args.source_only else args.compiled_module,
        build=args.build, timeout=args.timeout, cache_dir=args.cache_dir, memory_limit_mb=args.memory_limit_mb,
        source_backend=args.source_backend, repl_rev=args.repl_rev, local_repl_path=args.local_repl_path)
    repo = next(r.repo_key for r in bundle.workspace.manifest.repositories if r.root_scope is not None)
    hierarchy = build_hierarchy(bundle, repo)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'bundle.json').write_text(bundle.to_json())
    (args.output_dir / 'hierarchy.json').write_text(json.dumps(hierarchy.to_dict(), ensure_ascii=False, indent=2))
    print(json.dumps({'repository': repo, 'declarations': len(bundle.workspace.declarations),
                      'nodes': len(hierarchy.nodes), 'diagnostics': len(bundle.diagnostics),
                      'semantic_acquisition': [d for d in bundle.diagnostics if d.startswith('compiled_acquisition:')],
                      'bundle_digest': bundle.digest()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
