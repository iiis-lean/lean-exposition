"""Handwritten bilingual fixture for transport and browser verification."""
from copy import deepcopy
import json
from pathlib import Path

from lean_exposition.exposition import ContentStore
from lean_exposition.models import Workspace
from .demo import demo_fixture


def create_bilingual_demo(directory):
    directory = Path(directory)
    fixture = demo_fixture()
    workspace = Workspace.from_json(json.dumps(fixture['workspace']))
    english = deepcopy(fixture['blocks'])
    english['setup']['lead_in'] = 'For a natural number, consider its successor.'
    english['conclusion']['lead_out'] = 'Every natural number is smaller than its successor.'
    chinese = {
        'root': {'lead_in': '设 $n$ 为自然数。', 'synopsis': '后继函数把每个自然数映到一个严格更大的自然数。', 'lead_out': '因此，自然数的后继严格大于它自身。', 'anchors': []},
        'setup': {'lead_in': '对自然数定义后继函数。', 'synopsis': '定义 $f(n)=n+1$，并比较 $n$ 与 $f(n)$。', 'lead_out': '我们得到 $n\\le f(n)$。', 'anchors': []},
        'conclusion': {'lead_in': '现在考虑严格不等式。', 'synopsis': '加一后严格增大。', 'lead_out': '每个自然数都严格小于其后继。', 'anchors': []},
        'definition': {'content': '对 $n\\in\\mathbb{N}$，定义 $f(n)=n+1$。', 'anchors': []},
        'bound': {'statement': '对任意 $n\\in\\mathbb{N}$，有 $n\\le f(n)$。', 'proof': '由 $f(n)=n+1$ 以及 $0\\le 1$ 可得。', 'anchors': []},
        'result': {'statement': '对任意 $n\\in\\mathbb{N}$，有 $n<f(n)$。', 'proof': '由 $f(n)=n+1$ 以及 $0<1$ 可得。', 'anchors': []},
    }
    titles = {'root':'后继与严格增长','setup':'后继的定义与弱界','conclusion':'严格增长','definition':'后继函数','bound':'弱界','result':'严格界'}
    stores = []
    for locale, blocks in [('zh', chinese), ('en', english)]:
        store = ContentStore(workspace, fixture['hierarchy'], directory / f'content.{locale}.json', locale=locale)
        if not store.state['latest_manifest']:
            store.state['metadata'] = {node['id']: {'title': titles[node['id']] if locale == 'zh' else node['title'], 'short_description': ''} for node in fixture['hierarchy']['nodes']}
            store.publish(blocks)
        stores.append(store)
    return stores
