"""Material record, binding, digest, and TeX occurrence contracts."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lean_exposition.construction.materials import (
    MaterialBinding, MaterialBundle, MaterialRecord, MaterialTarget,
)
from lean_exposition.models import DeclRef, Provenance, SourceAsset, SourceRange, ValidationError
from lean_exposition.structure.order import material_subject_projections
from lean_exposition.structure.source import derive_tex_materials


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MaterialBundleTests(unittest.TestCase):
    def setUp(self):
        self.text = "A theorem.\n"
        self.asset = SourceAsset("paper", "repo", "paper.txt", digest(self.text))
        self.location = SourceRange("paper", 1, 1, 1, len(self.text))
        self.provenance = (Provenance("fixture", "paper.txt#1", (self.location,)),)
        self.parser = digest("parser")
        self.binder = digest("binder")
        self.parser_config = {"mode": "text"}
        parser_config_digest = hashlib.sha256(json.dumps(
            self.parser_config, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.record = MaterialRecord.create(
            asset=self.asset, occurrence_id="paper:1", source_range=self.location,
            parser_implementation_digest=self.parser,
            parser_config_digest=parser_config_digest,
            text=self.text, provenance=self.provenance,
        )

    def bundle(self, bindings=(), binder_config=None):
        return MaterialBundle.create(
            repo_key="repo", assets=(self.asset,), records=(self.record,), bindings=bindings,
            parser_implementation_digest=self.parser, parser_config=self.parser_config,
            binder_implementation_digest=self.binder, binder_config=binder_config or {},
        )

    def test_strict_roundtrip_and_duplicate_keys(self):
        target = MaterialTarget("declaration", "repo", ref=DeclRef("repo", "D"))
        binding = MaterialBinding(self.record.record_id, target, "states", "exact", self.provenance)
        bundle = self.bundle((binding,))
        self.assertEqual(MaterialBundle.from_json(bundle.to_json()), bundle)
        self.assertEqual(len(bundle.material_digest()), 64)
        self.assertEqual(len(bundle.binding_digest()), 64)
        data = bundle.to_dict()
        data["extra"] = True
        with self.assertRaisesRegex(ValidationError, "fields"):
            MaterialBundle.from_dict(data)
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            MaterialBundle.from_json('{"repo_key":"repo","repo_key":"other"}')

    def test_parser_and_binder_changes_invalidate_digests(self):
        baseline = self.bundle()
        changed_binder = self.bundle(binder_config={"labels": True})
        self.assertEqual(baseline.material_digest(), changed_binder.material_digest())
        self.assertNotEqual(baseline.binding_digest(), changed_binder.binding_digest())
        stale = replace(baseline, parser_config={"mode": "changed"})
        with self.assertRaisesRegex(ValidationError, "identity mismatch"):
            stale.validate()

    def test_exact_multiple_declarations_in_one_atom_is_unique(self):
        bindings = tuple(MaterialBinding(
            self.record.record_id,
            MaterialTarget("declaration", "repo", ref=DeclRef("repo", name)),
            "states", "exact", self.provenance,
        ) for name in ("A", "B"))
        bundle = self.bundle(bindings)
        target_atoms = {binding.target.canonical_key(): ("unit",) for binding in bindings}
        projection = next(iter(material_subject_projections(bundle, target_atoms).values()))
        self.assertEqual(projection.status, "exact")
        self.assertEqual(projection.atoms, ("unit",))

    def test_binding_statuses_are_explicit_and_one_decision_per_target(self):
        target = MaterialTarget("declaration_locator", "repo", identifier="raw.Name")
        for status in ("exact", "candidate", "ambiguous", "unresolved"):
            self.bundle((MaterialBinding(self.record.record_id, target, "explains", status,
                                         self.provenance),))
        with self.assertRaisesRegex(ValidationError, "duplicate material binding"):
            self.bundle((
                MaterialBinding(self.record.record_id, target, "explains", "candidate",
                                self.provenance),
                MaterialBinding(self.record.record_id, target, "explains", "ambiguous",
                                self.provenance),
            ))


class TexMaterialTests(unittest.TestCase):
    def test_repeated_include_has_distinct_occurrences_and_nested_order(self):
        files = {
            "main.tex": "\\documentclass{article}\n\\input{part}\n\\input{part}\n",
            "part.tex": "\\section{Setup}\\label{sec:setup}\nBody.\n",
        }
        assets = tuple(SourceAsset(path, "repo", "corpus/" + path, digest(text))
                       for path, text in sorted(files.items()))
        result = derive_tex_materials(repo_key="repo", assets=assets, files=files,
                                      roots=("main.tex",), producer="lc_tex_document",
                                      strength="protected")
        bodies = [record for record in result.materials.records if record.text == "Body."]
        self.assertEqual(len(bodies), 2)
        self.assertNotEqual(bodies[0].occurrence_id, bodies[1].occurrence_id)
        self.assertNotEqual(bodies[0].record_id, bodies[1].record_id)
        sequence = result.order_evidence.sequences[0]
        self.assertEqual(len(sequence.members), 5)
        self.assertEqual(result.order_evidence.material_digest,
                         result.materials.material_digest())
        self.assertEqual(result.order_evidence.binding_digest,
                         result.materials.binding_digest())
        self.assertTrue(any(item["code"] == "ambiguous_include_line"
                            for item in result.materials.diagnostics))

    def test_tex_bytes_must_match_fixed_source_asset(self):
        text = "\\documentclass{article}\nBody.\n"
        asset = SourceAsset("main", "repo", "main.tex", digest("different"))
        with self.assertRaisesRegex(ValidationError, "asset digest mismatch"):
            derive_tex_materials(repo_key="repo", assets=(asset,),
                                 files={"main.tex": text}, roots=("main.tex",))


if __name__ == "__main__":
    unittest.main()
