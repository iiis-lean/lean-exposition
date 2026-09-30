import json
import unittest
from copy import deepcopy

from test_workflows import FakeStructured, FakeTools, TERMINAL_SCHEMA, SECTION_SCHEMA
from lean_exposition.workflows import (FeatureAnnotationWorkflow, SourceOrderEvaluationWorkflow,
    DownstreamTaskWorkflow, reader_workflow, EetWorkflow, EetDraftRequest, NamingWorkflow)


class ContractTests(unittest.TestCase):
    def test_annotation_rejects_unknown_duplicate_missing_labels_and_evidence(self):
        valid = {'labels': [{'item_id': 'x', 'label': 'high', 'confidence': .8, 'evidence': ['source']}]}
        invalid = []
        for key, value in [('item_id', 'unknown'), ('label', 'invented'), ('evidence', ['unknown'])]:
            changed = deepcopy(valid); changed['labels'][0][key] = value; invalid.append(changed)
        invalid += [{'labels': []}, {'labels': valid['labels'] * 2}]
        for data in invalid:
            with self.subTest(data=data):
                result = FeatureAnnotationWorkflow({'one': FakeStructured(lambda *_: data)}).annotate(
                    rubric={'labels': ['high', 'low']}, items=[{'item_id': 'x', 'source': 'fixed fact'}])
                self.assertEqual(result.annotations['one'].execution.error.kind, 'output_contract')

    def test_adjudication_sees_original_items_and_agreement_is_computed(self):
        def annotator(label):
            return FakeStructured(lambda *_: {'labels': [{'item_id': 'x', 'label': label, 'confidence': .7, 'evidence': ['source']}]})
        judge = FakeStructured(lambda *_: {'decisions': [{'item_id': 'x', 'label': 'high', 'needs_human_review': True, 'reason': 'Ambiguous.'}]})
        items = [{'item_id': 'x', 'source': 'actual fact'}]
        result = FeatureAnnotationWorkflow({'a': annotator('high'), 'b': annotator('low')}, adjudicator=judge).annotate(
            rubric={'labels': ['high', 'low']}, items=items)
        self.assertEqual(result.adjudication.execution.data['decisions'][0]['agreement'], .5)
        payload = json.loads(judge.calls[0][0].split('\n\nINPUT\n')[1])
        self.assertEqual(payload['items'], items)
        self.assertNotIn('agreement', judge.calls[0][1]['properties']['decisions']['items']['properties'])

    def test_evaluation_rejects_unmatched_ids_scores_answers_and_citations(self):
        for data in ({'preferred_candidate': 'bad', 'scores': [], 'reason': 'x'},
                     {'preferred_candidate': 'a', 'scores': [], 'reason': 'x'}):
            call = SourceOrderEvaluationWorkflow(FakeStructured(lambda *_: data)).blind_review(candidates=[{'candidate': 'a'}], rubric={})
            self.assertEqual(call.execution.status, 'failed')
        for answers, refs in (([], ['r']), ([' '], ['r']), (['yes'], ['bad'])):
            call = SourceOrderEvaluationWorkflow(FakeStructured(lambda *_: {'answers': answers, 'confidence': .8, 'evidence_refs': refs})).reading_comparison(
                condition={'text': 'evidence', 'evidence_refs': ['r']}, questions=['q'])
            self.assertEqual(call.execution.status, 'failed')

    def test_empty_allowlist_stays_empty_and_protocol_reaches_executor(self):
        bound = reader_workflow(FakeTools(), object(), allowed_tools=[])
        self.assertEqual(bound.tools, ())
        self.assertEqual(bound.handlers, {})
        with self.assertRaisesRegex(ValueError, 'unsupported protocol'):
            DownstreamTaskWorkflow(FakeTools(), tools=[], handlers={}).run_experiment(
                protocol={'imaginary_budget': 1}, task={}, output_schema={})

    def test_sequential_order_is_bound_by_node_id_and_cancellation_stops_next(self):
        seen = []
        def response(prompt, schema, stage):
            node = stage.removeprefix('eet.draft.')
            seen.append(node)
            return {'title': node, 'content': node, 'anchors': []} if 'title' in schema['properties'] else {'accepted': True, 'issues': []}
        workflow = EetWorkflow(FakeStructured(response))
        requests = [EetDraftRequest(n, 'en', {}, {}, TERMINAL_SCHEMA) for n in ('b', 'a')]
        result = workflow.generate_group(requests, ordered_node_ids=['a', 'b'], strategy='sequential')
        self.assertTrue(result.succeeded)
        self.assertEqual([(key, data['title']) for key, data in result.final_drafts], [('a', 'a'), ('b', 'b')])
        seen.clear()
        result = workflow.generate_group(requests, ordered_node_ids=['a', 'b'], strategy='sequential', cancelled=lambda: bool(seen))
        self.assertTrue(result.cancelled)
        self.assertEqual(seen, ['a'])

    def test_review_receives_exact_source_and_rejects_unknown_issue_node(self):
        executor = FakeStructured(lambda *_: {'accepted': False, 'issues': [{'node_id': 'bad', 'category': 'mathematical', 'message': 'x'}]})
        result = EetWorkflow(executor).validate(locale='en', ordered_node_ids=['a'], drafts=[], stitching={}, source_materials={'a': {'proof': 'exact'}})
        self.assertEqual(result.execution.error.kind, 'output_contract')
        self.assertIn('"proof":"exact"', executor.calls[0][0])

    def test_terminal_continuity_gets_only_one_targeted_repair(self):
        reviews = []
        def response(prompt, schema, stage):
            if stage == 'eet.validate':
                reviews.append(stage)
                return {'accepted': len(reviews) > 1, 'issues': [] if len(reviews) > 1 else [
                    {'node_id': 'b', 'category': 'continuity', 'message': 'Use the preceding symbol n.'}]}
            return {'title': 'Count', 'content': 'A finite count.', 'anchors': []}
        executor = FakeStructured(response)
        result = EetWorkflow(executor).generate_group([EetDraftRequest(n, 'en', {}, {}, TERMINAL_SCHEMA) for n in ('a', 'b')])
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.repairs), 1)
        self.assertEqual(len(reviews), 2)
        self.assertIn('targeted_revision', executor.calls[-2][0])

    def test_naming_budget_and_hygiene_are_local_gates(self):
        executor = FakeStructured()
        result = NamingWorkflow(executor, locale='en').name(kind='scope', material={}, max_input_characters=1)
        self.assertEqual(result.execution.error.kind, 'input_budget')
        self.assertEqual(executor.calls, [])
        bad = FakeStructured(lambda *_: {'title': 'scope:private', 'short_description': 'A role', 'evidence_refs': []})
        self.assertEqual(NamingWorkflow(bad, locale='en').name(kind='scope', material={}).execution.status, 'failed')
