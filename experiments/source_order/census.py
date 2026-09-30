"""Reproducible scope census against source-priority Kahn, without model calls."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import heapq
import json
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from lean_exposition.construction import RepositoryBuildBundle
from lean_exposition.importers import load_project
from lean_exposition.structure import build_hierarchy, derive_narrative_order, NarrativeOrder
from lean_exposition.structure import hierarchy as hierarchy_module
from lean_exposition.structure import order as order_module
from analyze import region_summary, unit_facts

ROOT = Path(__file__).resolve().parents[2]
INPUT = Path('/tmp/lean-exposition-input-audit-20260917/iiis-lean')
CASES = [('coverage','FinitePairwiseCoverageGap'), ('sensitivity',None),
         ('erdos1025','Erdos1025'), ('balanced','BalancedDigraphs'),
         ('uniform','UniformMissingTraceFamily')]

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

def source_kahn(problem, constraints):
    """Same accepted hard constraints; only source position and stable ID break ties."""
    successors, predecessors = order_module._graph(problem, constraints)
    degrees = {a: len(predecessors[a]) for a in problem.atoms}
    ready = [(order_module._tupleize(problem.source_keys[a]), a) for a in problem.atoms if not degrees[a]]
    heapq.heapify(ready)
    result=[]
    widths=[]
    while ready:
        widths.append(len(ready))
        _, atom=heapq.heappop(ready)
        result.append(atom)
        for child in successors[atom]:
            degrees[child]-=1
            if not degrees[child]:
                heapq.heappush(ready,(order_module._tupleize(problem.source_keys[child]),child))
    if len(result)!=len(problem.atoms):
        raise ValueError('cyclic census input')
    return tuple(result), widths

def compare(problem):
    start=time.perf_counter()
    result=order_module.solve_order(problem)
    fused_seconds=time.perf_counter()-start
    start=time.perf_counter()
    baseline,widths=source_kahn(problem,result.constraints)
    baseline_seconds=time.perf_counter()-start
    source_start=order_module._source_order(problem,result.constraints)
    reverse=order_module._reverse_frontier(problem,result.constraints)
    baseline_result=replace(result,order=baseline,
        metrics=order_module._metrics(problem,result.constraints,source_start,reverse,baseline))
    order_module.validate_scope_order(problem,baseline_result)
    edges=[(e.provider,e.consumer) for e in problem.edges]+list(result.constraints)
    violations={}
    for label,seq in [('fused',result.order),('kahn',baseline)]:
        pos={a:i for i,a in enumerate(seq)}
        violations[label]=sum(pos[a]>=pos[b] for a,b in edges)
    assert not any(violations.values())
    n=len(problem.atoms)
    changed=result.order!=baseline
    size_bin='0-3' if n<4 else '4-12' if n<=12 else '13-30' if n<=30 else '31+'
    basis=Counter(problem.source_basis.values())
    candidate=changed and 4<=n<=30 and max(widths,default=0)>1 and not basis.get('missing_source',0)
    row=dict(scope_id=problem.scope_id,parent_id=problem.parent_id,size=n,size_bin=size_bin,
        same_order=not changed,source_coverage=dict(basis),anchored_atoms=sum(v is not None for v in problem.source_anchors.values()),
        ready_max=max(widths,default=0),ambiguous_steps=sum(w>1 for w in widths),
        accepted_protected=len(result.constraints),accepted_relation_ids=list(result.accepted_relation_ids),
        rejected_relation_ids=list(result.rejected_relation_ids),soft_relations=len(problem.tie_breaker_relations),
        fused_order=list(result.order),kahn_order=list(baseline),legal_violations=violations,
        fused=result.metrics,kahn=baseline_result.metrics,
        fused_seconds=fused_seconds,kahn_seconds=baseline_seconds,
        search_budget=order_module.ORDER_IMPLEMENTATION['improvement_candidate_budget'],
        budget_exhausted=None,budget_observation='not exposed by production solver',
        candidate=candidate,exclusion=None if candidate else 'same_order' if not changed else 'size_or_freedom_or_missing_source')
    return result,baseline_result,row

def census(bundle, repo, output):
    rows=[]; baselines=[]; problems=[]
    def capture(problem):
        result,baseline,row=compare(problem)
        rows.append(row);baselines.append(baseline);problems.append(asdict(problem))
        return result
    with patch.object(hierarchy_module,'solve_order',capture):
        narrative=derive_narrative_order(bundle,repo)
    data=narrative.to_dict()
    baseline=NarrativeOrder.create(**{k:data[k] for k in (
        'repo_key','repository_revision','repository_input_digest','workspace_digest','source_digest',
        'config_digest','material_digest','binding_digest','order_evidence_digest')},scopes=tuple(baselines))
    fused_h=build_hierarchy(bundle,repo,narrative_order=narrative).to_dict()
    baseline_h=build_hierarchy(bundle,repo,narrative_order=baseline).to_dict()
    assert unit_facts(fused_h)==unit_facts(baseline_h)
    for name,value in [('problems',problems),('fused-order',data),('kahn-order',baseline.to_dict()),
                       ('fused-hierarchy',fused_h),('kahn-hierarchy',baseline_h),('scopes',rows)]:
        write(output/(name+'.json'),value)
    return dict(status='succeeded',declarations=len(bundle.workspace.declarations),bundle_digest=bundle.digest(),
        workspace_digest=bundle.workspace.digest(),source_revision=data['repository_revision'],
        coverage=asdict(bundle.dependency_coverage),diagnostics=list(bundle.diagnostics),
        scopes=len(rows),same_order=sum(r['same_order'] for r in rows),different=sum(not r['same_order'] for r in rows),
        candidates=sum(r['candidate'] for r in rows),size_bins=dict(Counter(r['size_bin'] for r in rows)),
        region_rebuild=dict(fused=region_summary(fused_h),kahn=region_summary(baseline_h),
                            interpretation='Joint order and rebuilt contiguous Region effect; fixed aggregation.'))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'data/experiments/coverage_reader/census')
    args=p.parse_args();output=args.output
    policy=dict(cases=CASES,scope_denominator='All post-helper, post-unary-compression sibling scopes, including identical and singleton/empty cases.',
        candidate_rule='Different legal orders, 4–30 atoms inclusive, ready width > 1, no missing_source atoms; all candidates retained.',
        source_policy='Current default source-location evidence; no added or guessed author sequence.',
        region_policy='Rebuild contiguous Regions for each order; aggregation fixed.',
        native_build=False,model_calls=0,
        implementation=order_module.ORDER_IMPLEMENTATION,order_digest=order_module.ORDER_IMPLEMENTATION_DIGEST,
        census_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write(output/'policy.json',policy)
    results={}
    for label,name in CASES:
        start=time.perf_counter();dest=output/label
        try:
            if label=='coverage':
                bundle=RepositoryBuildBundle.from_json((ROOT/'data/experiments/coverage_reader/fixed/bundle.json').read_text())
            elif label=='sensitivity':
                bundle=load_project(ROOT/'data/research/native/sensitivity/source',repo_key=label,
                    modules=('Sensitivity.Defs','Sensitivity.Multilinear','Sensitivity.Subcube',
                             'Sensitivity.Parity','Sensitivity.HuangBridge','Sensitivity.Main'),
                    build=False,timeout=60,memory_limit_mb=1200,cache_dir=dest/'load-cache')
            else:
                bundle=load_project(INPUT/name,repo_key=label,build=False)
            dest.mkdir(parents=True,exist_ok=True)
            (dest/'bundle.json').write_text(bundle.to_json())
            if any(d.startswith('compiled_acquisition:incomplete:') for d in bundle.diagnostics):
                write(dest/'acquisition-failure.json',dict(diagnostics=list(bundle.diagnostics),
                    coverage=asdict(bundle.dependency_coverage),source_declarations=len(bundle.workspace.declarations)))
                raise RuntimeError('Compiled acquisition incomplete under the fixed 1200 MB budget; source fallback retained, primary census skipped.')
            results[label]=census(bundle,label,dest)
        except Exception as exc:
            results[label]=dict(status='failed',error=type(exc).__name__+': '+str(exc))
        results[label]['elapsed_seconds']=time.perf_counter()-start
        write(output/'report.json',results)
        print(label,json.dumps({k:v for k,v in results[label].items() if k in ('status','scopes','same_order','different','candidates','elapsed_seconds','error')}),flush=True)
    rows=['| Project | Scope | Atoms | Ready width | Same | Candidate / exclusion | Distance fused/Kahn | Frontier fused/Kahn | Displacement fused/Kahn |',
          '|---|---|---:|---:|---|---|---:|---:|---:|']
    for label,_ in CASES:
        path=output/label/'scopes.json'
        if results[label]['status']!='succeeded':
            rows.append(f'| {label} | FAILED | | | | {results[label]["error"]} | | | |');continue
        for r in json.loads(path.read_text()):
            f,k=r['fused'],r['kahn']
            rows.append(f'| {label} | {r["scope_id"]} | {r["size"]} | {r["ready_max"]} | {r["same_order"]} | {"candidate" if r["candidate"] else r["exclusion"]} | {f["dependency_distance"]}/{k["dependency_distance"]} | {f["frontier_peak"]}/{k["frontier_peak"]} | {f["source_displacement"]}/{k["source_displacement"]} |')
    (output/'windows.md').write_text('\n'.join(rows)+'\n')

if __name__=='__main__':
    main()
