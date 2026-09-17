from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structure"))
from test_graph import P, bundle, ref, workspace
from lean_exposition.models import Scope, TextContent
from lean_exposition.structure import BuildConfig, build_hierarchy as _build_hierarchy
from lean_exposition.features import extract_features
from lean_exposition.recommendation import RecommendationConfig, make_structural_policy


def setup(w, *, compress=True, targets=()):
    h = _build_hierarchy(bundle(w, "preserve"), "r",
                         config=BuildConfig(scope_compression="unary" if compress else "none"))
    f = extract_features(w, h)
    return h, f, make_structural_policy(w, h, f, targets=targets)


def score(policy, h, expanded=(), candidates=None, **extra):
    return policy({"view_id": "unused", "hierarchy_id": h.hierarchy_id, "expanded": list(expanded), "locale": "en", **extra},
                  candidates or [h.root_id])["recommendations"]


class StructuralRecommendationTests(unittest.TestCase):
    def test_target_provider_closure_and_default_no_outcomes(self):
        w = workspace("abcd", [(ref("a"), ref("b"), "lean_type"), (ref("b"), ref("c"), "lean_value")], outcomes=(ref("c"),))
        h, f, policy = setup(w)
        target = score(policy, h)[0]["components"]["target"]
        names = {n["representative"]["local_id"] for n in h.nodes if n["id"] in target["unit_ids"]}
        self.assertEqual(names, set("abc"))
        w = replace(w, manifest=replace(w.manifest, repositories=(replace(w.manifest.repositories[0], primary_outcomes=()), *w.manifest.repositories[1:])))
        h, f, policy = setup(w)
        target = score(policy, h)[0]["components"]["target"]
        self.assertEqual(target["count"], 4)
        self.assertEqual(target["basis"], "all_internal_units_no_outcomes")

    def test_exact_gains_and_duplicate_pair_deduplication(self):
        w = workspace("ab", [(ref("a"), ref("b"), "lean_type"), (ref("a"), ref("b"), "lean_value")])
        h, f, policy = setup(w)
        gains = score(policy, h)[0]["components"]["structural_gain"]
        self.assertEqual(gains["G_decl"], .5)
        self.assertEqual(gains["G_dep"], .5)
        self.assertEqual(gains["new_decl_pairs"], 1)

    def test_unary_layer_zero_gain_and_no_after_content_required(self):
        w = workspace("ab", scopes={"a": "a", "b": "a"})
        w = replace(w, scopes=(w.scopes[0], w.scopes[1], w.scopes[2]))
        h, f, policy = setup(w, compress=False)
        gains = score(policy, h)[0]["components"]["structural_gain"]
        self.assertEqual(gains["G_decl"], 0)
        self.assertEqual(gains["G_dep"], 0)
        # Context has no block, manifest, generated text, or runtime.
        self.assertTrue(score(policy, h))

    def test_type_missing_not_high_cost_but_required_formal_missing_is(self):
        w = workspace("ab")
        h, f, policy = setup(w)
        self.assertNotEqual(score(policy, h)[0]["components"]["estimated_cost"]["tier"], "high")
        d = w.declarations[0]
        w = replace(w, declarations=(replace(d, statement=replace(d.statement, formal=TextContent(None, "missing", P, reason="missing"))), w.declarations[1]))
        h, f, policy = setup(w)
        row = score(policy, h)[0]["components"]
        self.assertEqual(row["estimated_cost"]["tier"], "high")
        self.assertTrue(row["feature_coverage"]["missing_cost_material"])

    def test_floor_is_not_free_and_ties_use_source_order(self):
        w = workspace("abcd", scopes={"a": "a", "b": "a", "c": "b", "d": "b"})
        h, f, policy = setup(w)
        root = next(n for n in h.nodes if n["id"] == h.root_id)
        candidates = root["children"]
        rows = score(policy, h, [h.root_id], list(reversed(candidates)), synopsis_codepoints={id: 100000 for id in candidates})
        self.assertEqual([r["target_id"] for r in rows], candidates)
        self.assertTrue(all(r["components"]["estimated_cost"]["codepoints"] == 120 for r in rows))

    def test_restored_expanded_descendants_count_io_and_visible_terminal_cost(self):
        w = workspace("abc", scopes={"a": "a", "b": "a", "c": "b"})
        h, f, policy = setup(w, compress=False)
        a = h.resolve_node_id("a")
        row = score(policy, h, expanded=[a])[0]["components"]
        ids = {item["node_id"] for item in row["cost_basis"]["children"]}
        self.assertIn(a, ids)
        self.assertTrue(any(item["expanded_section_io_approximation"] for item in row["cost_basis"]["children"]))
        self.assertGreater(row["structural_gain"]["G_decl"], 0)
        self.assertEqual(len(ids), 4)  # two sections plus the two re-exposed terminals in A

    def test_current_configuration(self):
        config = RecommendationConfig().to_dict()
        self.assertEqual([config[k] for k in ("material_small", "material_medium", "planned_small", "planned_medium", "planned_large", "minimum_increment", "tier_low_max", "tier_medium_max")], [800, 3000, 240, 600, 1200, 120, 600, 1600])
        self.assertEqual(config["ordering"][:3], ["cost_tier", "G_decl_descending", "G_dep_descending"])
        self.assertIn("Uncalibrated", config["calibration"])
        self.assertEqual(config["policy"], "structural")
        self.assertEqual(len(config["config_digest"]), 64)

    def test_wrong_structure_and_illegal_candidates_fail(self):
        h, f, policy = setup(workspace("a"))
        with self.assertRaises(ValueError):
            policy({"hierarchy_id": "wrong", "expanded": []}, [h.root_id])
        with self.assertRaises(ValueError):
            score(policy, h, expanded=[h.root_id])


if __name__ == "__main__": unittest.main()
