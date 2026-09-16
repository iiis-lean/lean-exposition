"""Normalize only unary display wrappers, preserving immutable source scopes."""
from dataclasses import replace
import unittest

from test_graph import P, ref, workspace
from lean_exposition.models import Scope
from lean_exposition.structure import BuildConfig, Hierarchy, build_hierarchy


def fixture(names, scopes, placement, pairs=()):
    w = workspace(names, [(ref(a), ref(b), "lean_value") for a, b in pairs], scopes=placement)
    records = w.scopes[:2] + tuple(Scope(id, "r", "module", id, P, parent) for id, parent in scopes)
    return replace(w, scopes=records)


def nodes(h):
    return {n["id"]: n for n in h.nodes}


def unit_order(h):
    index = nodes(h)
    def visit(id):
        node = index[id]
        if node["kind"] == "unit":
            return [node["representative"]["local_id"]]
        return [ref for child in node["children"] for ref in visit(child)]
    return visit(h.root_id)


class ScopeCompressionTests(unittest.TestCase):
    def test_repository_absorbs_scope_chain_before_regions(self):
        w = fixture("abc", [("directory", "root"), ("module", "directory")], {n: "module" for n in "abc"})
        original = w.to_json()
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False, region_k=2))
        index = nodes(h)
        self.assertFalse(any(n["kind"] == "scope" for n in h.nodes))
        self.assertEqual(h.resolve_node_id("directory"), h.root_id)
        self.assertEqual(h.resolve_node_id("module"), h.root_id)
        self.assertEqual({r["scope_id"] for r in index[h.root_id]["metadata"]["collapsed_scopes"]}, {"directory", "module"})
        self.assertFalse(any(n["kind"] == "region" and len(n["children"]) == 1 for n in h.nodes))
        self.assertEqual(w.to_json(), original)

    def test_unique_unit_survives_and_receives_transitive_aliases(self):
        w = fixture("a", [("outer", "root"), ("inner", "outer")], {"a": "inner"})
        old = build_hierarchy(w, "r", config=BuildConfig(native_helper=False, scope_compression="none"))
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False))
        index = nodes(h)
        self.assertEqual(len(h.nodes), 2)
        unit = index[index[h.root_id]["children"][0]]
        self.assertEqual(unit["kind"], "unit")
        self.assertEqual(h.resolve_node_id("root"), h.root_id)
        for name in ("outer", "inner"):
            old_id = old.resolve_node_id(name)
            self.assertEqual(h.resolve_node_id(name), unit["id"])
            self.assertEqual(h.resolve_node_id(old_id), unit["id"])
        self.assertEqual(h.resolve_node_id(unit["id"]), unit["id"])
        self.assertEqual(h.edges, old.edges)
        self.assertEqual(Hierarchy.from_dict(h.to_dict()), h)

    def test_mixed_direct_decl_and_branch_retains_meaningful_scope(self):
        w = fixture("abc", [("outer", "root"), ("branch", "outer")],
                    {"a": "outer", "b": "branch", "c": "branch"}, [("b", "a")])
        old = build_hierarchy(w, "r", config=BuildConfig(native_helper=False, scope_compression="none"))
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False))
        branch = nodes(h)[h.resolve_node_id("branch")]
        self.assertEqual(branch["kind"], "scope")
        self.assertEqual(len(branch["children"]), 2)
        self.assertEqual(h.resolve_node_id("outer"), h.root_id)
        self.assertEqual(unit_order(h), unit_order(old))
        self.assertEqual(h.edges, old.edges)
        self.assertEqual(nodes(h)[h.root_id]["decl_refs"], nodes(old)[old.root_id]["decl_refs"])

    def test_nonroot_unary_wrapper_maps_to_unit_with_a_sibling(self):
        w = fixture("ab", [("wrapper", "root")], {"a": "root", "b": "wrapper"})
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False))
        target = nodes(h)[h.resolve_node_id("wrapper")]
        self.assertEqual(target["kind"], "unit")
        self.assertEqual(target["representative"]["local_id"], "b")
        self.assertEqual(len(nodes(h)[h.root_id]["children"]), 2)

    def test_repeat_build_and_stable_unit_id(self):
        w = fixture("abc", [("outer", "root"), ("inner", "outer")], {n: "inner" for n in "abc"})
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False))
        self.assertEqual(h, build_hierarchy(w, "r", config=BuildConfig(native_helper=False)))
        old = build_hierarchy(w, "r", config=BuildConfig(native_helper=False, scope_compression="none"))
        self.assertEqual(h.root_id, old.root_id)
        self.assertEqual({n["id"] for n in h.nodes if n["kind"] == "unit"},
                         {n["id"] for n in old.nodes if n["kind"] == "unit"})
        self.assertNotEqual(h.hierarchy_id, old.hierarchy_id)

    def test_unknown_or_dangling_alias_is_rejected(self):
        h = build_hierarchy(fixture("a", [], {"a": "root"}), "r")
        with self.assertRaises(KeyError):
            h.resolve_node_id("unknown")
        data = h.to_dict()
        root = next(n for n in data["nodes"] if n["id"] == h.root_id)
        root["metadata"]["aliases"]["broken"] = "nonexistent"
        with self.assertRaisesRegex(ValueError, "aliases"):
            Hierarchy.from_dict(data)


if __name__ == "__main__":
    unittest.main()
