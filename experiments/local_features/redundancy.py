"""Descriptive within-project redundancy checks; no label-guided selection."""
import json
import math
from prepare import OUT

PAIRS=[('formal_chars','formal_lines'),('formal_chars','syntax_nodes'),('syntax_nodes','tactic_syntax_nodes'),('type_expr_nodes','type_expr_depth')]


def ranks(values):
    ordered=sorted(enumerate(values),key=lambda x:x[1]);result=[0.0]*len(values);i=0
    while i<len(ordered):
        j=i+1
        while j<len(ordered)and ordered[j][1]==ordered[i][1]:j+=1
        for k in range(i,j):result[ordered[k][0]]=(i+j-1)/2
        i=j
    return result


def spearman(xs,ys):
    a=ranks(xs);b=ranks(ys);n=len(a)
    if n<2:return None
    ma=sum(a)/n;mb=sum(b)/n
    denominator=math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb)for x,y in zip(a,b))/denominator if denominator else None


def main():
    rows=json.loads((OUT/'local_features.json').read_text());result={}
    for project in sorted({r['project']for r in rows}):
        result[project]={}
        for a,b in PAIRS:
            values=[(r['features'][a],r['features'][b])for r in rows if r['project']==project and r['stratum']=='random'and r['features'][a]is not None and r['features'][b]is not None]
            result[project][a+'/'+b]={'n':len(values),'spearman':spearman([v[0]for v in values],[v[1]for v in values])}
    (OUT/'feature_redundancy.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
