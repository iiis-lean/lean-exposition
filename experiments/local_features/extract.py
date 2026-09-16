"""Extract genuine native Lean Expr/telescope observations, with missing LC coverage."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from prepare import OUT, digest

PROJECTS = {'sensitivity':Path('data/research/native/sensitivity/source'), 'agree':Path('data/research/native/agree_to_disagree/source')}
RAW = {'sensitivity':Path('data/research/loading/native/sensitivity/raw.json'), 'agree':Path('data/research/loading/native/agree_to_disagree/raw.json')}


def main():
    manifest=json.loads((OUT/'sample_manifest.json').read_text()); rows=manifest['samples']; results={}; evidence={}
    template=Path(__file__).with_name('type_probe.lean').read_text()
    for project,path in PROJECTS.items():
        raw=json.loads(RAW[project].read_text())
        for module,expected in raw['source_digests'].items():
            actual=hashlib.sha256((path/(module.replace('.','/')+'.lean')).read_bytes()).hexdigest()
            assert actual==expected, (project,module,'source changed')
        for relative,expected in raw['input_digests'].items():
            assert hashlib.sha256((path/relative).read_bytes()).hexdigest()==expected,(project,relative,'input changed')
        selected=[row for row in rows if row['project']==project]
        imports='\n'.join('import '+m for m in sorted({r['module']for r in selected}))
        source=imports+'\n'+template.replace('__NAMES__','#[ '+', '.join(json.dumps(r['lean_name'])for r in selected)+' ]').replace('__SOURCES__','#[ '+', '.join(json.dumps(r['card']['formal'],ensure_ascii=False)for r in selected)+' ]')
        query=OUT/(project+'_type_probe.lean');query.write_text(source)
        result=subprocess.run(['lake','env','lean',str(query.resolve())],cwd=path,text=True,capture_output=True,timeout=180)
        (OUT/(project+'_type_probe.log')).write_text(result.stdout+result.stderr)
        if result.returncode: raise RuntimeError('Lean probe failed; see '+str(OUT/(project+'_type_probe.log')))
        parsed=[json.loads(line.split('FEATURE_JSON ',1)[1])for line in result.stdout.splitlines()if 'FEATURE_JSON 'in line]
        assert len(parsed)==len(selected)
        for row in parsed: results[(project,row['lean_name'])]=row
        evidence[project]={'toolchain':raw['toolchain'],'source_digests':raw['source_digests'],'input_digests':raw['input_digests'],'query_sha256':hashlib.sha256(source.encode()).hexdigest(),'project':str(path.resolve()),'rows':parsed}
    features=[];cards=[]
    for row in rows:
        compiled=results.get((row['project'],row['lean_name']))
        feature={**row['observed']}
        feature['nl_available']=bool(row['card']['natural_language'])
        feature['nl_chars']=len(row['card']['natural_language']) if feature['nl_available'] else None
        feature['nl_missing_reason']=None if feature['nl_available'] else 'No NL text available in the fixed declaration card; not measured as zero.'
        keys=['type_expr_nodes','type_expr_depth','conclusion_exists','conclusion_sigma']
        feature.update({k:compiled[k] if compiled else None for k in keys})
        categories=Counter(b['category']for b in compiled['binders'])if compiled else Counter()
        for category in ['instance','proposition_parameter','type_parameter','proof_premise','object_parameter']:
            feature['binder_'+category]=categories[category]if compiled else None
        syntax=compiled.get('syntax') if compiled else None
        if syntax and syntax['status']=='parsed':
            feature['syntax_nodes']=syntax['nodes'];feature['syntax_depth']=syntax['depth'];feature['syntax_missing_reason']=None
            feature['tactic_syntax_nodes']=len(syntax['tactic_kinds'])
        else:
            feature['tactic_syntax_nodes']=None
            if syntax:feature['syntax_missing_reason']=syntax['reason']
        feature['binder_total']=len(compiled['binders'])if compiled else None
        feature['binder_explicit']=sum(b['explicitness']=='explicit'for b in compiled['binders'])if compiled else None
        card={**row['card'],'full_type':compiled['full_type']if compiled else None}
        feature_row={'sample_id':row['sample_id'],'project':row['project'],'stratum':row['stratum'],'features':feature,'compiled':compiled,
            'compiled_missing_reason':None if compiled else 'Fixed LC source snapshot has no matched Lean 4.32 compiled artifact in the inspected release candidate; no dirty/other-version fallback.'}
        features.append(feature_row);cards.append(card)
    frozen={'protocol':'local-roles-v1','manifest_digest':digest(manifest),'cards_digest':digest(cards),'cards':cards}
    (OUT/'cards.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
    from tactic_groups import augment
    augment(features)
    (OUT/'local_features.json').write_text(json.dumps(features,ensure_ascii=False,indent=2))
    (OUT/'compiled_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print({'samples':len(features),'compiled':sum(r['compiled']is not None for r in features),'cards_digest':frozen['cards_digest']})

if __name__=='__main__':main()
