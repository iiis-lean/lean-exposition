"""Partial-order evidence projection and deterministic conflict handling."""
import hashlib
import unittest

from test_graph import bundle, workspace, ref
from lean_exposition.construction import MaterialBundle
from lean_exposition.models import DeclRef, Provenance
from lean_exposition.structure import build_hierarchy, derive_narrative_order
from lean_exposition.structure.order import (
    OrderEdge, OrderEvidenceBundle, OrderProblem, OrderRelation, OrderSequence, OrderSubject,
    ProjectedOrderRelation, SubjectProjection, project_order_evidence, solve_order,
)
from lean_exposition.structure.source import (
    SourceSequence, SourceSequenceSpec, derive_sequence_order_evidence,
)


D = hashlib.sha256(b"fixture").hexdigest()
P = (Provenance("fixture", "order"),)


def subject(name):
    return OrderSubject("declaration", ref=DeclRef("repo", name))


def relation(name, before, after, *, producer="lc_tex_document", source_order=0,
             strength="protected"):
    return OrderRelation(name, subject(before), subject(after), strength, "fixture", producer,
                         source_order, P)


def projections(*names):
    return {subject(name).canonical_key(): SubjectProjection("exact", (name,)) for name in names}


def problem(projected, edges=()):
    atoms = ("A", "B", "C")
    return OrderProblem("scope", "parent", atoms,
                        tuple(OrderEdge(a, b, weight) for a, b, weight in edges),
                        {atom: (atom,) for atom in atoms},
                        {atom: "fixture" for atom in atoms},
                        {atom: None for atom in atoms}, {atom: None for atom in atoms}, (),
                        projected.protected, projected.tie_breakers)


class OrderEvidenceTests(unittest.TestCase):
    def test_manual_protected_cycle_fails_at_parse(self):
        with self.assertRaisesRegex(ValueError, "manual protected.*cyclic"):
            OrderEvidenceBundle.create(material_digest=D, binding_digest=D, relations=(
                relation("ab", "A", "B", producer="manual_config"),
                relation("ba", "B", "A", producer="manual_config"),
            ))

    def test_automatic_and_fuzzy_evidence_cannot_be_protected(self):
        with self.assertRaisesRegex(ValueError, "cannot be protected"):
            relation("auto", "A", "B", producer="automatic_tex")
        with self.assertRaisesRegex(ValueError, "cannot be protected"):
            relation("fuzzy", "A", "B", producer="fuzzy_match")

    def test_nonmanual_cycle_has_stable_acyclic_subset_and_rejected_id(self):
        relations = (
            relation("r1", "A", "B", source_order=1),
            relation("r2", "B", "C", source_order=2),
            relation("r3", "C", "A", source_order=3),
        )
        results = []
        for items in (relations, tuple(reversed(relations))):
            evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                                  relations=items)
            projected = project_order_evidence(evidence, projections("A", "B", "C"))
            results.append(solve_order(problem(projected)))
        self.assertEqual(results[0].to_dict(), results[1].to_dict())
        self.assertEqual(results[0].accepted_relation_ids, ("r1", "r2"))
        self.assertEqual(results[0].rejected_relation_ids, ("r3",))
        self.assertEqual(results[0].diagnostics[0]["reason"],
                         "earlier_protected_relation_cycle")

    def test_dependency_wins_and_names_rejected_relation(self):
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              relations=(relation("narrative", "A", "B"),))
        projected = project_order_evidence(evidence, projections("A", "B", "C"))
        result = solve_order(problem(projected, (("B", "A", 1),)))
        self.assertEqual(result.rejected_relation_ids, ("narrative",))
        self.assertEqual(result.diagnostics[0]["reason"], "dependency_cycle")
        self.assertLess(result.order.index("B"), result.order.index("A"))

    def test_candidate_and_multi_atom_endpoints_do_not_make_hard_edges(self):
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              relations=(relation("ab", "A", "B"),))
        candidate = projections("A", "B")
        candidate[subject("A").canonical_key()] = SubjectProjection("candidate", ("A",))
        projected = project_order_evidence(evidence, candidate)
        self.assertEqual(projected.protected, ())
        self.assertEqual(projected.diagnostics[0]["reason"], "before_candidate")
        multi = projections("A", "B")
        multi[subject("A").canonical_key()] = SubjectProjection("exact", ("A", "C"))
        projected = project_order_evidence(evidence, multi)
        self.assertEqual(projected.protected, ())
        self.assertEqual(projected.diagnostics[0]["reason"], "before_non_unique_atom")

    def test_unrelated_atoms_are_not_added_to_hard_constraints(self):
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              relations=(relation("ab", "A", "B"),))
        projected = project_order_evidence(evidence, projections("A", "B", "C"))
        result = solve_order(problem(projected))
        self.assertEqual(result.constraints, (("A", "B"),))
        self.assertFalse(any("C" in pair for pair in result.constraints))

    def test_tie_breaker_changes_ready_choice_without_becoming_constraint(self):
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              relations=(relation(
                                                  "ba", "B", "A", strength="tie_breaker"),))
        projected = project_order_evidence(evidence, projections("A", "B", "C"))
        result = solve_order(problem(projected))
        self.assertEqual(result.constraints, ())
        self.assertLess(result.order.index("B"), result.order.index("A"))

    def test_same_atom_relation_is_internalized(self):
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              relations=(relation("ab", "A", "B"),))
        mapping = projections("A", "B")
        mapping[subject("A").canonical_key()] = SubjectProjection("exact", ("unit",))
        mapping[subject("B").canonical_key()] = SubjectProjection("exact", ("unit",))
        projected = project_order_evidence(evidence, mapping)
        self.assertEqual(projected.protected, ())
        self.assertEqual(projected.diagnostics[0]["code"], "order_relation_internalized")

    def test_strict_roundtrip_and_sequence_expansion(self):
        sequence = OrderSequence("route", (subject("A"), subject("B"), subject("C")),
                                 "tie_breaker", "route", "authoritative_route", 4, P)
        evidence = OrderEvidenceBundle.create(material_digest=D, binding_digest=D,
                                              sequences=(sequence,))
        self.assertEqual(OrderEvidenceBundle.from_json(evidence.to_json()), evidence)
        self.assertEqual([item.relation_id for item in evidence.expanded_relations()],
                         ["route:0", "route:1"])
        data = evidence.to_dict()
        data["extra"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            OrderEvidenceBundle.from_dict(data)

    def test_ordered_module_convenience_uses_common_evidence_contract(self):
        spec = SourceSequenceSpec("repo", sequences=(
            SourceSequence("modules", "lean_modules", "primary", "tie_breaker",
                           modules=("A", "B")),
        ))
        evidence = derive_sequence_order_evidence(
            spec, material_digest=D, binding_digest=D,
            subjects={"modules": (subject("A"), subject("B"))},
        )
        self.assertEqual(evidence.sequences[0].producer, "lean_modules")
        self.assertEqual(evidence.sequences[0].members, (subject("A"), subject("B")))

    def test_hierarchy_projects_fixed_evidence_and_pins_its_digests(self):
        materials = MaterialBundle.create(
            repo_key="r", assets=(), records=(),
            parser_implementation_digest=D, parser_config={},
            binder_implementation_digest=D, binder_config={},
        )
        evidence = OrderEvidenceBundle.create(
            material_digest=materials.material_digest(),
            binding_digest=materials.binding_digest(),
            relations=(OrderRelation(
                "c-before-a", OrderSubject("declaration", ref=ref("c")),
                OrderSubject("declaration", ref=ref("a")), "protected",
                "fixture", "manual_config", 0, P,
            ),),
        )
        built = bundle(workspace("abc"))
        order = derive_narrative_order(
            built, "r", materials=materials, order_evidence=evidence,
        )
        self.assertEqual(order.material_digest, materials.material_digest())
        self.assertEqual(order.order_evidence_digest, evidence.digest())
        self.assertIn("c-before-a", {value for scope in order.scopes
                                     for value in scope.accepted_relation_ids})
        hierarchy = build_hierarchy(
            built, "r", materials=materials, order_evidence=evidence,
        )
        self.assertEqual(hierarchy.config["order_evidence_digest"], evidence.digest())
        with self.assertRaisesRegex(ValueError, "must be supplied together"):
            build_hierarchy(built, "r", materials=materials)


if __name__ == "__main__":
    unittest.main()
