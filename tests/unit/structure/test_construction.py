"""Structure invariants and small independent interval objective oracle."""
from dataclasses import replace
from functools import lru_cache
import hashlib
import itertools
import unittest

from test_graph import workspace, ref, P
from lean_exposition.models import SourceAsset, SourceRange, Provenance
from lean_exposition.structure import BuildConfig, Hierarchy, HierarchyCycleError, build_hierarchy, derive_source_order, partition_regions
from lean_exposition.structure.source import scan_tex


def edges(*pairs):
    return [(ref(a), ref(b), "lean_value") for a, b in pairs]


def units(h):
    return [n for n in h.nodes if n["kind"] == "unit"]


class HelperTests(unittest.TestCase):
    def test_shortcut_reconsidered_and_outcome_preserved(self):
        w = workspace("abc", edges(("a", "b"), ("b", "c"), ("a", "c")), outcomes=(ref("c"),))
        h = build_hierarchy(w, "r")
        self.assertEqual(len(units(h)), 1)
        self.assertEqual(units(h)[0]["representative"]["local_id"], "c")
        self.assertEqual(len(h.edges), 3)
        self.assertEqual(len(w.declarations), 3)

    def test_shared_unused_protected_external(self):
        w = workspace("abcde", edges(("a", "b"), ("a", "c"), ("b", "d")), outcomes=(ref("b"),))
        h = build_hierarchy(w, "r")
        self.assertEqual(len(units(h)), 5)
        w = workspace(["a", ref("x", "s")], [(ref("a"), ref("x", "s"), "lean_value")])
        h = build_hierarchy(w, "r")
        self.assertEqual(len(h.external_refs), 1)
        self.assertTrue(h.external_refs[0]["loaded"])

    def test_cumulative_capacity_and_keep_separate(self):
        w = workspace("abcd", edges(("a", "b"), ("b", "c"), ("c", "d")))
        h = build_hierarchy(w, "r", config=BuildConfig(max_declarations=2))
        self.assertEqual(sorted(len(n["decl_refs"]) for n in units(h)), [2, 2])
        h = build_hierarchy(w, "r", config=BuildConfig(max_codepoints=1))
        self.assertEqual(len(units(h)), 4)
        h = build_hierarchy(w, "r", keep_separate=[ref("b"), ref("d")])
        self.assertEqual(len(units(h)), 2)

    def test_lc_cross_registered_files_disabled(self):
        w = workspace("ab", edges(("a", "b")))
        w = replace(w, declarations=tuple(replace(d, module=d.lean_name, provenance=(Provenance("lc_current", "fixture"),)) for d in w.declarations))
        self.assertEqual(len(units(build_hierarchy(w, "r"))), 2)
        self.assertEqual(len(units(build_hierarchy(w, "r", config=BuildConfig(lc_cross_file_helper=True)))), 1)

    def test_generated_ownership_before_optional_helpers(self):
        w = workspace("abc", edges(("a", "b")))
        a, b, c = w.declarations
        w = replace(w, declarations=(a, replace(b, generated_from=ref("a")), replace(c, extraction_status=replace(c.extraction_status, state="compiler_only"))))
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False, max_declarations=1))
        self.assertEqual(sorted(len(n["decl_refs"]) for n in units(h)), [1, 2])
        self.assertEqual(sum(n["metadata"]["technical"] for n in units(h)), 1)

    def test_unknown_technical_not_absorbed(self):
        w = workspace("ab", edges(("a", "b")))
        w = replace(w, declarations=(replace(w.declarations[0], extraction_status=replace(w.declarations[0].extraction_status, state="compiler_only")), w.declarations[1]))
        self.assertEqual(len(units(build_hierarchy(w, "r"))), 2)


class HierarchyTests(unittest.TestCase):
    def test_scope_quotient_cycle_has_witness(self):
        w = workspace("abc", edges(("a", "b"), ("b", "c")), scopes={"a": "a", "b": "b", "c": "a"})
        with self.assertRaises(HierarchyCycleError) as caught:
            build_hierarchy(w, "r")
        self.assertEqual(caught.exception.witnesses[0][0], caught.exception.witnesses[0][-1])

    def test_dependency_order_and_roundtrip_are_enumeration_stable(self):
        w = workspace("abc", edges(("a", "b")))
        h = build_hierarchy(w, "r", config=BuildConfig(native_helper=False))
        root = next(n for n in h.nodes if n["id"] == h.root_id)
        by_id = {n["id"]: n for n in h.nodes}
        order = [by_id[i]["representative"]["local_id"] for i in root["children"] if by_id[i]["kind"] == "unit"]
        self.assertLess(order.index("a"), order.index("b"))
        self.assertEqual(Hierarchy.from_dict(h.to_dict()), h)
        self.assertEqual(h.workspace_digest, w.digest())
        self.assertEqual(len(h.source_digest), 64)
        self.assertEqual(len(h.config_digest), 64)
        # Enumeration does not affect structure ordering or coverage.
        reverse = build_hierarchy(replace(w, declarations=tuple(reversed(w.declarations))), "r",
                                  config=BuildConfig(native_helper=False))
        reverse_root = next(n for n in reverse.nodes if n["id"] == reverse.root_id)
        self.assertEqual(root["children"], reverse_root["children"])

    def test_hierarchy_identity_rejects_stale_digest_or_content(self):
        h = build_hierarchy(workspace("ab"), "r")
        data = h.to_dict()
        data["workspace_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            Hierarchy.from_dict(data)

    def test_unresolved_local_is_not_leaf(self):
        w = workspace("a", [(ref("missing"), ref("a"), "lean_value")])
        h = build_hierarchy(w, "r")
        self.assertEqual(len(units(h)), 1)
        self.assertFalse(h.external_refs[0]["loaded"])
        self.assertTrue(any(d["code"] == "unresolved_local_reference" for d in h.diagnostics))


class RegionTests(unittest.TestCase):
    def test_contiguous_exact_cover_and_fanout(self):
        for n in range(1, 42):
            tree, _, _ = partition_regions(tuple(map(str, range(n))), k=3)
            def leaves(t):
                self.assertLessEqual(len(t), 3)
                return [v for item in t for v in ([item] if isinstance(item, str) else leaves(item))]
            self.assertEqual(leaves(tree), list(map(str, range(n))))

    def test_dp_small_oracle(self):
        n, k = 9, 3
        pairs = [(0, 1), (1, 7), (4, 5), (6, 8)]
        for values in itertools.product(range(2), repeat=len(pairs)):
            weights = dict(zip(pairs, values))
            @lru_cache(None)
            def oracle(i, j):
                m = j - i
                if m <= k:
                    return m * sum(w for (a, b), w in weights.items() if i <= a < b < j)
                return min(oracle(i, mid) + oracle(mid, j) + m * sum(w for (a, b), w in weights.items() if i <= a < mid <= b < j)
                           for mid in range(i + (m + 2) // 3, i + (2 * m) // 3 + 1))
            _, cost, _ = partition_regions(range(n), weights, k=k)
            self.assertEqual(cost, oracle(0, n))
        self.assertEqual(partition_regions(range(121), {(0, 1): 1})[2], "balanced")


class RegionWeightTests(unittest.TestCase):
    def test_override_changes_optimal_cut_without_changing_facts(self):
        w = workspace("abcdefg", edges(("b", "c"), ("b", "d")), scopes={n: "a" for n in "abcdefg"})
        config = BuildConfig(native_helper=False, region_k=2)
        baseline = build_hierarchy(w, "r", config=config)
        weighted = build_hierarchy(w, "r", config=config, edge_weights={(ref("b"), ref("c")): 10})
        def first_size(h):
            scope = next(n for n in h.nodes if n["kind"] == "scope" and n["source_scope"] == "a")
            return len(next(n for n in h.nodes if n["id"] == scope["children"][0])["decl_refs"])
        self.assertEqual((first_size(baseline), first_size(weighted)), (4, 3))
        self.assertEqual(baseline.edges, weighted.edges)
        self.assertEqual(sorted((n["representative"]["local_id"], n["decl_refs"]) for n in units(baseline)),
                         sorted((n["representative"]["local_id"], n["decl_refs"]) for n in units(weighted)))
        self.assertNotEqual(baseline.hierarchy_id, weighted.hierarchy_id)
        self.assertEqual(weighted.config["region_edge_weights"]["overrides"][0]["weight"], 10)
        self.assertEqual(weighted.config["region_edge_weights"]["default_weight"], 1)

    def test_empty_override_is_identical_default(self):
        w = workspace("abc", edges(("a", "c")))
        self.assertEqual(build_hierarchy(w, "r").to_dict(), build_hierarchy(w, "r", edge_weights={}).to_dict())
        self.assertNotIn("region_edge_weights", build_hierarchy(w, "r").config)

    def test_invalid_weights_and_noninternal_pairs_rejected(self):
        w = workspace("ab", edges(("a", "b")) + [(ref("e", "external"), ref("b"), "lean_value")])
        for value in (-1, float("inf"), float("nan"), True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_hierarchy(w, "r", edge_weights={(ref("a"), ref("b")): value})
        for pair in ((ref("b"), ref("a")), (ref("e", "external"), ref("b"))):
            with self.assertRaises(ValueError):
                build_hierarchy(w, "r", edge_weights={pair: 1})


class SourceTests(unittest.TestCase):
    def test_main_before_nested_after_repeated_missing(self):
        files = {"main.tex": "before\n\\input{part}\nafter\n\\input{part}\n\\input{missing}", "part.tex": "part\n\\input{sub/x}", "sub/x.tex": "x\n\\input{main}"}
        positions, diagnostics = scan_tex(files, ["main.tex"])
        self.assertLess(positions["main.tex", 1][0][1], positions["part.tex", 1][0][1])
        self.assertLess(positions["sub/x.tex", 1][0][1], positions["main.tex", 3][0][1])
        self.assertEqual(len(positions["part.tex", 1]), 2)
        self.assertEqual({d["code"] for d in diagnostics}, {"include_cycle", "missing_include", "ambiguous_include_line"})

    def test_comments_verbatim_and_conditional_report(self):
        files = {"a.tex": "% \\input{missing}\n\\begin{verbatim}\n\\input{missing}\n\\end{verbatim}\n\\iffoo\n\\input{\\dynamic}\n\\fi"}
        _, diagnostics = scan_tex(files, ["a.tex"])
        self.assertNotIn("missing_include", {d["code"] for d in diagnostics})
        self.assertIn("dynamic_include", {d["code"] for d in diagnostics})

    def test_lc_segment_origins_use_document_occurrence(self):
        w = workspace("a")
        text = "\\documentclass{article}\nbefore\n"
        asset = SourceAsset("article", "r", "corpus/main.tex", hashlib.sha256(text.encode()).hexdigest())
        d = w.declarations[0]
        origin = Provenance("lc_origin", "origin", (SourceRange("article", 2, None, 2, None),))
        d = replace(d, statement=replace(d.statement, nl=replace(d.statement.nl, provenance=P + (origin,))))
        w = replace(w, declarations=(d,), manifest=replace(w.manifest, assets=(asset,)))
        source = derive_source_order(w, "r", corpus_files={"main.tex": text}, corpus_prefix="corpus")
        self.assertEqual(source.records[ref("a")]["basis"], "document_occurrence")

    def test_nested_input_resolves_against_main_directory(self):
        files = {"article/main.tex": "\\input{sections/a}",
                 "article/sections/a.tex": "\\input{sections/b}",
                 "article/sections/b.tex": "correct",
                 "article/sections/sections/b.tex": "wrong"}
        positions, diagnostics = scan_tex(files, ["article/main.tex"])
        self.assertIn(("article/sections/b.tex", 1), positions)
        self.assertNotIn(("article/sections/sections/b.tex", 1), positions)
        self.assertNotIn("missing_include", {d["code"] for d in diagnostics})

    def test_inline_input_origin_is_explicit_ambiguous_fallback(self):
        w = workspace("a")
        text = "\\documentclass{article}\nbefore \\input{child} after\n"
        asset = SourceAsset("article", "r", "main.tex", hashlib.sha256(text.encode()).hexdigest())
        d = w.declarations[0]
        origin = Provenance("lc_origin", "origin", (SourceRange("article", 2, 22, 2, 27),))
        d = replace(d, statement=replace(d.statement, nl=replace(d.statement.nl, provenance=P + (origin,))))
        w = replace(w, declarations=(d,), manifest=replace(w.manifest, assets=(asset,)))
        source = derive_source_order(w, "r", corpus_files={"main.tex": text, "child.tex": "child"})
        self.assertEqual(source.records[ref("a")]["basis"], "path_fallback_source_position")
        self.assertTrue(source.records[ref("a")]["ambiguous_occurrence"])
        self.assertTrue(any(d["code"] == "ambiguous_include_line" for d in source.diagnostics))

    def test_union_and_fallback_are_distinct(self):
        w = workspace("ab")
        text = "abcdef\n"
        asset = SourceAsset("asset", "r", "file.lean", hashlib.sha256(text.encode()).hexdigest())
        w = replace(w, manifest=replace(w.manifest, assets=(asset,)), declarations=tuple(replace(d, source_refs=(SourceRange("asset", 1, 1, 1, 5),)) for d in w.declarations))
        source = derive_source_order(w, "r", asset_texts={"asset": text})
        self.assertEqual(source.measure(w.declarations), {"measurement": "source_range_union", "codepoints": 4})
        self.assertEqual(source.summary([ref("a"), ref("a")])["members"], 1)
        self.assertEqual(derive_source_order(w, "r").measure(w.declarations)["measurement"], "conservative_formal_sum")


if __name__ == "__main__":
    unittest.main()
