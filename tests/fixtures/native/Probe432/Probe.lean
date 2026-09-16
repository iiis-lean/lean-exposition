namespace Probe

/-- A supplementary-plane Unicode identifier used to audit source columns. -/
def «😀α» : Nat := 1

private def hiddenValue : Nat := «😀α» + 1

structure PairBox (α : Type) where
  left : α
  right : α

instance [Inhabited α] : Inhabited (PairBox α) :=
  ⟨⟨default, default⟩⟩

theorem unicodeTermProof : «😀α» = 1 := rfl

end Probe
