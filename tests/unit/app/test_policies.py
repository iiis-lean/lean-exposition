import json
from pathlib import Path
import tempfile
import unittest
from lean_exposition.app.bilingual_demo import create_bilingual_demo
from lean_exposition.app.policies import library_policy
from lean_exposition.features import extract_features
from lean_exposition.reading import ReaderService


class LibraryPolicyTests(unittest.TestCase):
    def test_bilingual_dispatch_and_rejects_mismatched_feature_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            stores=create_bilingual_demo(path)
            features=extract_features(stores[0].workspace,stores[0].hierarchy)
            file=path/'features.json';features.save(file)
            policy=library_policy(stores,{store.instance_id:file for store in stores})
            service=ReaderService(stores,path/'readers.json',recommendation_policy=policy)
            try:
                for store in stores:
                    opened=service.call('open_reader',{'instance_id':store.instance_id})
                    result=service.call('recommend',{'reader_id':opened['reader_id']})
                    self.assertTrue(result['ok'],result)
                    self.assertEqual(result['policy_id'],'structural')
                    self.assertEqual(result['locale'],store.locale)
                    compact=result['recommendations'][0]['components']
                    self.assertNotIn('unit_ids',compact['target'])
                    self.assertNotIn('children',compact['cost_basis'])
                    self.assertNotIn('consumer_contributions',compact['structural_gain'])
                    self.assertNotIn('config',compact)
                invalid=features.to_dict();invalid['config_digest']='wrong-config'
                file.write_text(json.dumps(invalid))
                with self.assertRaisesRegex(ValueError,'config digest'):
                    library_policy(stores,{stores[0].instance_id:file})
                invalid=features.to_dict();invalid['workspace_digest']='wrong-workspace'
                file.write_text(json.dumps(invalid))
                with self.assertRaisesRegex(ValueError,'fixed input'):
                    library_policy(stores,{stores[0].instance_id:file})
            finally:
                service.close()
