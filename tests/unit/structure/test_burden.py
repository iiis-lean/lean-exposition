from dataclasses import replace
import unittest
from test_graph import workspace
from lean_exposition.features import extract_features
from lean_exposition.structure import build_hierarchy
from lean_exposition.structure.regions import partition_regions


def leaves(tree):
    return [x for part in tree for x in ([part] if isinstance(part, str) else leaves(part))]


def objective(tree, weights):
    atoms = leaves(tree)
    total = 0
    for (a, b), weight in weights.items():
        if a in atoms and b in atoms:
            child = next((part for part in tree if not isinstance(part, str) and a in leaves(part) and b in leaves(part)), None)
            total += objective(child, {(a, b): weight}) if child else len(atoms) * weight
    return total


class BurdenTests(unittest.TestCase):
    def test_exact_primary_tie_selects_different_legal_group(self):
        items = tuple('abcdefgh')
        plain, cost, _ = partition_regions(items, {(0, 7): 1}, k=3)
        alternative, weighted_cost, _ = partition_regions(items, {(0, 7): 1}, k=3, burdens=[100, 1, 1, 1, 1, 1, 1, 1])
        self.assertNotEqual(plain, alternative)
        self.assertEqual(cost, weighted_cost)
        self.assertEqual(objective(plain, {('a', 'h'): 1}), objective(alternative, {('a', 'h'): 1}))
        self.assertEqual(leaves(alternative), list(items))
        def check(t):
            self.assertTrue(2 <= len(t) <= 3)
            for part in t:
                if not isinstance(part, str): check(part)
        check(alternative)

    def test_default_unchanged_and_features_bound_to_new_identity(self):
        w = workspace('abc')
        default = build_hierarchy(w, 'r')
        f = extract_features(w, default)
        alternative = build_hierarchy(w, 'r', region_burden_features=f)
        self.assertEqual(default.to_dict(), build_hierarchy(w, 'r', region_burden_features=None).to_dict())
        self.assertNotIn('region_burden', default.config)
        self.assertEqual(default.edges, alternative.edges)
        self.assertNotEqual(default.hierarchy_id, alternative.hierarchy_id)
        self.assertEqual(extract_features(w, alternative).hierarchy_id, alternative.hierarchy_id)
        with self.assertRaises(ValueError):
            build_hierarchy(w, 'r', region_burden_features=replace(f, workspace_digest='stale'))

    def test_missing_disables_and_balanced_fallback_unchanged(self):
        w = workspace('abc')
        f = extract_features(w, build_hierarchy(w, 'r')).to_dict()
        next(iter(f['declarations'].values()))['metrics']['formal_material_codepoints']['value'] = None
        h = build_hierarchy(w, 'r', region_burden_features=f)
        root = next(n for n in h.nodes if n['id'] == h.root_id)
        self.assertEqual(root['metadata']['region']['burden_status'], 'missing_disabled')
        self.assertEqual(partition_regions(tuple('abcdefgh'), k=3), partition_regions(tuple('abcdefgh'), k=3, burdens=[100,1,1,1,1,1,1,1]))
