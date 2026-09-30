"""Source module filtering uses fixed providers and complete pair evidence."""
from dataclasses import replace
import unittest

from lean_exposition.models import Dependency, Workspace, ValidationError
from lean_exposition.structure import DependencyGraph
from lean_exposition.structure.dependencies import DependencyAnalysisPolicy, load_foundation_catalog
import test_dependencies as fixtures
from test_dependencies import analysis_workspace, P, OTHER_REV, REV


class SourceDependencyTests(unittest.TestCase):
    def workspace(self, repo, name, modules):
        current = analysis_workspace(OTHER_REV)
        a, b = current.declarations
        deps = tuple(Dependency(type(a.ref)(repo, name), 'lean_type', P,
                                provider_module=module) for module in modules)
        return replace(current, declarations=(replace(a, statement=replace(a.statement, deps=deps)), b))

    def decision(self, current, catalog=None):
        return DependencyAnalysisPolicy(catalog or load_foundation_catalog()).analyze(
            current, 'target', DependencyGraph.from_workspace(current).edges).decisions[0]

    def test_source_rules_work_when_exact_revision_is_absent(self):
        for repo, name, module in (
            ('target/lean', 'Nat.add', 'Init.Data.Nat.Basic'),
            ('target/dependency/mathlib', 'Finset.filter', 'Mathlib.Data.Finset.Filter'),
            ('target/dependency/mathlib', 'Mathlib.Tactic.Ring.of_eq', 'Mathlib.Tactic.Ring.Basic'),
        ):
            with self.subTest(module=module):
                current = self.workspace(repo, name, (module,))
                before = current.to_json()
                full = DependencyGraph.from_workspace(current)
                analysis = DependencyAnalysisPolicy(load_foundation_catalog()).analyze(current, 'target', full.edges)
                self.assertFalse(analysis.decisions[0].keep)
                self.assertTrue(analysis.decisions[0].reason.startswith('source_module:'))
                self.assertEqual(len(full.edges), 1)
                self.assertEqual(len(full.analysis_view(analysis).edges), 0)
                self.assertEqual(current.to_json(), before)
                self.assertEqual(Workspace.from_json(before), current)
                no_exact_catalog = replace(load_foundation_catalog(), providers=())
                self.assertFalse(self.decision(current, no_exact_catalog).keep)

    def test_internal_other_project_unknown_and_mathematics_are_preserved(self):
        for repo, name, module in (
            ('target', 'Lean.custom', 'Lean.Custom'),
            ('WeightedSieve', 'Finset.custom', 'Mathlib.Data.Finset.Filter'),
            ('target/external/unknown', 'x', 'Mathlib.Tactic.Ring.Basic'),
            ('target/dependency/mathlib', 'Real.sqrt', 'Mathlib.Analysis.Real.Sqrt'),
            ('target/dependency/mathlib', 'Sensitivity.huang_degree_theorem', 'Mathlib.Combinatorics.SimpleGraph.DegreeSum'),
            ('target/dependency/mathlib', 'Finset.dens', 'Mathlib.Data.Finset.Density'),
            ('target/dependency/mathlib', 'Set.Countable', 'Mathlib.Data.Set.Countable'),
            ('target/dependency/mathlib', 'Nat.someTheorem', 'Mathlib.NumberTheory.Basic'),
            ('target/dependency/mathlib', 'Finset.custom', 'Mathlib.Data.Finset.Filtered'),
            ('target/dependency/mathlib', 'Mathlib.Tactic.unknown', None),
        ):
            with self.subTest(name=name):
                self.assertTrue(self.decision(self.workspace(repo, name, (module,))).keep)

    def test_all_occurrences_must_have_consistent_source(self):
        for modules in (
            ('Mathlib.Data.Finset.Filter', None),
            ('Mathlib.Data.Finset.Filter', 'Mathlib.Combinatorics.Other'),
        ):
            current = self.workspace('target/dependency/mathlib', 'Finset.filter', modules)
            self.assertTrue(self.decision(current).keep)
        current = self.workspace('target/dependency/mathlib', 'Finset.filter',
                                 ('Mathlib.Data.Finset.Filter',))
        a, b = current.declarations
        current = replace(current, declarations=(replace(a, proof=replace(a.statement,
            deps=(replace(a.statement.deps[0], evidence_kind='lean_value', provider_module=None),))), b))
        self.assertTrue(self.decision(current).keep)

    def test_explicit_important_outcome_overrides_module_rule(self):
        current = self.workspace('target/dependency/mathlib', 'Finset.filter', ('Mathlib.Data.Finset.Filter',))
        ref = current.declarations[0].statement.deps[0].provider
        current = replace(current, manifest=replace(current.manifest, repositories=tuple(
            replace(r, primary_outcomes=(ref,)) if r.repo_key == ref.repo_key else r
            for r in current.manifest.repositories)))
        self.assertEqual(self.decision(current).reason, 'important_outcome')

    def test_exact_keep_and_occurrence_restrictions_override_batch_rules(self):
        catalog = replace(fixtures.DependencyAnalysisTests().catalog(),
                          module_rules=load_foundation_catalog().module_rules)
        for name, expected in (('Base.inst', 'reviewed_keep'),
                               ('Base.owner.rec', 'foundation_not_applicable_to_occurrence')):
            current = self.workspace('target/dependency/mathlib', name, ('Mathlib.Tactic.Ring.Basic',))
            current = replace(current, manifest=replace(current.manifest, repositories=tuple(
                replace(r, revision=REV) if r.repo_key == 'target/dependency/mathlib' else r
                for r in current.manifest.repositories)))
            decision = self.decision(current, catalog)
            self.assertTrue(decision.keep)
            self.assertEqual(decision.reason, expected)
        # An exact keep from a different revision cannot masquerade as reviewed.
        current = self.workspace('target/dependency/mathlib', 'Base.inst', ('Mathlib.Tactic.Ring.Basic',))
        self.assertFalse(self.decision(current, catalog).keep)

    def test_module_type_and_nonempty_validation(self):
        current = self.workspace('target/dependency/mathlib', 'x', ('',))
        with self.assertRaises(ValidationError):
            current.validate()

    def test_missing_fixed_provider_identity_is_not_a_module_rule_match(self):
        current = self.workspace('target/dependency/mathlib', 'Finset.filter', ('Mathlib.Data.Finset.Filter',))
        for changes in ({'toolchain': None}, {'revision': None, 'version_status': 'unresolved',
                                             'unresolved_reason': 'missing lock'}):
            changed = replace(current, manifest=replace(current.manifest, repositories=tuple(
                replace(r, **changes) if r.repo_key == 'target/dependency/mathlib' else r
                for r in current.manifest.repositories)))
            decision = self.decision(changed)
            self.assertTrue(decision.keep)
            self.assertEqual(decision.reason, 'provider_unresolved')

    def test_rule_fingerprint_changes_with_source_rules(self):
        catalog = load_foundation_catalog()
        policy = DependencyAnalysisPolicy(catalog)
        current = self.workspace('target/dependency/mathlib', 'Finset.filter', ('Mathlib.Data.Finset.Filter',))
        edges = DependencyGraph.from_workspace(current).edges
        empty = DependencyAnalysisPolicy(replace(catalog, module_rules=()))
        self.assertNotEqual(policy.analyze(current, 'target', edges).catalog_digest,
                            empty.analyze(current, 'target', edges).catalog_digest)

    def test_default_features_views_and_recommendation_share_rules_and_reject_stale_cache(self):
        from test_graph import bundle
        from lean_exposition.structure import build_hierarchy
        from lean_exposition.features import extract_features
        from lean_exposition.exposition import scope_view
        from lean_exposition.recommendation import make_structural_policy

        current = self.workspace('target/dependency/mathlib', 'Finset.filter', ('Mathlib.Data.Finset.Filter',))
        hierarchy = build_hierarchy(bundle(current), 'target')
        features = extract_features(current, hierarchy)
        self.assertEqual(features.metadata['hidden_dependency_pairs'], 1)
        self.assertEqual(features.nodes[hierarchy.root_id]['interface']['incoming_pairs'], 0)
        self.assertEqual(scope_view(current, hierarchy.to_dict(), hierarchy.root_id)['incoming'], [])
        self.assertEqual(len(scope_view(current, hierarchy.to_dict(), hierarchy.root_id,
                                        full_dependencies=True)['incoming']), 1)
        make_structural_policy(current, hierarchy, features)
        old_catalog = replace(load_foundation_catalog(), module_rules=())
        old = DependencyAnalysisPolicy(old_catalog).analyze(current, 'target', DependencyGraph.from_workspace(current).edges)
        stale = extract_features(current, hierarchy, dependency_analysis=old)
        with self.assertRaisesRegex(ValueError, 'dependency analysis'):
            make_structural_policy(current, hierarchy, stale)


if __name__ == '__main__':
    unittest.main()
