from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structure"))
from test_graph import P, bundle, ref, workspace
from lean_exposition.features import FeatureSet, extract_features
from lean_exposition.features.core import FEATURE_CONFIG_DIGEST, ref_key
from lean_exposition.models import TextContent
from lean_exposition.structure import BuildConfig, build_hierarchy as _build_hierarchy


def build_hierarchy(workspace, repo_key, **kwargs):
    return _build_hierarchy(bundle(workspace, "preserve"), repo_key, **kwargs)


class FeatureTests(unittest.TestCase):
    def test_missing_compiled_is_not_zero_and_does_not_poison_formal_cost(self):
        w = workspace("ab")
        h = build_hierarchy(w, "r", config=BuildConfig())
        f = extract_features(w, h)
        row = f.declarations[ref_key(ref("a"))]
        self.assertIsNone(row["metrics"]["type_expr_nodes"]["value"])
        self.assertEqual(row["metrics"]["formal_material_codepoints"]["value"], 7)
        self.assertEqual(row["metrics"]["formal_proof_codepoints"]["status"], "not_applicable")
        self.assertEqual(FeatureSet.from_dict(f.to_dict()), f)

    def test_pair_occurrences_are_deduplicated(self):
        deps = [(ref("a"), ref("b"), "lean_type"), (ref("a"), ref("b"), "lean_value")]
        w = workspace("ab", deps)
        h = build_hierarchy(w, "r", config=BuildConfig()).to_dict()
        f = extract_features(w, h)
        self.assertEqual(f.nodes[h["root_id"]]["decl_count"], 2)
        self.assertEqual(f.nodes[h["root_id"]]["interface"]["internal_pairs"], 1)
        self.assertEqual(f.nodes[h["root_id"]]["metrics"]["formal_material_codepoints"]["value"], 14)

    def test_partial_formal_and_nl_coverage_remain_explicit(self):
        w = workspace("ab")
        d = w.declarations[0]
        missing = TextContent(None, "missing", P, reason="unavailable")
        w = replace(w, declarations=(replace(d, statement=replace(d.statement, formal=missing, nl=missing)), w.declarations[1]))
        h = build_hierarchy(w, "r", config=BuildConfig())
        f = extract_features(w, h)
        material = f.nodes[h.root_id]["metrics"]["formal_material_codepoints"]
        self.assertIsNone(material["value"])
        self.assertEqual(material["coverage"], .5)
        self.assertEqual(material["observed_sum"], 7)

    def test_compiled_provenance_and_exact_syntax_groups(self):
        w = workspace("a")
        h = build_hierarchy(w, "r")
        row = {"lean_name": "a", "binders": [{"category": "object_parameter"}, {"category": "instance"}],
               "type_expr_nodes": 12, "type_expr_depth": 4, "full_type": "fixture",
               "syntax": {"status": "parsed", "tactic_kinds": ["Lean.Parser.Tactic.simp", "Lean.Parser.Tactic.simpConfig", "custom.simp"]}}
        evidence = {"repo_key": "r", "workspace_digest": w.digest(), "config_digest": FEATURE_CONFIG_DIGEST, "rows": [row]}
        f = extract_features(w, h, compiled=evidence)
        m = f.declarations[ref_key(ref("a"))]["metrics"]
        self.assertEqual(m["binder_object_parameter"]["value"], 1)
        self.assertEqual(m["binder_proof_premise"]["value"], 0)
        self.assertEqual(m["rewrite_simplify_syntax_nodes"]["value"], 1)
        evidence["workspace_digest"] = "wrong"
        with self.assertRaises(ValueError):
            extract_features(w, h, compiled=evidence)
        evidence["workspace_digest"] = w.digest()
        evidence["config_digest"] = "wrong"
        with self.assertRaises(ValueError):
            extract_features(w, h, compiled=evidence)


if __name__ == "__main__": unittest.main()
