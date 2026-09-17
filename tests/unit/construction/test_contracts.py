"""Direct tests for the unified construction boundary."""
from dataclasses import replace
import hashlib
import json
import unittest

from lean_exposition.construction import (
    CanonicalDeclLocator, CoverageContribution, DeclarationContribution,
    DeclUnitSeed, DependencyCoverage, FieldContribution, RepositoryAdapterResult,
    RepositoryBuildBundle, ScopeSeed, SourceTextContribution, StructurePolicy,
    UnresolvedDeclLocator, build_repository,
)
from lean_exposition.models import (
    DeclRef, Dependency, Provenance, Repository, SourceAsset, SourceRange, Status,
    TextContent, ValidationError,
)


class ConstructionTests(unittest.TestCase):
    def setUp(self):
        self.ref = DeclRef("demo", "Demo.answer")
        self.external = DeclRef("library", "Nat")
        self.provenance = (Provenance("fixture", "catalog"),)
        self.asset = SourceAsset("demo-main", "demo", "Demo.lean", hashlib.sha256(b"source").hexdigest())
        self.source_range = SourceRange(self.asset.asset_id, 1, 1, 1, 20)

    def field(self, name, value, *, authority="source", provenance=None):
        return FieldContribution(name, "present", value, authority, "fixture", "read",
                                 provenance or self.provenance, "a" * 64)

    def declaration(self, *, fields=(), locator=None):
        text = TextContent("the answer", "present", self.provenance)
        formal = TextContent("def answer := 42", "present", self.provenance)
        required = (
            self.field("lean_name", "Demo.answer", authority="lean_environment"),
            self.field("module", "Demo", authority="lean_environment"),
            self.field("native_scope", "demo:root"),
            self.field("kind", "def", authority="lean_environment"),
            self.field("statement.nl", text),
            self.field("statement.formal", formal),
            self.field("statement.deps", (), authority="lean_environment"),
            self.field("extraction_status", Status("imported", self.provenance)),
            self.field("provenance", self.provenance),
            self.field("source_refs", (self.source_range,)),
        )
        return DeclarationContribution(locator or CanonicalDeclLocator(self.ref), required + fields)

    def adapter(self, *, declarations=None, coverage=(), source_texts=(), units=None):
        return RepositoryAdapterResult(
            repositories=(Repository("demo", "lean:v4.28.0", "demo:root", revision="b" * 40),),
            assets=(self.asset,),
            declarations=tuple(declarations or (self.declaration(),)),
            scopes=(ScopeSeed("demo:root", "demo", "repository", "demo", self.provenance),),
            units=tuple(units or (DeclUnitSeed("demo:answer", self.ref, (), self.provenance),)),
            unit_aggregation="native_helpers",
            coverage=tuple(coverage),
            source_texts=tuple(source_texts),
        )

    def test_bundle_roundtrip_digest_and_strict_json(self):
        bundle = build_repository(self.adapter())
        restored = RepositoryBuildBundle.from_json(bundle.to_json())
        self.assertEqual(restored, bundle)
        self.assertEqual(restored.digest(), bundle.digest())
        self.assertEqual(StructurePolicy.from_json(bundle.structure_policy.to_json()),
                         bundle.structure_policy)
        self.assertEqual(DependencyCoverage.from_json(bundle.dependency_coverage.to_json()),
                         bundle.dependency_coverage)

        data = json.loads(bundle.to_json())
        data["typo"] = True
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            RepositoryBuildBundle.from_json(json.dumps(data))
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            RepositoryBuildBundle.from_json('{"workspace": {}, "workspace": {}}')
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            StructurePolicy.from_json(bundle.structure_policy.to_json().replace(
                '"unit_aggregation":', '"typo": true, "unit_aggregation":'))
        missing = json.loads(bundle.structure_policy.to_json())
        del missing["production_structure"]
        with self.assertRaisesRegex(ValidationError, "production_structure"):
            StructurePolicy.from_json(json.dumps(missing))

    def test_unknown_and_confirmed_empty_coverage_have_distinct_identity(self):
        unknown = build_repository(self.adapter())
        complete = build_repository(self.adapter(coverage=(
            CoverageContribution(self.ref, "statement", "lean_type", "complete", self.provenance),
        )))
        self.assertEqual(unknown.workspace.digest(), complete.workspace.digest())
        self.assertNotEqual(unknown.dependency_coverage.digest(), complete.dependency_coverage.digest())
        self.assertEqual(unknown.dependency_coverage.entries[1].status, "unknown")
        self.assertEqual(complete.dependency_coverage.entries[1].status, "complete")

    def test_rejects_stale_sidecars_and_sidecar_references(self):
        bundle = build_repository(self.adapter())
        with self.assertRaisesRegex(ValidationError, "stale StructurePolicy"):
            replace(bundle, structure_policy=replace(bundle.structure_policy,
                                                      workspace_digest="0" * 64)).validate()
        with self.assertRaisesRegex(ValidationError, "stale DependencyCoverage"):
            replace(bundle, dependency_coverage=replace(bundle.dependency_coverage,
                                                        workspace_digest="0" * 64)).validate()
        text = SourceTextContribution(DeclRef("demo", "missing"), "summary", "en", "summary",
                                      self.provenance, "c" * 64)
        with self.assertRaisesRegex(ValidationError, "source text references unknown"):
            replace(bundle, source_texts=(text,)).validate()

    def test_authority_resolves_lower_observation_but_equal_authority_conflict_fails(self):
        lower = self.field("kind", "opaque", authority="project_metadata")
        lower_observation = DeclarationContribution(CanonicalDeclLocator(self.ref), (lower,))
        result = build_repository(self.adapter(declarations=(self.declaration(), lower_observation)))
        self.assertEqual(result.workspace.declarations[0].kind, "def")

        conflict = self.field("kind", "theorem", authority="lean_environment")
        conflicting_observation = DeclarationContribution(CanonicalDeclLocator(self.ref), (conflict,))
        with self.assertRaisesRegex(ValidationError, "authoritative conflict for kind"):
            build_repository(self.adapter(declarations=(self.declaration(), conflicting_observation)))

    def test_same_semantic_value_merges_value_provenance(self):
        extra_provenance = (Provenance("source", "Demo.lean"),)
        duplicate = self.field(
            "statement.nl", TextContent("the answer", "present", extra_provenance),
            authority="source", provenance=extra_provenance,
        )
        observation = DeclarationContribution(CanonicalDeclLocator(self.ref), (duplicate,))
        bundle = build_repository(self.adapter(declarations=(self.declaration(), observation)))
        self.assertEqual(bundle.workspace.declarations[0].statement.nl.provenance,
                         self.provenance + extra_provenance)

    def test_dependency_occurrences_merge_by_provider_and_evidence_kind(self):
        p2 = (Provenance("compiler", "Demo.olean"),)
        first = self.field("statement.deps", (Dependency(self.external, "lean_type", self.provenance),),
                           authority="lean_environment")
        second = self.field("statement.deps", (Dependency(self.external, "lean_type", p2),
                                                Dependency(self.external, "lean_value", p2)),
                            authority="lean_environment", provenance=p2)
        observations = DeclarationContribution(CanonicalDeclLocator(self.ref), (first,))
        more_observations = DeclarationContribution(CanonicalDeclLocator(self.ref), (second,))
        bundle = build_repository(self.adapter(
            declarations=(self.declaration(), observations, more_observations)))
        dependencies = bundle.workspace.declarations[0].statement.deps
        self.assertEqual([(d.provider, d.evidence_kind) for d in dependencies],
                         [(self.external, "lean_type"), (self.external, "lean_value")])
        self.assertEqual(dependencies[0].provenance, self.provenance + p2)
        self.assertEqual(bundle.workspace.manifest.repositories[-1].version_status, "unresolved")

    def test_unresolved_locator_is_reported_and_authoritative_link_is_merged(self):
        unresolved = DeclarationContribution(
            UnresolvedDeclLocator("demo", "Other", "missing"),
            (self.field("statement.nl", TextContent("unused", "present", self.provenance)),),
        )
        bundle = build_repository(self.adapter(declarations=(self.declaration(), unresolved)))
        self.assertEqual(bundle.unresolved_locators, (unresolved.locator,))
        self.assertEqual(len(bundle.workspace.declarations), 1)

        linked_text = TextContent("author text", "present", (Provenance("author", "paper"),))
        linked = DeclarationContribution(
            UnresolvedDeclLocator("demo", "Demo", "answer", authoritative_ref=self.ref),
            (self.field("statement.nl", linked_text, authority="lc_catalog"),),
        )
        linked_bundle = build_repository(self.adapter(declarations=(self.declaration(), linked)))
        self.assertFalse(linked_bundle.unresolved_locators)
        self.assertEqual(linked_bundle.workspace.declarations[0].statement.nl.text, "author text")

    def test_source_text_is_fixed_and_validated(self):
        text = SourceTextContribution(self.ref, "summary", "en", "A short summary.",
                                      self.provenance, "d" * 64)
        bundle = build_repository(self.adapter(source_texts=(text,)))
        self.assertEqual(RepositoryBuildBundle.from_json(bundle.to_json()).source_texts, (text,))
        duplicate = replace(bundle, source_texts=(text, text))
        with self.assertRaisesRegex(ValidationError, "duplicate source text"):
            duplicate.validate()

    def test_builder_requires_explicit_complete_units_and_valid_coverage(self):
        empty_units = replace(self.adapter(), units=())
        with self.assertRaisesRegex(ValidationError, "explicit complete unit seeds"):
            build_repository(empty_units)
        bad = CoverageContribution(self.ref, "proof", "lean_value", "complete", self.provenance)
        with self.assertRaisesRegex(ValidationError, "without proof"):
            build_repository(self.adapter(coverage=(bad,)))


if __name__ == "__main__":
    unittest.main()
