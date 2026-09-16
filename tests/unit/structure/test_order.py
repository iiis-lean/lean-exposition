"""Deterministic narrative-order contracts and integration boundaries."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_graph import P, ref, workspace
from lean_exposition.models import Provenance, SourceAsset, SourceRange
from lean_exposition.structure import (
    BuildConfig,
    NarrativeOrder,
    OrderEdge,
    OrderProblem,
    SourceSequence,
    SourceSequenceSpec,
    build_hierarchy,
    dependency_distance,
    derive_narrative_order,
    derive_source_order,
    solve_order,
)


def order_problem(atoms, pairs=(), *, keys=None, basis=None, protected=()):
    edges = tuple(OrderEdge(a, b, weight) for a, b, weight in pairs)
    keys = keys or {atom: (index,) for index, atom in enumerate(atoms)}
    basis = basis or {atom: "missing_source" for atom in atoms}
    roles = {atom: value.removeprefix("explicit_") if value.startswith("explicit_") else None
             for atom, value in basis.items()}
    anchors = {atom: None for atom in atoms}
    return OrderProblem("scope", "parent", tuple(atoms), edges, keys, basis, roles, anchors,
                        tuple(tuple(sequence) for sequence in protected))


def is_legal(problem, order, constraints=()):
    positions = {atom: index for index, atom in enumerate(order)}
    return all(positions[a] < positions[b]
               for a, b in [*((edge.provider, edge.consumer) for edge in problem.edges), *constraints])


class DeterministicOrderTests(unittest.TestCase):
    def test_graph_families_are_legal_and_never_worse_than_source_start(self):
        cases = {
            "chain": ("abcd", (("a", "b", 1), ("b", "c", 1), ("c", "d", 1))),
            "fork": ("abcd", (("a", "c", 1), ("b", "c", 1), ("b", "d", 1))),
            "diamond": ("abcd", (("a", "b", 1), ("a", "c", 1), ("b", "d", 1), ("c", "d", 1))),
            "branches": ("abcdef", (("a", "b", 2), ("b", "c", 1), ("d", "e", 3))),
            "multi_consumer": ("abcde", (("a", "c", 1), ("a", "d", 1), ("a", "e", 1), ("b", "e", 1))),
            "no_edges": ("abc", ()),
        }
        for name, (atoms, pairs) in cases.items():
            with self.subTest(name=name):
                problem = order_problem(atoms, pairs)
                result = solve_order(problem)
                self.assertTrue(is_legal(problem, result.order, result.constraints))
                self.assertLessEqual(result.metrics["dependency_distance"],
                                     result.metrics["source_start_distance"])
                self.assertEqual(result.metrics["dependency_distance"],
                                 result.metrics["frontier_area"])
                self.assertEqual(dependency_distance(problem, result.order),
                                 result.metrics["dependency_distance"])

    def test_insertion_result_is_a_strict_local_minimum(self):
        problem = order_problem("abcdef", (
            ("a", "d", 2), ("a", "e", 1), ("b", "d", 1),
            ("b", "f", 3), ("c", "e", 2), ("d", "f", 1),
        ))
        result = solve_order(problem)
        distance = dependency_distance(problem, result.order)
        for old_index, atom in enumerate(result.order):
            without = list(result.order)
            without.pop(old_index)
            for new_index in range(len(result.order)):
                candidate = tuple(without[:new_index] + [atom] + without[new_index:])
                if is_legal(problem, candidate, result.constraints):
                    self.assertGreaterEqual(dependency_distance(problem, candidate), distance)

    def test_protected_sequence_yields_to_dependency_with_diagnostic(self):
        problem = order_problem("abc", (("b", "a", 1),), protected=(("a", "b", "c"),))
        result = solve_order(problem)
        self.assertEqual(result.constraints, (("b", "c"),))
        self.assertLess(result.order.index("b"), result.order.index("a"))
        self.assertEqual(result.diagnostics[0]["code"], "dependency_overrides_source_order")

    def test_first_protected_sequence_wins_an_incompatible_later_sequence(self):
        problem = order_problem("ab", protected=(("a", "b"), ("b", "a")))
        result = solve_order(problem)
        self.assertEqual(result.constraints, (("a", "b"),))
        self.assertEqual(result.order, ("a", "b"))
        self.assertEqual(result.diagnostics[0]["sequence"], 1)

    def test_atom_and_edge_enumeration_do_not_change_scope_artifact(self):
        pairs = (("a", "d", 2), ("b", "d", 1), ("b", "e", 3), ("c", "e", 1))
        keys = {atom: (index % 2, atom) for index, atom in enumerate("abcde")}
        first = solve_order(order_problem("abcde", pairs, keys=keys))
        second = solve_order(order_problem(tuple(reversed("abcde")), tuple(reversed(pairs)),
                                           keys=dict(reversed(tuple(keys.items())))))
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_metrics_include_weighted_span_source_and_ready_evidence(self):
        basis = {"a": "explicit_primary", "b": "explicit_primary",
                 "c": "explicit_supporting", "d": "missing_source"}
        result = solve_order(order_problem("abcd", (("a", "d", 3), ("b", "d", 1)), basis=basis))
        self.assertEqual(result.metrics["edge_weight"], 4)
        self.assertIn("dependency_span_p50", result.metrics)
        self.assertEqual(result.metrics["source_basis_coverage"]["explicit_primary"], 2)
        self.assertEqual(result.metrics["source_role_order"]["primary"]["atoms"], 2)
        self.assertGreaterEqual(result.metrics["ready_maximum_width"], 1)


class SourceSequenceTests(unittest.TestCase):
    def test_multi_origin_prefers_primary_and_preserves_all_matches(self):
        main = "\\documentclass{article}\nmain\n"
        support = "\\documentclass{article}\nsupport\n"
        assets = (
            SourceAsset("main", "r", "corpus/main.tex", hashlib.sha256(main.encode()).hexdigest()),
            SourceAsset("support", "r", "corpus/support.tex", hashlib.sha256(support.encode()).hexdigest()),
        )
        w = workspace("a")
        declaration = w.declarations[0]
        origin = Provenance("lc_origin", "fixture", (
            SourceRange("support", 2, None, 2, None),
            SourceRange("main", 2, None, 2, None),
        ))
        declaration = replace(declaration, statement=replace(
            declaration.statement,
            nl=replace(declaration.statement.nl, provenance=P + (origin,)),
        ))
        w = replace(w, declarations=(declaration,), manifest=replace(w.manifest, assets=assets))
        spec = SourceSequenceSpec("r", input_digest="0" * 64, sequences=(
            SourceSequence("support", "tex_document", "supporting", "tie_breaker",
                           roots=("support.tex",)),
            SourceSequence("main", "tex_document", "primary", "protected",
                           roots=("main.tex",)),
        ))
        source = derive_source_order(w, "r", corpus_files={"main.tex": main, "support.tex": support},
                                     corpus_prefix="corpus", source_spec=spec)
        record = source.records[ref("a")]
        self.assertEqual(record["selected_sequence"], "main")
        self.assertEqual(record["selected_role"], "primary")
        self.assertEqual(record["sequence_matches"], ["main", "support"])
        self.assertEqual(len(record["anchors"]), 2)
        self.assertEqual(len(record["ranges"]), 2)

    def test_native_module_sequence_is_an_opt_in_tie_breaker(self):
        w = workspace("abc")
        modules = {"a": "A", "b": "B", "c": "C"}
        w = replace(w, declarations=tuple(replace(d, module=modules[d.ref.local_id])
                                             for d in w.declarations))
        spec = SourceSequenceSpec("r", input_digest="0" * 64, sequences=(
            SourceSequence("modules", "lean_modules", "primary", "tie_breaker",
                           modules=("C", "A", "B")),
        ))
        source = derive_source_order(w, "r", source_spec=spec)
        self.assertEqual(sorted("abc", key=lambda name: source.key(ref(name))), ["c", "a", "b"])
        self.assertTrue(all(source.records[ref(name)]["selected_sequence"] == "modules"
                            for name in "abc"))

    def test_spec_strict_json_roundtrip_and_identity_validation(self):
        spec = SourceSequenceSpec("r", revision="abc", sequences=(
            SourceSequence("modules", "lean_modules", "supporting", "tie_breaker", modules=("A",)),
        ))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            spec.save(path)
            self.assertEqual(SourceSequenceSpec.load(path), spec)
            data = json.loads(path.read_text())
            data["extra"] = True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "fields"):
                SourceSequenceSpec.load(path)
        with self.assertRaisesRegex(ValueError, "identity"):
            derive_source_order(workspace("a"), "r", source_spec=spec)

    def test_unknown_sequence_members_fail(self):
        w = workspace("a")
        spec = SourceSequenceSpec("r", sequences=(
            SourceSequence("modules", "lean_modules", "primary", "tie_breaker", modules=("Missing",)),
        ))
        with self.assertRaisesRegex(ValueError, "unknown Lean module"):
            derive_source_order(w, "r", source_spec=spec)


class NarrativeArtifactTests(unittest.TestCase):
    def test_roundtrip_duplicate_key_and_unknown_field_rejection(self):
        artifact = derive_narrative_order(workspace("abc"), "r",
                                          config=BuildConfig(native_helper=False))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "order.json"
            artifact.save(path)
            self.assertEqual(NarrativeOrder.load(path), artifact)
            path.write_text('{"repo_key":"r","repo_key":"s"}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                NarrativeOrder.load(path)
        data = artifact.to_dict()
        data["extra"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            NarrativeOrder.from_dict(data)

    def test_hierarchy_consumes_complete_artifact_and_rejects_stale_inputs(self):
        w = workspace("abc", ((ref("a"), ref("c"), "lean_value"),))
        config = BuildConfig(native_helper=False)
        artifact = derive_narrative_order(w, "r", config=config)
        hierarchy = build_hierarchy(w, "r", config=config, narrative_order=artifact)
        self.assertEqual(hierarchy.config["narrative_order_id"], artifact.narrative_order_id)
        # Region-only settings do not invalidate a pre-Region order artifact.
        build_hierarchy(w, "r", config=replace(config, region_k=3), narrative_order=artifact)
        with self.assertRaisesRegex(ValueError, "inputs"):
            build_hierarchy(w, "r", config=replace(config, max_declarations=2),
                            narrative_order=artifact)
        changed = workspace("abcd", ((ref("a"), ref("c"), "lean_value"),))
        with self.assertRaisesRegex(ValueError, "inputs"):
            build_hierarchy(changed, "r", config=config, narrative_order=artifact)
        changed_scope = replace(artifact.scopes[-1], metrics={**artifact.scopes[-1].metrics,
                                                               "dependency_distance": 999})
        fabricated = NarrativeOrder.create(
            repo_key=artifact.repo_key,
            repository_revision=artifact.repository_revision,
            repository_input_digest=artifact.repository_input_digest,
            workspace_digest=artifact.workspace_digest,
            source_digest=artifact.source_digest,
            config_digest=artifact.config_digest,
            scopes=(*artifact.scopes[:-1], changed_scope),
            diagnostics=artifact.diagnostics,
        )
        with self.assertRaisesRegex(ValueError, "metrics"):
            build_hierarchy(w, "r", config=config, narrative_order=fabricated)

    def test_protected_module_conflict_is_diagnosed_in_integrated_order(self):
        w = workspace("ac", ((ref("a"), ref("c"), "lean_value"),))
        w = replace(w, declarations=tuple(replace(d, module=d.ref.local_id.upper())
                                           for d in w.declarations))
        spec = SourceSequenceSpec("r", sequences=(
            SourceSequence("main", "lean_modules", "primary", "protected", modules=("C", "A")),
        ))
        artifact = derive_narrative_order(w, "r", config=BuildConfig(native_helper=False),
                                          source_spec=spec)
        self.assertTrue(any(diagnostic["code"] == "dependency_overrides_source_order"
                            for diagnostic in artifact.diagnostics))

    def test_reordering_rebuilds_regions_without_changing_facts(self):
        names = "abcdefgh"
        w = workspace(names)
        w = replace(w, declarations=tuple(replace(d, module=d.ref.local_id.upper())
                                           for d in w.declarations))
        config = BuildConfig(native_helper=False, region_k=2)
        baseline = build_hierarchy(w, "r", config=config)
        spec = SourceSequenceSpec("r", sequences=(
            SourceSequence("main", "lean_modules", "primary", "protected",
                           modules=tuple(name.upper() for name in reversed(names))),
        ))
        reordered = build_hierarchy(w, "r", config=config, source_spec=spec)
        def unit_facts(hierarchy):
            return sorted((node["representative"]["local_id"], node["decl_refs"])
                          for node in hierarchy.nodes if node["kind"] == "unit")
        self.assertEqual(unit_facts(baseline), unit_facts(reordered))
        self.assertEqual(baseline.edges, reordered.edges)
        self.assertEqual(baseline.external_refs, reordered.external_refs)
        baseline_root = next(node for node in baseline.nodes if node["id"] == baseline.root_id)
        reordered_root = next(node for node in reordered.nodes if node["id"] == reordered.root_id)
        self.assertEqual(baseline_root["decl_refs"], reordered_root["decl_refs"])
        self.assertEqual(baseline_root["metadata"]["aliases"],
                         reordered_root["metadata"]["aliases"])
        self.assertNotEqual(baseline.hierarchy_id, reordered.hierarchy_id)

    def test_source_sequence_does_not_change_helper_aggregation(self):
        w = workspace("abc", ((ref("a"), ref("c"), "lean_value"),
                              (ref("b"), ref("c"), "lean_value")))
        w = replace(w, declarations=tuple(replace(d, module=d.ref.local_id.upper())
                                           for d in w.declarations))
        config = BuildConfig(max_declarations=2)
        baseline = build_hierarchy(w, "r", config=config)
        spec = SourceSequenceSpec("r", sequences=(
            SourceSequence("modules", "lean_modules", "primary", "tie_breaker",
                           modules=("B", "A", "C")),
        ))
        reordered = build_hierarchy(w, "r", config=config, source_spec=spec)
        def groups(hierarchy):
            return sorted([node["decl_refs"] for node in hierarchy.nodes if node["kind"] == "unit"],
                          key=repr)
        self.assertEqual(groups(baseline), groups(reordered))


if __name__ == "__main__":
    unittest.main()
