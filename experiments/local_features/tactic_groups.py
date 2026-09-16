"""Prespecified exact Syntax-kind groups, not semantic tactic-step counts."""
import json
from prepare import OUT

# Known Lean parser node names only; nested pattern/config/location wrappers are excluded.
GROUPS = {
 'tactic_case_induction_nodes': ['Lean.Parser.Tactic.cases','Lean.Parser.Tactic.induction','Lean.Parser.Tactic.rcases','Lean.Parser.Tactic.byCases','Lean.Parser.Tactic.obtain'],
 'tactic_construct_apply_nodes': ['Lean.Parser.Tactic.constructor','Lean.Parser.Tactic.refine','Lean.Parser.Tactic.exact','Lean.Parser.Tactic.apply'],
 'tactic_rewrite_simplify_nodes': ['Lean.Parser.Tactic.rwSeq','Lean.Parser.Tactic.tacticRwa__','Lean.Parser.Tactic.simp','Lean.Parser.Tactic.simpAll','Lean.Parser.Tactic.simpa','Lean.Parser.Tactic.calc'],
}


def augment(rows):
    for row in rows:
        syntax=(row.get('compiled')or{}).get('syntax')
        for feature,kinds in GROUPS.items():
            row['features'][feature]=sum(kind in kinds for kind in syntax['tactic_kinds'])if syntax and syntax['status']=='parsed'else None
    return rows


def main():
    path=OUT/'local_features.json';rows=json.loads(path.read_text());augment(rows)
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (OUT/'syntax_kind_groups.json').write_text(json.dumps({'version':'exact-kind-groups-v1','groups':GROUPS,'definition':'Counts occurrences of exact parser nodes, not executed tactics or mathematical steps. Includes obtain as explicit decomposition; excludes rcasesPat/config/location/rwRule wrappers. Term-level calc and custom tactic macros outside these kind names are not counted; zero means no mapped node, not absence of the semantic method.','coverage':'native parsed command AST only; all LC missing'},indent=2))
    print('Fixed Syntax-kind groups added without changing cards or labels')

if __name__=='__main__':main()
