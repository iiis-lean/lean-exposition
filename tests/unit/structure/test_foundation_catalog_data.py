from dataclasses import dataclass
import unittest
from lean_exposition.models import (
    DeclContent,
    DeclRef,
    DeclUnit,
    Provenance,
    RawDecl,
    Repository,
    Scope,
    Status,
    TextContent,
    Workspace,
    WorkspaceManifest,
)
from lean_exposition.structure.dependencies import (
    DependencyAnalysisPolicy,
    load_foundation_catalog,
)


P = (Provenance("fixture", "production-foundation-catalog"),)
TEXT = TextContent("fixture", "present", P)


def workspace(*, toolchain: str, mathlib_revision: str,
              lean_input_digest: str) -> Workspace:
    target = Repository(
        "target", toolchain, "target:root", input_digest="0" * 64
    )
    mathlib = Repository(
        "target/dependency/mathlib", toolchain, None,
        revision=mathlib_revision,
    )
    lean = Repository(
        "target/lean", toolchain, None, input_digest=lean_input_digest
    )
    project = Repository(
        "WeightedSieve", toolchain, None, revision="5" * 40
    )
    ref = DeclRef("target", "result")
    declaration = RawDecl(
        ref,
        "result",
        "Target",
        "target:root",
        "theorem",
        DeclContent(TEXT, TEXT),
        Status("extracted", P),
        P,
    )
    return Workspace(
        WorkspaceManifest((target, mathlib, lean, project), ()),
        (declaration,),
        (Scope("target:root", "target", "repository", "target", P),),
        (DeclUnit("result", ref),),
    )


@dataclass(frozen=True)
class Occurrence:
    part: str


@dataclass(frozen=True)
class Edge:
    provider: DeclRef
    consumer: DeclRef
    occurrences: tuple[Occurrence, ...]


def edge(repo_key: str, local_id: str, part: str = "statement") -> Edge:
    return Edge(
        DeclRef(repo_key, local_id),
        DeclRef("target", "result"),
        (Occurrence(part),),
    )


class ProductionFoundationCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_foundation_catalog()
        cls.policy = DependencyAnalysisPolicy(cls.catalog)

    def test_catalog_has_exact_current_provider_identities(self):
        identities = {
            (
                item.identity.provider_name,
                item.identity.toolchain,
                item.identity.revision,
                item.identity.input_digest,
            )
            for item in self.catalog.providers
        }
        self.assertEqual(
            identities,
            {
                (
                    "lean", "leanprover/lean4:v4.28.0", None,
                    "26df5f74b79af0cd9e298b6583993699a54938d047ba1919428da80d3ae80c6e",
                ),
                (
                    "lean", "leanprover/lean4:v4.32.0", None,
                    "cc346fd7850a83fb7af3902dfc378f74d8eb8c59c6d34322488269ebfe7615b1",
                ),
                (
                    "mathlib", "leanprover/lean4:v4.28.0",
                    "8f9d9cff6bd728b17a24e163c9402775d9e6a365", None,
                ),
                (
                    "mathlib", "leanprover/lean4:v4.32.0",
                    "81a5d257c8e410db227a6665ed08f64fea08e997", None,
                ),
            },
        )
        self.assertEqual(len(self.catalog.providers), 4)
        self.assertTrue(all(
            entry.source == "reviewed:global-foundation-2026-09-17"
            for provider in self.catalog.providers
            for entry in provider.entries
        ))

    def test_lean_and_mathlib_foundations_are_filtered_on_exact_428_identity(self):
        current = workspace(
            toolchain="leanprover/lean4:v4.28.0",
            mathlib_revision="8f9d9cff6bd728b17a24e163c9402775d9e6a365",
            lean_input_digest=(
                "26df5f74b79af0cd9e298b6583993699a54938d047ba1919428da80d3ae80c6e"
            ),
        )
        edges = (
            edge("target/lean", "Eq"),
            edge("target/dependency/mathlib", "Set"),
            edge("target/dependency/mathlib", "MeasureTheory.measure_biUnion", "proof"),
            edge("WeightedSieve", "Public.theorem", "proof"),
        )
        analysis = self.policy.analyze(current, "target", edges)
        decisions = {item.provider.local_id: item for item in analysis.decisions}
        self.assertFalse(decisions["Eq"].keep)
        self.assertFalse(decisions["Set"].keep)
        self.assertTrue(decisions["MeasureTheory.measure_biUnion"].keep)
        self.assertEqual(
            decisions["MeasureTheory.measure_biUnion"].reason,
            "foundation_unknown",
        )
        self.assertTrue(decisions["Public.theorem"].keep)
        self.assertEqual(decisions["Public.theorem"].reason, "other_project")

    def test_432_keeps_mathematics_and_hides_reviewed_container_basics(self):
        current = workspace(
            toolchain="leanprover/lean4:v4.32.0",
            mathlib_revision="81a5d257c8e410db227a6665ed08f64fea08e997",
            lean_input_digest=(
                "cc346fd7850a83fb7af3902dfc378f74d8eb8c59c6d34322488269ebfe7615b1"
            ),
        )
        edges = (
            edge("target/lean", "Eq"),
            edge("target/dependency/mathlib", "Finset.card"),
            edge("target/dependency/mathlib", "Nat.Prime"),
            edge("target/dependency/mathlib", "Nat.chineseRemainder", "proof"),
        )
        analysis = self.policy.analyze(current, "target", edges)
        decisions = {item.provider.local_id: item for item in analysis.decisions}
        self.assertFalse(decisions["Eq"].keep)
        self.assertFalse(decisions["Finset.card"].keep)
        self.assertTrue(decisions["Nat.Prime"].keep)
        self.assertEqual(decisions["Nat.Prime"].reason, "reviewed_keep")
        self.assertTrue(decisions["Nat.chineseRemainder"].keep)
        self.assertEqual(
            decisions["Nat.chineseRemainder"].reason, "foundation_unknown"
        )

    def test_revision_mismatch_is_conservative(self):
        current = workspace(
            toolchain="leanprover/lean4:v4.32.0",
            mathlib_revision="6" * 40,
            lean_input_digest=(
                "cc346fd7850a83fb7af3902dfc378f74d8eb8c59c6d34322488269ebfe7615b1"
            ),
        )
        decision = self.policy.decide(
            current,
            "target",
            edge("target/dependency/mathlib", "Finset.card"),
        )
        self.assertTrue(decision.keep)
        self.assertEqual(decision.reason, "catalog_identity_mismatch")


if __name__ == "__main__":
    unittest.main()
