"""Focused contracts for frozen facts, provenance, and unit ownership."""
from dataclasses import replace
import json
from pathlib import Path
import unittest

from lean_exposition.models import (
    DeclRef, DeclUnit, Repository, SourceRange, ValidationError, Workspace,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "models"


class FactTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace.from_json((FIXTURES / "workspace.json").read_text())

    def invalid(self, workspace):
        with self.assertRaises(ValidationError):
            workspace.validate()

    def test_roundtrip_preserves_definition_body_and_content_states(self):
        restored = Workspace.from_json(self.workspace.to_json())
        self.assertEqual(restored, self.workspace)
        self.assertEqual(restored.digest(), self.workspace.digest())
        self.assertIn(":= 1", restored.declarations[0].statement.formal.text)
        self.assertIsNone(restored.declarations[0].statement.nl.text)
        self.assertEqual(restored.declarations[1].statement.nl.text, "")
        self.assertEqual(restored.declarations[0].kind, "def")

    def test_workspace_identity_tracks_current_facts_without_manual_versions(self):
        self.assertNotIn("schema_version", json.loads(self.workspace.to_json())["manifest"])
        self.assertNotIn("input_version", json.loads(self.workspace.to_json())["manifest"])
        changed = replace(self.workspace, declarations=tuple(reversed(self.workspace.declarations)))
        self.assertNotEqual(changed.digest(), self.workspace.digest())

    def test_statement_proof_evidence_and_external_stub_are_preserved(self):
        theorem = self.workspace.declarations[1]
        self.assertEqual(theorem.statement.deps[0].evidence_kind, "lean_type")
        self.assertEqual(theorem.proof.deps[0].evidence_kind, "lc_declared")
        self.assertEqual(theorem.proof.deps[1].evidence_kind, "text_reference")
        self.assertNotIn(theorem.statement.deps[0].provider, {d.ref for d in self.workspace.declarations})
        self.assertEqual(self.workspace.manifest.repositories[-1].version_status, "unresolved")

    def test_recursive_coverage_and_no_feature_identity(self):
        self.assertEqual(self.workspace.coverage("main"), frozenset(d.ref for d in self.workspace.declarations))
        singleton = replace(self.workspace, units=tuple(DeclUnit(str(i), d.ref) for i, d in enumerate(self.workspace.declarations)))
        singleton.validate()
        self.assertEqual(singleton.declarations, self.workspace.declarations)
        replace(self.workspace, units=()).validate()

    def test_rejects_duplicate_ownership_self_cycles_and_missing_coverage(self):
        a, b = self.workspace.units
        for units in ((a, replace(b, representative=a.representative)),
                      (replace(a, members=("helper", "helper")), b),
                      (replace(a, members=("main",)), b),
                      (a, replace(b, members=("main",))), (b,)):
            with self.subTest(units=units):
                self.invalid(replace(self.workspace, units=units))

    def test_rejects_unknown_repositories_and_invalid_scope(self):
        a, b = self.workspace.declarations
        self.invalid(replace(self.workspace, declarations=(replace(a, ref=DeclRef("unknown", "x")), b)))
        self.invalid(replace(self.workspace, declarations=(replace(a, native_scope="missing"), b)))
        scope = self.workspace.scopes[0]
        self.invalid(replace(self.workspace, scopes=(replace(scope, parent=scope.scope_id),)))

    def test_rejects_unfrozen_version_but_allows_explicit_unresolved_stub(self):
        repo, external = self.workspace.manifest.repositories
        cases = (
            ((replace(repo, revision="main"), external), "revision must be a full"),
            ((replace(repo, revision=None, input_digest=None), external), "repository has no fixed version"),
            ((repo, replace(external, unresolved_reason=None)), "unresolved repository needs reason"),
            ((repo, replace(external, root_scope="root")), "unresolved repository is an external stub"),
        )
        for repositories, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValidationError, error):
                replace(self.workspace, manifest=replace(self.workspace.manifest, repositories=repositories)).validate()
        unresolved = Repository(external.repo_key, None, None, version_status="unresolved",
                                unresolved_reason="not in input lock")
        replace(self.workspace, manifest=replace(self.workspace.manifest,
                                                repositories=(repo, unresolved))).validate()

    def test_multiple_repositories_locks_and_same_local_id(self):
        workspace = Workspace.from_json((FIXTURES / "multi_repository.json").read_text())
        restored = Workspace.from_json(workspace.to_json())
        self.assertEqual(restored, workspace)
        first, second = restored.declarations
        self.assertEqual(first.ref.local_id, second.ref.local_id)
        self.assertNotEqual(first.ref, second.ref)
        repos = {repo.repo_key: repo for repo in restored.manifest.repositories}
        self.assertNotEqual(repos[first.ref.repo_key].input_digest, repos[second.ref.repo_key].input_digest)
        self.assertEqual(second.statement.deps[0].provider, first.ref)
        lock = restored.manifest.dependency_locks[0]
        self.assertEqual((lock.repo_key, lock.dependency_repo_key), (second.ref.repo_key, first.ref.repo_key))
        scopes = {scope.scope_id: scope for scope in restored.scopes}
        assets = {asset.asset_id: asset for asset in restored.manifest.assets}
        for decl in restored.declarations:
            self.assertEqual(repos[decl.ref.repo_key].root_scope, decl.native_scope)
            self.assertIsNone(scopes[decl.native_scope].parent)
            self.assertEqual(scopes[decl.native_scope].repo_key, decl.ref.repo_key)
            self.assertEqual(assets[decl.source_refs[0].asset_id].repo_key, decl.ref.repo_key)
        for asset in assets.values():
            asset.verify((FIXTURES / asset.path).read_bytes())
        self.assertEqual(restored.coverage("example-one"), frozenset((first.ref,)))
        self.assertEqual(restored.coverage("consumer-one"), frozenset((second.ref,)))

    def test_completion_status_is_optional_but_validated_when_supplied(self):
        data = json.loads(self.workspace.to_json())
        for decl in data["declarations"]:
            decl.pop("completion_status")
        workspace = Workspace.from_json(json.dumps(data))
        self.assertTrue(all(decl.completion_status is None for decl in workspace.declarations))
        self.assertEqual(Workspace.from_json(workspace.to_json()), workspace)
        a, b = self.workspace.declarations
        broken = replace(a, completion_status=replace(a.completion_status, state=""))
        with self.assertRaisesRegex(ValidationError, "status state must be nonempty"):
            replace(self.workspace, declarations=(broken, b)).validate()

    def test_source_asset_bytes_and_ranges(self):
        asset = self.workspace.manifest.assets[0]
        asset.verify((FIXTURES / "Example.lean").read_bytes())
        with self.assertRaises(ValidationError):
            asset.verify(b"changed")
        a, b = self.workspace.declarations
        for source in (SourceRange("missing", 1, 1, 1, 2), SourceRange(asset.asset_id, 0, None, 2, None),
                       SourceRange(asset.asset_id, 2, 2, 2, 1), SourceRange(asset.asset_id, 1, None, 1, 3)):
            self.invalid(replace(self.workspace, declarations=(replace(a, source_refs=(source,)), b)))
        replace(self.workspace, declarations=(replace(a, source_refs=(SourceRange(asset.asset_id, 1, None, 1, None),)), b)).validate()

    def test_strict_json_unknown_fields_types_and_duplicates(self):
        data = json.loads(self.workspace.to_json())
        data["typo"] = 1
        with self.assertRaises(ValidationError):
            Workspace.from_json(json.dumps(data))
        data.pop("typo")
        data["declarations"][0]["local_public"] = "false"
        with self.assertRaises(ValidationError):
            Workspace.from_json(json.dumps(data))
        with self.assertRaises(ValidationError):
            Workspace.from_json('{"manifest": {}, "manifest": {}}')

    def test_missing_text_needs_reason_and_present_text_cannot_be_null(self):
        a, b = self.workspace.declarations
        for nl in (replace(a.statement.nl, reason=None), replace(a.statement.nl, status="present")):
            self.invalid(replace(self.workspace, declarations=(replace(a, statement=replace(a.statement, nl=nl)), b)))

    def test_generated_private_identity_and_provenance_roundtrip(self):
        a, b = self.workspace.declarations
        generated = replace(a, lean_name="_private.Example.0.Example.one", generated_from=b.ref,
                            kernel_kind="defnDecl")
        workspace = replace(self.workspace, declarations=(generated, b))
        restored = Workspace.from_json(workspace.to_json()).declarations[0]
        self.assertEqual(restored.generated_from, b.ref)
        self.assertEqual(restored.provenance, a.provenance)
        self.assertEqual(restored.lean_name, generated.lean_name)
        self.invalid(replace(workspace, declarations=(replace(generated, generated_from=a.ref), b)))

    def test_python_records_reject_wrong_container_and_record_types(self):
        self.invalid(replace(self.workspace, declarations=list(self.workspace.declarations)))
        self.invalid(replace(self.workspace, manifest=json.loads(self.workspace.to_json())["manifest"]))

    def test_primary_outcomes_are_not_all_local_public_declarations(self):
        self.assertTrue(self.workspace.declarations[0].local_public)
        self.assertEqual(self.workspace.manifest.repositories[0].primary_outcomes,
                         (self.workspace.declarations[1].ref,))


if __name__ == "__main__":
    unittest.main()
