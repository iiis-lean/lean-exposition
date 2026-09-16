"""Validate paired material, label identities, real extraction and tool isolation."""
from collections import Counter
import json
from prepare import OUT,digest
from protocol import LABELS, PROMPT, SCHEMA

manifest=json.loads((OUT/'sample_manifest.json').read_text());features=json.loads((OUT/'local_features.json').read_text());cards=json.loads((OUT/'cards.json').read_text())
ids=[r['sample_id']for r in manifest['samples']]
assert len(ids)==50==len(set(ids))
assert Counter(r['stratum']for r in manifest['samples'])=={'random':40,'boundary':10}
assert cards['cards_digest']==digest(cards['cards'])
assert cards['manifest_digest']==digest(manifest)
assert sum(r['compiled']is not None for r in features)==24
assert json.loads((OUT/'probe_validation.json').read_text())['status']=='passed'
labels={p:json.loads((OUT/f'labels_{p}.json').read_text())for p in ['glm','luna']}
for provider,rows in labels.items():
    assert len(rows)==50 and {r['sample_id']for r in rows}==set(ids)
    for row in rows:
        for key in LABELS:assert row[key]['value']in {'yes','no','uncertain'}and row[key]['evidence'].strip()
for index in range(10):
    a=json.loads((OUT/f'runs/glm/batch_{index:02d}.json').read_text());b=json.loads((OUT/f'runs/luna/batch_{index:02d}.json').read_text())
    assert a['input_digest']==b['input_digest'] and a['prompt']==b['prompt'] and a['schema']==b['schema']
    assert a['sample_ids']==b['sample_ids']==ids[index*5:index*5+5]
    assert a['model']=='glm-5.3-flash'and b['model']=='gpt-5.6-luna'and b['reasoning']=='max'
    assert all(i['type']in {'userMessage','agentMessage','reasoning','plan','contextCompaction'}for i in b['result']['metadata']['items'])
summary={'status':'passed','samples':50,'declaration_label_sets':100,'paired_declarations':50,'paired_judgments':200,'identical_batch_materials':10,'compiled_native':24,'syntax_parsed':sum(r['features']['syntax_nodes']is not None for r in features),'compiler_only_excluded':True,'tool_free_codex_runs':10}
(OUT/'validation.json').write_text(json.dumps(summary,indent=2));print(summary)
