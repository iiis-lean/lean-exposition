"""Recompute descriptive agreement and prespecified feature cross-tabs."""
from collections import Counter
import json
from pathlib import Path
import statistics

from prepare import OUT
from protocol import LABELS


def agreement(left,right):
    n=len(left); counts=Counter(zip(left,right)); same=sum(a==b for a,b in zip(left,right))
    lm=Counter(left);rm=Counter(right); expected=sum(lm[k]*rm[k]for k in ['yes','no','uncertain'])/(n*n)if n else None
    observed=same/n if n else None
    kappa=(observed-expected)/(1-expected)if n and expected!=1 else None
    definite=[(a,b)for a,b in zip(left,right)if a!='uncertain'and b!='uncertain']
    return {'n':n,'agreement':observed,'cohen_kappa':kappa,'kappa_null_reason':('empty sample'if not n else 'expected agreement equals one (degenerate constant marginals)')if kappa is None else None,
        'table':{a:{b:counts[a,b]for b in ['yes','no','uncertain']}for a in ['yes','no','uncertain']},
        'definite_pairs':len(definite),'definite_agreement':sum(a==b for a,b in definite)/len(definite)if definite else None,
        'luna_uncertain_rate':lm['uncertain']/n if n else None,'glm_uncertain_rate':rm['uncertain']/n if n else None,
        'either_uncertain_rate':sum(a=='uncertain'or b=='uncertain'for a,b in zip(left,right))/n if n else None,
        'both_uncertain_rate':counts['uncertain','uncertain']/n if n else None}


def quantile(values,p):
    values=sorted(values);index=(len(values)-1)*p;low=int(index);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(index-low)


def summary(values):
    return {'n':len(values),'median':statistics.median(values)if values else None,'min':min(values)if values else None,'max':max(values)if values else None}


def main():
    features=json.loads((OUT/'local_features.json').read_text());ids={r['sample_id']for r in features}
    labels={provider:{r['sample_id']:r for r in json.loads((OUT/f'labels_{provider}.json').read_text())}for provider in ['luna','glm']}
    assert set(labels['luna'])==ids==set(labels['glm']), 'All 50 paired labels required'
    groups={'all':features,'random':[r for r in features if r['stratum']=='random'],'boundary':[r for r in features if r['stratum']=='boundary']}
    for project in sorted({r['project']for r in features}):
        for stratum in ['all','random','boundary']:
            groups[project+'/'+stratum]=[r for r in features if r['project']==project and (stratum=='all'or r['stratum']==stratum)]
    agreements={group:{label:agreement([labels['luna'][r['sample_id']][label]['value']for r in rows],[labels['glm'][r['sample_id']][label]['value']for r in rows])for label in LABELS}for group,rows in groups.items()}
    disagreements=[]
    for row in features:
        for label in LABELS:
            a=labels['luna'][row['sample_id']][label];b=labels['glm'][row['sample_id']][label]
            if a['value']!=b['value']:disagreements.append({'sample_id':row['sample_id'],'project':row['project'],'stratum':row['stratum'],'label':label,'luna':a,'glm':b})
    (OUT/'agreement.json').write_text(json.dumps({'category_order':['yes','no','uncertain'],'groups':agreements,'disagreements':disagreements},ensure_ascii=False,indent=2))
    keys=[k for k,v in features[0]['features'].items()if not k.endswith('_reason')]
    cuts={}; coverage={};review={}
    projects=sorted({r['project']for r in features})
    for key in keys:
        cuts[key]={}
        for project in projects:
            values=[r['features'][key]for r in groups['random']if r['project']==project and r['features'][key]is not None]
            cuts[key][project]=[quantile(values,p)for p in [.25,.5,.75]]if values else None
        coverage[key]={project:sum(r['features'][key]is not None for r in features if r['project']==project)for project in projects}
    for provider in labels:
        review[provider]={}
        for group,rows in groups.items():
            review[provider][group]={}
            for label in LABELS:
                feature_stats={}
                for key in keys:
                    pairs=[(r['features'][key],labels[provider][r['sample_id']][label]['value'])for r in rows if r['features'][key]is not None]
                    binary=[(v,y)for v,y in pairs if y!='uncertain']
                    quartiles={str(i):{'yes':0,'no':0,'uncertain':0}for i in range(1,5)}
                    for row in rows:
                        value=row['features'][key]; boundaries=cuts[key][row['project']]
                        if value is not None and boundaries is not None:
                            category=labels[provider][row['sample_id']][label]['value']
                            quartiles[str(1+sum(value>cut for cut in boundaries))][category]+=1
                    feature_stats[key]={'feature_observed':len(pairs),'definite_pairs':len(binary),'uncertain_observed':sum(y=='uncertain'for _,y in pairs),
                        'yes':summary([v for v,y in binary if y=='yes']),'no':summary([v for v,y in binary if y=='no']),
                        'quartile_cross_tab':quartiles,'constant_observed':len({v for v,_ in pairs})<=1}
                review[provider][group][label]={'label_counts':dict(Counter(labels[provider][r['sample_id']][label]['value']for r in rows)), 'features':feature_stats}
    (OUT/'feature_review.json').write_text(json.dumps({'quartiles_from':'fixed within-project random stratum, available values only; pooled tables aggregate project-relative quartile bins; bins use <= boundaries, tied cuts retained without threshold search','quartile_cuts':cuts,'coverage_by_project':coverage,'analyses':review},ensure_ascii=False,indent=2))
    print(json.dumps({'agreement':agreements['all'],'disagreements':len(disagreements)},indent=2))

if __name__=='__main__':main()
