import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_writing as fixtures
from lean_exposition.exposition.writing import mathematical_prompt


class WritingQualityTests(unittest.TestCase):
    setUp = fixtures.WritingTests.setUp
    make = fixtures.WritingTests.make

    def test_hygiene_is_hard_budget_is_soft_and_anchors_remain_structured(self):
        job = self.store.create_writing_job()
        for text in ['unit:abc', '/root/source.lean', 'scope-123', 'bad\x08eta']:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'identifier|control'):
                self.store.submit_draft(job, {**self.fixture['blocks']['root'], 'synopsis': text})
        draft = self.store.submit_draft(job, {**self.fixture['blocks']['root'], 'synopsis': '短概要',
            'anchors': [{'part': 'synopsis', 'targets': [{'node_id': 'root'}]}]})
        self.assertTrue(draft['diagnostics']['warnings'])
        self.assertEqual(self.store.get_step(job)['draft']['synopsis'], '短概要')

    def test_locale_examples_are_complete_and_pitfalls_are_generic(self):
        for locale, before, after in [('en', 'Before expansion:', 'After expansion'), ('zh', '展开前：', '展开后')]:
            prompt = mathematical_prompt(locale, {}, {})
            self.assertIn(before, prompt)
            self.assertIn(after, prompt)
            self.assertIn('floor division', prompt)
            self.assertIn('coarsening', prompt)
            self.assertIn('preserve every field, conjunct, quantifier, equivalence clause and side condition', prompt)
            self.assertIn('Follow the definition direction and constructor order supplied by the source', prompt)
            self.assertIn('unless the source explicitly states that equivalence', prompt)
            self.assertIn('refer to the named mathematical object instead of presenting a simplified definition', prompt)
            self.assertIn('Do not infer stronger algebraic, order-theoretic or structural properties', prompt)
            self.assertNotIn('binomial weight', prompt)
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            self.assertEqual(payload['locale'], locale)
            self.assertNotIn('prompt_version', payload)

    def test_fresh_campaign_all_groups_use_agent_and_refuses_second_run(self):
        from lean_exposition.runtime.agents import AgentResult, CodexAgentConfig
        scripts = Path(__file__).resolve().parents[3] / 'scripts'
        sys.path.insert(0, str(scripts))
        import math_reader_agent_generate as driver

        config = CodexAgentConfig(model='gpt-5.6-sol', reasoning='high',
            codex_home='/unused', cwd='/unused', auth_path=__file__)
        output = Path(self.tmp.name) / 'campaign'
        seen = []
        class FakeExecutor:
            def __init__(inner, bound):
                self.assertNotEqual(bound.codex_home, config.codex_home)
            def start(inner, prompt, schema):
                return None
            def result(inner, handle, timeout=None):
                return AgentResult(status='succeeded', data={'title': 'Finite sets', 'short_description': 'Counting finite sets.', 'evidence_refs': []})
        def fake_agent(store, job, factory, *, trace, record_request):
            seen.append(store._job(job)['parent_id'])
            while store.get_step(job)['status'] == 'active':
                step = store.get_step(job)
                payload = dict(self.fixture['blocks'][step['node_id']])
                if store.kind(step['node_id']) != 'section':
                    payload['title'] = 'Finite sets'
                draft = store.submit_draft(job, payload)
                store.accept_draft(job, draft['draft_id'])
            trace.append({'tool': 'fixture', 'result': 'published'})
            return {'published': True, 'result': AgentResult(status='succeeded'), 'trace': trace}
        with patch.object(driver, 'CodexAgentExecutor', FakeExecutor), patch.object(driver, 'run_agent_job', fake_agent):
            result = driver.campaign(self.workspace, self.fixture['hierarchy'], output, 'en', config)
            self.assertTrue(result['complete'])
            self.assertEqual(seen, list(driver.ordered_groups(self.fixture['hierarchy'])))
            before = (output / 'content.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                driver.campaign(self.workspace, self.fixture['hierarchy'], output, 'en', config)
            self.assertEqual(before, (output / 'content.json').read_bytes())
        self.assertEqual(json.loads((output / 'campaign.json').read_text())['status'], 'completed')
        with self.assertRaisesRegex(ValueError, 'Flash'):
            driver.validate_config(CodexAgentConfig(model='deepseek-pro', cwd='/tmp', auth_path=__file__))
