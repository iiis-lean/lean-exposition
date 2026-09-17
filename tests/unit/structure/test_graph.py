"""Synthetic graph invariants; no Lean extraction or real data mutation."""
from dataclasses import replace
import unittest
from lean_exposition.models.facts import (
    DeclContent, DeclRef, DeclUnit, Dependency, Provenance, RawDecl, Repository,
    Scope, SourceAsset, SourceRange, Status, TextContent, Workspace, WorkspaceManifest,
)
from lean_exposition.construction import (
    COVERAGE_DOMAINS, CoverageEntry, DependencyCoverage, RepositoryBuildBundle,
    StructurePolicy,
)
from lean_exposition.structure import DependencyGraph

P = (Provenance("fixture", "synthetic"),)
TEXT = TextContent("fixture", "present", P)


def bundle(workspace, unit_aggregation="native_helpers", coverage_status="unknown"):
    """Create explicit construction sidecars for structure-only fixtures."""
    digest = workspace.digest()
    entries = tuple(
        CoverageEntry(decl.ref, part, domain, coverage_status, P)
        for decl in workspace.declarations
        for part in (("statement", "proof") if decl.proof is not None else ("statement",))
        for domain in COVERAGE_DOMAINS
    )
    return RepositoryBuildBundle(
        workspace,
        StructurePolicy(digest, unit_aggregation, P, True),
        DependencyCoverage(digest, entries),
    )


def ref(name, repo="r"):
    return DeclRef(repo, name)


def workspace(names, pairs=(), *, units=True, scopes=None, outcomes=()):
    repositories = (
        Repository("r", "lean", "root", input_digest="0" * 64, primary_outcomes=tuple(outcomes)),
        Repository("s", "lean", "other", input_digest="1" * 64),
        Repository("external", None, None, version_status="unresolved", unresolved_reason="not loaded"),
    )
    scope_records = (Scope("root", "r", "root", "r", P), Scope("other", "s", "root", "s", P),
                     Scope("a", "r", "module", "A", P, "root"), Scope("b", "r", "module", "B", P, "root"))
    refs = [name if isinstance(name, DeclRef) else ref(name) for name in names]
    declarations = tuple(RawDecl(
        value, value.local_id, "Fixture", (scopes or {}).get(value.local_id, "root" if value.repo_key == "r" else "other"),
        "theorem", DeclContent(TEXT, TEXT, tuple(Dependency(provider, kind, P)
            for provider, consumer, kind in pairs if consumer == value)), Status("extracted", P), P,
    ) for value in refs)
    forest = tuple(DeclUnit(value.repo_key + ":" + value.local_id, value) for value in refs) if units else ()
    return Workspace(WorkspaceManifest(repositories, ()), declarations, scope_records, forest)


class GraphTests(unittest.TestCase):
    def test_chain_shortcut_and_isolation(self):
        ws = workspace("abcd", [(ref("a"), ref("b"), "lean_type"), (ref("b"), ref("c"), "lean_value"),
                                (ref("a"), ref("c"), "lc_declared")], outcomes=(ref("d"), ref("unloaded")))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.order().ordered, tuple(map(ref, "abcd")))
        self.assertEqual(len(graph.edges), 3)
        self.assertEqual(graph.boundary([ref("d")]).output_decls, {ref("d")})
        self.assertEqual(graph.unloaded_primary_outcomes, {ref("unloaded")})
        self.assertEqual(graph.incoming[ref("d")], ())
        self.assertEqual(graph.order(refs=[ref("c")]).ordered, (ref("c"),))

    def test_shared_provider_counts_and_projection(self):
        consumers = [f"c{i}" for i in range(10)]
        graph = DependencyGraph.from_workspace(workspace(["p", *consumers],
            [(ref("p"), ref(c), "lean_type") for c in consumers]))
        boundary = graph.boundary(map(ref, consumers))
        self.assertEqual((boundary.incoming_pair_count, boundary.incoming_provider_count), (10, 1))
        edge = graph.project({"provider": [ref("p")], "consumers": map(ref, consumers)}).edges[0]
        self.assertEqual((edge.pair_count, edge.provider_count), (10, 1))
        self.assertEqual(edge.base_edges, graph.edges)

    def test_evidence_occurrences_filter_and_references(self):
        deps = [(ref("p", "s"), ref("c"), "lean_type"), (ref("missing"), ref("c"), "lc_declared"),
                (ref("Nat", "external"), ref("c"), "lean_value"), (ref("text"), ref("c"), "text_reference")]
        ws = workspace(["c", ref("p", "s")], deps)
        consumer = ws.declarations[0]
        ws = replace(ws, declarations=(replace(consumer,
             proof=DeclContent(TEXT, TEXT, (consumer.statement.deps[0], consumer.statement.deps[0]))), ws.declarations[1]))
        graph = DependencyGraph.from_workspace(ws)
        cross = graph.outgoing[ref("p", "s")][0]
        self.assertEqual(len(cross.occurrences), 3)
        self.assertEqual({value.part for value in cross.occurrences}, {"statement", "proof"})
        self.assertEqual(graph.unloaded_refs, {ref("missing"), ref("Nat", "external")})
        self.assertEqual(graph.order().ordered, (ref("p", "s"), ref("c")))
        self.assertEqual(len(graph.boundary([ref("c")]).incoming), 3)
        text = DependencyGraph.from_workspace(ws, evidence_kinds={"text_reference"})
        self.assertEqual(text.unloaded_refs, {ref("text")})
        self.assertEqual(len(text.edges), 1)

    def test_recursive_ownership_and_raw_reading_coverage(self):
        ws = workspace("abc", scopes={"a": "a", "b": "a", "c": "a"})
        ws = replace(ws, units=(DeclUnit("u", ref("c"), ("v",)), DeclUnit("v", ref("b"), ("w",)), DeclUnit("w", ref("a"))))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.unit_coverages["u"], set(map(ref, "abc")))
        self.assertEqual(graph.unit_coverages["v"], set(map(ref, "ab")))
        self.assertEqual(graph.representative_owner[ref("a")], "w")
        self.assertEqual(graph.top_level_owner[ref("a")], "u")
        self.assertEqual(graph.scope_children("a").groups, {"unit:u": set(map(ref, "abc"))})
        self.assertEqual(graph.repository_coverages["r"], graph.loaded_decls)

    def test_facts_only(self):
        graph = DependencyGraph.from_workspace(workspace("a", units=False))
        self.assertEqual(graph.unit_coverages, {})
        self.assertEqual(graph.top_level_owner, {})
        self.assertEqual(graph.order().ordered, (ref("a"),))
        with self.assertRaises(ValueError):
            graph.scope_children("root")
        with self.assertRaises(KeyError):
            _ = graph.unit_coverages["invented"]

    def test_cycles_and_blocked_downstream(self):
        graph = DependencyGraph.from_workspace(workspace("abcde", [
            (ref(a), ref(b), "lean_value") for a, b in [("a", "a"), ("b", "c"), ("c", "b"), ("c", "d")]]))
        order = graph.order()
        self.assertEqual(order.ordered, (ref("e"),))
        self.assertEqual(order.blocked, (ref("d"),))
        self.assertEqual(tuple(c.nodes for c in order.cycles), ((ref("a"),), (ref("b"), ref("c"))))
        self.assertEqual(order.cycles[0].witness, (ref("a"), ref("a")))
        pairs = {(edge.provider, edge.consumer) for edge in graph.edges}
        for cycle in order.cycles:
            self.assertEqual(cycle.witness[0], cycle.witness[-1])
            self.assertTrue(all(pair in pairs for pair in zip(cycle.witness, cycle.witness[1:])))

    def test_projection_cycle_and_internal_edges(self):
        graph = DependencyGraph.from_workspace(workspace("abc", [(ref("a"), ref("b"), "lean_type"),
            (ref("b"), ref("c"), "lean_type")], scopes={"a": "a", "b": "b", "c": "a"}))
        self.assertTrue(graph.order().is_acyclic)
        projection = graph.scope_children("root")
        self.assertEqual(projection.order().cycles[0].nodes, ("scope:a", "scope:b"))
        all_in_one = graph.project({"all": graph.loaded_decls})
        self.assertTrue(all_in_one.order().is_acyclic)
        self.assertEqual(all_in_one.internal_edges["all"], graph.edges)

    def test_partial_groups_validation_and_boundary(self):
        graph = DependencyGraph.from_workspace(workspace("abc", [(ref("a"), ref("b"), "lean_type"),
            (ref("b"), ref("c"), "lean_type")]))
        projection = graph.project({"middle": [ref("b")], "empty": []})
        self.assertEqual(projection.uncovered, {ref("a"), ref("c")})
        self.assertEqual(len(projection.boundary.incoming), 1)
        self.assertEqual(len(projection.boundary.outgoing), 1)
        with self.assertRaises(ValueError):
            graph.project({"one": [ref("a")], "two": [ref("a")]})
        with self.assertRaises(ValueError):
            graph.project({"unknown": [ref("absent")]})
        with self.assertRaises(TypeError):
            projection.groups["new"] = frozenset()
        with self.assertRaises(TypeError):
            graph.incoming[ref("a")] = ()

    def test_cross_scope_reading_conflict(self):
        ws = workspace("ab", scopes={"a": "a", "b": "b"})
        ws = replace(ws, units=(DeclUnit("u", ref("a"), ("v",)), DeclUnit("v", ref("b"))))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.native_scope_coverages["a"], {ref("a")})
        self.assertEqual(graph.reading_scope_coverages["a"], {ref("a"), ref("b")})
        for scope in ("root", "a", "b"):
            with self.assertRaisesRegex(ValueError, "coverage conflict"):
                graph.scope_children(scope)

    def test_source_position_priority_and_permutations(self):
        ws = workspace("abc", [(ref("a"), ref("c"), "lean_type"), (ref("b"), ref("c"), "lean_type")])
        asset = SourceAsset("file", "r", "Fixture.lean", "0" * 64)
        ws = replace(ws, manifest=replace(ws.manifest, assets=(asset,)), declarations=tuple(
            replace(decl, source_refs=(SourceRange("file", line, None, line, None),))
            for decl, line in zip(ws.declarations, (20, 10, 1))))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.order().ordered, tuple(map(ref, "bac")))
        self.assertEqual(graph.order({ref("c"): -10, ref("a"): 0}).ordered, tuple(map(ref, "abc")))
        permuted = replace(ws, declarations=tuple(replace(decl, statement=replace(decl.statement,
            deps=tuple(reversed(decl.statement.deps)))) for decl in reversed(ws.declarations)),
            scopes=tuple(reversed(ws.scopes)), units=tuple(reversed(ws.units)))
        other = DependencyGraph.from_workspace(permuted)
        self.assertEqual(graph.edges, other.edges)
        self.assertEqual(graph.order(), other.order())
        self.assertEqual(graph.scope_children("root"), other.scope_children("root"))
        for name in ("incoming", "outgoing", "unit_coverages", "representative_owner", "source_keys"):
            self.assertEqual(tuple(getattr(graph, name)), tuple(getattr(other, name)))

    def test_empty_graph_and_invalid_query_inputs(self):
        graph = DependencyGraph.from_workspace(workspace(()))
        self.assertTrue(graph.order().is_acyclic)
        self.assertEqual(graph.scope_children("root").boundary.coverage, frozenset())
        self.assertEqual(graph.project({}).uncovered, frozenset())
        with self.assertRaises(ValueError):
            DependencyGraph.from_workspace(workspace("a"), evidence_kinds="lean_type")
        with self.assertRaises(ValueError):
            graph.order({ref("unknown"): 0})
        with self.assertRaises(ValueError):
            graph.project({"valid": [], 5: []})
        with self.assertRaises(ValueError):
            graph.boundary([ref("unknown")])

    def test_manifest_asset_order_is_not_narrative_order(self):
        ws = workspace("ab")
        assets = (SourceAsset("z", "r", "Z.lean", "0" * 64),
                  SourceAsset("a", "r", "A.lean", "0" * 64))
        ws = replace(ws, manifest=replace(ws.manifest, assets=assets), declarations=tuple(
            replace(decl, source_refs=(SourceRange(asset.asset_id, 1, None, 1, None),))
            for decl, asset in zip(ws.declarations, assets)))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.order().ordered, (ref("b"), ref("a")))
        swapped = replace(ws, manifest=replace(ws.manifest, assets=tuple(reversed(assets))))
        self.assertEqual(graph.order(), DependencyGraph.from_workspace(swapped).order())

    def test_deep_unit_forest(self):
        ws = workspace([str(i) for i in range(1100)])
        ws = replace(ws, units=tuple(DeclUnit(str(i), ref(str(i)), (str(i + 1),) if i < 1099 else ())
                                     for i in range(1100)))
        graph = DependencyGraph.from_workspace(ws)
        self.assertEqual(graph.unit_coverages["0"], graph.loaded_decls)
        self.assertEqual(graph.top_level_owner[ref("1099")], "0")

    def test_repository_view_keeps_one_tree_and_resolves_external_references(self):
        ws = workspace(["c", "d", ref("p", "s"), ref("unrelated", "s")], [
            (ref("p", "s"), ref("c"), "lean_type"),
            (ref("Nat", "external"), ref("c"), "lean_type"),
            (ref("missing"), ref("c"), "lean_value"),
            (ref("c"), ref("d"), "lean_value"),
            (ref("d"), ref("p", "s"), "lean_value"),
        ])
        graph = DependencyGraph.from_workspace(ws)
        view = graph.repository_view("r")
        self.assertEqual(view.repo_key, "r")
        self.assertEqual(view.root_scope, "root")
        self.assertEqual(view.coverage, {ref("c"), ref("d")})
        self.assertEqual(set(view.scopes), {"root", "a", "b"})
        self.assertEqual(view.external_refs, {ref("p", "s"), ref("Nat", "external")})
        self.assertEqual(view.unloaded_local_refs, {ref("missing")})
        self.assertIs(view.external_decl(ref("p", "s")), ws.declarations[2])
        self.assertIsNone(view.external_decl(ref("Nat", "external")))
        with self.assertRaises(KeyError):
            view.external_decl(ref("unrelated", "s"))
        with self.assertRaises(ValueError):
            view.scope_children("other")
        with self.assertRaises(TypeError):
            view.external_declarations[ref("p", "s")] = None
        projection = view.root_projection()
        self.assertEqual(projection.boundary.coverage, view.coverage)
        self.assertEqual(projection.uncovered, frozenset())
        self.assertTrue(all(ref.repo_key == "r" for refs in projection.groups.values() for ref in refs))
        self.assertEqual(view.order().ordered, (ref("c"), ref("d")))
        self.assertTrue(view.order().is_acyclic)
        self.assertFalse(graph.order().is_acyclic)
        self.assertEqual(view.boundary.outgoing[0].consumer, ref("p", "s"))
        self.assertEqual(view.scope_children("a").uncovered, view.coverage)

    def test_repository_view_rejects_stub_and_supports_facts_only_queries(self):
        graph = DependencyGraph.from_workspace(workspace("a", units=False))
        for repo in ("external", "unknown"):
            with self.assertRaisesRegex(ValueError, "loaded root Scope"):
                graph.repository_view(repo)
        view = graph.repository_view("r")
        self.assertEqual(view.order().ordered, (ref("a"),))
        with self.assertRaisesRegex(ValueError, "coverage conflict"):
            view.root_projection()

    def test_deep_chain(self):
        names = [str(i) for i in range(1200)]
        ws = workspace(names, [(ref(str(i)), ref(str(i + 1)), "lean_value") for i in range(1199)])
        self.assertEqual(len(DependencyGraph.from_workspace(ws).order().ordered), 1200)


if __name__ == "__main__":
    unittest.main()
