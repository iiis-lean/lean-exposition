"""Explicit native Lean probes with fixed-source verification; no ignored imports."""
import hashlib
import json
from pathlib import Path
import subprocess

from .core import FEATURE_CONFIG_DIGEST


def collect_native_features(workspace, repo_key, project_path, output_path, *, timeout=180):
    root, output = Path(project_path).resolve(), Path(output_path).resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = next(r for r in workspace.manifest.repositories if r.repo_key == repo_key)
    if (root / "lean-toolchain").read_text().strip() != repo.toolchain:
        raise ValueError("native feature toolchain differs from workspace")
    verified = {}
    for asset in workspace.manifest.assets:
        if asset.repo_key == repo_key:
            content = (root / asset.path).read_bytes()
            asset.verify(content)
            verified[asset.path] = asset.sha256
    declarations = sorted((d for d in workspace.declarations if d.ref.repo_key == repo_key
                           and d.extraction_status.state == "extracted" and d.source_refs), key=lambda d: d.lean_name)
    template = Path(__file__).with_name("type_probe.lean").read_text()
    source = "\n".join("import " + module for module in sorted({d.module for d in declarations})) + "\n" + template
    source = source.replace("__NAMES__", "#[" + ",".join(json.dumps(d.lean_name, ensure_ascii=False) for d in declarations) + "]")
    source = source.replace("__SOURCES__", "#[" + ",".join(json.dumps((d.statement.formal.text or "") +
        (d.proof.formal.text or "" if d.proof else ""), ensure_ascii=False) for d in declarations) + "]")
    query = output / "type_probe.lean"
    query.write_text(source)
    result = subprocess.run(["lake", "env", "lean", str(query)], cwd=root, capture_output=True, text=True, timeout=timeout)
    (output / "type_probe.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Native feature probe failed; inspect its bounded local log")
    rows = [json.loads(line.split("FEATURE_JSON ", 1)[1]) for line in result.stdout.splitlines() if "FEATURE_JSON " in line]
    if {row["lean_name"] for row in rows} != {d.lean_name for d in declarations} or len(rows) != len(declarations):
        raise ValueError("native feature result coverage mismatch")
    envelope = {"repo_key": repo_key, "workspace_digest": workspace.digest(),
                "config_digest": FEATURE_CONFIG_DIGEST, "rows": rows,
                "evidence": {"toolchain": repo.toolchain, "assets": verified,
                             "query_sha256": hashlib.sha256(source.encode()).hexdigest(),
                             "scope": "authored declarations with source ranges; compiler-only records remain missing"}}
    (output / "compiled.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
    return envelope
