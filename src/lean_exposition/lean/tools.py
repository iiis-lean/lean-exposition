"""Run the selected project's exact toolchain; never update its dependencies."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
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


def extract_modules(project: str | Path, modules: tuple[str, ...], *, timeout: int = 300,
                    repl_rev: str | None = None, local_repl_path: str | Path | None = None,
                    evidence_dir: str | Path | None = None) -> dict:
    """Extract author syntax and compiled Expr constants from selected modules.

    Requires the optional lean-interact dependency. An incremental selected-module build checks artifact freshness; no update is run.
    Raw responses may be saved for audit; they are not a persistent feature cache.
    """
    root = Path(project).resolve()
    if not modules or len(set(modules)) != len(modules):
        raise ValueError("modules must be a nonempty sequence without duplicates")
    inputs = [root / "lean-toolchain", *(root / (m.replace('.', '/') + '.lean') for m in modules)]
    inputs.extend(root / name for name in ('lake-manifest.json', 'lakefile.toml', 'lakefile.lean')
                  if (root / name).is_file())
    initial_digests = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    for module in modules:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", module):
            raise ValueError(f"unsupported module identifier: {module}")
    toolchain = (root / 'lean-toolchain').read_text().strip()
    if repl_rev is None:
        repl_rev = {'leanprover/lean4:v4.28.0': 'v1.3.14',
                    'leanprover/lean4:v4.32.0': 'v1.3.18'}.get(toolchain)
        if repl_rev is None:
            raise ValueError(f'no verified REPL mapping for {toolchain}; supply repl_rev explicitly')
    if local_repl_path is not None:
        local_repl_path = Path(local_repl_path).resolve()
        if (local_repl_path / 'lean-toolchain').read_text().strip() != toolchain:
            raise ValueError('local REPL toolchain does not match project toolchain')
    from lean_interact import FileCommand, LeanREPLConfig, LeanServer
    from lean_interact.project import LocalProject

    build = _run(["lake", "build", *("+" + m for m in modules)], cwd=root, timeout=timeout)
    if build.returncode:
        raise RuntimeError(f"selected-module build failed:\n{build.stdout}\n{build.stderr}")
    source_digests = {m: hashlib.sha256((root / (m.replace('.', '/') + '.lean')).read_bytes()).hexdigest()
                      for m in modules}
    config = LeanREPLConfig(project=LocalProject(directory=root, auto_build=False), repl_rev=repl_rev,
                            local_repl_path=Path(local_repl_path).resolve() if local_repl_path else None)
    server = LeanServer(config)
    source = {}
    try:
        for module in modules:
            response = server.run(FileCommand(path=str(root / (module.replace('.', '/') + '.lean')),
                                              declarations=True), timeout=timeout)
            data = response.model_dump(by_alias=True, exclude_none=False)
            if any(m.get("severity") == "error" for m in data.get("messages", [])):
                raise RuntimeError(f"Lean source extraction failed for {module}: {data['messages']}")
            if "declarations" not in data:
                raise RuntimeError(f"Lean source extraction returned no declarations field: {data}")
            source[module] = data
    finally:
        server.kill()
    query = Path(__file__).with_name("environment.lean").read_text()
    query = '\n'.join(f"import {m}" for m in modules) + '\n' + query.replace(
        "__MODULES__", '#[' + ', '.join(json.dumps(m) for m in modules) + ']')
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lean", delete=False) as handle:
        handle.write(query)
        probe = Path(handle.name)
    try:
        result = _run(["lake", "env", "lean", str(probe)], cwd=root, timeout=timeout)
    finally:
        probe.unlink(missing_ok=True)
    if result.returncode:
        raise RuntimeError(f"compiled environment extraction failed:\n{result.stdout}\n{result.stderr}")
    marker = "LEAN_EXPOSITION_JSON "
    compiled = [json.loads(line.split(marker, 1)[1]) for line in result.stdout.splitlines() if marker in line]
    if any(hashlib.sha256((root / p).read_bytes()).hexdigest() != digest
           for p, digest in initial_digests.items()):
        raise RuntimeError("project inputs changed during extraction")
    data = {"extractor_sha256": hashlib.sha256(query.encode()).hexdigest(), "lean_interact_version": version("lean-interact"),
            "input_digests": initial_digests, "source_digests": source_digests, "source": source, "compiled": compiled, "repl_rev": repl_rev, "local_repl_path": str(local_repl_path) if local_repl_path else None,
            "toolchain": (root / "lean-toolchain").read_text().strip()}
    if evidence_dir is not None:
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data
