"""Narrow, cached access to Toolkit's source parser; no Lean process is started."""
from dataclasses import asdict
import hashlib
import inspect
import json
import tempfile
from pathlib import Path


def source_response(text: str, module: str, *, cache_dir=None) -> dict:
    from lean_mcp_toolkit.backends.text_ast.declarations import parse_declarations
    # Include the whole parser package: comments and namespace rules affect output too.
    package = Path(inspect.getfile(parse_declarations)).parent
    implementation = b''.join(p.name.encode() + p.read_bytes() for p in sorted(package.glob('*.py')))
    key = hashlib.sha256(implementation + Path(__file__).read_bytes() + module.encode() + text.encode()).hexdigest()
    path = Path(cache_dir) / 'source' / (key + '.json') if cache_dir else None
    if path and path.exists():
        return json.loads(path.read_text())
    parsed = parse_declarations(text=text, module_dot=module)
    declarations = []
    for decl in parsed.declarations:
        raw = asdict(decl)
        raw.pop('short_name')
        declarations.append(raw)
    coverage = asdict(parsed.coverage)
    coverage['unrecognized_commands'] = list(coverage['unrecognized_commands'])
    coverage.update(backend='text_ast', classification_ratio=parsed.coverage.classification_ratio)
    result = dict(success=True, error_message=None, total_declarations=len(declarations),
                  declarations=declarations, source_diagnostics=coverage)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            json.dump(result, handle, ensure_ascii=False)
            temporary = Path(handle.name)
        temporary.replace(path)
    return result


def source_authors(text: str, module: str, *, cache_dir=None) -> dict:
    """Normalize source slices to the native normalizer's author-range contract."""
    response = source_response(text, module, cache_dir=cache_dir)
    lines = text.splitlines(keepends=True)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    def span(start, end):
        return {'start': start, 'finish': end}

    def position(offset):
        import bisect
        line = max(0, bisect.bisect_right(offsets, offset) - 1)
        return {'line': line + 1, 'column': offset - offsets[line]}

    authors = []
    for raw in response['declarations']:
        start, end = raw['decl_start_pos'], raw['decl_end_pos']
        modifiers = {'visibility': 'private' if 'private' in (raw['full_declaration'] or '').split()[:4] else 'regular'}
        if raw['docstring']:
            modifiers['docString'] = {'content': raw['docstring'],
                                     'range': span(raw['doc_start_pos'], raw['doc_end_pos'])}
        value = None
        if raw['value'] is not None:
            begin = offsets[start['line'] - 1] + start['column']
            finish = offsets[end['line'] - 1] + end['column']
            # RHS is a suffix of the exact declaration, never a search across files.
            body = raw['value']
            local = text[begin:finish].rfind(body)
            if local >= 0:
                value = {'range': span(position(begin + local), position(begin + local + len(body)))}
        authors.append(dict(name=raw['name'].rsplit('.', 1)[-1], fullName=raw['name'],
                            kind=raw['kind'], range=span(start, end), modifiers=modifiers,
                            scope={'currNamespace': raw['name'].rsplit('.', 1)[0] if '.' in raw['name'] else ''},
                            value=value))
    return {'declarations': authors, 'source_diagnostics': response['source_diagnostics']}
