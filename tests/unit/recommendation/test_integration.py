import json
from pathlib import Path
import tempfile
import unittest
from lean_exposition.app.demo import demo_fixture
from lean_exposition.exposition import ContentStore
from lean_exposition.models import Workspace
from lean_exposition.reading import ReaderService
from lean_exposition.features import extract_features
from lean_exposition.recommendation import make_structural_policy


class IntegrationTests(unittest.TestCase):
    def test_real_reader_legacy_and_explicit_locale_structure_identity(self):
        fixture = demo_fixture()
        workspace = Workspace.from_json(json.dumps(fixture['workspace']))
        hierarchy = fixture['hierarchy']
        policy = make_structural_policy(workspace, hierarchy, extract_features(workspace, hierarchy))
        with tempfile.TemporaryDirectory() as directory:
            for locale in (None, 'en', 'zh'):
                store = ContentStore(workspace, hierarchy, Path(directory) / f'{locale}.json', locale=locale)
                blocks = json.loads(json.dumps(fixture['blocks']))
                if locale:
                    for block in blocks.values():
                        for part in ('lead_in', 'lead_out'):
                            if part in block and not block[part]: block[part] = 'The successor preserves the natural-number comparison.'
                store.publish(blocks)
                service = ReaderService([store], Path(directory) / f'{locale}-reader.json', recommendation_policy=policy)
                try:
                    opened = service.call('open_reader', {'instance_id': store.instance_id})
                    result = service.call('recommend', {'reader_id': opened['reader_id']})
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['policy_id'], 'structural')
                    self.assertEqual(result['recommendations'][0]['target_id'], 'root')
                    self.assertNotEqual(store.structure_id, hierarchy['hierarchy_id'])
                    if locale == 'zh': self.assertIn('预计成本', result['recommendations'][0]['reason'])
                finally: service.close()
