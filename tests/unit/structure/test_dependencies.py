"""Foundation catalog and conservative analysis-dependency decisions."""
from dataclasses import dataclass, replace
import unittest

from lean_exposition.models import (
    DeclContent, DeclRef, DeclUnit, Dependency, Provenance, RawDecl, Repository,
    Scope, Status, TextContent, ValidationError, Workspace, WorkspaceManifest,
)
from lean_exposition.structure.dependencies import (
    DependencyAnalysisPolicy, FoundationCatalog, FoundationEntry,
    ProviderFoundation, ProviderIdentity, automatic_generator_config_digest,
    canonical_provider_name, generate_provider_foundation,
    merge_foundation_catalog,
)
from lean_exposition.structure import DependencyGraph
from lean_exposition.exposition import scope_view


P = (Provenance("fixture", "foundation"),)
TEXT = TextContent("fixture", "present", P)
REV = "1" * 40
OTHER_REV = "2" * 40
IMPLEMENTATION = "a" * 64


def raw(ref, scope, *, kind="theorem", state="extracted", generated_from=None, deps=()):
    return RawDecl(
        ref, ref.local_id, "Fixture", scope, kind,
        DeclContent(TEXT, TEXT, tuple(Dependency(provider, "lean_type", P)
                                      for provider in deps)),
        Status(state, P), P, generated_from=generated_from,
    )


def provider_workspace(repo_key="mathlib"):
    root = repo_key + ":root"
    owner = DeclRef(repo_key, "Base.owner")
    generated = DeclRef(repo_key, "Base.owner.rec")
    constructor = DeclRef(repo_key, "Base.owner.mk")
    instance = DeclRef(repo_key, "Base.inst")
    unknown = DeclRef(repo_key, "Math.deepTheorem")
    declarations = (
        raw(owner, root, kind="structure"),
        raw(generated, root, kind="recursor", state="compiler_only", generated_from=owner),
        raw(constructor, root, kind="constructor", state="compiler_only", generated_from=owner),
        raw(instance, root, kind="instance", state="compiler_only"),
        raw(unknown, root, kind="theorem", state="compiler_only"),
    )
    return Workspace(
        WorkspaceManifest((Repository(repo_key, "leanprover/lean4:v4.32.0", root,
                                      revision=REV),), ()),
        declarations, (Scope(root, repo_key, "repository", repo_key, P),),
        tuple(DeclUnit(ref.local_id, ref)
              for ref in (owner, generated, constructor, instance, unknown)),
    )


def analysis_workspace(mathlib_revision=REV):
    target = Repository("target", "leanprover/lean4:v4.32.0", "target:root",
                        input_digest="0" * 64)
    mathlib = Repository("target/dependency/mathlib", "leanprover/lean4:v4.32.0", None,
                         revision=mathlib_revision)
    lean = Repository("target/lean", "leanprover/lean4:v4.32.0", None,
                      input_digest="3" * 64)
    batteries = Repository("target/dependency/batteries", "leanprover/lean4:v4.32.0", None,
                           revision="4" * 40)
    project = Repository("WeightedSieve", "leanprover/lean4:v4.32.0", None,
                         revision="5" * 40)
    unresolved = Repository("target/external/unknown", None, None, version_status="unresolved",
                            unresolved_reason="not fixed")
    a, b = DeclRef("target", "a"), DeclRef("target", "b")
    declarations = (raw(a, "target:root"), raw(b, "target:root"))
    return Workspace(
        WorkspaceManifest((target, mathlib, lean, batteries, project, unresolved), ()),
        declarations, (Scope("target:root", "target", "repository", "target", P),),
        (DeclUnit("a", a), DeclUnit("b", b)),
    )


@dataclass(frozen=True)
class Occurrence:
    part: str


@dataclass(frozen=True)
class Edge:
    provider: DeclRef
    consumer: DeclRef
    occurrences: tuple[Occurrence, ...]


def edge(repo, local_id, consumer="a", parts=("proof",)):
    return Edge(DeclRef(repo, local_id), DeclRef("target", consumer),
                tuple(Occurrence(part) for part in parts))


class FoundationCatalogTests(unittest.TestCase):
    def automatic_provider(self, reviewed=()):
        return generate_provider_foundation(
            provider_workspace(), "mathlib", implementation_digest=IMPLEMENTATION,
            reviewed_entries=reviewed,
        )

    def test_provider_aliases_share_global_identity(self):
        self.assertEqual(canonical_provider_name("mathlib"), "mathlib")
        self.assertEqual(canonical_provider_name("project/dependency/Mathlib"), "mathlib")
        self.assertEqual(canonical_provider_name("project/lean"), "lean")
        catalog = FoundationCatalog((self.automatic_provider(),))
        repository = analysis_workspace().manifest.repositories[1]
        self.assertEqual(catalog.match(repository).identity.provider_name, "mathlib")

    def test_revision_lock_matches_catalog_with_additional_input_digest(self):
        provider = self.automatic_provider()
        richer = replace(provider, identity=replace(provider.identity,
                                                    input_digest="9" * 64))
        catalog = FoundationCatalog((richer,))
        repository = analysis_workspace().manifest.repositories[1]
        self.assertIs(catalog.match(repository), richer)

    def test_generation_is_mechanical_and_review_overrides(self):
        reviewed = FoundationEntry(
            "Base.inst", "reviewed_keep", ("proof", "statement"),
            "This instance is a public mathematical interface.", "reviewed:global-foundation",
        )
        provider = self.automatic_provider((reviewed,))
        values = {entry.local_id: entry for entry in provider.entries}
        self.assertEqual(values["Base.owner.rec"].classification, "automatic_ambient")
        self.assertEqual(values["Base.owner.rec"].parts, ("proof",))
        self.assertEqual(values["Base.inst"], reviewed)
        self.assertNotIn("Base.owner.mk", values)
        self.assertNotIn("Math.deepTheorem", values)
        self.assertNotIn("Base.owner", values)
        self.assertEqual(provider.generator_config_digest,
                         automatic_generator_config_digest())

    def test_catalog_strict_round_trip_and_digest(self):
        catalog = FoundationCatalog((self.automatic_provider(),))
        loaded = FoundationCatalog.from_json(catalog.to_json())
        self.assertEqual(loaded, catalog)
        self.assertEqual(loaded.digest(), catalog.digest())
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            FoundationCatalog.from_json('{"providers": [], "providers": []}')
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            FoundationCatalog.from_json('{"providers": [], "schema_version": 1}')

    def test_automatic_entries_cannot_claim_statement_or_review_source(self):
        for entry in (
            FoundationEntry("x", "automatic_ambient", ("proof", "statement"),
                            "bad", "automatic:test"),
            FoundationEntry("x", "automatic_ambient", ("proof",),
                            "bad", "reviewed:test"),
        ):
            with self.assertRaises(ValidationError):
                entry.validate()

    def test_multiple_revisions_are_global_and_exact(self):
        first = self.automatic_provider()
        second_identity = replace(first.identity, revision=OTHER_REV)
        second = replace(first, identity=second_identity)
        catalog = merge_foundation_catalog(FoundationCatalog((first,)), second)
        self.assertEqual(len(catalog.providers), 2)
        self.assertIsNotNone(catalog.match(analysis_workspace().manifest.repositories[1]))
        mismatch = replace(analysis_workspace().manifest.repositories[1], revision="6" * 40)
        self.assertIsNone(catalog.match(mismatch))
        self.assertTrue(catalog.has_provider_name(mismatch))

    def test_partial_runtime_identity_never_chooses_ambiguous_catalog(self):
        first = self.automatic_provider()
        richer_a = replace(first, identity=replace(first.identity, input_digest="8" * 64))
        richer_b = replace(first, identity=replace(first.identity, input_digest="9" * 64))
        catalog = FoundationCatalog((richer_a, richer_b))
        repository = analysis_workspace().manifest.repositories[1]
        self.assertIsNone(catalog.match(repository))


class DependencyAnalysisTests(unittest.TestCase):
    def catalog(self):
        reviewed = (
            FoundationEntry(
                "Math.reviewedAmbient", "reviewed_ambient", ("proof", "statement"),
                "Globally reviewed representation infrastructure.",
                "reviewed:global-foundation",
            ),
            FoundationEntry(
                "Base.inst", "reviewed_keep", ("proof", "statement"),
                "Public mathematical interface.", "reviewed:global-foundation",
            ),
        )
        provider = generate_provider_foundation(
            provider_workspace(), "mathlib", implementation_digest=IMPLEMENTATION,
            reviewed_entries=reviewed,
        )
        return FoundationCatalog((provider,))

    def test_analysis_filters_only_matching_ambient_entries(self):
        workspace = analysis_workspace()
        edges = (
            edge("target", "b"),
            edge("WeightedSieve", "Public.theorem"),
            edge("target/dependency/mathlib", "Base.owner.rec"),
            edge("target/dependency/mathlib", "Math.reviewedAmbient", consumer="b",
                 parts=("statement",)),
            edge("target/dependency/mathlib", "Base.inst", consumer="b"),
            edge("target/dependency/mathlib", "Math.deepTheorem"),
            edge("target/external/unknown", "x", consumer="b"),
        )
        analysis = DependencyAnalysisPolicy(self.catalog()).analyze(
            workspace, "target", edges)
        by_pair = {(item.provider, item.consumer): item for item in analysis.decisions}
        self.assertTrue(by_pair[(DeclRef("target", "b"), DeclRef("target", "a"))].keep)
        self.assertEqual(by_pair[(DeclRef("WeightedSieve", "Public.theorem"),
                                  DeclRef("target", "a"))].reason, "other_project")
        self.assertFalse(by_pair[(DeclRef("target/dependency/mathlib", "Base.owner.rec"),
                                  DeclRef("target", "a"))].keep)
        self.assertEqual(by_pair[(DeclRef("target/dependency/mathlib", "Base.owner.rec"),
                                  DeclRef("target", "a"))].classification,
                         "automatic_ambient")
        self.assertTrue(by_pair[(DeclRef("target/dependency/mathlib", "Base.owner.rec"),
                                 DeclRef("target", "a"))].reason == "ambient_foundation")

    def test_analysis_graph_and_scope_view_hide_only_ambient_pairs(self):
        workspace = analysis_workspace()
        a, b = workspace.declarations
        dependencies = (
            Dependency(DeclRef("target/dependency/mathlib", "Math.reviewedAmbient"),
                       "lean_type", P),
            Dependency(DeclRef("WeightedSieve", "Public.theorem"), "lean_type", P),
            Dependency(b.ref, "lean_type", P),
        )
        workspace = replace(workspace, declarations=(
            replace(a, statement=replace(a.statement, deps=dependencies)), b,
        ))
        full = DependencyGraph.from_workspace(workspace)
        analysis = DependencyAnalysisPolicy(self.catalog()).analyze(
            workspace, "target", full.edges,
        )
        filtered = full.analysis_view(analysis)
        self.assertEqual(len(full.edges), 3)
        self.assertEqual(len(filtered.edges), 2)
        self.assertIn((DeclRef("WeightedSieve", "Public.theorem"), a.ref),
                      {(edge.provider, edge.consumer) for edge in filtered.edges})
        hierarchy = {"nodes": [{
            "id": "root", "kind": "repo", "title": "target",
            "source_scope": "target:root", "representative": None,
            "decl_refs": [{"repo_key": "target", "local_id": "a"},
                          {"repo_key": "target", "local_id": "b"}],
            "children": [],
        }], "edges": []}
        core = scope_view(workspace, hierarchy, "root", limit=0,
                          dependency_analysis=analysis)
        raw_view = scope_view(workspace, hierarchy, "root", limit=0,
                              dependency_analysis=analysis, full_dependencies=True)
        self.assertEqual(len(core["incoming"]), 1)
        self.assertEqual(len(raw_view["incoming"]), 2)
        self.assertEqual(len(core["internal"]), 1)

    def test_statement_use_of_automatic_entry_is_kept(self):
        decision = DependencyAnalysisPolicy(self.catalog()).decide(
            analysis_workspace(), "target",
            edge("target/dependency/mathlib", "Base.owner.rec", parts=("statement",)),
        )
        self.assertTrue(decision.keep)
        self.assertEqual(decision.reason, "foundation_not_applicable_to_occurrence")

    def test_reviewed_ambient_can_hide_statement_but_reviewed_keep_wins(self):
        policy = DependencyAnalysisPolicy(self.catalog())
        ambient = policy.decide(
            analysis_workspace(), "target",
            edge("target/dependency/mathlib", "Math.reviewedAmbient", parts=("statement",)),
        )
        kept = policy.decide(
            analysis_workspace(), "target",
            edge("target/dependency/mathlib", "Base.inst"),
        )
        self.assertFalse(ambient.keep)
        self.assertTrue(kept.keep)
        self.assertEqual(kept.reason, "reviewed_keep")

    def test_unknown_and_catalog_mismatch_are_conservative(self):
        policy = DependencyAnalysisPolicy(self.catalog())
        unknown = policy.decide(
            analysis_workspace(), "target",
            edge("target/dependency/mathlib", "Math.deepTheorem"),
        )
        mismatch = policy.decide(
            analysis_workspace(OTHER_REV), "target",
            edge("target/dependency/mathlib", "Base.owner.rec"),
        )
        self.assertTrue(unknown.keep)
        self.assertEqual(unknown.reason, "foundation_unknown")
        self.assertTrue(mismatch.keep)
        self.assertEqual(mismatch.reason, "catalog_identity_mismatch")

    def test_analysis_never_filters_another_repository_consumer(self):
        decision = DependencyAnalysisPolicy(self.catalog()).decide(
            analysis_workspace(), "target",
            Edge(DeclRef("target/dependency/mathlib", "Base.owner.rec"),
                 DeclRef("WeightedSieve", "consumer"), (Occurrence("proof"),)),
        )
        self.assertTrue(decision.keep)
        self.assertEqual(decision.reason, "outside_target_repository")

    def test_analysis_preserves_full_pair_recovery(self):
        edges = (
            edge("target/dependency/mathlib", "Base.owner.rec"),
            edge("WeightedSieve", "Public.theorem", consumer="b"),
        )
        analysis = DependencyAnalysisPolicy(self.catalog()).analyze(
            analysis_workspace(), "target", edges)
        full_pairs = frozenset((item.provider, item.consumer) for item in edges)
        self.assertEqual(analysis.kept_pairs | analysis.hidden_pairs, full_pairs)
        self.assertFalse(analysis.kept_pairs & analysis.hidden_pairs)
        self.assertEqual(analysis.hidden_pairs,
                         {(DeclRef("target/dependency/mathlib", "Base.owner.rec"),
                           DeclRef("target", "a"))})

    def test_duplicate_edge_pairs_are_rejected(self):
        repeated = edge("target/dependency/mathlib", "Base.owner.rec")
        with self.assertRaisesRegex(ValidationError, "unique edge pairs"):
            DependencyAnalysisPolicy(self.catalog()).analyze(
                analysis_workspace(), "target", (repeated, repeated))


if __name__ == "__main__":
    unittest.main()
