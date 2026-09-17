"""Conservative explicit Lean references, without elaboration or a Lean process."""
from collections import defaultdict
from dataclasses import replace
import re

from lean_exposition.construction import CoverageContribution, FieldContribution
from lean_exposition.models import Dependency, Provenance, TextContent

_SEGMENT = r"(?:«[^»\n]+»|[^\W\d][\w'₀-₉]*)"
NAME = re.compile(rf"{_SEGMENT}(?:\.{_SEGMENT})*")
KEYWORDS = set('by exact apply rw simp simpa intro intros have let fun forall theorem lemma def abbrev axiom constant instance structure class inductive opaque private protected noncomputable unsafe partial where do if then else match with at using from in Type Sort Prop namespace section end open variable variables import set_option attribute rfl trivial decide constructor cases case obtain rcases induction show change calc return true false'.split())


def _binders(text):
    names = set()
    for match in re.finditer(r'[({\[]\s*([^:(){}\[\]\n]+)\s*:', text):
        names.update(NAME.findall(match.group(1)))
    for match in re.finditer(r'(?:\b(?:fun|intro|intros|rintro)\s+|∀\s+)([^\n,:=↦]+)', text):
        names.update(NAME.findall(match.group(1).split('=>')[0]))
    for match in re.finditer(r'\b(?:have|let|obtain|rcases)\s+([^\n:=]+)', text):
        names.update(NAME.findall(match.group(1).split('with')[-1]))
    return names


def add_text_references(adapter, source_texts):
    """Resolve explicit references against selected source declarations.

    Unknown external names and ambiguous matches remain diagnostics, never
    fabricated declarations. Binder suppression deliberately prefers missing
    an edge over inventing a global reference for a local variable.
    """
    from lean_mcp_toolkit.backends.text_ast.comments import mask_comments_and_strings
    index = defaultdict(list)
    fields = {}
    for contribution in adapter.declarations:
        values = {f.field: f.value for f in contribution.fields if f.state == 'present'}
        ref = contribution.locator.ref
        fields[ref] = values
        index[values['lean_name']].append(ref)
    contexts, imports = {}, {}
    # A single linear pass per module; no rescanning file prefixes per declaration.
    for module, text in source_texts.items():
        stack = [([], set(), [], {})]
        contexts[module] = []
        imports[module] = set()
        for line in mask_comments_and_strings(text).splitlines():
            stripped = line.strip()
            if re.match(r'(?:public\s+)?import\s+', stripped):
                imports[module].update(NAME.findall(stripped)[1:])
            if re.match(r'(namespace|section)\b', stripped):
                stack.append((list(stack[-1][0]), set(stack[-1][1]), list(stack[-1][2]), dict(stack[-1][3])))
                stack[-1][2].append(stripped)
            elif re.match(r'end\b', stripped) and len(stack) > 1:
                stack.pop()
            elif re.match(r'open\s+', stripped) and not re.search(r'\bin\s*$', stripped):
                values = NAME.findall(stripped)[1:]
                if values and values[0] != 'scoped':
                    stack[-1][0].extend(values)
                    stack[-1][2].append(stripped)
            elif re.match(r'variables?\b', stripped):
                stack[-1][1].update(_binders(stripped))
                stack[-1][2].append(stripped)
            alias = re.match(r'alias\s+(' + NAME.pattern + r')\s*:=\s*(' + NAME.pattern + r')', stripped)
            if alias:
                stack[-1][3][alias.group(1)] = alias.group(2)
            contexts[module].append((tuple(stack[-1][0]), frozenset(stack[-1][1]), tuple(stack[-1][2]), dict(stack[-1][3])))
    closure = {}
    for module in imports:
        reached, pending = {module}, list(imports[module])
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(imports.get(current, ()))
        closure[module] = reached
    result, coverage, diagnostics = [], list(adapter.coverage), list(adapter.diagnostics)
    for contribution in adapter.declarations:
        ref = contribution.locator.ref
        values = fields[ref]
        module, full_name = values['module'], values['lean_name']
        ranges = values.get('source_refs', ())
        line = ranges[0].start_line if ranges else 1
        states = contexts.get(module, ())
        opens, section_vars, context_lines, aliases = states[min(line - 1, len(states) - 1)] if states else ((), (), (), {})
        namespace = full_name.rsplit('.', 1)[0] if '.' in full_name else ''
        statement = values.get('statement.formal')
        statement_text = statement.text or '' if statement else ''
        local_names = set(section_vars) | _binders(mask_comments_and_strings(statement_text))
        added = []
        if context_lines:
            origin = (Provenance('source_scope_context', f'{module}:{line}'),)
            added.append(FieldContribution('source_context', 'present',
                (TextContent('\n'.join(context_lines), 'present', origin),), 'text_ast', 'text_ast',
                'source_scope_context', origin))
        for part in ('statement', 'proof'):
            formal = values.get(part + '.formal')
            if not isinstance(formal, TextContent) or not formal.text:
                continue
            masked = mask_comments_and_strings(formal.text)
            bound = local_names | _binders(masked)
            # Remove declaration header/name, not the type following it.
            if part == 'statement':
                masked = re.sub(r'\b(?:theorem|lemma|def|abbrev|axiom|constant|instance|structure|class|inductive|opaque)\s+' + NAME.pattern, ' ', masked, count=1)
            deps, unresolved, ambiguous = {}, set(), set()
            for token in dict.fromkeys(NAME.findall(masked)):
                if token in KEYWORDS or token.split('.')[0] in bound:
                    continue
                rooted = token.startswith('_root_.')
                name = token.removeprefix('_root_.')
                name = aliases.get(name, name)
                prefixes = namespace.split('.') if namespace else []
                tiers = [[name]] if rooted else [[('.'.join(prefixes[:i]) + '.' if i else '') + name]
                                                    for i in range(len(prefixes), -1, -1)]
                if not rooted:
                    tiers.append([o + '.' + name for o in opens])
                candidates = []
                for tier in tiers:
                    candidates = list(dict.fromkeys(r for n in tier for r in index.get(n, ())
                                      if fields[r]['module'] in closure.get(module, {module})))
                    if candidates:
                        break
                if len(candidates) == 1:
                    provider = candidates[0]
                    if provider != ref:
                        origin = (Provenance('text_reference', f'{module}:{line}:{part}:{token}', ranges),)
                        deps[provider] = Dependency(provider, 'text_reference', origin)
                elif candidates:
                    ambiguous.add(token)
                else:
                    unresolved.add(token)
            origin = (Provenance('text_reference_scan', f'{module}:{line}:{part}', ranges),)
            added.append(FieldContribution(part + '.deps', 'present', tuple(deps.values()),
                                           'text_ast', 'text_ast', 'text_reference', origin))
            coverage.append(CoverageContribution(ref, part, 'text_reference', 'partial', origin))
            for kind, names in [('ambiguous', ambiguous), ('unresolved', unresolved)]:
                if names:
                    diagnostics.append(f'text_reference_{kind}:{module}:{line}:{part}:' + ','.join(sorted(names)))
        result.append(replace(contribution, fields=contribution.fields + tuple(added)))
    return replace(adapter, declarations=tuple(result), coverage=tuple(coverage), diagnostics=tuple(diagnostics))
