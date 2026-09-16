"""Small real Lean checks for classification distinctions and head shapes."""
import json
from pathlib import Path
import subprocess
from collections import Counter
from prepare import OUT

template=Path(__file__).with_name('type_probe.lean').read_text()
fixture='''
namespace FeatureProbe
axiom binderKinds {α : Type} (P : Prop) [Inhabited α] (h : P) (x : α) : P
axiom sigmaShape (α : Type) : Sigma (fun _ : α => α)
theorem existsShape (x : Nat) : ∃ y : Nat, y = x := ⟨x, rfl⟩
end FeatureProbe
'''
query=template.replace('__SOURCES__','#["axiom binderKinds : Prop", "axiom sigmaShape : Prop", "axiom existsShape : Prop"]').replace('open Lean Meta','open Lean Meta\n'+fixture).replace('__NAMES__','#["FeatureProbe.binderKinds", "FeatureProbe.sigmaShape", "FeatureProbe.existsShape"]')
p=OUT/'probe_validation.lean';p.write_text(query)
r=subprocess.run(['lake','env','lean',str(p.resolve())],cwd='data/research/native/sensitivity/source',text=True,capture_output=True,timeout=60)
(OUT/'probe_validation.log').write_text(r.stdout+r.stderr)
assert r.returncode==0,r.stdout+r.stderr
rows=[json.loads(line.split('FEATURE_JSON ',1)[1])for line in r.stdout.splitlines()if 'FEATURE_JSON 'in line]
assert Counter(x['category']for x in rows[0]['binders'])==Counter({'type_parameter':1,'proposition_parameter':1,'instance':1,'proof_premise':1,'object_parameter':1})
assert rows[1]['conclusion_sigma']and rows[2]['conclusion_exists']
assert not rows[0]['conclusion_exists']
(OUT/'probe_validation.json').write_text(json.dumps({'status':'passed','rows':rows},indent=2))
print('Real Lean binder/Exists/Sigma fixture checks passed')
