"""Ordinary Ask preserves useful answers without promoting them to approved inputs."""
import copy
import tempfile
import unittest

from mcdxkit.documents import DocumentWorkflow


class ChatValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.flow = DocumentWorkflow(self.tmp.name)
        self.pid = self.flow.create('Chat citation checks')['id']
        self.source = self.flow.add_source(self.pid, 'spec.pdf', b'%PDF-1.4\nsynthetic')
        with self.flow.edit(self.pid) as state:
            state['dataset_id'] = 'dataset'
            state['sources'][0].update(status='ready', document_id='d1')

    def ask(self, payload, rows=None):
        if rows is None:
            rows = [{'document_id': 'd1', 'content': 'New walls: 2 inches.', 'positions': [[7, 0, 1, 0, 1]]}]

        class Retrieval:
            def retrieve(self, dataset, ids, question):
                return rows

        class Agent:
            model = 'test:free'

            def answer(self, context, search, **kwargs):
                search(context['request'])
                return copy.deepcopy(payload)

            def verify(self, statements, evidence):
                raise AssertionError('Ordinary chat must not make a second model verification call')

        return self.flow.ask(self.pid, 'Compare requirements in a table; list missing information.', Retrieval(), agent=Agent())

    def payload(self, body='New walls: 2 inches [E1].'):
        return {'status': 'supported', 'assessment': 'The retrieved clause states the limit.',
                'sections': [{'heading': 'Requirements', 'body': body, 'evidence_ids': ['E1']}],
                'gaps': ['The existing-wall limit was not found in the retrieved passages.'], 'conflicts': []}

    def test_long_table_and_open_questions_survive_without_a_statement_gate(self):
        # More than 40 assertions, but a small valid Markdown report. Counting sentence fragments
        # must not reject or dismantle its table. No verifier response is available.
        table = '| Element | Requirement |\n| --- | --- |\n' + '\n'.join(
            '| New walls | The limit is 2 inches. It applies to new walls. '
            'Existing-wall limits remain unknown. Keep the scope. Consult the source. [E1] |' for _ in range(10))
        payload = self.payload(table)
        payload['sections'].append({'heading': 'Open questions', 'body': '- Which existing-wall clause applies?', 'evidence_ids': ['E1']})
        result = self.ask(payload)
        self.assertEqual(result['content']['sections'], payload['sections'])
        self.assertIn(payload['gaps'][0], result['content']['gaps'])
        self.assertEqual(result['grounding']['status'], 'citations_checked')
        self.assertNotIn('statements', result['grounding'])
        self.assertEqual(result['evidence'][0]['source_id'], self.source['id'])
        self.assertEqual(result['evidence'][0]['positions'][0][0], 7)
        self.assertEqual(result['content']['suggestions'], [])
        self.assertEqual(result['extraction_ids'], [])

    def test_literal_number_warning_does_not_delete_chat_or_approve_a_value(self):
        payload = self.payload('New walls: 5 inches [E1].')
        result = self.ask(payload)
        self.assertEqual(result['content']['sections'][0]['body'], payload['sections'][0]['body'])
        self.assertEqual(result['audit']['unsupported_numbers'], [{'heading': 'Requirements', 'values': ['5']}])
        self.assertIsNone(result['review'])
        self.assertEqual(result['content']['suggestions'], [])
        self.assertEqual(result['extraction_ids'], [])

    def test_foreign_citations_are_rejected_in_every_displayed_field(self):
        for field in ('body', 'heading', 'assessment', 'gaps', 'conflicts'):
            with self.subTest(field=field):
                payload = self.payload()
                if field in ('body', 'heading'):
                    payload['sections'][0][field] += ' [E999]'
                elif field in ('gaps', 'conflicts'):
                    payload[field] = ['Missing provision [E999]']
                else:
                    payload[field] += ' [E999]'
                with self.assertRaisesRegex(ValueError, 'citation|evidence|invalid answer'):
                    self.ask(payload)
        self.assertEqual(self.flow.get(self.pid)['answers'], [])

    def test_abstention_keeps_the_specific_missing_source_and_never_verifies(self):
        result = self.ask({'status': 'insufficient', 'assessment': 'The addendum was not found.',
                           'sections': [], 'gaps': ['Upload the signed addendum.'], 'conflicts': []}, rows=[])
        self.assertEqual(result['grounding']['status'], 'abstained')
        self.assertEqual(result['content']['sections'], [])
        self.assertIn('Upload the signed addendum.', result['content']['gaps'])

    def test_model_memory_cannot_supply_citations_when_retrieval_is_empty(self):
        with self.assertRaisesRegex(ValueError, 'evidence|citation'):
            self.ask(self.payload(), rows=[])

    def test_conflicting_sources_and_question_format_are_preserved(self):
        payload = self.payload('The retrieved passages disagree [E1].')
        payload.update(status='conflicted', conflicts=['The scope of the clauses needs clarification [E1].'])
        result = self.ask(payload)
        self.assertEqual(result['assessment']['status'], 'conflicted')
        self.assertEqual(result['assessment']['conflicts'], payload['conflicts'])

    def test_receipt_records_a_source_used_only_in_an_open_question(self):
        payload = self.payload()
        payload['gaps'] = ['Does the referenced addendum supersede the limit? [E2]']
        result = self.ask(payload, rows=[
            {'document_id': 'd1', 'content': 'New walls: 2 inches.', 'positions': [[7, 0, 1, 0, 1]]},
            {'document_id': 'd1', 'content': 'Refer to the addendum.', 'positions': [[8, 0, 1, 0, 1]]}])
        receipt = self.flow.answer_audit(self.pid, result['id'])
        self.assertEqual(result['audit']['cited'], ['E1', 'E2'])
        self.assertTrue(next(e for e in receipt['evidence'] if e['id'] == 'E2')['cited'])
        self.assertEqual(receipt['grounding']['status'], 'citations_checked')
        self.assertEqual(receipt['grounding']['statements'], [])

