"""Generate explicit-locale mathematical packages through the API workflow."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from lean_exposition.exposition import ContentStore, render, writing_view
from lean_exposition.exposition.content import atomic_json, digest
from lean_exposition.exposition.writing import mathematical_prompt
from lean_exposition.models import Workspace
from lean_exposition.runtime import ApiConfig, RuntimeFailure, StructuredExecutor


def load_credential_file(path):
    if path:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                key, value = line.removeprefix('export ').split('=', 1)
                os.environ[key.strip()] = value.strip().strip('\"\'')


def preflight(workspace, hierarchy, generation_strategy):
    """Source-only exact sizes; generated ancestor prose has a separately reported reserve."""
    with tempfile.TemporaryDirectory(prefix='math-preflight-') as temporary:
        store = ContentStore(workspace, hierarchy, Path(temporary) / 'content.json', locale='zh',
                             generation_strategy=generation_strategy)
        rows = []
        for node in hierarchy['nodes']:
            material = writing_view(workspace, hierarchy, node['id'], mathematical=True)
            prompt = mathematical_prompt('zh', material, {})
            rows.append({'node_id': node['id'], 'source_prompt_characters': len(prompt),
                         'card_count': len(material['cards']), 'proof_card_count': sum(bool(card.get('proof')) for card in material['cards']),
                         'relation_counts': {kind: len(material[kind]) for kind in ('incoming', 'outgoing', 'internal')},
                         'agent_material_characters': len(json.dumps(store._agent_material(node['id']), ensure_ascii=False)),
                         'api_source_over_budget': len(prompt) > store.max_input_characters,
                         'ancestor_and_preview_reserve': store.max_input_characters - len(prompt)})
        return {'nodes': rows, 'max_source_prompt_characters': max(row['source_prompt_characters'] for row in rows),
                'max_agent_material_characters': max(row['agent_material_characters'] for row in rows),
                'api_source_over_budget': [row['node_id'] for row in rows if row['api_source_over_budget']],
                'context_note': 'Exact source material only; runtime checks the complete prompt including actual generated ancestor boundaries and accepted prefix.'}


class RecordedRuntime:
    def __init__(self, config, directory):
        self.config, self.directory = config, directory
        self.executor = StructuredExecutor(config)
        self.directory.mkdir(parents=True, exist_ok=True)
        indices = [int(path.name.split('-')[1]) for path in directory.glob('call-*.json')]
        self.counter = max(indices, default=-1) + 1
        self.provenance_path = directory.parent / 'block_providers.json'
        self.provenance = json.loads(self.provenance_path.read_text()) if self.provenance_path.exists() else {}
        self.lock = threading.Lock()

    def execute(self, prompt, schema, *, trace_label=None):
        # One workflow call is one provider call. A later retry receives its own
        # call record and is never folded into the evidence for this result.
        with self.lock:
            index = self.counter
            self.counter += 1
        start = time.monotonic()
        atomic_json(self.directory / f'call-{index:04d}-request.json',
                    {'prompt': prompt, 'schema': schema, 'input_digest': digest({'prompt': prompt, 'schema': schema}),
                     'model': self.config.model, 'protocol': self.config.protocol,
                     'reasoning': self.config.reasoning, 'extra_body': self.config.extra_body,
                     'max_output_tokens': self.config.max_output_tokens,
                     'timeout': self.config.timeout, 'attempt': 0, 'trace_label': trace_label, 'tools': []})
        result = self.executor.execute(prompt, schema, trace_label=trace_label)
        atomic_json(self.directory / f'call-{index:04d}-result.json',
                    {**asdict(result), 'seconds': time.monotonic() - start})
        if result.status == 'succeeded' and trace_label and trace_label.startswith('eet.draft.'):
            node_id = trace_label.removeprefix('eet.draft.')
            with self.lock:
                self.provenance[node_id] = {'model': self.config.model, 'protocol': self.config.protocol,
                    'reasoning': self.config.reasoning, 'max_output_tokens': self.config.max_output_tokens,
                    'request': f'calls/call-{index:04d}-request.json',
                    'result': f'calls/call-{index:04d}-result.json',
                    'usage': asdict(result.usage)}
                atomic_json(self.provenance_path, self.provenance)
        return result

    def __call__(self, prompt, schema):
        result = self.execute(prompt, schema, trace_label='content.generate')
        if result.status == 'succeeded':
            return result.data
        if result.error and result.error.kind == 'length':
            raise RuntimeError('Provider output budget exhausted; raw result retained. Do not retry unchanged budget.')
        raise RuntimeFailure(result)


def completeness(store):
    manifest = store.manifest() if store.state['latest_manifest'] else {'blocks': {}, 'metadata': {}}
    missing = sorted(set(store.nodes) - set(manifest['blocks']))
    required_metadata = {node['id'] for node in store.nodes.values() if node['kind'] != 'unit'}
    terminals = {node['id'] for node in store.nodes.values() if node['kind'] == 'unit'}
    missing_titles = sorted(node for node in terminals if not manifest['metadata'].get(node, {}).get('title', '').strip())
    return {'instance_id': store.instance_id, 'structure_id': store.structure_id, 'locale': store.locale,
            'generation_strategy': store.generation_strategy,
            'content_digest': store.content_digest, 'repo_key': store.hierarchy['repo_key'],
            'hierarchy_id': store.hierarchy['hierarchy_id'],
            'workspace_digest': store.workspace.digest(),
            'structure_config_digest': store.hierarchy.get('config_digest'),
            'manifest_id': store.state['latest_manifest'], 'node_count': len(store.nodes),
            'block_count': len(manifest['blocks']), 'missing_nodes': missing,
            'missing_metadata': sorted(required_metadata - set(manifest['metadata'])),
            'terminal_titles': {'required': len(terminals), 'present': len(terminals) - len(missing_titles), 'missing': missing_titles},
            'raw_decl_count': len(store.nodes[store.hierarchy['root_id']]['decl_refs']),
            'complete': not missing and not (required_metadata - set(manifest['metadata'])) and not missing_titles}


def record_fixed_sources(store, recorded):
    """Account for honest source-missing entries that intentionally make no model call."""
    if not store.state['latest_manifest']:
        return
    for node_id in store.manifest()['blocks']:
        node = store.nodes[node_id]
        metadata = node.get('metadata', {})
        if metadata.get('technical') and metadata.get('source_missing') and store.kind(node_id) == 'content':
            recorded.provenance.setdefault(node_id, {'provider': 'fixed_source', 'model_call': False,
                'reason': 'source_missing', 'locale': store.locale, 'decl_refs': node['decl_refs']})
    atomic_json(recorded.provenance_path, recorded.provenance)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--hierarchy', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--locale', choices=['zh', 'en'], default='zh')
    parser.add_argument('--runtime-config')
    parser.add_argument('--credential-file')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--max-groups', type=int)
    parser.add_argument('--max-input-characters', type=int, default=360000)
    parser.add_argument('--generation-strategy', choices=('sequential', 'concurrent'), default='concurrent')
    args = parser.parse_args()
    workspace = Workspace.from_json(Path(args.workspace).read_text())
    hierarchy = json.loads(Path(args.hierarchy).read_text())
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.preflight:
        report = preflight(workspace, hierarchy, args.generation_strategy)
        atomic_json(output / 'preflight.json', report)
        print(json.dumps({key: value for key, value in report.items() if key != 'nodes'}))
        return
    if not args.runtime_config:
        parser.error('--runtime-config is required for generation')
    config = ApiConfig(**json.loads(Path(args.runtime_config).read_text()))
    load_credential_file(args.credential_file)
    for name, value in [('workspace.json', json.loads(workspace.to_json())), ('hierarchy.json', hierarchy)]:
        destination = output / name
        if destination.exists() and json.loads(destination.read_text()) != value:
            raise ValueError('Output package already belongs to different fixed inputs.')
        if not destination.exists():
            atomic_json(destination, value)
    recorded = RecordedRuntime(config, output / 'calls')
    store = ContentStore(workspace, hierarchy, output / 'content.json', recorded,
                         executor=recorded, locale=args.locale,
                         max_input_characters=args.max_input_characters,
                         generation_strategy=args.generation_strategy)
    groups = 0
    try:
        if not store.state['latest_manifest']:
            store.name_regions()
            if any(value['status'] != 'succeeded' for value in store.state['metadata_jobs'].values()):
                raise RuntimeError('Metadata generation incomplete; retry this package before publication.')
            store.generate_root()
            groups += 1
        queue = [hierarchy['root_id']]
        while queue:
            parent = queue.pop(0)
            children = store.nodes[parent]['children']
            queue.extend(child for child in children if store.nodes[child]['children'])
            if not children or all(child in store.manifest()['blocks'] for child in children):
                continue
            if args.max_groups is not None and groups >= args.max_groups:
                break
            store.generate_children(parent)
            groups += 1
            atomic_json(output / 'completion.json', completeness(store))
            print(json.dumps({'locale': args.locale, 'groups_this_run': groups,
                              'blocks': len(store.manifest()['blocks'])}), flush=True)
    finally:
        record_fixed_sources(store, recorded)
        atomic_json(output / 'completion.json', completeness(store))
        if store.state['latest_manifest']:
            for name, expanded in [('folded', set()), ('expanded', {node for node in store.nodes if store.nodes[node]['children'] and
                                                   all(child in store.manifest()['blocks'] for child in store.nodes[node]['children'])})]:
                (output / (name + '.md')).write_text(render(hierarchy, store.manifest(), expanded)['text'])
    print(json.dumps(completeness(store), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
