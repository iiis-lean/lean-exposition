"""Direct tests for Toolkit text_ast inventory consumption."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from lean_exposition.construction import (
    CanonicalDeclLocator, CoverageContribution, DeclarationContribution,
    DeclUnitSeed, FieldContribution, RepositoryAdapterResult, ScopeSeed,
    build_repository,
)
from lean_exposition.importers.common import asset_from_bytes, qualified_id
from lean_exposition.importers.source import (
    build_provisional_source_bundle, consume_text_ast_json, iter_text_ast_jsonl,
    merge_source_inventory,
)
from lean_exposition.models import (
    DeclRef, Dependency, Provenance, Repository, Status, TextContent,
    ValidationError,
)
from lean_exposition.structure import build_hierarchy, derive_narrative_order


def _position(line: int, column: int) -> dict[str, int]:
    return {"line": line, "column": column}


def _diagnostics(*, total: int = 1, classified: int = 1,
                 issues: list[dict] | None = None) -> dict:
    return {
        "backend": "text_ast",
        "total_top_level_commands": total,
        "classified_top_level_commands": classified,
        "classification_ratio": 1.0 if total == 0 else classified / total,
        "unrecognized_commands": issues or [],
    }


def _response(declarations: list[dict], *, total_commands: int = 1,
              classified_commands: int = 1,
              issues: list[dict] | None = None) -> dict:
    return {
        "success": True,
        "error_message": None,
        "total_declarations": len(declarations),
        "declarations": declarations,
        "source_diagnostics": _diagnostics(
            total=total_commands, classified=classified_commands, issues=issues,
        ),
    }


def _declaration(*, name: str, kind: str, full: str, start_line: int,
                 end_line: int, end_column: int, signature: str | None = None,
                 value: str | None = None, docstring: str | None = None,
                 doc_line: int | None = None) -> dict:
    return {
        "name": name,
        "kind": kind,
        "signature": signature,
        "value": value,
        "full_declaration": full,
        "docstring": docstring,
        "decl_start_pos": _position(start_line, 0),
        "decl_end_pos": _position(end_line, end_column),
        "doc_start_pos": _position(doc_line, 0) if doc_line is not None else None,
        "doc_end_pos": (_position(doc_line, len(docstring))
                        if doc_line is not None and docstring is not None else None),
    }


class SourceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            "/-- 文档 -/\n"
            "@[simp]\n"
            "private theorem «name with spaces» : True := by\n"
            "  trivial\n"
        )
        full = self.source.rstrip("\n")
        self.declaration = _declaration(
            name="Δ.«name with spaces»", kind="theorem", full=full,
            start_line=1, end_line=4, end_column=len("  trivial"),
            signature=": True", value=":= by\n  trivial",
            docstring="/-- 文档 -/", doc_line=1,
        )

    def inventory(self, *, response: dict | None = None):
        return consume_text_ast_json(
            repo_key="demo", path="Demo.lean", module="Demo",
            source=self.source.encode(), payload=response or _response([self.declaration]),
        )

    def test_consumes_current_contract_with_source_slices_and_unknown_dependencies(self):
        inventory = self.inventory()
        self.assertTrue(inventory.complete)
        self.assertEqual(inventory.asset.sha256, hashlib.sha256(self.source.encode()).hexdigest())
        contribution = inventory.declarations[0]
        self.assertEqual(contribution.locator.raw_name, "Δ.«name with spaces»")
        fields = {field.field: field for field in contribution.fields}
        self.assertEqual(fields["kind"].value, "theorem")
        self.assertEqual(fields["statement.nl"].value.text, "/-- 文档 -/")
        self.assertEqual(fields["statement.formal"].value.text, ": True")
        self.assertEqual(fields["proof.formal"].value.text, ":= by\n  trivial")
        self.assertNotIn("statement.deps", fields)
        self.assertNotIn("proof.deps", fields)
        self.assertEqual(contribution.locator.source_range.start_column, 1)
        self.assertEqual(contribution.locator.source_range.end_column, len("  trivial") + 1)

    def test_preserves_mutual_member_and_unknown_command_diagnostics(self):
        source = "mutual\n  def even : Nat := 0\n  def odd : Nat := 1\nend\ncustom_command x\n"
        even = _declaration(
            name="even", kind="definition", full="  def even : Nat := 0",
            start_line=2, end_line=2, end_column=len("  def even : Nat := 0"),
            signature=": Nat", value=":= 0",
        )
        odd = _declaration(
            name="odd", kind="definition", full="  def odd : Nat := 1",
            start_line=3, end_line=3, end_column=len("  def odd : Nat := 1"),
            signature=": Nat", value=":= 1",
        )
        issue = {"line": 5, "column": 0, "head": "custom_command", "source": "custom_command x"}
        inventory = consume_text_ast_json(
            repo_key="demo", path="Mutual.lean", module="Mutual", source=source.encode(),
            payload=_response([even, odd], total_commands=5, classified_commands=4, issues=[issue]),
        )
        self.assertEqual([item.locator.raw_name for item in inventory.declarations], ["even", "odd"])
        self.assertAlmostEqual(inventory.coverage.classification_ratio, 0.8)
        self.assertEqual(inventory.coverage.unrecognized_commands[0].head, "custom_command")

    def test_strict_contract_rejects_stale_shape_source_and_diagnostics(self):
        payload = _response([self.declaration])
        payload["unexpected"] = True
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            self.inventory(response=payload)
        payload = _response([self.declaration])
        payload["source_diagnostics"]["classification_ratio"] = 0.5
        with self.assertRaisesRegex(ValidationError, "classification_ratio"):
            self.inventory(response=payload)
        with self.assertRaisesRegex(ValidationError, "source digest mismatch"):
            consume_text_ast_json(
                repo_key="demo", path="Demo.lean", module="Demo", source=self.source.encode(),
                payload=_response([self.declaration]), expected_sha256="0" * 64,
            )

    def test_jsonl_streams_files_and_resumes_at_record_boundary(self):
        response = _response([], total_commands=0, classified_commands=0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = []
            for name in ("A.lean", "B.lean"):
                data = f"-- {name}\n".encode()
                (root / name).write_bytes(data)
                lines.append(json.dumps({
                    "path": name,
                    "module": name.removesuffix(".lean"),
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "result": response,
                }))
            resumed = list(iter_text_ast_jsonl(
                io.StringIO(lines[0] + "\n\n" + lines[1] + "\n"), repo_key="demo",
                source_root=root, start_record=1,
            ))
            self.assertEqual([item.path for item in resumed], ["B.lean"])
            with self.assertRaisesRegex(ValidationError, "invalid text_ast JSONL line"):
                list(iter_text_ast_jsonl(
                    io.StringIO(lines[0] + "\n{"), repo_key="demo", source_root=root,
                ))

    def test_chunked_single_file_promotes_once_and_requires_complete_chunks(self):
        inventory = self.inventory()
        first = inventory.__class__(
            **{**inventory.__dict__, "declarations": (), "chunk_index": 0, "chunk_count": 2}
        )
        second = inventory.__class__(
            **{**inventory.__dict__, "chunk_index": 1, "chunk_count": 2}
        )
        options = {"repo_key": "demo", "toolchain": "leanprover/lean4:v4.32.0"}
        bundle = build_provisional_source_bundle((first, second), **options)
        self.assertEqual(len(bundle.workspace.manifest.assets), 1)
        self.assertEqual(len(bundle.workspace.declarations), 1)
        self.assertEqual(sum(item.startswith("text_ast_coverage:")
                             for item in bundle.diagnostics), 1)
        with self.assertRaisesRegex(ValidationError, "incomplete source inventory chunks"):
            build_provisional_source_bundle((first,), **options)

    def test_source_only_promotion_is_explicit_and_dependency_coverage_is_unknown(self):
        inventory = self.inventory()
        bundle = build_provisional_source_bundle(
            (inventory,), repo_key="demo", toolchain="leanprover/lean4:v4.32.0",
            revision="a" * 40, primary_outcome_names=("Δ.«name with spaces»",),
        )
        self.assertIn("stage:provisional-source-only", bundle.diagnostics)
        self.assertEqual(bundle.structure_policy.unit_aggregation, "preserve")
        self.assertFalse(bundle.structure_policy.production_structure)
        self.assertEqual(len(bundle.workspace.declarations), 1)
        statuses = {
            (entry.part, entry.evidence_domain): entry.status
            for entry in bundle.dependency_coverage.entries
        }
        self.assertEqual(statuses[("statement", "lean_type")], "unknown")
        self.assertEqual(statuses[("proof", "lean_value")], "unknown")
        self.assertEqual(bundle.workspace.manifest.repositories[0].primary_outcomes,
                         (bundle.workspace.declarations[0].ref,))
        order = derive_narrative_order(bundle, "demo")
        self.assertTrue(order.scopes)
        with self.assertRaisesRegex(ValueError, "provisional source-only"):
            build_hierarchy(bundle, "demo")
        with self.assertRaisesRegex(ValueError, "provisional source-only"):
            build_hierarchy(
                bundle.workspace,
                "demo",
                structure_policy=bundle.structure_policy,
                dependency_coverage=bundle.dependency_coverage,
            )

    def test_compiled_merge_uses_unique_module_name_range_and_keeps_compiler_dependencies(self):
        inventory = self.inventory()
        source_range = inventory.declarations[0].locator.source_range
        ref = DeclRef("demo", "_private.Demo.0.name_with_spaces")
        external = DeclRef("demo/lean", "True")
        provenance = (Provenance("compiled", "Demo.olean"),)
        missing = TextContent(None, "missing", provenance, "No compiler-authored NL")
        compiler_fields = (
            self._field("lean_name", "Δ.«name with spaces»", provenance),
            self._field("module", "Demo", provenance),
            self._field("native_scope", qualified_id("demo", "Demo"), provenance,
                        authority="source"),
            self._field("kind", "theorem", provenance),
            self._field("statement.nl", missing, provenance),
            self._field("statement.formal", TextContent("compiler statement", "present", provenance),
                        provenance),
            self._field("statement.deps", (Dependency(external, "lean_type", provenance),),
                        provenance),
            self._field("proof.nl", missing, provenance),
            self._field("proof.formal", TextContent("compiler proof", "present", provenance),
                        provenance),
            self._field("proof.deps", (), provenance),
            self._field("extraction_status", Status("extracted", provenance), provenance),
            self._field("provenance", provenance, provenance),
            self._field("source_refs", (source_range,), provenance, authority="source"),
        )
        compiled = RepositoryAdapterResult(
            repositories=(
                Repository("demo", "leanprover/lean4:v4.32.0", qualified_id("demo", "/"),
                           revision="a" * 40),
                Repository("demo/lean", "leanprover/lean4:v4.32.0", None,
                           input_digest="b" * 64),
            ),
            assets=(asset_from_bytes("demo", "Demo.lean", self.source.encode()),),
            declarations=(DeclarationContribution(CanonicalDeclLocator(ref), compiler_fields),),
            scopes=(
                ScopeSeed(qualified_id("demo", "/"), "demo", "repository", "demo", provenance),
                ScopeSeed(qualified_id("demo", "Demo"), "demo", "module", "Demo", provenance,
                          qualified_id("demo", "/")),
            ),
            units=(DeclUnitSeed(qualified_id("demo", ref.local_id), ref, (), provenance),),
            unit_aggregation="native_helpers",
            coverage=(
                CoverageContribution(ref, "statement", "lean_type", "complete", provenance),
                CoverageContribution(ref, "proof", "lean_value", "complete", provenance),
            ),
        )
        merged = merge_source_inventory(compiled, (inventory,), strict_identity=True)
        bundle = build_repository(merged)
        declaration = bundle.workspace.declarations[0]
        self.assertEqual(declaration.ref, ref)
        self.assertEqual(declaration.statement.nl.text, "/-- 文档 -/")
        self.assertEqual(declaration.statement.formal.text, ": True")
        self.assertEqual(declaration.proof.formal.text, ":= by\n  trivial")
        self.assertEqual(declaration.statement.deps[0].provider, external)
        self.assertEqual(declaration.extraction_status.state, "extracted")
        coverage = {(item.part, item.evidence_domain): item.status
                    for item in bundle.dependency_coverage.entries}
        self.assertEqual(coverage[("statement", "lean_type")], "complete")
        self.assertEqual(coverage[("proof", "lean_value")], "complete")

    def test_ambiguous_compiled_match_remains_unresolved_or_fails_strictly(self):
        inventory = self.inventory()
        source_range = inventory.declarations[0].locator.source_range
        provenance = (Provenance("compiled", "Demo.olean"),)
        observations = []
        for suffix in ("one", "two"):
            observations.append(DeclarationContribution(
                CanonicalDeclLocator(DeclRef("demo", suffix)),
                (
                    self._field("lean_name", "Δ.«name with spaces»", provenance),
                    self._field("module", "Demo", provenance),
                    self._field("source_refs", (source_range,), provenance, authority="source"),
                ),
            ))
        compiled = RepositoryAdapterResult(
            repositories=(Repository("demo", None, None, input_digest="a" * 64),),
            assets=(inventory.asset,), declarations=tuple(observations), scopes=(), units=(),
            unit_aggregation="native_helpers",
        )
        merged = merge_source_inventory(compiled, (inventory,))
        self.assertIsNone(merged.declarations[-1].locator.authoritative_ref)
        with self.assertRaisesRegex(ValidationError, "did not resolve uniquely"):
            merge_source_inventory(compiled, (inventory,), strict_identity=True)

    @staticmethod
    def _field(name: str, value: object, provenance: tuple[Provenance, ...],
               *, authority: str = "lean_environment") -> FieldContribution:
        return FieldContribution(
            name, "present", value, authority, "fixture", "read", provenance, "c" * 64,
        )


if __name__ == "__main__":
    unittest.main()
