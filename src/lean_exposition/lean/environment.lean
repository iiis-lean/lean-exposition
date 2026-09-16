import Lean
import Lean.Meta.Eqns
import Lean.ProjFns
-- Selected project imports are prepended by the Python wrapper.
open Lean Meta
run_meta do
  let env ← getEnv
  let selected : Array String := __MODULES__
  let ownerOf := fun n => (env.getModuleIdxFor? n).map fun i => env.header.moduleNames[i]!.toString
  let dependencies := fun (e : Expr) => e.getUsedConstants.map fun n =>
    Json.mkObj [("name", toJson n.toString), ("module", toJson (ownerOf n))]
  let names := env.header.moduleNames.zipIdx |>.foldl (fun acc (moduleName, index) =>
    if selected.contains moduleName.toString then acc ++ env.header.moduleData[index]!.constNames else acc) #[]
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
    let payload := Json.mkObj [
      ("name", toJson name.toString), ("user_name", toJson (privateToUserName name).toString),
      ("module", toJson (ownerOf name)), ("kind", toJson kind),
      ("generator", toJson (generator.map Name.toString)), ("range", rangeJson.getD Json.null),
      ("type", toJson (dependencies info.type)),
      ("value", toJson ((info.value? true).map dependencies))]
    logInfo m!"LEAN_EXPOSITION_JSON {payload.compress}"
