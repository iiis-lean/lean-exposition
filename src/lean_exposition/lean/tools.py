"""Read compiled environments independently of source extraction and building."""
from __future__ import annotations

import hashlib
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import tempfile


def _run(command, *, cwd, timeout):
    """Terminate the Lake child process as well if a bounded query times out."""
    with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _query(command, *, cwd, timeout):
    """Spool Lean output to disk instead of retaining a second whole JSON copy."""
    with tempfile.TemporaryFile(mode='w+') as output:
        with subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                              text=True, start_new_session=True) as process:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        output.seek(0)
        facts, messages = [], []
        marker = 'LEAN_EXPOSITION_JSON '
        for line in output:
            if marker in line:
                facts.append(json.loads(line.split(marker, 1)[1]))
            elif len(messages) < 100:
                messages.append(line)
        if process.returncode:
            raise RuntimeError('compiled environment extraction failed:\n' + ''.join(messages))
        return facts


def _inputs(root, modules):
    paths = [root / 'lean-toolchain', *(root / (m.replace('.', '/') + '.lean') for m in modules)]
    paths += [root / n for n in ('lake-manifest.json', 'lakefile.toml', 'lakefile.lean') if (root / n).is_file()]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _artifact_stamp(root):
    # Local cache only: paths, sizes and nanosecond mtimes cover local and package
    # artifacts, including module-system private/server companions. Not a remote
    # attestation that sources and binaries match.
    result = []
    def visit(directory):
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                visit(entry.path)
            elif '.olean' in entry.name:
                stat = entry.stat()
                result.append((str(Path(entry.path).relative_to(root)), stat.st_size, stat.st_mtime_ns))
    visit(root / '.lake/build/lib/lean')
    packages = root / '.lake/packages'
    if packages.is_dir():
        for package in packages.iterdir():
            visit(package / '.lake/build/lib/lean')
    # Lake configuration and small custom artifact layouts.
    if (root / '.lake').is_dir():
        for p in (root / '.lake').iterdir():
            if p.is_file() and '.olean' in p.name:
                stat = p.stat()
                result.append((str(p.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return sorted(result)


def extract_sources(project, modules, *, source_backend="toolkit_text_ast", timeout=300,
                    repl_rev=None, local_repl_path=None, cache_dir=None):
    """Return the common author-command contract without querying semantic facts."""
    root = Path(project).resolve()
    if source_backend == 'toolkit_text_ast':
        from lean_exposition.importers.toolkit import source_authors
        return {module: source_authors((root / (module.replace('.', '/') + '.lean')).read_text(),
                                      module, cache_dir=cache_dir) for module in modules}
    if source_backend != 'lean_interact':
        raise ValueError('unknown source backend: ' + source_backend)
    toolchain = (root / 'lean-toolchain').read_text().strip()
    if repl_rev is None:
        repl_rev = {'leanprover/lean4:v4.28.0': 'v1.3.14',
                    'leanprover/lean4:v4.32.0': 'v1.3.18'}.get(toolchain)
        if repl_rev is None:
            raise ValueError('supply repl_rev for this LeanInteract toolchain: ' + toolchain)
    if local_repl_path is not None:
        local_repl_path = Path(local_repl_path).resolve()
        if (local_repl_path / 'lean-toolchain').read_text().strip() != toolchain:
            raise ValueError('local REPL toolchain does not match project toolchain')
    from lean_interact import FileCommand, LeanREPLConfig, LeanServer
    from lean_interact.project import LocalProject
    server = LeanServer(LeanREPLConfig(project=LocalProject(directory=root, auto_build=False),
                                      repl_rev=repl_rev, local_repl_path=local_repl_path))
    source = {}
    try:
        for module in modules:
            response = server.run(FileCommand(path=str(root / (module.replace('.', '/') + '.lean')),
                                              declarations=True), timeout=timeout)
            data = response.model_dump(by_alias=True, exclude_none=False)
            errors = [m for m in data.get('messages', []) if m.get('severity') == 'error']
            if errors or 'declarations' not in data:
                raise RuntimeError(f'LeanInteract source extraction failed for {module}: {errors}')
            source[module] = data
    finally:
        server.kill()
    return source


def extract_modules(project: str | Path, modules: tuple[str, ...], *, timeout: int = 300,
                    repl_rev: str | None = None, local_repl_path: str | Path | None = None,
                    evidence_dir: str | Path | None = None, build: bool = False,
                    include_source: bool = True, cache_dir: str | Path | None = None,
                    _artifact_state=None, memory_limit_mb: int | None = None,
                    source_backend="toolkit_text_ast") -> dict:
    """Read compiled facts and select Toolkit or optional LeanInteract source extraction.

    Semantic module queries are isolated; source extraction shares one REPL per
    call when LeanInteract is selected. Builds remain explicit at this layer.
    """
    if source_backend not in {'toolkit_text_ast', 'lean_interact'}:
        raise ValueError('unknown source backend: ' + source_backend)
    if memory_limit_mb is not None and memory_limit_mb <= 0:
        raise ValueError("memory_limit_mb must be positive")
    root = Path(project).resolve()
    if not modules or len(set(modules)) != len(modules):
        raise ValueError('modules must be a nonempty sequence without duplicates')
    for module in modules:
        if not re.fullmatch(r"[^\W\d][\w']*(?:\.[^\W\d][\w']*)*", module):
            raise ValueError(f'unsupported module identifier: {module}')
    initial = _inputs(root, modules)
    toolchain = (root / 'lean-toolchain').read_text().strip()
    if build:
        result = _run(['lake', 'build', *('+' + m for m in modules)], cwd=root, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'selected-module build failed:\n{result.stdout}\n{result.stderr}')
    template = Path(__file__).with_name('environment.lean').read_text()
    stamp = (_artifact_state if _artifact_state is not None else _artifact_stamp(root)) if cache_dir else None
    compiled, source = [], {}
    for module in modules:
        query = f'import {module}\n' + template.replace('__MODULES__', '#[' + json.dumps(module) + ']')
        identity = dict(inputs=initial, artifacts=stamp, query=query, toolchain=toolchain, threads=1)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cached = Path(cache_dir) / 'compiled' / (key + '.json') if cache_dir else None
        if cached and cached.is_file():
            facts = json.loads(cached.read_text())
        else:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.lean', delete=False) as handle:
                handle.write(query)
                probe = Path(handle.name)
            try:
                command = ['lake', 'env', 'lean', '-j1']
                if memory_limit_mb is not None:
                    command.append('-M' + str(memory_limit_mb))
                facts = _query([*command, str(probe)], cwd=root, timeout=timeout)
            finally:
                probe.unlink(missing_ok=True)
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w', dir=cached.parent, delete=False) as handle:
                    json.dump(facts, handle, ensure_ascii=False)
                    temporary = Path(handle.name)
                temporary.replace(cached)
        compiled.extend(facts)
    if include_source:
        source = extract_sources(root, modules, source_backend=source_backend, timeout=timeout,
                                 repl_rev=repl_rev, local_repl_path=local_repl_path, cache_dir=cache_dir)
    if initial != _inputs(root, modules) or (cache_dir and _artifact_state is None and stamp != _artifact_stamp(root)):
        raise RuntimeError('project inputs or compiled artifacts changed during extraction')
    data = dict(extractor_sha256=hashlib.sha256(template.encode()).hexdigest(),
                input_digests=initial, source_digests={m: initial[m.replace('.', '/') + '.lean'] for m in modules},
                source=source, compiled=compiled, toolchain=toolchain,
                source_backend=source_backend, build_requested=build)
    if evidence_dir:
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'raw.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data
