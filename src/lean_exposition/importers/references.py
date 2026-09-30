"""Conservative explicit Lean references, without elaboration or a Lean process."""
from dataclasses import replace
import hashlib

from lean_exposition.construction import CoverageContribution, FieldContribution
from lean_exposition.models import DeclRef, Dependency, Provenance, TextContent
from .reference_index import ReferenceIndex, adapter_reference_index
from .text_scope import SourceContext, name_parts, scan_scope


def add_text_references(adapter, source_texts, *, reference_index=None):
    """Resolve lexical references and explicitly typed receiver dot notation.

    The optional index may cover a larger, fixed source import closure than the
    target adapter. Unknown syntax/types and ambiguous names remain diagnostics;
    coverage stays partial. Compiled facts are never rewritten here.
    """
    local_index, module_states = adapter_reference_index(adapter, source_texts)
    index = local_index
    if reference_index is not None:
        target_repos = {d.locator.ref.repo_key for d in adapter.declarations}
        for signature in reference_index.signatures:
            if signature.ref.repo_key in target_repos and signature.module in source_texts and signature.source_sha256:
                actual = hashlib.sha256(source_texts[signature.module].encode()).hexdigest()
                if actual != signature.source_sha256:
                    raise ValueError(f'text reference index source changed: {signature.module}')
        # The target adapter owns identities for its declarations (including
        # compiled canonical identities), even when also present in the index.
        owned = {(s.ref.repo_key, s.module, s.name, s.line) for s in local_index.signatures}
        extras = tuple(replace(s, ref=DeclRef(s.ref.repo_key, s.name))
                       if s.public and s.ref.repo_key not in target_repos else s
                       for s in reference_index.signatures
                       if (s.ref.repo_key, s.module, s.name, s.line) not in owned)
        index = ReferenceIndex(local_index.signatures + extras,
                               {**local_index.imports, **reference_index.imports},
                               reference_index.repositories, reference_index.diagnostics)
    result, coverage = [], list(adapter.coverage)
    local_signatures = {s.ref: s for s in local_index.signatures}
    diagnostics = list(adapter.diagnostics) + list(index.diagnostics)
    for contribution in adapter.declarations:
        ref = contribution.locator.ref
        values = {f.field: f.value for f in contribution.fields if f.state == 'present'}
        module = values['module']
        ranges = values.get('source_refs', ())
        line = ranges[0].start_line if ranges else 1
        states = module_states.get(module, ((), ()))[0]
        context = states[min(line - 1, len(states) - 1)] if states else SourceContext()
        namespace = '.'.join(name_parts(local_signatures[ref].name)[:-1])
        statement = values.get('statement.formal')
        statement_scan = scan_scope(statement.text or '', declaration=True) if statement else scan_scope('')
        locals_for_proof = dict(context.variables)
        locals_for_proof.update((b.name, b.type_text) for b in statement_scan.parameters)
        added = []
        if context.lines:
            origin = (Provenance('source_scope_context', f'{module}:{line}'),)
            added.append(FieldContribution('source_context', 'present',
                (TextContent('\n'.join(context.lines), 'present', origin),), 'text_ast', 'text_ast',
                'source_scope_context', origin))
        for part in ('statement', 'proof'):
            formal = values.get(part + '.formal')
            if not isinstance(formal, TextContent) or not formal.text:
                continue
            scan = statement_scan if part == 'statement' else scan_scope(formal.text)
            initial = dict(context.variables) if part == 'statement' else locals_for_proof
            deps, unresolved, ambiguous, uncertain_dots = {}, set(), set(), set()
            occurrences = [(token, bound, None) for token, bound in scan.occurrences(initial)]
            used_section_names = {name_parts(token.text)[0] for token, _, _ in occurrences} & dict(context.variables).keys()
            for name, typ in context.variables:
                if name in used_section_names and typ:
                    occurrences.extend((token, bound, name) for token, bound in scan_scope(typ).occurrences(initial))
            for token, bound, section_name in occurrences:
                name = token.text
                parts = name_parts(name)
                head, method = parts[0], '.'.join(parts[1:])
                if head in bound:
                    if not method:
                        continue
                    candidates, reason = index.dot_candidates(bound[head], method, repo_key=ref.repo_key,
                        module=module, namespace=namespace, opens=context.opens, line=line)
                    if reason:
                        uncertain_dots.add(name + ':' + reason)
                else:
                    candidates = index.resolve(name, repo_key=ref.repo_key, module=module,
                        namespace=namespace, opens=context.opens, aliases=context.aliases, line=line)
                if len(candidates) == 1:
                    provider = candidates[0]
                    if provider.ref != ref:
                        origin = (Provenance('text_reference',
                            f'{module}:{line}:{part}:{token.line}:{token.column}:{name}', ranges),)
                        if section_name:
                            origin = (Provenance('text_reference',
                                f'{module}:{line}:{part}:section_type:{section_name}:{name}', ranges),)
                        if provider.source_sha256:
                            origin += (Provenance('text_reference_index',
                                f'{provider.ref.repo_key}:{provider.module}:{provider.name}:{provider.source_sha256}'),)
                        previous = deps.get(provider.ref)
                        if previous:
                            origin = tuple(dict.fromkeys(previous.provenance + origin))
                        deps[provider.ref] = Dependency(provider.ref, 'text_reference', origin,
                                                        provider_module=provider.module)
                elif candidates:
                    ambiguous.add(name)
                else:
                    unresolved.add(name)
            origin = (Provenance('text_reference_scan', f'{module}:{line}:{part}', ranges),)
            added.append(FieldContribution(part + '.deps', 'present', tuple(deps.values()),
                                           'text_ast', 'text_ast', 'text_reference', origin))
            coverage.append(CoverageContribution(ref, part, 'text_reference', 'partial', origin))
            for kind, names in [('ambiguous', ambiguous), ('unresolved', unresolved),
                                ('uncertain_dot', uncertain_dots), ('unsupported', set(scan.diagnostics))]:
                if names:
                    diagnostics.append(f'text_reference_{kind}:{module}:{line}:{part}:' + ','.join(sorted(names)))
        result.append(replace(contribution, fields=contribution.fields + tuple(added)))
    repositories = list(adapter.repositories)
    for repository in index.repositories:
        existing = next((r for r in repositories if r.repo_key == repository.repo_key), None)
        if existing is None:
            repositories.append(replace(repository, root_scope=None))
        elif (existing.revision and existing.revision != repository.revision) or (
                existing.toolchain and repository.toolchain and existing.toolchain != repository.toolchain):
            raise ValueError(f'text reference index repository identity mismatch: {repository.repo_key}')
    return replace(adapter, declarations=tuple(result), coverage=tuple(coverage),
                   repositories=tuple(repositories), diagnostics=tuple(diagnostics))
