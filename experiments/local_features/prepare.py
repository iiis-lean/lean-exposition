"""Freeze project-stratified random and explicitly separate boundary samples."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

OUT = Path('data/research/feature_selection')
CASES = {
 'uniform':'data/research/loading/lc/uniform-workspace.json',
 'erdos946':'data/research/loading/lc/erdos946-workspace.json',
 'sensitivity':'data/research/loading/native/sensitivity/workspace.json',
 'agree':'data/research/loading/native/agree_to_disagree/workspace.json',
}
SEED = 20260914


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def strip_comments(text):
    """Lexical whitespace-preserving nested comment removal, not Lean AST parsing."""
    result=[]; i=0; depth=0; string=False
    while i < len(text):
        if depth:
            if text[i:i+2]=='/-': depth+=1; i+=2
            elif text[i:i+2]=='-/': depth-=1; i+=2
            else:
                if text[i]=='\n': result.append('\n')
                i+=1
        elif not string and text[i:i+2]=='/-': depth=1; result.append(' '); i+=2
        elif not string and text[i:i+2]=='--':
            while i<len(text) and text[i]!='\n': i+=1
        else:
            char=text[i]; result.append(char); i+=1
            if char=='\\' and string and i<len(text): result.append(text[i]); i+=1
            elif char=='"': string=not string
    return '\n'.join(line for line in ''.join(result).splitlines() if line.strip())


def material(d, lc):
    statement=d['statement']['formal'].get('text') or ''
    proof=((d.get('proof')or{}).get('formal')or{}).get('text') or ''
    formal=(proof or statement) if lc else statement+proof
    nl='\n'.join((d.get(part)or{}).get('nl',{}).get('text') or '' for part in ['statement','proof']).strip()
    return strip_comments(formal),nl,formal


def main():
    rng=random.Random(SEED); rows=[]; populations={}
    for project,path in CASES.items():
        workspace=json.loads(Path(path).read_text()); repo=workspace['manifest']['repositories'][0]
        eligible=[d for d in workspace['declarations'] if d['ref']['repo_key']==repo['repo_key'] and d['extraction_status']['state'] in {'extracted','imported'}]
        eligible.sort(key=lambda d:d['lean_name']); lc=project in {'uniform','erdos946'}
        populations[project]={'eligible_count':len(eligible),'repo':repo,'workspace':path,'workspace_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        sizes=lambda d:len(material(d,lc)[0])
        theorems=[d for d in eligible if d['kind'] in {'theorem','lemma'}]
        boundaries=[(min(theorems,key=lambda d:(sizes(d),d['lean_name'])),'shortest_theorem'),(max(theorems,key=lambda d:(sizes(d),d['lean_name'])),'longest_theorem')]
        if lc:
            definitions=[d for d in eligible if d['kind'] not in {'theorem','lemma'}]
            boundaries.append((min(definitions,key=lambda d:(sizes(d),d['lean_name'])),'shortest_non_theorem'))
        boundary_names={d['lean_name'] for d,_ in boundaries}
        selected=[(d,'random','project_stratified_random')for d in rng.sample([d for d in eligible if d['lean_name'] not in boundary_names],10)]
        selected += [(d,'boundary',why)for d,why in boundaries]
        for d,stratum,why in selected:
            formal,nl,raw=material(d,lc)
            sample_id=project+':'+d['ref']['local_id']
            card={'sample_id':sample_id,'project':project,'lean_name':d['lean_name'],'kind':d['kind'],'formal':formal,'natural_language':nl,'full_type':None}
            deps={json.dumps(dep['provider'],sort_keys=True) for part in ['statement','proof'] for dep in (d.get(part)or{}).get('deps',[])}
            rows.append({'sample_id':sample_id,'project':project,'stratum':stratum,'selection_reason':why,'ref':d['ref'],'lean_name':d['lean_name'],'module':d['module'],'source_refs':d['source_refs'],'card':card,'raw_decl':d,'observed':{'formal_chars':len(formal),'formal_lines':len(formal.splitlines()),'formal_raw_chars':len(raw),'nl_chars':len(nl),'dependency_count':len(deps),'syntax_nodes':None,'syntax_depth':None,'syntax_missing_reason':'No reliable declaration Syntax AST extraction in this experiment; no regex substitute.'}})
    # Round robin gives each batch mixed projects; protocol input order is frozen.
    ordered=[]
    groups={p:[r for r in rows if r['project']==p]for p in CASES}
    while any(groups.values()):
        for group in groups.values():
            if group: ordered.append(group.pop(0))
    assert len(ordered)==50 and len({r['sample_id']for r in ordered})==50
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={'version':'sample-v1','seed':SEED,'design':'40 project-stratified random (10 each), sampled after removing 10 prespecified extreme-size boundary declarations; populations and boundary stratum analyzed separately','populations':populations,'samples':ordered}
    (OUT/'sample_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print({p:Counter(r['stratum']for r in ordered if r['project']==p)for p in CASES})

if __name__=='__main__': main()
