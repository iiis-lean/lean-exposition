from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structure"))
from test_graph import P, ref, workspace

from lean_exposition.construction import SourceTextContribution, build_repository
from lean_exposition.construction.materials import MaterialBinding, MaterialBundle, MaterialTarget
from lean_exposition.importers.common import adapter_result_from_workspace
from lean_exposition.importers.merge import merge_adapters


class MergeTests(unittest.TestCase):
    def adapters(self):
        source_ws = workspace("a")
        semantic_ws = workspace(["canonical_a"])
        semantic_ws = replace(semantic_ws, declarations=(replace(semantic_ws.declarations[0], lean_name="a"),))
        source = adapter_result_from_workspace(source_ws, unit_aggregation="preserve", authority="source", method="test",
            source_texts=(SourceTextContribution(ref("a"), "summary", "en", "A.", P, "a" * 64),))
        semantic = adapter_result_from_workspace(semantic_ws, unit_aggregation="preserve", authority="source", method="test",
            source_texts=(SourceTextContribution(ref("canonical_a"), "summary", "zh", "甲。", P, "b" * 64),))
        return source, semantic

    def test_canonicalization_preserves_both_text_contributors(self):
        merged = merge_adapters(*self.adapters())
        self.assertEqual([t.ref for t in merged.source_texts], [ref("canonical_a")] * 2)
        self.assertEqual([t.text for t in merged.source_texts], ["A.", "甲。"])
        build_repository(merged).validate()

    def test_material_declaration_target_is_canonicalized(self):
        source, semantic = self.adapters()
        binding = MaterialBinding("record", MaterialTarget("declaration", "r", ref=ref("a")), "explains", "exact", P)
        material = MaterialBundle("r", (), (), (binding,), "a" * 64, {}, "b" * 64, {})
        source = replace(source, materials=(material,))
        merged = merge_adapters(source, semantic)
        self.assertEqual(merged.materials[0].bindings[0].target.ref, ref("canonical_a"))
        self.assertEqual(source.materials[0].bindings[0].target.ref, ref("a"))
