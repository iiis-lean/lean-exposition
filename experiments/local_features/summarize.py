"""Generate an auditable compact result summary after all paired runs complete."""
import json
from pathlib import Path
from collections import Counter
from prepare import OUT
from protocol import LABELS


def main():
    agreement=json.loads((OUT/'agreement.json').read_text())
    review=json.loads((OUT/'feature_review.json').read_text())
    labels={p:json.loads((OUT/f'labels_{p}.json').read_text())for p in ['glm','luna']}
    manifest=json.loads((OUT/'sample_manifest.json').read_text())
    project={r['sample_id']:r['project']for r in manifest['samples']}
    summary={'populations':{p:r['eligible_count']for p,r in manifest['populations'].items()},'sample_counts':dict(Counter(project.values())),
        'agreement_all':agreement['groups']['all'],'agreement_random':agreement['groups']['random'],'label_counts_by_project':{},'runs':{},'coverage':review['coverage_by_project']}
    for provider in labels:
        summary['label_counts_by_project'][provider]={p:{k:dict(Counter(r[k]['value']for r in labels[provider]if project[r['sample_id']]==p))for k in LABELS}for p in sorted(set(project.values()))}
        attempts=[json.loads(path.read_text())for path in (OUT/'runs'/provider).glob('*attempt*.json')]
        finals=[json.loads(path.read_text())for path in (OUT/'runs'/provider).glob('batch_??.json')]
        summary['runs'][provider]={'attempts':len(attempts),'valid_final_batches':len(finals),'failed_attempts':sum(not r['valid']for r in attempts),'seconds_final_sum':sum(r['seconds']for r in finals),'model':finals[0]['model'],'reasoning':'max'}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines=['# Local feature experiment results','', '50 frozen declarations, two exploratory reference-label sets, 40 stratified-random and 10 separate boundary cases. These are descriptive results, not gold-standard labels or evidence of reading benefit.','',
        '| Label | Three-category agreement | Kappa | Definite pairs | Definite agreement | Luna / GLM uncertain |','|---|---:|---:|---:|---:|---:|']
    for label,row in summary['agreement_all'].items():
        kappa='null'if row['cohen_kappa']is None else f"{row['cohen_kappa']:.3f}"
        lines.append(f"| {label} | {row['agreement']:.1%} | {kappa} | {row['definite_pairs']} | {row['definite_agreement']:.1%} | {row['luna_uncertain_rate']:.1%} / {row['glm_uncertain_rate']:.1%} |")
    lines += ['', '## Project label counts', '', '| Model / project | A yes | B yes | C yes | D yes |','|---|---:|---:|---:|---:|']
    for provider,projects in summary['label_counts_by_project'].items():
        for name,row in projects.items():lines.append('| '+provider+' / '+name+' | '+' | '.join(str(row[k].get('yes',0))for k in LABELS)+' |')
    lines += ['', '## Coverage and interpretation', '',
        'Formal source size and dependency counts cover all 50 declarations. NL size is missing for cards without NL rather than encoded as zero. Complete compiled type/telescope and parsed command Syntax cover 24 native declarations; 26 LC declarations have explicit missing compiled/Syntax metrics because a matching fixed-version artifact was unavailable. No dirty or different-version artifact was substituted.', '',
        'The 24 native samples contain no direct Exists/Sigma conclusion and no proposition-parameter binder. These candidates have no observed variation here; this cannot establish general uselessness. A real Lean fixture separately validates Exists/Sigma and the distinction between P : Prop and h : P.', '',
        'Each model has independent per-project and per-stratum feature analyses, using fixed within-project random-sample quartiles. The full cross-tabs, definite-pair medians/ranges and uncertainty coverage are in feature_review.json. Pooled quartile tables aggregate project-relative bins; they do not erase project differences. Small cells and model disagreements preclude predictive claims.', '',
        'Dependency bodies are absent from local cards. The provided material can therefore leave an application\'s mathematical role unclear. A model choosing a definite label does not remove this limitation.', '',
        'Recompute: prepare.py → extract.py → validate_probe.py → tactic_groups.py → label.py → analyze.py → verify.py → summarize.py. label.py reuses valid matching saved batches. See README.md for the environment and protocol.', '',
        'Artifacts under ignored data/research/feature_selection: sample_manifest.json, cards.json, local_features.json, compiled_evidence.json, labels_luna.json, labels_glm.json, agreement.json, feature_review.json, retained_features.json, validation.json and per-attempt raw outputs in runs/.']
    lines += ['', 'See [INTERPRETATION.md](INTERPRETATION.md) for rubric-adherence checks, actual per-candidate evidence, native-only limits and conservative retain/defer recommendations.']
    Path(__file__).with_name('RESULTS.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary['runs']))

if __name__=='__main__':main()
