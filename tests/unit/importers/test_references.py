"""Source reference regressions without invoking Lean or an external process."""
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from lean_exposition.importers.references import add_text_references
from lean_exposition.importers.source import consume_text_ast_json, provisional_source_adapter
from lean_exposition.importers.toolkit import source_response
from lean_exposition.importers.reference_index import (
    FixedSourceRoot, ReferenceIndex, ReferenceSignature, build_source_reference_index,
)
from lean_exposition.models import DeclRef
from lean_exposition.construction import build_repository


def scan(texts, **kwargs):
    files = [consume_text_ast_json(repo_key='r', path=m.replace('.', '/') + '.lean',
             module=m, source=t.encode(), payload=source_response(t, m))
             for m, t in texts.items()]
    adapter = provisional_source_adapter(files, repo_key='r')
    with patch('subprocess.Popen', side_effect=AssertionError('source scan started a process')):
        result = add_text_references(adapter, texts, **kwargs)
    for original, changed in zip(adapter.declarations, result.declarations, strict=True):
        assert changed.locator == original.locator
        assert changed.fields[:len(original.fields)] == original.fields
    return result


def edges(adapter, name, part='statement'):
    names = {d.locator.ref: next(f.value for f in d.fields if f.field == 'lean_name')
             for d in adapter.declarations}
    decl = next(d for d in adapter.declarations if names[d.locator.ref] == name)
    return {names.get(dep.provider, dep.provider.local_id) for f in decl.fields
            if f.field == part + '.deps' for dep in f.value}


class ReferenceScopeTests(unittest.TestCase):
    def test_tactic_names_are_not_global_terms_but_explicit_terms_remain(self):
        result = scan({'M': '''def by_cases (a b : Nat) := a + b
def symm (a : Nat) := a
def h := 42
theorem commands (p : Prop) : True := by
  by_cases h : p
  · have v := h
    symm
    trivial
  · trivial
theorem inline : True := by trivial; symm
theorem combinator : True := by trivial <;> symm
theorem terms : True := by
  have a := by_cases 1 2
  have b := symm 1
  exact _root_.by_cases a b
theorem multiline : True := by
  exact
    symm 1
theorem simpTerms : True := by
  simp only [
    symm, by_cases]
def direct := (symm 1, by_cases 1 2)
def letTerm := let a := 1; by_cases a a
'''})
        for name in ('commands', 'inline', 'combinator'):
            self.assertFalse(edges(result, name, 'proof'), name)
        self.assertEqual(edges(result, 'terms', 'proof'), {'by_cases', 'symm'})
        self.assertEqual(edges(result, 'direct'), {'by_cases', 'symm'})
        self.assertEqual(edges(result, 'multiline', 'proof'), {'symm'})
        self.assertEqual(edges(result, 'simpTerms', 'proof'), {'symm', 'by_cases'})
        self.assertEqual(edges(result, 'letTerm'), {'by_cases'})

    def test_projection_keyword_in_theorem_slice_does_not_move_header(self):
        from lean_exposition.importers.text_scope import scan_scope
        text = '(P Q : Partition) (h : (P ⊔ Q).class = P.class) : P.Measurable'
        params = scan_scope(text, declaration=True).parameters
        self.assertEqual([b.name for b in params], ['P', 'Q', 'h'])
        result = scan({'M': '''def Partition := Nat
def Partition.class (p : Partition) := p
def Partition.Measurable (p : Partition) := True
theorem use (P Q : Partition) (h : (P ⊔ Q).class = P.class) : P.Measurable := by
  exact P.Measurable
'''})
        self.assertIn('Partition.Measurable', edges(result, 'use'))
        self.assertIn('Partition.Measurable', edges(result, 'use', 'proof'))

    def test_parenthesized_receiver_projection_never_becomes_a_global_name(self):
        result = scan({'M': '''def symm (a : Nat) := a
def fact := 1
def projected := (fact).symm
def explicit := symm fact
'''})
        self.assertEqual(edges(result, 'projected'), {'fact'})
        self.assertEqual(edges(result, 'explicit'), {'symm', 'fact'})
        self.assertTrue(any('unsupported_projection' in d and 'symm' in d for d in result.diagnostics))

    def test_relative_open_resolves_at_open_site_and_scope_exit(self):
        result = scan({'M': '''namespace Outer
namespace Inner
def item := 1
end Inner
section
open Inner
namespace Nested
def use := item
end Nested
end
def outside := item
end Outer
'''})
        self.assertEqual(edges(result, 'Outer.Nested.use'), {'Outer.Inner.item'})
        self.assertFalse(edges(result, 'Outer.outside'))

    def test_nested_function_argument_preserves_outer_type_head(self):
        result = scan({'M': '''def Box (a : Type) := Nat
def Box.size (b : Box a) := b
def nested (b : Box (Nat → Bool)) := b.size
def function (f : Box Nat → Bool) := f.size
def relation (h : Box Nat = Box Bool) := h.size
'''})
        self.assertIn('Box.size', edges(result, 'nested'))
        self.assertNotIn('Box.size', edges(result, 'function'))
        self.assertNotIn('Box.size', edges(result, 'relation'))

    def test_expression_ascription_is_not_a_binder(self):
        result = scan({'M': '''def hadamardCount (n s : Nat) := n + s
def signOf (b : Nat) := b
def normalizedCount (n s : Nat) : Nat := (hadamardCount n s : Nat)
def squared (b : Nat) := ((signOf b : Nat) ^ (2 : Nat))
'''})
        self.assertEqual(edges(result, 'normalizedCount'), {'hadamardCount'})
        self.assertEqual(edges(result, 'squared'), {'signOf'})

    def test_binding_order_and_nested_scope_restore(self):
        result = scan({'M': '''def n : Nat := 7
def previous : Nat := by
  have h : Nat := n
  let n : Nat := 1
  exact h + n
def after : Nat := (fun (n : Nat) => n) n
def sibling : Nat := (let n := 1; n) + n
def rhs : Nat := let n := n; n
'''})
        for name in ('previous', 'after', 'sibling', 'rhs'):
            self.assertEqual(edges(result, name), {'n'}, name)

    def test_binders_suppress_globals_only_in_their_body(self):
        result = scan({'M': '''def n : Nat := 7
def h : Nat := 2
def bound (n : Nat) := n
def quantified : Prop := ∀ n : Nat, n = n
def lambda := fun n => n
def localProof : Nat := by
  have h : Nat := by
    let n := 1
    exact n
  exact n + h
'''})
        for name in ('bound', 'quantified', 'lambda'):
            self.assertFalse(edges(result, name), name)
        self.assertEqual(edges(result, 'localProof'), {'n'})

    def test_multiline_section_and_anonymous_instance(self):
        result = scan({'M': '''def n := 7
def h := 2
class Fixture where
  value : Nat
section
variable
  (n : Nat)
  [Fixture]
def within := n
end
def outside := n
def anonymous [Fixture] (h : Nat) := h
'''})
        self.assertFalse(edges(result, 'within'))
        self.assertEqual(edges(result, 'outside'), {'n'})
        self.assertIn('Fixture', edges(result, 'anonymous'))

    def test_comments_strings_unicode_and_quoted_names(self):
        result = scan({'M': '''def «odd name» : Nat := 1
def «a.b» : Nat := 3
def quotedBind («a.b» : Nat) := «a.b»
def α₁ : Nat := 2
def use := «odd name» + α₁
def masked := "α₁" /- nested /- «odd name» -/ α₁ -/
def bind («odd name» : Nat) := «odd name»
'''})
        self.assertEqual(edges(result, 'use'), {'«odd name»', 'α₁'})
        self.assertFalse(edges(result, 'masked'))
        self.assertFalse(edges(result, 'bind'))
        self.assertFalse(edges(result, 'quotedBind'))

    def test_typed_dot_receiver_and_section_type(self):
        result = scan({'M': '''namespace N
def BoolFun := Nat
def BoolFun.degree (f : BoolFun) := f
def BoolFun.sensitivity (f : BoolFun) := f
section
variable
  (f : BoolFun)
def use := f.degree + f.sensitivity
end
def explicit (f : BoolFun) := f.degree
theorem theoremUse (f : BoolFun) : f.degree = f.degree := by rfl
def unknown := fun f => f.degree
end N
'''})
        self.assertEqual(edges(result, 'N.use'), {'N.BoolFun', 'N.BoolFun.degree', 'N.BoolFun.sensitivity'})
        self.assertEqual(edges(result, 'N.explicit'), {'N.BoolFun', 'N.BoolFun.degree'})
        self.assertEqual(edges(result, 'N.theoremUse'), {'N.BoolFun', 'N.BoolFun.degree'})
        self.assertFalse(edges(result, 'N.unknown'))
        self.assertTrue(any('f.degree:receiver_type_unknown' in d for d in result.diagnostics))

    def test_tactic_tuple_bindings_and_unsupported_pattern_diagnostic(self):
        result = scan({'M': '''def h := 1
def k := 1
theorem t : True := by
  obtain ⟨h, k⟩ := pair
  exact use h k
theorem complex : True := by
  rcases pair with h | k
  trivial
'''})
        self.assertFalse(edges(result, 't', 'proof'))
        self.assertTrue(any('unsupported_pattern' in d and 'rcases' in d for d in result.diagnostics))

    def test_unimported_same_name_cannot_bind_dot_receiver(self):
        result = scan({'A': 'def T := Nat\ndef T.value (x : T) := x\n',
                       'B': 'def T := Nat\ndef T.value (x : T) := x\n',
                       'M': 'import A\ndef use (x : T) := x.value\n'})
        self.assertEqual(edges(result, 'use'), {'T', 'T.value'})
        self.assertFalse(any('text_reference_ambiguous:M' in d for d in result.diagnostics))

    def test_alias_and_namespace_resolution(self):
        result = scan({'M': '''namespace N
def item := 1
alias other := item
def use := other
end N
open N
def externalUse := N.item
'''})
        self.assertEqual(edges(result, 'N.use'), {'N.item'})
        self.assertEqual(edges(result, 'externalUse'), {'N.item'})

    def test_proof_branches_and_tuple_lambda_end_scope(self):
        result = scan({'M': 'def n := 1\ntheorem t : True := by\n  constructor\n  · let n := 2\n    exact n\n  · exact n\ndef pair := (fun n => n, n)\n'})
        self.assertEqual(edges(result, 't', 'proof'), {'n'})
        self.assertEqual(edges(result, 'pair'), {'n'})
        summed = scan({'M': 'def n := 1\ndef f := fun n => ∑ k, n + k\n'})
        self.assertFalse(edges(summed, 'f'))

    def test_local_type_resolves_at_binding_time(self):
        result = scan({'M': 'def T := Nat\ndef T.value (x : T) := x\ndef use (x : T) := let x : T := x; x.value\n'})
        self.assertEqual(edges(result, 'use'), {'T', 'T.value'})

    def test_unsupported_alternative_pattern_never_guesses_global_binder(self):
        result = scan({'M': 'def h := 1\ndef k := 2\ntheorem t : True := by\n  rcases pair with h | k\n  exact use h k\n'})
        self.assertFalse(edges(result, 't', 'proof'))
        self.assertTrue(any('unsupported_pattern' in d for d in result.diagnostics))

    def test_match_pattern_uncertainty_does_not_create_global_edges(self):
        result = scan({'M': 'def h := 1\ndef f (x : Nat) := match x with | h => h\n'})
        self.assertFalse(edges(result, 'f'))
        self.assertTrue(any('unsupported_pattern' in d for d in result.diagnostics))

    def test_params_valid_method_uses_explicit_receiver_type(self):
        result = scan({'M': 'structure Params where\n  size : Nat\ndef Params.Valid (p : Params) : Prop := p.size > 0\ntheorem valid (p : Params) : p.Valid := by sorry\n'})
        self.assertEqual(edges(result, 'valid'), {'Params', 'Params.Valid'})

    def test_multiple_typed_method_candidates_remain_ambiguous(self):
        result = scan({'A': 'def T := Nat\ndef T.value (x : T) := x\n',
                       'B': 'import A\ndef T.value (x : T) := x\n',
                       'M': 'import A B\ndef use (x : T) := x.value\n'})
        self.assertEqual(edges(result, 'use'), {'T'})
        self.assertTrue(any('text_reference_ambiguous:M' in d and 'x.value' in d for d in result.diagnostics))


class ReferenceIndexTests(unittest.TestCase):
    def test_compound_end_corrects_external_signature_without_guessing_private(self):
        target = 'import Library\ndef use := Finset.mem_filter + Finset.visible\n'
        self.write(self.project, 'Main', target)
        self.write(self.dependency, 'Library', '''namespace Finset
section Filter
variable (p : Nat)
def filter := p
end Finset.Filter
namespace Meta.Tools
def unrelated := 1
end Meta.Tools
namespace Finset
def mem_filter := 1
private def hidden := 2
def visible := 3
end Finset
''')
        roots = (FixedSourceRoot('r', self.project, 'a' * 40),
                 FixedSourceRoot('math', self.dependency, 'b' * 40))
        index = build_source_reference_index(roots, (('r', 'Main'),))
        names = {s.name:s for s in index.signatures}
        self.assertIn('Finset.mem_filter', names)
        self.assertNotIn('Finset.Finset.mem_filter', names)
        self.assertFalse(names['Finset.hidden'].public)
        self.assertFalse(names['Finset.visible'].parameters)
        result = scan({'Main': target}, reference_index=index)
        self.assertEqual(edges(result, 'use'), {'Finset.mem_filter', 'Finset.visible'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.dependency = self.root / 'dependency'
        self.project.mkdir()
        self.dependency.mkdir()

    def write(self, root, module, text):
        file = root / (module.replace('.', '/') + '.lean')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def test_fixed_source_closure_is_separate_from_export_scope_and_cached(self):
        target = 'import Bridge\ndef use := Shared.lemma\n'
        self.write(self.project, 'Main', target)
        self.write(self.project, 'Bridge', 'import Library\ndef bridge := 1\n')
        self.write(self.dependency, 'Library', 'namespace Shared\ntheorem lemma : True := by trivial\nend Shared\n')
        self.write(self.dependency, 'Unused', 'def Shared.lemma := 2\n')
        roots = (FixedSourceRoot('r', self.project, 'a' * 40),
                 FixedSourceRoot('math', self.dependency, 'b' * 40))
        with patch('subprocess.Popen', side_effect=AssertionError('Lean started')):
            index = build_source_reference_index(roots, (('r', 'Main'),), cache_dir=self.root / 'cache')
            with patch('lean_exposition.importers.source.consume_text_ast_json', side_effect=AssertionError('cache missed')):
                cached = build_source_reference_index(roots, (('r', 'Main'),), cache_dir=self.root / 'cache')
        self.assertEqual(index.signatures, cached.signatures)
        result = scan({'Main': target}, reference_index=index)
        self.assertEqual(len(result.declarations), 1)
        deps = [d for f in result.declarations[0].fields if f.field == 'statement.deps' for d in f.value]
        self.assertEqual([(d.provider.repo_key, d.provider_module) for d in deps], [('math', 'Library')])
        self.assertEqual(deps[0].provider.local_id, 'Shared.lemma')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            scan({'Main': target + 'def changed := 1\n'}, reference_index=index)
        bundle = build_repository(result)
        provider = next(r for r in bundle.workspace.manifest.repositories if r.repo_key == 'math')
        self.assertEqual(provider.revision, 'b' * 40)
        self.assertIsNone(provider.input_digest)
        self.assertTrue(all(c.status == 'partial' for c in result.coverage if c.evidence_domain == 'text_reference'))
        self.write(self.dependency, 'Library', 'namespace Shared\ndef changed := 1\nend Shared\n')
        changed = build_source_reference_index(roots, (('r', 'Main'),), cache_dir=self.root / 'cache')
        self.assertNotEqual(index.signatures, changed.signatures)

    def test_parser_identity_invalidates_signature_cache(self):
        self.write(self.project, 'Main', 'def n := 1\n')
        roots = (FixedSourceRoot('r', self.project, 'a' * 40),)
        cache = self.root / 'cache'
        build_source_reference_index(roots, (('r', 'Main'),), cache_dir=cache)
        old = set((cache / 'references').glob('*.json'))
        with patch('lean_exposition.importers.reference_index.reference_parser_fingerprint', return_value='changed'):
            build_source_reference_index(roots, (('r', 'Main'),), cache_dir=cache)
        self.assertEqual(len(set((cache / 'references').glob('*.json')) - old), 1)

    def test_external_private_names_are_never_promoted_or_imported(self):
        target = 'import Library\ndef use := hidden + visible\n'
        self.write(self.project, 'Main', target)
        self.write(self.dependency, 'Library', 'private def hidden := 1\ndef visible := 2\n')
        roots = (FixedSourceRoot('r', self.project, 'a' * 40),
                 FixedSourceRoot('math', self.dependency, 'b' * 40))
        index = build_source_reference_index(roots, (('r', 'Main'),))
        result = scan({'Main': target}, reference_index=index)
        self.assertEqual(edges(result, 'use'), {'visible'})
        self.assertTrue(any('hidden' in d and 'unresolved' in d for d in result.diagnostics))
        hidden = next(s for s in index.signatures if s.name == 'hidden')
        self.assertFalse(hidden.public)
        self.assertTrue(hidden.ref.local_id.startswith('source:'))

    def test_private_module_sections_are_not_exported(self):
        from lean_exposition.importers.reference_index import adapter_reference_index
        text = 'module\nprivate section\ndef hidden := 1\nend\n@[expose] public section\ndef visible := 2\n'
        file = consume_text_ast_json(repo_key='r', path='M.lean', module='M', source=text.encode(), payload=source_response(text, 'M'))
        index, _ = adapter_reference_index(provisional_source_adapter((file,), repo_key='r'), {'M': text})
        self.assertEqual({s.name: s.public for s in index.signatures}, {'hidden': False, 'visible': True})

    def test_multiple_providers_remain_ambiguous_and_keep_repo_identity(self):
        target = 'import A B\ndef use := Shared.item\n'
        index = ReferenceIndex((
            ReferenceSignature(DeclRef('first', 'item'), 'Shared.item', 'A', 'Shared', (), 'a' * 64),
            ReferenceSignature(DeclRef('second', 'item'), 'Shared.item', 'B', 'Shared', (), 'b' * 64),
        ), {('r', 'M'): (('first', 'A'), ('second', 'B'))})
        result = scan({'M': target}, reference_index=index)
        self.assertFalse(edges(result, 'use'))
        self.assertTrue(any('text_reference_ambiguous:M' in d for d in result.diagnostics))

    def test_signature_mismatch_is_not_resolved_by_unique_suffix(self):
        result = scan({'M': '''def A := Nat
def B := Nat
def A.value (x : B) := x
def use (a : A) := a.value
'''})
        self.assertEqual(edges(result, 'use'), {'A'})
        self.assertTrue(any('method_signature_unknown' in d for d in result.diagnostics))


if __name__ == '__main__':
    unittest.main()
