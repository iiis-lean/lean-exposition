namespace Probe

variable {α : Type} [Add α]

/-- documented public definition -/
def addSelf (x : α) : α := x + x

private def hidden (x : Nat) : Nat := x + 1

structure PairBox where
  left : Nat
  right : Nat

instance : Inhabited PairBox := ⟨⟨0, 0⟩⟩

/-- theorem docs -/
theorem addSelf_eq (x : α) : addSelf x = x + x := by
  rfl

end Probe
