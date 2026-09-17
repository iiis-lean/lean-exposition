"""Source-grounded cards and scope views, independent of generated prose."""
from dataclasses import asdict

from lean_exposition.models import DeclRef


def ref_key(ref):
    return (ref["repo_key"], ref["local_id"]) if isinstance(ref, dict) else (ref.repo_key, ref.local_id)


def decl_card(workspace, ref, *, proof=False, text_store=None, locale=None,
              profile="default", text_record_ids=None):
    key = ref_key(ref)
    declaration = next((d for d in workspace.declarations if ref_key(d.ref) == key), None)
    if declaration is None:
        return {"ref": {"repo_key": key[0], "local_id": key[1]}, "loaded": False,
                "missing_reason": "Declaration body is not loaded in this fixed workspace."}
    card = {"ref": asdict(declaration.ref), "loaded": True, "name": declaration.lean_name,
            "fine_kind": declaration.kind, "theorem_like": declaration.kind in {"theorem", "lemma"},
            "owner": declaration.native_scope, "module": declaration.module,
            "statement": _content(declaration.statement), "local_public": declaration.local_public,
            "primary_outcome": any(declaration.ref in repo.primary_outcomes for repo in workspace.manifest.repositories),
            "source_refs": [asdict(r) for r in declaration.source_refs],
            "source_available": bool(declaration.source_context or declaration.source_refs),
            "proof_available": declaration.proof is not None and declaration.proof.formal.text is not None,
            "completion_state": declaration.completion_status.state if declaration.completion_status else None,
            "extraction_status": {"state": declaration.extraction_status.state, "reason": declaration.extraction_status.reason}}
    if proof:
        card["proof"] = _content(declaration.proof) if declaration.proof else None
    if text_store is not None and locale is not None:
        record_id = (text_record_ids or {}).get(_record_key(declaration.ref))
        record = (text_store.pinned(record_id, declaration.ref, locale=locale, profile=profile)
                  if record_id is not None else
                  text_store.active(declaration.ref, locale=locale, profile=profile))
        if record is not None:
            card["summary"] = record["summary"]
            card["text_record_id"] = record["record_id"]
            if card["statement"]["nl"]["status"] != "present" and record["statement_nl"] is not None:
                card["statement"]["nl"] = {"text": record["statement_nl"], "status": "generated", "reason": None}
            if proof and card.get("proof") and card["proof"]["nl"]["status"] != "present" and record["proof_nl"] is not None:
                card["proof"]["nl"] = {"text": record["proof_nl"], "status": "generated", "reason": None}
    return card


def _content(content):
    return {field: {"text": getattr(content, field).text, "status": getattr(content, field).status,
                    "reason": getattr(content, field).reason} for field in ("nl", "formal")}


def _record_key(ref):
    key = ref_key(ref)
    return f"{key[0]}\0{key[1]}"


def scope_view(workspace, hierarchy, node_id, *, offset=0, limit=24,
               text_store=None, locale=None, profile="default", text_record_ids=None,
               dependency_analysis=None, full_dependencies=False):
    if dependency_analysis is None and not full_dependencies:
        from lean_exposition.structure import analyze_dependencies
        dependency_analysis = analyze_dependencies(workspace, hierarchy["repo_key"])
    nodes = {node["id"]: node for node in hierarchy["nodes"]}
    node = nodes[node_id]
    inside = {ref_key(ref) for ref in node["decl_refs"]}
    edge_ids = {(ref_key(e["provider_decl"]), ref_key(e["consumer_decl"])): e["id"] for e in hierarchy.get("edges", [])}
    incoming, outgoing, internal = [], [], []
    related = set(inside)
    hidden_pairs = (set() if dependency_analysis is None or full_dependencies else
                    {(ref_key(item.provider), ref_key(item.consumer))
                     for item in dependency_analysis.decisions if not item.keep})
    for d in workspace.declarations:
        consumer = ref_key(d.ref)
        for part_name, part in (("statement", d.statement), ("proof", d.proof)):
            if part is None:
                continue
            for dep in part.deps:
                provider = ref_key(dep.provider)
                if (provider, consumer) in hidden_pairs:
                    continue
                if provider not in inside and consumer not in inside:
                    continue
                edge = {"provider_decl": asdict(dep.provider), "consumer_decl": asdict(d.ref),
                        "part": part_name, "evidence_kind": dep.evidence_kind,
                        "edge_id": edge_ids.get((provider, consumer))}
                related.update((provider, consumer))
                (internal if provider in inside and consumer in inside else
                 outgoing if provider in inside else incoming).append(edge)
    loaded = {ref_key(d.ref) for d in workspace.declarations}
    primary = {ref_key(ref) for repo in workspace.manifest.repositories for ref in repo.primary_outcomes}
    ordered = sorted(related, key=lambda key: (0 if key in inside and key in primary else
                                             1 if key in inside else 2 if key in loaded else 3, key))
    page_end = len(ordered) if limit is None else offset + limit
    return {"node": {k: node.get(k) for k in ("id", "kind", "title", "source_scope", "representative")},
            "children": [{k: nodes[c].get(k) for k in ("id", "kind", "title", "decl_refs")} for c in node["children"]],
            "incoming": incoming, "outgoing": outgoing, "internal": internal,
            "primary_outcomes": [asdict(ref) for r in workspace.manifest.repositories
                                 for ref in r.primary_outcomes if ref_key(ref) in inside],
            "decl_refs": [{"repo_key": r, "local_id": d} for r, d in ordered],
            "cards": [decl_card(workspace, DeclRef(*key), proof=key in inside,
                                text_store=text_store, locale=locale, profile=profile,
                                text_record_ids=text_record_ids)
                      for key in ordered[offset:page_end]],
            "card_count": len(ordered), "card_offset": offset,
            "next_offset": page_end if page_end < len(ordered) else None,
            "omitted": ["cards outside the indicated page"] if len(ordered) > page_end or offset else []}


def writing_view(workspace, hierarchy, node_id, *, mathematical=False,
                 text_store=None, locale=None, profile="default", text_record_ids=None,
                 dependency_analysis=None, full_dependencies=False):
    """Compact section/naming context; complete facts remain in scope_view.

    The mathematical policy additionally retains proof dependencies and selected proofs.
    Select outcomes, local public declarations, child interfaces and one theme
    representative per child. Section writers never receive internal proof text.
    Unloaded external bodies are counted by repository, not repeated as cards.
    """
    nodes = {node['id']: node for node in hierarchy['nodes']}
    node = nodes[node_id]
    if node['kind'] == 'unit':
        view = scope_view(workspace, hierarchy, node_id, limit=None,
                          text_store=text_store, locale=locale, profile=profile,
                          text_record_ids=text_record_ids,
                          dependency_analysis=dependency_analysis,
                          full_dependencies=full_dependencies)
        return _mathematical_projection(view, node, nodes) if mathematical else view
    full = scope_view(workspace, hierarchy, node_id, limit=0,
                      text_store=text_store, locale=locale, profile=profile,
                      text_record_ids=text_record_ids,
                      dependency_analysis=dependency_analysis,
                      full_dependencies=full_dependencies)
    inside = {ref_key(ref) for ref in node['decl_refs']}
    declarations = {ref_key(d.ref): d for d in workspace.declarations}
    child_owner = {ref_key(ref): child for child in node['children'] for ref in nodes[child]['decl_refs']}
    selected = {ref_key(ref) for ref in full['primary_outcomes']}
    selected.update(key for key in inside if key in declarations and declarations[key].local_public and
                    (not mathematical or declarations[key].native_scope == node.get('source_scope')))
    selected.update(ref_key(edge['provider_decl']) for edge in full['outgoing'])
    connections = [edge for edge in full['internal']
                   if child_owner.get(ref_key(edge['provider_decl'])) != child_owner.get(ref_key(edge['consumer_decl']))]
    for edge in connections:
        selected.update((ref_key(edge['provider_decl']), ref_key(edge['consumer_decl'])))
    for child in node['children']:
        child_node = nodes[child]
        candidates = [ref_key(ref) for ref in child_node['decl_refs']]
        representative = child_node.get('representative')
        if representative:
            selected.add(ref_key(representative))
        elif candidates and not selected.intersection(candidates):
            candidates.sort(key=lambda key: (declarations[key].extraction_status.state == 'compiler_only' if key in declarations else True, key))
            selected.add(candidates[0])
    # Keep concrete providers needed to interpret selected interfaces, excluding
    # proof-only library references from this coarser writing layer.
    incoming = [edge for edge in full['incoming'] if ref_key(edge['consumer_decl']) in selected and (mathematical or edge['part'] == 'statement')]
    outgoing = [edge for edge in full['outgoing'] if ref_key(edge['provider_decl']) in selected]
    if mathematical:
        connections = [edge for edge in full['internal'] if ref_key(edge['consumer_decl']) in selected or edge in connections]
    related = set(selected)
    for edge in incoming + outgoing + (connections if mathematical else []):
        related.update((ref_key(edge['provider_decl']), ref_key(edge['consumer_decl'])))
    if mathematical:
        # Outgoing consumers describe future uses, not this scope's established
        # results. Their bodies are available only through explicit source queries.
        related.intersection_update(inside | {ref_key(edge['provider_decl']) for edge in incoming})
    loaded = sorted(related.intersection(declarations))
    missing_by_repo = {}
    for ref in full['decl_refs']:
        key = ref_key(ref)
        if key not in declarations:
            missing_by_repo[key[0]] = missing_by_repo.get(key[0], 0) + 1
    def unique(edges):
        result = {}
        for edge in edges:
            pair = (ref_key(edge['provider_decl']), ref_key(edge['consumer_decl']), edge['part'] if mathematical else None)
            result.setdefault(pair, edge)
        return list(result.values())
    result = {'node': full['node'],
            'children': [{k: nodes[child].get(k) for k in ('id', 'kind', 'title', 'representative')} |
                         {'declaration_count': len(nodes[child]['decl_refs'])} for child in node['children']],
            'incoming': unique(incoming), 'outgoing': unique(outgoing), 'internal': unique(connections),
            'primary_outcomes': full['primary_outcomes'],
            'cards': [decl_card(workspace, DeclRef(*key), proof=mathematical and key in selected,
                                text_store=text_store, locale=locale, profile=profile,
                                text_record_ids=text_record_ids)
                      for key in loaded],
            'selected_refs': [{'repo_key': key[0], 'local_id': key[1]} for key in sorted(selected)],
            'omitted_internal_refs': [ref for ref in node['decl_refs'] if ref_key(ref) not in selected],
            'unloaded_external_counts': missing_by_repo,
            'complete_relation_counts': {kind: len(full[kind]) for kind in ('incoming', 'outgoing', 'internal')},
            'material_policy': ('Selected statement/proof interfaces and proof evidence; unselected internal proofs are omitted. Use bound queries for additional source. ' if mathematical else '') + 'Partial interface context, not all facts. Internal proofs and unselected cards are omitted. '
                               'Use scope_view/decl_card for explicit additional material; do not infer a proof method from an absent proof.',
            'complete_scope_query': {'node_id': node_id}}
    return _mathematical_projection(result, node, nodes) if mathematical else result


def _mathematical_projection(view, node, nodes):
    """Lossless dependency incidence coding plus source-supported proof selection.

    Full DeclCard stays unchanged. At coarse layers a complete supplied NL proof
    is sufficient evidence; duplicate formal implementation is supplied only for
    root outcomes and direct terminal representatives, or when no NL proof exists.
    """
    from copy import deepcopy
    result = deepcopy(view)
    edges = [edge for kind in ("incoming", "outgoing", "internal") for edge in result[kind]]
    references = sorted({ref_key(edge[field]) for edge in edges for field in ("provider_decl", "consumer_decl")})
    positions = {key: index for index, key in enumerate(references)}
    evidence = sorted({edge["evidence_kind"] for edge in edges})
    result["dependency_refs"] = [{"repo_key": key[0], "local_id": key[1]} for key in references]
    result["dependency_encoding"] = {"columns": ["provider_ref_index", "consumer_ref_index", "part_index", "evidence_index"],
                                     "parts": ["statement", "proof"], "evidence_kinds": evidence,
                                     "direction": "provider_to_consumer", "edge_ids": "available in scope query"}
    for kind in ("incoming", "outgoing", "internal"):
        result[kind] = [[positions[ref_key(edge["provider_decl"])], positions[ref_key(edge["consumer_decl"])],
                         int(edge["part"] == "proof"), evidence.index(edge["evidence_kind"])] for edge in result[kind]]
    focus = {ref_key(ref) for ref in result.get("primary_outcomes", [])}
    cards = []
    missing = {}
    for card in result["cards"]:
        if not card.get("loaded"):
            repo = card["ref"]["repo_key"]
            missing[repo] = missing.get(repo, 0) + 1
            continue
        compact = {key: value for key, value in card.items() if key not in {"source_refs", "owner", "module", "extraction_status", "source_available"}}
        inside = {ref_key(ref) for ref in node["decl_refs"]}
        compact["writing_role"] = "local_declaration" if ref_key(card["ref"]) in inside else "incoming_provider_interface"
        compact["primary_outcome"] = bool(card.get("primary_outcome") and ref_key(card["ref"]) in inside)
        compact["local_public"] = bool(card.get("local_public") and card.get("owner") == node.get("source_scope"))
        proof = compact.get("proof")
        if node["kind"] != "unit" and proof and proof["nl"].get("text") and ref_key(card["ref"]) not in focus:
            compact["proof"] = {"nl": proof["nl"], "formal_omitted": "Complete source NL proof supplied; formal implementation remains in declaration query."}
        for part in ("statement", "proof"):
            content = compact.get(part)
            if content and content.get("nl", {}).get("text") and content.get("formal", {}).get("text") == content["nl"]["text"]:
                content["formal"] = {"same_as": "nl", "status": "present"}
        cards.append(compact)
    result["cards"] = cards
    result.pop("decl_refs", None)
    result["unloaded_external_counts"] = {**result.get("unloaded_external_counts", {}), **missing}
    result["material_policy"] = ("Selected mathematical interfaces and statement/proof dependency incidence. "
        "At coarse layers complete source NL proofs replace duplicate formal implementations except focused outcomes. "
        "Outgoing consumers are future uses, never results delivered by this scope. Missing external bodies are not reconstructed. The full source and edge identifiers remain in bound queries.")
    return result
