import Lean
open Lean Meta

private partial def exprShape : Expr → Nat × Nat
  | .app f a => let x := exprShape f; let y := exprShape a; (1+x.1+y.1, 1+max x.2 y.2)
  | .lam _ t b _ | .forallE _ t b _ => let x := exprShape t; let y := exprShape b; (1+x.1+y.1, 1+max x.2 y.2)
  | .letE _ t v b _ => let x := exprShape t; let y := exprShape v; let z := exprShape b; (1+x.1+y.1+z.1, 1+max x.2 (max y.2 z.2))
  | .mdata _ e | .proj _ _ e => let x := exprShape e; (1+x.1, 1+x.2)
  | _ => (1,1)

private partial def syntaxShape : Syntax → Nat × Nat
  | .node _ _ args => args.foldl (fun acc child => let v := syntaxShape child; (acc.1+v.1, max acc.2 (1+v.2))) (1,1)
  | _ => (1,1)

private partial def tacticKinds (stx : Syntax) : Array String := Id.run do
  let mut result := #[]
  if stx.getKind.toString.startsWith "Lean.Parser.Tactic." then
    result := result.push stx.getKind.toString
  for child in stx.getArgs do
    result := result ++ tacticKinds child
  return result

run_meta do
  let names : Array String := __NAMES__
  let sources : Array String := __SOURCES__
  for index in [:names.size] do
    let name := names[index]!
    let some info := (← getEnv).find? name.toName | throwError "Missing declaration {name}"
    let shape := exprShape info.type
    let fullType ← withOptions (fun o => o.setBool `pp.explicit true |>.setBool `pp.universes true) do
      return (← ppExpr info.type).pretty
    forallTelescope info.type fun binders conclusion => do
      let mut rows : Array Json := #[]
      for binder in binders do
        let decl ← binder.fvarId!.getDecl
        let domain ← whnf decl.type
        let category ← if decl.binderInfo == .instImplicit then pure "instance" else
          match domain with
          | .sort level => pure (if level == .zero then "proposition_parameter" else "type_parameter")
          | _ => do pure (if ← isProp decl.type then "proof_premise" else "object_parameter")
        let explicitness := match decl.binderInfo with
          | .default => "explicit"
          | .implicit => "implicit"
          | .strictImplicit => "strict_implicit"
          | .instImplicit => "instance_implicit"
        rows := rows.push (Json.mkObj [("name", toJson decl.userName.toString), ("category", toJson category),
          ("explicitness", toJson explicitness), ("domain", toJson (← ppExpr decl.type).pretty)])
      let head := conclusion.consumeMData.getAppFn.constName?
      let syntaxPayload := match Parser.runParserCategory (← getEnv) `command sources[index]! with
        | .error error => Json.mkObj [("status", toJson "missing"), ("reason", toJson error)]
        | .ok stx => let metrics := syntaxShape stx; Json.mkObj [("status", toJson "parsed"),
          ("nodes", toJson metrics.1), ("depth", toJson metrics.2), ("tactic_kinds", toJson (tacticKinds stx))]
      let payload := Json.mkObj [("lean_name", toJson name), ("full_type", toJson fullType), ("syntax", syntaxPayload),
        ("type_expr_nodes", toJson shape.1), ("type_expr_depth", toJson shape.2), ("binders", toJson rows),
        ("conclusion_head", toJson (head.map Name.toString)),
        ("conclusion_exists", toJson (head == some ``Exists)),
        ("conclusion_sigma", toJson (head == some ``Sigma || head == some ``PSigma))]
      logInfo m!"FEATURE_JSON {payload.compress}"
