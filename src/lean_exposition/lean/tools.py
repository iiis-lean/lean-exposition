"""Read compiled environments independently of source extraction and building."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import time
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
            elif any(suffix in entry.name for suffix in ('.olean', '.ir', '.so', '.ilean')):
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


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def _git_revision(root):
    """Read the fixed checkout identity without spawning Git during offline replay."""
    git = root / '.git'
    if git.is_file():
        git = (root / git.read_text().strip().removeprefix('gitdir: ')).resolve()
    if not (git / 'HEAD').is_file():
        return None
    head = (git / 'HEAD').read_text().strip()
    if head.startswith('ref: '):
        ref = head[5:]
        if (git / ref).is_file():
            return (git / ref).read_text().strip()
        if (git / 'packed-refs').is_file():
            for line in (git / 'packed-refs').read_text().splitlines():
                if line.endswith(' ' + ref):
                    return line.split()[0]
        return None
    return head


def _tree_digest(root):
    files = []
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith('.'))
        for name in sorted(names):
            if name.endswith('.lean') or name in {'lean-toolchain', 'lake-manifest.json', 'lakefile.toml'}:
                path = Path(directory) / name
                files.append((path.relative_to(root).as_posix(), _sha(path)))
    return _digest(files)


def _source_identity(root):
    manifest = root / 'lake-manifest.json'
    dependencies = []
    for package in json.loads(manifest.read_text()).get('packages', []) if manifest.exists() else []:
        directory = root / '.lake/packages' / package['name']
        if package.get('type') == 'path':
            directory = (root / package['dir']).resolve()
        if not directory.is_dir():
            raise ValueError('missing dependency sources: ' + package['name'])
        actual = _git_revision(directory)
        expected = package.get('rev')
        if expected and actual != expected:
            raise ValueError('dependency revision mismatch: ' + package['name'])
        dependencies.append(dict(name=package['name'], revision=expected, url=package.get('url'),
                                 source_sha256=_tree_digest(directory)))
    return dict(project_revision=_git_revision(root), project_source_sha256=_tree_digest(root),
                dependencies=dependencies)


def _runtime(root, lean_binary=None):
    """Resolve one installed, matching compiler; never let Lake mutate dependencies."""
    toolchain = (root / 'lean-toolchain').read_text().strip()
    if lean_binary is None:
        elan = Path(os.environ.get('ELAN_HOME', Path.home() / '.elan'))
        lean_binary = elan / 'toolchains' / toolchain.replace('/', '--').replace(':', '---') / 'bin/lean'
    lean = Path(lean_binary).resolve()
    if not lean.is_file():
        raise ValueError('matching Lean executable is unavailable; supply lean_binary: ' + str(lean))
    version = subprocess.run([str(lean), '--version'], capture_output=True, text=True, timeout=15, check=True).stdout.strip()
    expected = toolchain.rsplit(':', 1)[-1].removeprefix('v')
    if not re.search(r'\bversion ' + re.escape(expected) + r'(?:[, )]|$)', version):
        raise ValueError(f'toolchain mismatch: {toolchain}: {version}')
    paths = [root / '.lake/build/lib/lean']
    manifest = root / 'lake-manifest.json'
    for package in json.loads(manifest.read_text()).get('packages', []) if manifest.exists() else []:
        directory = root / '.lake/packages' / package['name']
        if package.get('type') == 'path':
            directory = (root / package['dir']).resolve()
        paths.append(directory / '.lake/build/lib/lean')
    return dict(lean=str(lean), version=version, binary_sha256=_sha(lean),
                env=dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, paths))))


def _tree_rss(pid):
    total, pending = 0, [pid]
    while pending:
        child = pending.pop()
        try:
            status = Path(f'/proc/{child}/status').read_text()
            match = re.search(r'^VmRSS:\s+(\d+)', status, re.M)
            total += int(match[1]) * 1024 if match else 0
            pending.extend(map(int, Path(f'/proc/{child}/task/{child}/children').read_text().split()))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return total


def _shared_rss():
    for path, key in (('/sys/fs/cgroup/memory/memory.stat', 'total_rss'),
                      ('/sys/fs/cgroup/memory.stat', 'anon')):
        if Path(path).is_file():
            return int(dict(line.split() for line in Path(path).read_text().splitlines()).get(key, 0))
    return 0


class QueryFailure(RuntimeError):
    def __init__(self, reason, statistics):
        self.reason, self.statistics = reason, statistics
        super().__init__(reason + ': ' + statistics.get('diagnostic', ''))


def _query(command, *, cwd, timeout, env=None, modules=(), on_module=None,
           rss_limit_bytes=None, shared_rss_limit_bytes=None):
    """Consume flushed module frames while Lean runs; only END publishes a module."""
    started = time.monotonic()
    completed, current, facts, byte_count = {}, None, [], 0
    messages, peak, reason = [], 0, None
    module_stats = []
    with tempfile.NamedTemporaryFile(mode='w+b') as output, tempfile.TemporaryFile(mode='w+b') as diagnostics:
        query_env = dict(os.environ if env is None else env, LEAN_EXPOSITION_OUTPUT=output.name)
        with subprocess.Popen(command, cwd=cwd, env=query_env, stdout=diagnostics, stderr=subprocess.STDOUT,
                              start_new_session=True) as process:
            position = 0
            def drain():
                nonlocal position, current, facts, byte_count
                # pread does not share the child's file offset.
                pending = os.pread(output.fileno(), 1024 * 1024, position)
                while pending:
                    end = pending.rfind(b'\n')
                    if end < 0:
                        # A single large declaration may exceed the read buffer.
                        extra = os.pread(output.fileno(), 1024 * 1024, position + len(pending))
                        if not extra:
                            return
                        pending += extra
                        continue
                    for raw in pending[:end + 1].splitlines(keepends=True):
                        line = raw.decode('utf-8').rstrip('\r\n')
                        if line.startswith('LEAN_EXPOSITION_BEGIN '):
                            module = json.loads(line.split(' ', 1)[1])
                            if current is not None or module not in modules or module in completed:
                                raise ValueError('invalid module BEGIN')
                            current, facts, byte_count = module, [], 0
                        elif line.startswith('LEAN_EXPOSITION_JSON '):
                            fact = json.loads(line.split(' ', 1)[1])
                            if current is None or fact.get('module') != current:
                                raise ValueError('record outside its module frame')
                            facts.append(fact)
                            byte_count += len(raw)
                        elif line.startswith('LEAN_EXPOSITION_END '):
                            end_record = json.loads(line.split(' ', 1)[1])
                            if (current is None or end_record.get('module') != current or
                                    end_record.get('records') != len(facts) or end_record.get('bytes') != byte_count):
                                raise ValueError('invalid module END checksum/count')
                            _validate_facts(current, facts)
                            if on_module:
                                on_module(current, facts, end_record)
                            completed[current] = facts if on_module is None else None
                            module_stats.append(dict(end_record, observed_seconds=time.monotonic() - started))
                            current, facts, byte_count = None, [], 0
                        elif len(messages) < 100:
                            messages.append(line[:2000])
                    position += end + 1
                    pending = os.pread(output.fileno(), 1024 * 1024, position)
            try:
                while process.poll() is None:
                    drain()
                    peak = max(peak, _tree_rss(process.pid))
                    if time.monotonic() - started > timeout:
                        reason = 'timeout'
                    elif rss_limit_bytes and peak > rss_limit_bytes:
                        reason = 'process_tree_rss_limit'
                    elif shared_rss_limit_bytes and _shared_rss() > shared_rss_limit_bytes:
                        reason = 'shared_rss_limit'
                    if reason:
                        os.killpg(process.pid, signal.SIGKILL)
                        break
                    time.sleep(.05)
                process.wait()
                drain()
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            diagnostics.seek(0)
            diagnostic_text = diagnostics.read(200000).decode('utf-8', errors='replace')
            stats = dict(seconds=time.monotonic() - started, peak_process_tree_rss_bytes=peak,
                         exit_code=process.returncode, stop_reason=reason, modules=module_stats,
                         output_bytes=os.fstat(output.fileno()).st_size, diagnostic='\n'.join(messages) + diagnostic_text)
    if reason or process.returncode or current is not None or set(completed) != set(modules):
        raise QueryFailure(reason or 'incomplete_or_failed_query', stats)
    return completed, stats


def _validate_facts(module, facts):
    names = set()
    required = {'name', 'user_name', 'module', 'kind', 'generator', 'range', 'type', 'value', 'type_text', 'docstring'}
    if not isinstance(facts, list):
        raise ValueError('invalid compiled facts')
    for fact in facts:
        if not isinstance(fact, dict) or required - fact.keys() or fact['module'] != module or fact['name'] in names:
            raise ValueError('invalid or duplicate compiled declaration')
        names.add(fact['name'])
        for refs in (fact['type'], fact['value']):
            if refs is not None and (not isinstance(refs, list) or any(
                    not isinstance(ref, dict) or not isinstance(ref.get('name'), str) or
                    'module' not in ref for ref in refs)):
                raise ValueError('invalid compiled references')


def _module_file(directory, module):
    return Path(directory) / 'modules' / (module + '.json')


def _write_module(directory, module, facts, identity):
    _validate_facts(module, facts)
    payload = dict(module=module, identity=identity, compiled=facts)
    payload['sha256'] = _digest(payload)
    path = _module_file(directory, module)
    _atomic_json(path, payload)
    return dict(path=path.relative_to(directory).as_posix(), sha256=_sha(path), records=len(facts),
                references=sum(len(f['type']) + len(f['value'] or []) for f in facts), bytes=path.stat().st_size)


def _read_module(directory, module, identity, record=None):
    path = _module_file(directory, module)
    if record and _sha(path) != record['sha256']:
        raise ValueError('compiled module file checksum mismatch: ' + module)
    payload = json.loads(path.read_bytes())
    checksum = payload.pop('sha256')
    if payload.get('identity') != identity or payload.get('module') != module or _digest(payload) != checksum:
        raise ValueError('compiled module identity/checksum mismatch: ' + module)
    _validate_facts(module, payload['compiled'])
    return payload['compiled']


def _validate_modules(modules):
    if not modules or len(set(modules)) != len(modules):
        raise ValueError('modules must be a nonempty sequence without duplicates')
    for module in modules:
        if not re.fullmatch(r"[^\W\d][\w']*(?:\.[^\W\d][\w']*)*", module):
            raise ValueError(f'unsupported module identifier: {module}')


def _batches(modules, size):
    batch = []
    for module in modules:
        if module.split('.')[-1] in {'Challenge', 'Solution'}:
            if batch:
                yield tuple(batch)
                batch = []
            yield (module,)
        else:
            batch.append(module)
            if len(batch) == size:
                yield tuple(batch)
                batch = []
    if batch:
        yield tuple(batch)


def export_compiled(project, modules, destination, *, lean_binary=None, batch_size=1,
                    timeout=180, memory_limit_mb=12288, shared_rss_limit_bytes=None,
                    task_timeout=None, min_free_bytes=1024**3, _artifact_state=None):
    """Checkpoint raw compiler facts, splitting failed batches at most down to singletons.

    The manifest is a portable source/facts identity. Its artifact stamp is explicitly
    a local resume guard, not an attestation of source/binary correspondence.
    """
    _validate_modules(modules)
    if batch_size < 1 or timeout <= 0 or memory_limit_mb <= 0 or (task_timeout is not None and task_timeout <= 0):
        raise ValueError('batch size, timeout and memory limit must be positive')
    root, destination = Path(project).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    initial = _inputs(root, modules)
    sources = _source_identity(root)
    runtime = _runtime(root, lean_binary)
    stamp = _artifact_state if _artifact_state is not None else _artifact_stamp(root)
    template = Path(__file__).with_name('environment.lean').read_text()
    exporter = dict(environment_sha256=hashlib.sha256(template.encode()).hexdigest(), tools_sha256=_sha(__file__))
    identity = dict(inputs=initial, sources=sources, toolchain=(root / 'lean-toolchain').read_text().strip(),
                    compiler={key: runtime[key] for key in ('version', 'binary_sha256')}, exporter=exporter,
                    options={'threads': 1, 'type_printing': 'selected_import_environment'})
    identity_digest = _digest(identity)
    manifest_path = destination / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('status') == 'invalidated':
            raise ValueError('compiled package invalidated; use a fresh destination')
        if manifest['identity'] != identity or manifest['requested_modules'] != list(modules):
            raise ValueError('compiled package identity changed; use a fresh destination')
        if manifest['local_artifact_stamp_sha256'] != _digest(stamp):
            raise ValueError('local compiled artifacts changed; use a fresh destination')
        for module, record in manifest['completed'].items():
            _read_module(destination, module, identity_digest, record)
    else:
        manifest = dict(identity=identity, identity_sha256=identity_digest, requested_modules=list(modules),
                        completed={}, failed={}, attempts=[], status='running',
                        local_artifact_stamp_sha256=_digest(stamp),
                        artifact_identity_scope='local relative path/size/mtime resume guard; not portable attestation')
    manifest.update(status='running', failed={}, preflight_seconds=time.monotonic() - started)
    manifest.pop('pause_reason', None)
    _atomic_json(manifest_path, manifest)

    def save(module, facts, module_stats):
        # Check source inputs before publishing each finished module. The final
        # artifact check also invalidates the entire manifest if a build raced us.
        if any(initial.get(path) != digest for path, digest in _inputs(root, (module,)).items()):
            raise ValueError('project inputs changed during extraction')
        if shutil.disk_usage(destination).free < min_free_bytes:
            raise QueryFailure('disk_space_limit', {})
        write_started = time.monotonic()
        record = _write_module(destination, module, facts, identity_digest)
        module_stats = dict(module_stats, checkpoint_seconds=time.monotonic() - write_started)
        manifest['completed'][module] = dict(record, timing=module_stats)
        _atomic_json(manifest_path, manifest)

    def run(batch):
        remaining = tuple(m for m in batch if m not in manifest['completed'])
        if not remaining:
            return
        if task_timeout and time.monotonic() - started >= task_timeout:
            raise QueryFailure('task_timeout', {})
        if shutil.disk_usage(destination).free < min_free_bytes:
            raise QueryFailure('disk_space_limit', {})
        if shared_rss_limit_bytes and _shared_rss() > shared_rss_limit_bytes:
            raise QueryFailure('shared_rss_limit', {})
        query = ''.join('import ' + module + '\n' for module in remaining)
        query += template.replace('__MODULES__', '#[' + ','.join(json.dumps(m) for m in remaining) + ']')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lean', delete=False) as handle:
            handle.write(query)
            probe = Path(handle.name)
        limit = min(timeout, max(.01, task_timeout - (time.monotonic() - started))) if task_timeout else timeout
        try:
            _, stats = _query([runtime['lean'], '-j1', '-M' + str(memory_limit_mb), str(probe)],
                cwd=root, timeout=limit, env=runtime['env'], modules=remaining, on_module=save,
                rss_limit_bytes=memory_limit_mb * 1024**2, shared_rss_limit_bytes=shared_rss_limit_bytes)
            manifest['attempts'].append(dict(requested=list(remaining), **stats))
            _atomic_json(manifest_path, manifest)
        except QueryFailure as exc:
            manifest['attempts'].append(dict(requested=list(remaining), **exc.statistics))
            _atomic_json(manifest_path, manifest)
            if exc.reason in {'shared_rss_limit', 'disk_space_limit', 'task_timeout'}:
                raise
            pending = tuple(m for m in remaining if m not in manifest['completed'])
            if len(pending) == 1:
                # A singleton already attempted in this call is terminal.
                if len(remaining) == 1:
                    manifest['failed'][pending[0]] = str(exc)
                else:
                    run(pending)
            elif pending:
                middle = len(pending) // 2
                run(pending[:middle])
                run(pending[middle:])
        finally:
            probe.unlink(missing_ok=True)
    try:
        for batch in _batches(modules, batch_size):
            run(batch)
        validation_started = time.monotonic()
        if initial != _inputs(root, modules) or stamp != _artifact_stamp(root) or sources != _source_identity(root):
            manifest['status'] = 'invalidated'
            raise ValueError('project inputs or compiled artifacts changed during extraction')
        manifest['validation_seconds'] = time.monotonic() - validation_started
        manifest['status'] = 'complete' if set(manifest['completed']) == set(modules) else 'incomplete'
    except QueryFailure as exc:
        manifest.update(status='paused', pause_reason=exc.reason)
    except BaseException:
        if manifest['status'] != 'invalidated':
            manifest['status'] = 'interrupted'
        raise
    finally:
        manifest['last_run_seconds'] = time.monotonic() - started
        _atomic_json(manifest_path, manifest)
    return manifest


def load_compiled(project, directory, *, modules=None):
    """Validate a portable facts package and create a normalizer payload, without Lean."""
    root, directory = Path(project).resolve(), Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    identity = manifest['identity']
    if manifest.get('status') == 'invalidated' or _digest(identity) != manifest['identity_sha256']:
        raise ValueError('invalid compiled package identity')
    selected = tuple(modules or manifest['requested_modules'])
    _validate_modules(selected)
    if set(selected) - manifest['completed'].keys():
        raise ValueError('compiled package does not cover requested modules')
    if _inputs(root, tuple(manifest['requested_modules'])) != identity['inputs'] or _source_identity(root) != identity['sources']:
        raise ValueError('project or dependency sources changed since extraction')
    compiled = []
    for module in selected:
        compiled.extend(_read_module(directory, module, manifest['identity_sha256'], manifest['completed'][module]))
    return dict(extractor_sha256=identity['exporter']['environment_sha256'], input_digests=identity['inputs'],
                source_digests={m: identity['inputs'][m.replace('.', '/') + '.lean'] for m in selected},
                project_revision=identity['sources']['project_revision'], source={}, compiled=compiled,
                toolchain=identity['toolchain'], build_requested=False)


def extract_modules(project: str | Path, modules: tuple[str, ...], *, timeout: int = 300,
                    repl_rev: str | None = None, local_repl_path: str | Path | None = None,
                    evidence_dir: str | Path | None = None, build: bool = False,
                    include_source: bool = True, cache_dir: str | Path | None = None,
                    _artifact_state=None, memory_limit_mb: int | None = None,
                    source_backend='toolkit_text_ast', lean_binary=None, batch_size=1) -> dict:
    """Use the same checkpointed exporter for singleton and bounded batch queries."""
    _validate_modules(modules)
    if source_backend not in {'toolkit_text_ast', 'lean_interact'}:
        raise ValueError('unknown source backend: ' + source_backend)
    if memory_limit_mb is not None and memory_limit_mb <= 0:
        raise ValueError('memory_limit_mb must be positive')
    root = Path(project).resolve()
    if build:
        result = _run(['lake', 'build', *('+' + m for m in modules)], cwd=root, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'selected-module build failed:\n{result.stdout}\n{result.stderr}')
    # Source parsing is deliberately excluded from raw cache identity.
    key = _digest(dict(inputs=_inputs(root, modules), sources=_source_identity(root),
                       artifacts=_artifact_state if _artifact_state is not None else _artifact_stamp(root),
                       tools=_sha(__file__), environment=_sha(Path(__file__).with_name('environment.lean'))))
    with tempfile.TemporaryDirectory(prefix='lean-exposition-') as temporary:
        directory = Path(cache_dir) / 'compiled' / key if cache_dir else Path(temporary)
        if not (directory / 'manifest.json').exists() or json.loads((directory / 'manifest.json').read_text())['status'] != 'complete':
            manifest = export_compiled(root, modules, directory, lean_binary=lean_binary, batch_size=batch_size,
                timeout=timeout, memory_limit_mb=memory_limit_mb or 12288, _artifact_state=_artifact_state)
            if manifest['status'] != 'complete':
                raise RuntimeError('compiled environment extraction incomplete: ' + json.dumps(manifest['failed']))
        data = load_compiled(root, directory)
        if include_source:
            data['source'] = extract_sources(root, modules, source_backend=source_backend, timeout=timeout,
                repl_rev=repl_rev, local_repl_path=local_repl_path, cache_dir=cache_dir)
        data.update(source_backend=source_backend, build_requested=build)
        if evidence_dir:
            _atomic_json(Path(evidence_dir) / 'raw.json', data)
        return data
