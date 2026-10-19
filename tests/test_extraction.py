"""Requirements retain qualifications and source identity before any input can change."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcdxkit.extraction import validate_extraction, parse_pages
from mcdxkit.documents import DocumentWorkflow
from test_pipeline import report, template


PAGES = [{'page': 1, 'text': 'For new walls, settlement shall not exceed 2 inches over 50 years. Calculate both short-term and long-term settlement.'}]
FACT = {'subject': 'New walls', 'property': 'Settlement limit', 'value_text': '2 inches', 'unit': 'in',
        'evidence': [{'page': 1, 'quote': PAGES[0]['text']}],
        'conditions': [{'text': 'New walls over 50 years; calculate both short-term and long-term settlement.',
                        'page': 1, 'quote': PAGES[0]['text']}]}


class ExtractionTests(unittest.TestCase):
    def test_concurrent_extraction_claim_prevents_a_second_provider_call(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        with tempfile.TemporaryDirectory() as temporary:
            flow = DocumentWorkflow(temporary); pid = flow.create('Concurrency')['id']
            sid = flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nfixture')['id']
            started, release = Event(), Event()
            class Extractor:
                model = 'test/extractor'
                calls = 0
                def extract(self, context):
                    self.calls += 1; started.set(); release.wait(3)
                    return {'facts': [FACT], 'gaps': []}, {}
            extractor = Extractor()
            with patch('mcdxkit.extraction.read_pages', return_value=PAGES), ThreadPoolExecutor(2) as pool:
                pending = pool.submit(flow.extract, pid, sid, '1', 'Limits?', extractor)
                self.assertTrue(started.wait(2))
                try:
                    with self.assertRaisesRegex(ValueError, 'already|uncertain'):
                        DocumentWorkflow(temporary).extract(pid, sid, '1', 'Limits?', extractor)
                finally: release.set()
                saved = pending.result()
                self.assertEqual(flow.extract(pid, sid, '1', 'Limits?', extractor)['id'], saved['id'])
            self.assertEqual(extractor.calls, 1)

    def test_interrupted_extraction_is_not_automatically_charged_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            flow = DocumentWorkflow(temporary); pid = flow.create('Interrupted')['id']
            sid = flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nfixture')['id']
            class Extractor:
                model = 'test/extractor'
                calls = 0
                def extract(self, context):
                    self.calls += 1
                    raise KeyboardInterrupt()
            extractor = Extractor()
            with patch('mcdxkit.extraction.read_pages', return_value=PAGES):
                with self.assertRaises(KeyboardInterrupt): flow.extract(pid, sid, '1', 'Limits?', extractor)
                with self.assertRaisesRegex(ValueError, 'already|uncertain'):
                    flow.extract(pid, sid, '1', 'Limits?', extractor)
            self.assertEqual(extractor.calls, 1)

    def test_bounded_page_selection(self):
        self.assertEqual(parse_pages('1, 3-4, 3'), [1, 3, 4])
        for value in ('0', '-1', '2-1', '1-90000', 'a', '', [True]):
            with self.subTest(value=value), self.assertRaises(ValueError): parse_pages(value)

    def test_condition_and_value_evidence_must_match_selected_pages(self):
        result = validate_extraction({'facts': [FACT], 'gaps': []}, PAGES)
        self.assertIn('short-term', result['facts'][0]['conditions'][0]['text'])
        for target in ('page', 'quote', 'value', 'condition'):
            bad = copy.deepcopy(FACT)
            if target == 'page': bad['evidence'][0]['page'] = 2
            if target == 'quote': bad['evidence'][0]['quote'] = 'Fabricated limit'
            if target == 'value': bad['value_text'] = '3 inches'
            if target == 'condition': bad['conditions'][0]['quote'] = 'Do not calculate short-term settlement'
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_extraction({'facts': [bad], 'gaps': []}, PAGES)

    def test_review_and_binding_require_current_evidence_and_explicit_applicability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); flow = DocumentWorkflow(root/'library')
            raw = root/'raw.txt'; raw.write_text(report()); model = root/'model.mcdx'; template(model)
            pid = flow.create('Test project', raw, model)['id']
            source = flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nsynthetic')
            class Extractor:
                model = 'test/extractor'
                def extract(self, context): return {'facts': [FACT], 'gaps': []}, {'provider': 'test'}
            with patch('mcdxkit.extraction.read_pages', return_value=PAGES):
                run = flow.extract(pid, source['id'], '1', 'Find settlement limits', extractor=Extractor())
                cached = flow.extract(pid, source['id'], '1', 'Find settlement limits', extractor=Extractor())
            self.assertEqual(run['id'], cached['id'])
            requirement = run['facts'][0]
            with self.assertRaisesRegex(ValueError, 'review'):
                flow.requirement_draft(pid, [requirement['id']], [], 'Reviewer', 'Inspect source', 'Applies to these walls')
            flow.review_requirement(pid, requirement['id'], 'reviewed', 'Reviewer', 'Confirmed conditions')
            with self.assertRaises(ValueError):
                flow.requirement_draft(pid, [requirement['id']], [], 'Reviewer', 'Review', '')
            with self.assertRaisesRegex(ValueError, 'unit'):
                flow.requirement_draft(pid, [requirement['id']], [{'requirement': requirement['id'], 'variable': 'P_downdrag', 'value': 2, 'unit': 'in'}], 'Reviewer', 'Review', 'Applicable')
            with self.assertRaisesRegex(ValueError, 'unit'):
                flow.requirement_draft(pid, [requirement['id']], [{'requirement': requirement['id'], 'variable': 'P_downdrag', 'value': 2, 'unit': 'kip'}], 'Reviewer', 'Review', 'Applicable')
            draft = flow.requirement_draft(pid, [requirement['id']], [], 'Reviewer', 'Basis only; retain inputs', 'Demonstration scope')
            self.assertEqual(draft['content']['suggestions'], [])
            self.assertIn('short-term', draft['content']['sections'][0]['body'])
            self.assertEqual(draft['requirement_decisions'][0]['id'], requirement['id'])
            flow.review_requirement(pid, requirement['id'], 'rejected', 'Reviewer', 'Wrong applicability')
            with self.assertRaisesRegex(ValueError, 'changed'):
                flow.build(pid, draft['id'])

    def test_schema_rejects_extra_fields_and_empty_evidence(self):
        for mutate in (lambda f: f.update(script='alert(1)'), lambda f: f.update(evidence=[])):
            bad = copy.deepcopy(FACT); mutate(bad)
            with self.assertRaises(ValueError): validate_extraction({'facts': [bad], 'gaps': []}, PAGES)

    def test_scope_is_resolved_from_retrieved_citations_never_model_page_numbers(self):
        from mcdxkit.extraction import resolve_scope
        evidence = [{'id': 'E1', 'source_id': 'a', 'positions': [[131,0,0,0,0]]},
                    {'id': 'E2', 'source_id': 'a', 'positions': [[132,0,0,0,0]]},
                    {'id': 'E3', 'source_id': 'b', 'positions': [[8,0,0,0,0]]},
                    {'id': 'E4', 'source_id': 'a', 'positions': []}]
        self.assertEqual(resolve_scope(evidence, ['E1', 'E2']), ('a', [131,132]))
        for ids in (['E9'], ['E1','E3'], ['E4'], []):
            with self.subTest(ids=ids), self.assertRaises(ValueError): resolve_scope(evidence, ids)
