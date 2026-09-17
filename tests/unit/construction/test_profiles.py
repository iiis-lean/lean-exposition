"""Direct tests for declarative repository profiles and artifact parsers."""
from dataclasses import replace
import hashlib
import json
import unittest

from lean_exposition.construction.contributions import (
    CanonicalDeclLocator, UnresolvedDeclLocator,
)
from lean_exposition.construction.profiles import (
    ContributorSpec, DeclarationLocatorSpec, MaterialAssetSpec, OrderHint,
    RepositoryIdentity, RepositoryProfile, ScopeHint, TargetSlice, UnitHint,
    parse_formalization_yaml, parse_proof_path_markdown, parse_published_html_shard,
    parse_published_site_bundle,
)
from lean_exposition.models import DeclRef, SourceAsset, ValidationError


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class RepositoryProfileTests(unittest.TestCase):
    def asset_spec(self, asset_id="paper", path="paper.yaml", text="{}",
                   parser="formalization_yaml"):
        return MaterialAssetSpec(
            asset_id, path, digest(text), "text/yaml", parser, "exact_name", "primary", {},
        )

    def profile(self, *, stage="provisional", assets=(), slices=(), contributors=None,
                primary_outcomes=None):
        return RepositoryProfile(
            "demo", RepositoryIdentity("repo", "a" * 40, "lean:v4.32.0"), stage,
            tuple(contributors or (
                ContributorSpec("source", "source_inventory", True, {"roots": ["Demo"]}),
                ContributorSpec("published", "published", False, {}),
            )),
            tuple(assets), tuple(primary_outcomes if primary_outcomes is not None else
                                 (DeclarationLocatorSpec("Demo.Main", "Demo"),)),
            (ScopeHint("repo:demo", "module", "Demo", None, ("Demo/",)),),
            (UnitHint("repo:main", DeclarationLocatorSpec("Demo.Main", "Demo"), ()),),
            (OrderHint("main-route", (DeclarationLocatorSpec("Demo.Def", "Demo"),
                                      DeclarationLocatorSpec("Demo.Main", "Demo")),
                       "tie_breaker", "author route"),),
            tuple(slices),
        )

    def test_strict_roundtrip_has_no_schema_version(self):
        profile = self.profile()
        restored = RepositoryProfile.from_json(profile.to_json())
        self.assertEqual(restored, profile)
        self.assertNotIn("schema_version", profile.to_dict())
        self.assertEqual(len(profile.digest()), 64)

        value = profile.to_dict()
        value["schema_version"] = 1
        with self.assertRaisesRegex(ValidationError, "profile fields"):
            RepositoryProfile.from_dict(value)
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            RepositoryProfile.from_json('{"profile_id":"one","profile_id":"two"}')

    def test_stage_gating_is_explicit(self):
        inventory = self.profile(stage="inventory")
        inventory.plan().require("inventory")
        with self.assertRaisesRegex(ValidationError, "does not permit"):
            inventory.plan().require("production_structure")

        bad_verified = self.profile(stage="verified_slice")
        with self.assertRaisesRegex(ValidationError, "compiled contributor"):
            bad_verified.validate()
        compiled = ContributorSpec("compiled", "compiled", True, {})
        target = TargetSlice(
            "main", ("Demo",), (DeclarationLocatorSpec("Demo.Main"),),
            (DeclarationLocatorSpec("Demo.Main"),), ("compiled",), (),
            ("repo:demo",), ("repo:main",), ("main-route",),
        )
        verified = self.profile(stage="verified_slice", slices=(target,),
                                contributors=(compiled,))
        verified.plan().require("production_structure")

    def test_target_slices_are_selected_without_cross_contamination(self):
        ns = TargetSlice("ns", ("NavierStokes",), (DeclarationLocatorSpec("NS.Main"),),
                         (), ("source",), ("ns-paper",), (), (), ())
        euler = TargetSlice("euler", ("Euler",), (DeclarationLocatorSpec("Euler.Main"),),
                            (), ("published",), ("euler-paper",), (), (), ())
        assets = (
            self.asset_spec("ns-paper", "ns.txt", "ns", "plain"),
            self.asset_spec("euler-paper", "euler.txt", "euler", "plain"),
        )
        profile = self.profile(assets=assets, slices=(ns, euler), primary_outcomes=())
        with self.assertRaisesRegex(ValidationError, "requires a selection"):
            profile.plan()
        ns_plan = profile.plan("ns")
        euler_plan = profile.plan("euler")
        self.assertEqual(ns_plan.source_roots, ("NavierStokes",))
        self.assertEqual([item.asset_id for item in ns_plan.material_assets], ["ns-paper"])
        self.assertEqual([item.contributor_id for item in ns_plan.contributors], ["source"])
        self.assertFalse(ns_plan.scope_hints)
        self.assertFalse(ns_plan.unit_hints)
        self.assertFalse(ns_plan.order_hints)
        self.assertEqual(euler_plan.source_roots, ("Euler",))
        self.assertFalse(set(ns_plan.source_roots) & set(euler_plan.source_roots))

        overlap = replace(euler, source_roots=("NavierStokes/Subdir",))
        with self.assertRaisesRegex(ValidationError, "overlap"):
            self.profile(assets=assets, slices=(ns, overlap), primary_outcomes=()).validate()


class ArtifactParserTests(unittest.TestCase):
    def plan(self, parser: str, text: str, *, stage="provisional"):
        path = {"formalization_yaml": "formalization.yaml",
                "proof_path_markdown": "PROOF-PATH.md",
                "published_html": "html/shard.html"}[parser]
        spec = MaterialAssetSpec(
            "artifact", path, digest(text), "text/plain", parser, "exact_name", "primary", {},
        )
        profile = RepositoryProfile(
            "artifact", RepositoryIdentity("repo", "b" * 40), stage,
            (ContributorSpec("published", "published", True, {}),), (spec,), (), (), (), (), (),
        )
        return profile.plan(), SourceAsset("artifact", "repo", path, digest(text))

    def test_formalization_yaml_extracts_primary_and_reports_unbound(self):
        text = """status:
  main_results:
    - name: Main result
      declaration: Demo.Main
      file: Demo.lean
    - name: Missing result
      declaration: Demo.Missing
alignment:
  statements:
    - statement: Paper lemma
      lean: Demo.Lemma; Demo.Other (+_cumulative)
"""
        plan, asset = self.plan("formalization_yaml", text)
        result = parse_formalization_yaml(
            plan=plan, asset=asset, text=text,
            declarations={"Demo.Main": DeclRef("repo", "main"),
                          "Demo.Lemma": DeclRef("repo", "lemma")},
        )
        self.assertEqual(len(result.materials.records), 3)
        self.assertEqual(len(result.primary_outcomes), 2)
        self.assertIsInstance(result.primary_outcomes[0], CanonicalDeclLocator)
        self.assertIsInstance(result.primary_outcomes[1], UnresolvedDeclLocator)
        self.assertEqual(sorted(binding.status for binding in result.materials.bindings),
                         ["exact", "exact", "unresolved", "unresolved"])
        self.assertIn("unbound_declaration", [item.code for item in result.diagnostics])
        self.assertEqual(result.materials, result.materials.from_json(result.materials.to_json()))

    def test_formalization_loader_is_injected_safely_and_validated(self):
        text = json.dumps({"status": {"main_results": []}})
        plan, asset = self.plan("formalization_yaml", text)
        called = []

        def loader(value):
            called.append(value)
            return json.loads(value)

        result = parse_formalization_yaml(plan=plan, asset=asset, text=text,
                                          declarations={}, yaml_loader=loader)
        self.assertEqual(called, [text])
        self.assertFalse(result.materials.records)
        with self.assertRaisesRegex(ValidationError, "root must be a mapping"):
            parse_formalization_yaml(plan=plan, asset=asset, text=text,
                                     declarations={}, yaml_loader=lambda _: [])

        try:
            import yaml  # noqa: F401
        except ImportError:
            return
        unsafe = "!!python/object/apply:builtins.str [unsafe]\n"
        unsafe_plan, unsafe_asset = self.plan("formalization_yaml", unsafe)
        with self.assertRaisesRegex(ValidationError, "invalid formalization.yaml"):
            parse_formalization_yaml(plan=unsafe_plan, asset=unsafe_asset,
                                     text=unsafe, declarations={})

    def test_formalization_asset_can_isolate_one_target_slice(self):
        text = """status:
  main_results:
    - declaration: Demo.Main
    - declaration: Other.Main
"""
        spec = MaterialAssetSpec(
            "artifact", "formalization.yaml", digest(text), "text/yaml",
            "formalization_yaml", "exact_name", "primary",
            {"allowed_declarations": ["Demo.Main"]},
        )
        profile = RepositoryProfile(
            "artifact", RepositoryIdentity("repo", "b" * 40), "provisional",
            (ContributorSpec("published", "published", True, {}),),
            (spec,), (), (), (), (), (),
        )
        asset = SourceAsset("artifact", "repo", "formalization.yaml", digest(text))
        result = parse_formalization_yaml(
            plan=profile.plan(), asset=asset, text=text,
            declarations={"Demo.Main": DeclRef("repo", "main"),
                          "Other.Main": DeclRef("repo", "other")},
            yaml_loader=lambda value: {
                "status": {"main_results": [
                    {"declaration": "Demo.Main"},
                    {"declaration": "Other.Main"},
                ]},
            },
        )
        self.assertEqual(len(result.materials.records), 1)
        self.assertEqual(result.primary_outcomes[0].ref, DeclRef("repo", "main"))

    def test_proof_path_returns_landmarks_and_not_final_order(self):
        text = """# The route
Use `Demo.Setup` before `Demo.Main`.

## Supporting context
The prose `contains spaces` and is not a Lean name.
"""
        plan, asset = self.plan("proof_path_markdown", text)
        result = parse_proof_path_markdown(
            plan=plan, asset=asset, text=text,
            declarations={"Demo.Setup": DeclRef("repo", "setup")},
        )
        self.assertEqual(len(result.materials.records), 2)
        self.assertEqual(len(result.order_landmarks), 2)
        self.assertIsInstance(result.order_landmarks[0], CanonicalDeclLocator)
        self.assertIsInstance(result.order_landmarks[1], UnresolvedDeclLocator)
        self.assertIn("unbound_landmark", [item.code for item in result.diagnostics])
        self.assertFalse(hasattr(result, "narrative_order"))
        self.assertFalse(hasattr(result, "regions"))

    def test_proof_path_recognizes_numbered_bold_sections(self):
        text = """# Route
Introduction.

**1. First step.** Use `Demo.First`.

**2. Second step.** Use `Demo.Second`.
"""
        plan, asset = self.plan("proof_path_markdown", text)
        result = parse_proof_path_markdown(plan=plan, asset=asset, text=text, declarations={})
        records = sorted(result.materials.records, key=lambda item: item.occurrence_id)
        self.assertEqual([record.heading for record in records],
                         ["Route", "1. First step.", "2. Second step."])
        self.assertEqual([item.raw_name for item in result.order_landmarks],
                         ["Demo.First", "Demo.Second"])

    def test_selected_html_shard_keeps_published_edges_separate(self):
        text = """<main>
<article data-lean-declaration="Demo.Main">
  <p class="decl-summary">The main published theorem.</p>
  <a data-lean-dependency="Demo.Helper">helper</a>
  <a data-lean-dependency="Demo.Unknown">unknown</a>
</article>
<article data-lean-declaration="Other.Hidden" data-lean-summary="skip"></article>
</main>"""
        plan, asset = self.plan("published_html", text)
        result = parse_published_html_shard(
            plan=plan, asset=asset, text=text,
            declarations={"Demo.Main": DeclRef("repo", "main"),
                          "Demo.Helper": DeclRef("repo", "helper")},
            allowed_declarations=("Demo.Main",),
        )
        self.assertEqual(len(result.materials.records), 1)
        self.assertEqual(result.materials.records[0].text, "The main published theorem.")
        self.assertEqual(len(result.published_dependencies), 2)
        self.assertTrue(all(edge.evidence_kind == "published"
                            for edge in result.published_dependencies))
        self.assertIn("unbound_published_dependency",
                      [item.code for item in result.diagnostics])
        self.assertIn("unselected_html_declaration",
                      [item.code for item in result.diagnostics])

    def test_published_site_bundle_validates_csr_and_keeps_edges_published(self):
        names = ["Demo.Main", "Demo.Helper"]
        shard_id = 2166136261
        for byte in names[0].encode():
            shard_id = ((shard_id ^ byte) * 16777619) & 0xFFFFFFFF
        shard_id = f"{shard_id % 256:03d}"
        texts = {
            "meta": "window.FLT_META=" + json.dumps({"names": names, "root": 0}) + ";",
            "edges": "window.FLT_EDGES={off:[0,1,1],dst:[1]};",
            "titles": "window.FLT_TITLES=" + json.dumps(["Main", "Helper"]) + ";",
            "shard": (f'FLT_SHARD_CB("{shard_id}",'
                      + json.dumps({"Demo.Main": {
                          "dc": "theorem Demo.Main : True", "proof_lines": 2,
                          "en": {"statement_html": "<p>The main statement.</p>",
                                 "proof_html": "<p>Apply the helper.</p>"},
                      }}) + ");"),
        }
        specs = []
        configs = {
            "meta": {"javascript_global": "FLT_META"},
            "edges": {"javascript_global": "FLT_EDGES"},
            "titles": {"javascript_global": "FLT_TITLES"},
            "shard": {"callback": "FLT_SHARD_CB", "shard_id": shard_id},
        }
        assets = {}
        for asset_id, text in texts.items():
            sha = digest(text)
            specs.append(MaterialAssetSpec(
                asset_id, f"{asset_id}.js", sha, "application/javascript",
                "published_bundle", "published_site_bundle", "supporting",
                configs[asset_id],
            ))
            assets[asset_id] = (SourceAsset(asset_id, "repo", f"{asset_id}.js", sha), text)
        profile = RepositoryProfile(
            "published", RepositoryIdentity("repo", "c" * 40), "provisional",
            (ContributorSpec("published", "published", True, {}),), tuple(specs),
            (), (), (), (), (),
        )
        declarations = {name: DeclRef("repo", name) for name in names}
        result = parse_published_site_bundle(
            plan=profile.plan(), assets=assets, declarations=declarations,
            allowed_declarations=("Demo.Main",),
        )
        self.assertEqual(len(result.materials.records), 1)
        self.assertIn("The main statement", result.materials.records[0].text)
        self.assertEqual(len(result.published_dependencies), 1)
        edge = result.published_dependencies[0]
        self.assertEqual(edge.evidence_kind, "published")
        self.assertEqual(edge.dependent.ref, declarations["Demo.Main"])
        self.assertEqual(edge.provider.ref, declarations["Demo.Helper"])

        bad_assets = dict(assets)
        bad = "window.FLT_EDGES={off:[0,1,1],dst:[2]};"
        bad_asset = SourceAsset("edges", "repo", "edges.js", digest(bad))
        bad_assets["edges"] = (bad_asset, bad)
        bad_profile = RepositoryProfile(
            "published", RepositoryIdentity("repo", "c" * 40), "provisional",
            (ContributorSpec("published", "published", True, {}),),
            tuple(replace(spec, sha256=digest(bad)) if spec.asset_id == "edges" else spec
                  for spec in specs), (), (), (), (), (),
        )
        with self.assertRaisesRegex(ValidationError, "CSR arrays"):
            parse_published_site_bundle(
                plan=bad_profile.plan(), assets=bad_assets, declarations=declarations,
                allowed_declarations=("Demo.Main",),
            )

        duplicate_text = texts["meta"]
        duplicate_asset = SourceAsset(
            "meta-copy", "repo", "meta-copy.js", digest(duplicate_text)
        )
        duplicate_spec = MaterialAssetSpec(
            "meta-copy", "meta-copy.js", duplicate_asset.sha256,
            "application/javascript", "published_bundle", "published_site_bundle",
            "supporting", {"javascript_global": "FLT_META"},
        )
        duplicate_profile = replace(profile, material_assets=(*profile.material_assets,
                                                               duplicate_spec))
        duplicate_assets = dict(assets)
        duplicate_assets["meta-copy"] = (duplicate_asset, duplicate_text)
        with self.assertRaisesRegex(ValidationError, "globals must be unique"):
            parse_published_site_bundle(
                plan=duplicate_profile.plan(), assets=duplicate_assets,
                declarations=declarations, allowed_declarations=("Demo.Main",),
            )

    def test_parser_rejects_asset_not_selected_by_target(self):
        text = "# Route\n`Demo.Main`\n"
        plan, asset = self.plan("proof_path_markdown", text)
        wrong = replace(asset, asset_id="other")
        with self.assertRaisesRegex(ValidationError, "not selected"):
            parse_proof_path_markdown(plan=plan, asset=wrong, text=text, declarations={})


if __name__ == "__main__":
    unittest.main()
