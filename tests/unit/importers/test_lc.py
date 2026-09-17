import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from lean_exposition.construction import build_repository
from lean_exposition.importers.lc import LCRepositoryAdapter, LCRepositoryInput, _load_lc_facts, load_lc_workspace
from lean_exposition.models.facts import ValidationError, Workspace


class LCImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def git(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, path, name, value):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value) if not isinstance(value, str) else value)

    def commit(self, path):
        self.git(path, "add", ".")
        self.git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        return self.git(path, "rev-parse", "HEAD")

    def fixture(self, name, dependency=None):
        path = self.root / name
        path.mkdir()
        self.git(path, "init", "-q")
        self.write(path, "lean-toolchain", "leanprover/lean4:v4.28.0\n")
        self.write(path, "lake-manifest.json", {"packages": []})
        self.write(path, ".lean_constellation/index/nodes.json", {"entries": [
            {"path": "Main", "node_id": "root", "kind": "scope", "active": True,
             "lifecycle": "active", "active_contract_version": 1},
            {"path": "Main.Child", "node_id": "child", "kind": "content", "active": True,
             "lifecycle": "active", "active_contract_version": 1}]})
        self.write(path, ".lean_constellation/nodes/root/contracts/1.json", {
            "exports": [{"repo": None, "node": "Main.Child", "name": "x", "revision": 1}],
            "interfaces": []})
        base = ".lean_constellation/nodes/child/decl_graph/decls/x/"
        self.write(path, base + "decl.json", {"name": "x", "node_path": "Main.Child", "module": "Child",
                   "kind": "def", "current_revision": 2, "public": True, "lifecycle": "active"})
        self.write(path, base + "revisions/1.json", {"invalid": "historical revision must not be read"})
        self.write(path, base + "revisions/2.json", {"lean_decl_name": "N.x", "change": {
            "summary": "Defines the fixture value."}, "statement": {
            "nl": {"text": ""}, "formal": {"code": "def x : Nat := 2"},
            "deps": [dependency] if dependency else []}, "proof": None})
        self.write(path, "Child.lean", "import Init\n-- lean-constellation: declaration-source-begin\ndef x : Nat := 2\n")
        self.commit(path)
        return path

    def test_current_exports_empty_nl_and_implicit_root(self):
        path = self.fixture("repo")
        # HEAD snapshot ignores subsequent working-tree edits.
        self.write(path, "Child.lean", "uncommitted modification")
        workspace = load_lc_workspace(LCRepositoryInput(path, "repo")).workspace
        decl = workspace.declarations[0]
        self.assertEqual(decl.statement.formal.text, "def x : Nat := 2")
        self.assertEqual(decl.statement.nl.text, "")
        self.assertEqual(decl.statement.nl.status, "present")
        self.assertIsNone(decl.proof)
        self.assertEqual(decl.kind, "def")
        self.assertTrue(decl.local_public)
        self.assertEqual(workspace.manifest.repositories[0].primary_outcomes, (decl.ref,))
        root = next(s for s in workspace.scopes if s.parent is None)
        self.assertEqual(root.kind, "repository")
        self.assertEqual(root.name, "repo")
        self.assertIn("def x", decl.source_context[0].text)
        self.assertEqual(decl.source_refs[0].start_line, 3)
        self.assertEqual(Workspace.from_json(workspace.to_json()), workspace)

    def test_adapter_bundle_is_lossless_preserves_units_and_exposes_summary(self):
        path = self.fixture("repo")
        legacy = _load_lc_facts(LCRepositoryInput(path, "repo"))[0]
        bundle = load_lc_workspace(LCRepositoryInput(path, "repo"))
        self.assertEqual(bundle.workspace.to_json(), legacy.to_json())
        self.assertEqual(bundle.structure_policy.unit_aggregation, "preserve")
        self.assertEqual(len(bundle.workspace.units), len(bundle.workspace.declarations))
        self.assertEqual(bundle.source_texts[0].text, "Defines the fixture value.")
        entry = next(item for item in bundle.dependency_coverage.entries
                     if item.evidence_domain == "lc_declared")
        self.assertEqual(entry.status, "complete")

    def test_locked_provider_is_read_instead_of_checkout_head(self):
        provider = self.fixture("provider")
        locked = self.git(provider, "rev-parse", "HEAD")
        self.write(provider, "Child.lean", "new projection")
        latest = self.commit(provider)
        dep = {"kind": "repo_decl", "ref": {"repo": "provider", "node": "Main.Child", "name": "x", "revision": 1}}
        consumer = self.fixture("consumer", dep)
        self.write(consumer, "lake-manifest.json", {"packages": [{"name": "provider", "rev": locked}]})
        self.commit(consumer)
        workspace = load_lc_workspace(LCRepositoryInput(consumer, "consumer"), [LCRepositoryInput(provider, "provider")]).workspace
        self.assertEqual(workspace.manifest.repositories[1].revision, locked)
        refs = {d.ref for d in workspace.declarations}
        self.assertIn(workspace.declarations[0].statement.deps[0].provider, refs)
        with self.assertRaisesRegex(ValidationError, "conflicts with Lake lock"):
            load_lc_workspace(LCRepositoryInput(consumer, "consumer"), [LCRepositoryInput(provider, "provider", latest)])

    def test_missing_provider_is_honest_stub(self):
        dep = {"kind": "repo_decl", "ref": {"repo": "missing", "node": "Main", "name": "x"}}
        path = self.fixture("repo", dep)
        workspace = load_lc_workspace(LCRepositoryInput(path, "repo")).workspace
        self.assertEqual(workspace.manifest.repositories[-1].repo_key, "missing")
        self.assertEqual(workspace.manifest.repositories[-1].version_status, "unresolved")

    def test_absent_exports_is_not_replaced_with_interfaces(self):
        path = self.fixture("repo")
        self.write(path, ".lean_constellation/nodes/root/contracts/1.json", {"interfaces": []})
        self.commit(path)
        with self.assertRaisesRegex(ValidationError, "no exports"):
            load_lc_workspace(LCRepositoryInput(path, "repo"))

    def test_local_public_is_not_automatically_a_primary_outcome(self):
        path = self.fixture("repo")
        self.write(path, ".lean_constellation/nodes/root/contracts/1.json", {"exports": [], "interfaces": []})
        self.commit(path)
        workspace = load_lc_workspace(LCRepositoryInput(path, "repo")).workspace
        self.assertTrue(workspace.declarations[0].local_public)
        self.assertEqual(workspace.manifest.repositories[0].primary_outcomes, ())

    def test_missing_local_dependency_fails(self):
        dep = {"kind": "repo_decl", "ref": {"repo": None, "node": "Main", "name": "absent"}}
        path = self.fixture("repo", dep)
        with self.assertRaisesRegex(ValidationError, "does not resolve"):
            load_lc_workspace(LCRepositoryInput(path, "repo"))

    def test_missing_export_fails(self):
        path = self.fixture("repo")
        self.write(path, ".lean_constellation/nodes/root/contracts/1.json", {
            "exports": [{"repo": None, "node": "Main", "name": "absent"}], "interfaces": []})
        self.commit(path)
        with self.assertRaisesRegex(ValidationError, "Main export does not resolve"):
            load_lc_workspace(LCRepositoryInput(path, "repo"))
