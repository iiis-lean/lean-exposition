"""Remove this experiment's completed temporary homes; never inspect secret contents."""
import json
from pathlib import Path
import shutil
from prepare import OUT


def main():
    assert json.loads((OUT/'validation.json').read_text())['status']=='passed'
    runtime=OUT.parent/'runtime_delivery'
    candidates=[runtime/'isolated',runtime/'capability_home']
    for relative in ['isolated_probe/codex_schema.json','final_isolation/codex_schema.json']:
        record=json.loads((runtime/relative).read_text());task_home=Path(record['codex_home']).parent
        assert str(task_home).startswith('/tmp/lean-exposition-codex-')
        candidates.append(task_home)
    candidates += list(Path('/tmp').glob('lean-label-luna-*'))+list(Path('/tmp').glob('lean-label-glm-*'))
    removed=[]
    for directory in candidates:
        if directory.exists():
            shutil.rmtree(directory);removed.append(str(directory))
    residual_label_homes=[str(p)for pattern in ['lean-label-luna-*','lean-label-glm-*']for p in Path('/tmp').glob(pattern)]
    residual_auth_files=[str(p)for p in runtime.rglob('auth.json')]
    assert not residual_label_homes and not residual_auth_files
    assert all(not p.exists()for p in candidates)
    result={'status':'passed','scope':'B-lane runtime probes and feature-labeling homes only','removed_home_paths':removed,'residual_label_homes':residual_label_homes,'residual_runtime_auth_files':residual_auth_files,'secret_contents_read':False,'global_auth_modified':False}
    (OUT/'credential_cleanup.json').write_text(json.dumps(result,indent=2));print(json.dumps({'status':'passed','removed_homes':len(removed),'residual_label_homes':len(residual_label_homes),'residual_runtime_auth_files':len(residual_auth_files)}))

if __name__=='__main__':main()
