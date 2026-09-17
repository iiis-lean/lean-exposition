"""Checks for the shareable real-project profile contracts."""
from pathlib import Path
import unittest

from lean_exposition.construction import RepositoryProfile


ROOT = Path(__file__).resolve().parents[3]
PROFILE_DIR = ROOT / "configs" / "project_profiles"


class ProjectProfileTests(unittest.TestCase):
    def load(self, name):
        return RepositoryProfile.from_json((PROFILE_DIR / name).read_text())

    def test_erdos1025_uses_only_preserving_lc_catalog_adapter(self):
        profile = self.load("erdos1025.json")
        plan = profile.plan()
        self.assertEqual(profile.stage, "formal")
        self.assertEqual(profile.identity.revision,
                         "d12396b85d7839ef96beadbef9525150a33a0df5")
        self.assertEqual([(item.kind, item.config["text_ast"],
                           item.config["unit_aggregation"])
                          for item in plan.contributors],
                         [("lc_catalog", False, "preserve")])
        self.assertEqual([item.config["tex_path"] for item in plan.material_assets],
                         ["main.tex", "paper/essays2withchanges.tex"])
        self.assertEqual(len(plan.primary_outcomes), 5)

    def test_multicolor_uses_only_preserving_lc_catalog_adapter(self):
        profile = self.load("multicolor_triangle_ramsey.json")
        plan = profile.plan()
        self.assertEqual(profile.stage, "formal")
        self.assertEqual(profile.identity.revision,
                         "ef70c6bd6f71bbbdd39052867bf280726858db3e")
        self.assertEqual([(item.kind, item.config["text_ast"],
                           item.config["unit_aggregation"])
                          for item in plan.contributors],
                         [("lc_catalog", False, "preserve")])
        self.assertEqual(sum(item.config["document_root"]
                             for item in plan.material_assets), 1)
        self.assertEqual(len(plan.material_assets), 8)
        self.assertEqual(len(plan.primary_outcomes), 7)

    def test_hopf_is_source_inventory_with_fixed_pdf_material(self):
        profile = self.load("hopf_s6.json")
        plan = profile.plan()
        self.assertEqual(profile.stage, "inventory")
        self.assertEqual(profile.identity.revision,
                         "9ac8a456b526527837d7082ff775213ca8bc9809")
        self.assertEqual([(item.kind, item.config["source_roots"])
                          for item in plan.contributors],
                         [("source_inventory", ["Solution.lean"])])
        self.assertEqual([(item.parser_id, item.sha256)
                          for item in plan.material_assets],
                         [("pdf_pages",
                           "283bba102dd1d5dc346af81b28145bdaaea6654398d5032e76e97bafb9a858f2")])

    def test_navier_stokes_and_euler_are_isolated_inventory_slices(self):
        profile = self.load("navier_stokes_euler.json")
        self.assertEqual(profile.stage, "inventory")
        ns = profile.plan("navier-stokes")
        euler = profile.plan("euler")
        self.assertEqual(ns.source_roots, ("NavierStokes.lean", "NavierStokes"))
        self.assertEqual(euler.source_roots, ("Euler.lean", "Euler"))
        self.assertEqual({item.contributor_id for item in ns.contributors}, {"ns-source"})
        self.assertEqual({item.contributor_id for item in euler.contributors}, {"euler-source"})
        self.assertEqual(len(ns.primary_outcomes), 2)
        self.assertEqual(len(euler.primary_outcomes), 2)

    def test_flt_uses_the_fixed_published_bundle_without_compiled_claims(self):
        profile = self.load("anthropic_fermats_last_theorem.json")
        plan = profile.plan()
        self.assertEqual(profile.stage, "provisional")
        self.assertEqual([item.kind for item in plan.contributors], ["published"])
        bundle_assets = [item for item in plan.material_assets
                         if item.parser_id == "published_bundle"]
        self.assertEqual(len(bundle_assets), 4)
        self.assertEqual(sum(item.config.get("callback") == "FLT_SHARD_CB"
                             for item in bundle_assets), 1)
        self.assertFalse(any(item.kind == "compiled" for item in plan.contributors))

    def test_all_shareable_profiles_use_the_single_current_contract(self):
        paths = sorted(PROFILE_DIR.glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            profile = RepositoryProfile.from_json(path.read_text())
            self.assertNotIn("schema_version", profile.to_dict(), path.name)
            self.assertEqual(profile, RepositoryProfile.from_json(profile.to_json()))


if __name__ == "__main__":
    unittest.main()
