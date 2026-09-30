"""Small lexical Lean scopes, deliberately independent of elaboration.

Only recognized binder positions create locals. Type ascriptions in ordinary
expressions never do. Unsupported binding syntax is reported to the caller.
"""
from dataclasses import dataclass
import re

SEGMENT = r"(?:«[^»\n]+»|[^\W\d][\w'₀-₉]*)"
NAME = re.compile(rf"{SEGMENT}(?:\.{SEGMENT})*")
NAME_SEGMENTS = re.compile(SEGMENT)
TOKEN = re.compile(rf"{NAME.pattern}|:=|=>|->|[^\s]", re.UNICODE)
DECLARATIONS = set('theorem lemma def abbrev axiom constant instance structure class inductive opaque'.split())
KEYWORDS = set(('by exact apply rw simp simpa intro intros rintro have let rec fun forall exists '
    'where do if then else match with at using from in Type Sort Prop namespace section end '
    'open scoped variable variables import public set_option attribute rfl trivial decide '
    'constructor cases case obtain rcases induction show change calc return true false '
    'private protected noncomputable unsafe partial alias include omit export deriving '
    'termination_by decreasing_by suffices generalize rename_i all_goals next').split()) | DECLARATIONS


def name_parts(name):
    """Dots inside a quoted Lean identifier are not namespace separators."""
    return NAME_SEGMENTS.findall(name)


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class Binding:
    name: str
    type_text: str | None
    start: int
    end: int
    implicit: bool = False


@dataclass(frozen=True)
class ScopeScan:
    tokens: tuple[Token, ...]
    bindings: tuple[Binding, ...]
    excluded: frozenset[int]
    parameters: tuple[Binding, ...]
    diagnostics: tuple[str, ...]

    def occurrences(self, initial=None):
        active = {name: [Binding(name, typ, -1, len(self.tokens))]
                  for name, typ in (initial or {}).items()}
        starts = {}
        for binding in self.bindings:
            starts.setdefault(binding.start, []).append(binding)
        for i, token in enumerate(self.tokens):
            for binding in starts.get(i, ()):
                active.setdefault(binding.name, []).append(binding)
            for name in tuple(active):
                active[name] = [b for b in active[name] if b.end > i]
                if not active[name]:
                    del active[name]
            if i not in self.excluded and NAME.fullmatch(token.text) and token.text not in KEYWORDS:
                yield token, {n: bs[-1].type_text for n, bs in active.items()}


def tokenize(text):
    from lean_mcp_toolkit.backends.text_ast.comments import mask_comments_and_strings
    masked = mask_comments_and_strings(text)
    tokens = []
    line, beginning, previous = 1, 0, 0
    for match in TOKEN.finditer(masked):
        gap = masked[previous:match.start()]
        line += gap.count('\n')
        if '\n' in gap:
            beginning = masked.rfind('\n', previous, match.start()) + 1
        tokens.append(Token(match.group(), match.start(), match.end(), line, match.start() - beginning))
        previous = match.end()
    return masked, tuple(tokens)


def declaration_head(tokens):
    """Find a command head only through its leading attributes/modifiers.

    Theorem inventories also supply slices beginning at the parameters. A
    keyword in their type or a projection such as `(P ⊔ Q).class` is not a head.
    """
    i = 0
    while i < len(tokens):
        word = tokens[i].text
        if word == '@' and i + 1 < len(tokens) and tokens[i + 1].text == '[':
            i, depth = i + 2, 1
            while i < len(tokens) and depth:
                depth += (tokens[i].text == '[') - (tokens[i].text == ']')
                i += 1
        elif word in {'private', 'protected', 'public', 'noncomputable', 'unsafe', 'partial', 'nonrec', 'meta'}:
            i += 1
        else:
            return i if word in DECLARATIONS else None
    return None


def scan_scope(text, *, declaration=False, variables=False):
    masked, tokens = tokenize(text)
    count = len(tokens)
    matching, enclosing, stack = {}, {}, []
    closing = {')': '(', '}': '{', ']': '[', '⟩': '⟨'}
    for i, token in enumerate(tokens):
        enclosing[i] = stack[-1] if stack else None
        if token.text in closing.values():
            stack.append(i)
        elif token.text in closing and stack and tokens[stack[-1]].text == closing[token.text]:
            start = stack.pop()
            matching[start] = i
    excluded, bindings, parameters, diagnostics = set(), [], [], []

    def word(i):
        return tokens[i].text if i < count else ''

    def raw(start, end):
        return masked[tokens[start].start:tokens[end - 1].end].strip() if start < end else None

    def top_find(start, end, choices):
        i = start
        while i < end:
            if word(i) in choices:
                return i
            i = matching.get(i, i) + 1
        return end

    def scope_end(i):
        end = matching.get(enclosing.get(i), count)
        # A nested tactic block ends on dedent; indentation of its first token
        # can differ from its line indentation (e.g. `have h := by`).
        for before in range(i - 1, -1, -1):
            if word(before) == '·':
                for after in range(i + 1, end):
                    if tokens[after].line > tokens[after - 1].line and tokens[after].column <= tokens[before].column:
                        end = after
                        break
                break
        for before in range(i - 1, -1, -1):
            if word(before) in {'by', '·', '=>'} and tokens[before].line < tokens[i].line:
                first = before + 1
                if first <= i and tokens[first].line > tokens[before].line:
                    indent = tokens[first].column
                    if tokens[i].column >= indent:
                        for after in range(i + 1, end):
                            if tokens[after].line > tokens[after - 1].line and tokens[after].column < indent:
                                return after
                        return end
        return end

    def add_names(start, end, typ, begin, finish, *, implicit=False, parameter=False):
        for i in range(start, end):
            name = word(i)
            if NAME.fullmatch(name) and name not in KEYWORDS and len(name_parts(name)) == 1:
                excluded.add(i)
                binding = Binding(name, typ, begin, finish, implicit)
                bindings.append(binding)
                if parameter:
                    parameters.append(binding)

    def group(i, finish, *, parameter=False):
        stop = matching[i]
        colon = top_find(i + 1, stop, {':'})
        if colon < stop and all(NAME.fullmatch(word(j)) or word(j) == '_' for j in range(i + 1, colon)):
            add_names(i + 1, colon, raw(colon + 1, stop), stop + 1, finish,
                      implicit=word(i) != '(', parameter=parameter)
        elif word(i) == '[':
            # An anonymous instance is a type expression, not a named binder.
            pass
        elif colon == stop and all(NAME.fullmatch(word(j)) or word(j) == '_' for j in range(i + 1, stop)):
            add_names(i + 1, stop, None, stop + 1, finish,
                      implicit=word(i) != '(', parameter=parameter)
        else:
            diagnostics.append(f'unsupported_binder:{tokens[i].line}:{tokens[i].column}')
        return stop + 1

    header = 0
    if declaration:
        command = declaration_head(tokens)
        if command is not None:
            header = command + 1
            if header < count and NAME.fullmatch(word(header)) and word(header) not in KEYWORDS:
                excluded.add(header)
                header += 1
        else:
            # Toolkit theorem statements are parameter/type slices, whereas
            # definitions retain their command and name.
            header = 0
    elif variables:
        header = 1 if word(0) in {'variable', 'variables'} else 0
    if declaration or variables:
        while header < count and word(header) in {'(', '{', '['} and header in matching:
            header = group(header, count, parameter=True)

    for i, token in enumerate(tokens):
        keyword = token.text
        if i and word(i - 1) == '.' and NAME.fullmatch(keyword):
            # `(term).field` has no simple typed local receiver. The suffix
            # cannot be resolved as a free global with the same spelling.
            excluded.add(i)
            diagnostics.append(f'unsupported_projection:{token.line}:{token.column}:{keyword}')
        # These spellings are also global theorem names. Suppress only tactic
        # command positions; `exact symm h` / `by_cases hp hn` remain terms.
        command_position = False
        if keyword in {'by_cases', 'symm'} and i and i not in excluded:
            previous = word(i - 1)
            blocks = [j for j in range(i) if word(j) == 'by'
                      and enclosing[j] == enclosing[i] and i < scope_end(j)]
            if blocks:
                command_position = previous in {'by', '·', ';'} or (
                    previous == '>' and i >= 3 and [word(i-3), word(i-2)] == ['<', ';'])
                first = blocks[-1] + 1
                bullets = [j for j in range(first, i) if word(j) == '·'
                           and enclosing[j] == enclosing[i]
                           and not any(tokens[k].line > tokens[k-1].line
                                       and tokens[k].column <= tokens[j].column
                                       for k in range(j + 1, i + 1))]
                if bullets:
                    first = bullets[-1] + 1
                if token.line > tokens[i - 1].line and previous not in {
                        'exact', 'apply', 'refine', 'using', 'from', ':=', '(', ',', '$'}:
                    command_position |= token.column <= tokens[first].column
        if keyword in {'by_cases', 'symm'} and command_position:
            excluded.add(i)
            if keyword == 'by_cases' and NAME.fullmatch(word(i + 1)) and word(i + 2) == ':':
                end = scope_end(i)
                finish = i + 3
                while finish < end and tokens[finish].line == token.line and word(finish) != ';':
                    finish += 1
                add_names(i + 1, i + 2, raw(i + 3, finish), finish, end)
        elif keyword in {'fun', '∀', 'forall', '∃', 'exists'}:
            end = scope_end(i)
            sep = top_find(i + 1, end, {'=>', '↦', ','})
            if sep == end:
                diagnostics.append(f'unsupported_binder:{token.line}:{token.column}:{keyword}')
                continue
            if enclosing.get(i) is not None:
                # A comma separates tuple elements, except when it belongs to
                # a nested binder such as `fun x => ∑ y, f x y`.
                j, nested = sep + 1, 0
                while j < end:
                    if word(j) in {'∀', '∃', 'forall', 'exists', '∑', '∑ᶠ', '∏', '⋃', '⋂'}:
                        nested += 1
                    elif word(j) == ',':
                        if not nested:
                            end = j
                            break
                        nested -= 1
                    j = matching.get(j, j) + 1
            j = i + 1
            while j < sep and word(j) in {'(', '{', '['} and j in matching:
                j = group(j, end)
            colon = top_find(j, sep, {':', '∈', '∉', '<', '>', '≤', '≥'})
            if all(NAME.fullmatch(word(k)) or word(k) == '_' for k in range(j, colon)):
                typ = raw(colon + 1, sep) if word(colon) == ':' else None
                add_names(j, colon, typ, sep + 1, end)
            elif j < sep:
                diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
        elif keyword in {'let', 'have', 'suffices'}:
            end = scope_end(i)
            name = i + 1
            if word(name) == 'rec':
                diagnostics.append(f'unsupported_binding:{token.line}:{token.column}:let_rec')
                name += 1
            if word(name) == '⟨' and name in matching:
                stop = matching[name]
                finish = stop + 1
                while finish < end and tokens[finish].line == token.line and word(finish) != ';':
                    finish += 1
                add_names(name + 1, stop, None, finish, end)
                continue
            if not NAME.fullmatch(word(name)) or word(name) in KEYWORDS:
                if word(name) not in {':', '_'}:
                    diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
                continue
            excluded.add(name)
            assign = top_find(name + 1, end, {':=', '←'})
            if assign == end:
                diagnostics.append(f'unsupported_binding:{token.line}:{token.column}:{keyword}')
                continue
            colon = top_find(name + 1, assign, {':'})
            typ = raw(colon + 1, assign) if colon < assign else None
            # RHS ends at an explicit separator or the next sibling command.
            finish = end
            j = assign + 1
            while j < end:
                if word(j) in {';', 'in'}:
                    finish = j + 1
                    break
                if tokens[j].line > token.line and tokens[j].column <= token.column:
                    finish = j
                    break
                j = matching.get(j, j) + 1
            bindings.append(Binding(word(name), typ, finish, end))
            j = name + 1
            while j < assign and word(j) in {'(', '{', '['} and j in matching:
                j = group(j, finish)
        elif keyword in {'intro', 'intros', 'rintro', 'rename_i'}:
            end = scope_end(i)
            j = i + 1
            while j < end and tokens[j].line == token.line and word(j) not in {';', '<', '|'}:
                j += 1
            if all(NAME.fullmatch(word(k)) or word(k) == '_' for k in range(i + 1, j)):
                add_names(i + 1, j, None, j, end)
            else:
                diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
        elif keyword in {'obtain', 'rcases'}:
            end = scope_end(i)
            start = i + 1 if keyword == 'obtain' else top_find(i + 1, end, {'with'}) + 1
            if start < end and word(start) == '⟨' and start in matching:
                stop = matching[start]
                # Tuple patterns only; alternatives and constructor patterns need elaboration.
                if all(NAME.fullmatch(word(k)) or word(k) in {'_', ',', '⟨', '⟩'} for k in range(start + 1, stop)):
                    finish = stop + 1
                    while finish < end and tokens[finish].line == token.line and word(finish) != ';':
                        finish += 1
                    add_names(start + 1, stop, None, finish, end)
                else:
                    diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
            else:
                diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
                if start < end:
                    stop = top_find(start, end, {':='}) if keyword == 'obtain' else start
                    if keyword == 'rcases':
                        while stop < end and tokens[stop].line == token.line:
                            stop += 1
                    # Ambiguous pattern identifiers must not become guessed
                    # global edges while the syntax remains unsupported.
                    add_names(start, stop, None, stop, end)
        elif keyword in {'match', 'cases', 'case', 'induction'}:
            diagnostics.append(f'unsupported_pattern:{token.line}:{token.column}:{keyword}')
            end = scope_end(i)
            if keyword == 'case':
                arrow = top_find(i + 1, end, {'=>'})
                if arrow < end:
                    excluded.add(i + 1)  # Case label, not a declaration use.
                    add_names(i + 2, arrow, None, arrow + 1, end)
            else:
                with_at = top_find(i + 1, end, {'with'})
                branch = with_at + 1
                while branch < end:
                    if word(branch) != '|':
                        branch += 1
                        continue
                    arrow = top_find(branch + 1, end, {'=>'})
                    if arrow == end:
                        break
                    stop = top_find(arrow + 1, end, {'|'})
                    # Constructor-vs-variable patterns need elaboration. All
                    # possible bound names are suppressed, with the diagnostic
                    # above; this deliberately does not invent pattern edges.
                    add_names(branch + 1, arrow, None, arrow + 1, stop)
                    branch = stop
    return ScopeScan(tokens, tuple(bindings), frozenset(excluded), tuple(parameters), tuple(diagnostics))


@dataclass(frozen=True)
class SourceContext:
    opens: tuple[str | tuple[str, str], ...] = ()
    variables: tuple[tuple[str, str | None], ...] = ()
    lines: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    namespace: str = ''
    public: bool = True


def module_contexts(text):
    """One linear pass over commands, including multiline variable binders."""
    from lean_mcp_toolkit.backends.text_ast.comments import mask_comments_and_strings
    lines = mask_comments_and_strings(text).splitlines()
    contexts, imports = [], set()
    stack = [SourceContext()]
    scope_names = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        state = stack[-1]
        command = re.match(r'(?:(?:public|private)\s+)?import\s+(.+)', stripped)
        if command:
            imports.update(NAME.findall(command.group(1)))
        if stripped == 'module':
            stack[-1] = SourceContext(state.opens, state.variables, state.lines, state.aliases, state.namespace, False)
            state = stack[-1]
        scope_command = re.sub(r'^(?:@\[[^\[\]]*\]\s*)+', '', stripped)
        command = re.match(r'(?:(public|private)\s+)?(namespace|section)\b\s*(.*)', scope_command)
        if command:
            namespace = state.namespace
            if command.group(2) == 'namespace' and command.group(3):
                namespace = '.'.join(filter(None, (namespace, command.group(3))))
            public = command.group(1) == 'public' if command.group(1) else state.public
            stack.append(SourceContext(state.opens, state.variables, state.lines + (stripped,), state.aliases, namespace, public))
            scope_names.append(command.group(3).strip())
        elif re.match(r'end\b', stripped) and len(stack) > 1:
            closing = stripped[3:].strip()
            if not closing:
                stack.pop()
                scope_names.pop()
            else:
                # A compound `end Finset.Filter` closes both the namespace
                # and section; `namespace Meta.Tools` can also be one frame.
                for start in range(len(scope_names) - 1, -1, -1):
                    if '.'.join(scope_names[start:]) == closing:
                        del scope_names[start:]
                        del stack[start + 1:]
                        break
        elif re.match(r'open\s+', stripped) and not re.search(r'\bin\s*$', stripped):
            names = NAME.findall(stripped)[1:]
            if names and names[0] != 'scoped':
                # Simple namespace opens only; selected/renamed opens remain explicit diagnostics.
                stack[-1] = SourceContext(state.opens + tuple((state.namespace, n) for n in names), state.variables,
                                          state.lines + (stripped,), state.aliases, state.namespace, state.public)
        elif re.match(r'variables?\b', stripped):
            end = i + 1
            balance = sum(stripped.count(c) for c in '([{') - sum(stripped.count(c) for c in ')]}')
            while end < len(lines) and (balance > 0 or lines[end].lstrip().startswith(('(', '[', '{'))):
                balance += sum(lines[end].count(c) for c in '([{') - sum(lines[end].count(c) for c in ')]}')
                end += 1
            source = '\n'.join(lines[i:end])
            parameters = scan_scope(source, variables=True).parameters
            variables = dict(state.variables)
            variables.update((b.name, b.type_text) for b in parameters)
            stack[-1] = SourceContext(state.opens, tuple(variables.items()), state.lines + (source,), state.aliases, state.namespace, state.public)
            contexts.extend([stack[-1]] * (end - i))
            i = end
            continue
        command = re.match(r'alias\s+(' + NAME.pattern + r')\s*:=\s*(' + NAME.pattern + r')', stripped)
        if command:
            state = stack[-1]
            alias = command.group(1)
            if state.namespace:
                alias = state.namespace + '.' + alias
            stack[-1] = SourceContext(state.opens, state.variables, state.lines,
                                      state.aliases + ((alias, command.group(2)),), state.namespace, state.public)
        contexts.append(stack[-1])
        i += 1
    return tuple(contexts), frozenset(imports)
