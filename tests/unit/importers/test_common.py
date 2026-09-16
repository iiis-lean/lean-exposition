import unittest

from lean_exposition.importers.common import assemble_workspace, asset_from_bytes, qualified_id
from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, Provenance, RawDecl, Repository, Scope,
    Status, TextContent, ValidationError, Workspace,
)


class CommonTests(unittest.TestCase):
    def test_singleton_roundtrip_and_unresolved_reference(self):
        p = (Provenance("fixture", "input"),)
        content = DeclContent(TextContent("", "present", p), TextContent("def x := 1", "present", p),
                              (Dependency(DeclRef("external", "x"), "lean_value", p),))
        decl = RawDecl(DeclRef("repo", "x"), "x", "Main", "root", "def", content,
                       Status("imported", p), p)
        workspace = assemble_workspace(
            repositories=[Repository("repo", "lean:v4.28.0", "root", revision="a" * 40)],
            assets=[], declarations=[decl], scopes=[Scope("root", "repo", "repository", "repo", p)])
        self.assertEqual(len(workspace.units), 1)
        self.assertEqual(workspace.units[0].members, ())
        self.assertEqual(workspace.manifest.repositories[1].version_status, "unresolved")
        self.assertEqual(Workspace.from_json(workspace.to_json()), workspace)

    def test_asset_digest_and_collision_safe_identity(self):
        self.assertNotEqual(qualified_id("a:b", "c"), qualified_id("a", "b:c"))
        asset = asset_from_bytes("repo", "Main.lean", b"test")
        asset.verify(b"test")
        with self.assertRaises(ValidationError):
            asset.verify(b"changed")
