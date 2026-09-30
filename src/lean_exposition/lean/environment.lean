import Lean
import Lean.Meta.Eqns
import Lean.ProjFns
-- Selected project imports are prepended by the Python wrapper.
open Lean Meta

-- Reconstruct the import order of one target, independently of its batch peers.
-- Lean permits equivalent generated constants in several modules; the global
-- const2ModIdx map otherwise reports whichever import was visited first.
private partial def exportImportOrder (env : Environment) (indices : Std.HashMap Name Nat)
    (name : Name) (seen : NameHashSet) (order : Array Nat) : NameHashSet × Array Nat := Id.run do
  if seen.contains name then return (seen, order)
  let mut seen := seen.insert name
  let mut order := order
  let some index := indices[name]? | return (seen, order)
  for imported in env.header.moduleData[index]!.imports do
    let (nextSeen, nextOrder) := exportImportOrder env indices imported.module seen order
    seen := nextSeen
    order := nextOrder
  return (seen, order.push index)

run_meta do
  let env ← getEnv
  let selected : Array String := __MODULES__
  let indices : Std.HashMap Name Nat := env.header.moduleNames.zipIdx.foldl
    (fun result (name, index) => result.insert name index) {}
  -- run_meta captures stdout until the command finishes; a dedicated file is
  -- required for checkpoints to become observable before process exit.
  let some outputPath ← IO.getEnv "LEAN_EXPOSITION_OUTPUT"
    | throwError "LEAN_EXPOSITION_OUTPUT is required"
  let out ← IO.FS.Handle.mk outputPath .write
  for module in selected do
    let started ← IO.monoMsNow
    out.putStrLn ("LEAN_EXPOSITION_BEGIN " ++ (toJson module).compress)
    out.flush
    let names := env.header.moduleNames.zipIdx |>.foldl (fun acc (moduleName, index) =>
      if moduleName.toString == module then acc ++ env.header.moduleData[index]!.constNames else acc) #[]
    let dependencyStart ← IO.monoMsNow
    let mut refs : Std.HashMap Name (Array Name × Option (Array Name)) := {}
    let mut needed : NameHashSet := {}
    for name in names do
      let some info := env.find? name | continue
      let typeRefs := info.type.getUsedConstants
      let valueRefs := (info.value? true).map Expr.getUsedConstants
      refs := refs.insert name (typeRefs, valueRefs)
      needed := needed.insert name
      for ref in typeRefs ++ valueRefs.getD #[] do
        needed := needed.insert ref
    let dependencyEnd ← IO.monoMsNow
    let mut seen : NameHashSet := {}
    let mut order : Array Nat := #[]
    for imported in #[module.toName, `Lean, `Lean.Meta.Eqns, `Lean.ProjFns] do
      let (nextSeen, nextOrder) := exportImportOrder env indices imported seen order
      seen := nextSeen
      order := nextOrder
    let mut owners : Std.HashMap Name String := {}
    for index in order do
      let data := env.header.moduleData[index]!
      for name in data.constNames ++ data.extraConstNames do
        if needed.contains name && !owners.contains name then
          owners := owners.insert name env.header.moduleNames[index]!.toString
    let ownerOf := fun name => owners[name]?
    let dependencies := fun (names : Array Name) => names.map fun name =>
      Json.mkObj [("name", toJson name.toString), ("module", toJson (ownerOf name))]
    let ownershipEnd ← IO.monoMsNow
    let mut count := 0
    let mut bytes := 0
    let mut typeMs := 0
    let mut writeMs := 0
    for name in names do
      let some info := env.find? name | continue
      let kind := match info with
        | .axiomInfo _ => "axiom"
        | .defnInfo _ => "definition"
        | .thmInfo _ => "theorem"
        | .opaqueInfo _ => "opaque"
        | .quotInfo _ => "quotient"
        | .inductInfo _ => "inductive"
        | .ctorInfo _ => "constructor"
        | .recInfo _ => "recursor"
      let generator := match info with
        | .ctorInfo c => some c.induct
        | .recInfo r => some r.getMajorInduct
        | _ => env.getProjectionStructureName? name
      let ranges ← findDeclarationRanges? name
      let rangeJson := ranges.map fun r => Json.mkObj [
        ("start", Json.mkObj [("line", toJson r.range.pos.line), ("column", toJson r.range.pos.column)]),
        ("finish", Json.mkObj [("line", toJson r.range.endPos.line), ("column", toJson r.range.endPos.column)])]
      let typeStart ← IO.monoMsNow
      let typeText ← ppExpr info.type
      let typeEnd ← IO.monoMsNow
      typeMs := typeMs + typeEnd - typeStart
      let (typeNames, valueNames) := refs[name]!
      let typeRefs := dependencies typeNames
      let valueRefs := valueNames.map dependencies
      let docString ← findDocString? env name
      let payload := Json.mkObj [
        ("type_text", toJson typeText.pretty), ("docstring", toJson docString),
        ("name", toJson name.toString), ("user_name", toJson (privateToUserName name).toString),
        ("module", toJson (ownerOf name)), ("kind", toJson kind),
        ("generator", toJson (generator.map Name.toString)), ("range", rangeJson.getD Json.null),
        ("type", toJson typeRefs),
        ("value", toJson valueRefs)]
      let writeStart ← IO.monoMsNow
      let line := "LEAN_EXPOSITION_JSON " ++ payload.compress
      out.putStrLn line
      out.flush
      count := count + 1
      bytes := bytes + line.utf8ByteSize + 1
      let writeEnd ← IO.monoMsNow
      writeMs := writeMs + writeEnd - writeStart
    let finished ← IO.monoMsNow
    out.putStrLn ("LEAN_EXPOSITION_END " ++ (Json.mkObj [
      ("module", toJson module), ("records", toJson count), ("bytes", toJson bytes),
      ("elapsed_ms", toJson (finished - started)),
      ("type_print_ms", toJson typeMs), ("dependency_ms", toJson (dependencyEnd - dependencyStart)),
      ("ownership_ms", toJson (ownershipEnd - dependencyEnd)),
      ("write_ms", toJson writeMs)]).compress)
    out.flush
