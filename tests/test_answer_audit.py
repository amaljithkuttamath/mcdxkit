"""Audit receipts must preserve historical evidence and enforce project ownership."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from mcdxkit.documents import DocumentWorkflow


class AnswerAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.flow = DocumentWorkflow(Path(self.directory.name))
        self.pid = self.flow.create('Synthetic audit project')['id']
        self.source = self.flow.add_source(self.pid, 'Requirements.pdf', b'%PDF-1.4\nSynthetic audit content')
        self.aid = 'a' * 32
        row = {'id': 'E1', 'source_id': self.source['id'], 'source_name': self.source['name'],
               'source_sha256': self.source['sha256'], 'text': 'New walls: settlement not above 2 inches.', 'positions': [[7, 0, 0, 0, 0]]}
        self.answer = {'id': self.aid, 'created': 10, 'revision': 1, 'conversation_id': self.aid, 'parent_id': None,
                       'request': 'What is the limit?', 'kind': 'research', 'model': 'vendor/model:free',
                       'agent_version': 'knowledge/6', 'review': None,
                       'content': {'sections': [{'heading': 'Limit', 'body': '2 inches [E1].', 'evidence_ids': ['E1']}],
                                   'suggestions': [], 'gaps': ['Engineering review is outstanding.']},
                       'assessment': {'status': 'supported', 'explanation': 'Model answer', 'conflicts': []},
                       'evidence': [row], 'searches': [{'query': 'wall limit', 'passages': 1}],
                       'coverage': {'ready': 1, 'total': 1, 'excluded': []}, 'context_reads': []}
        with self.flow.edit(self.pid) as state:
            self.answer['revision'] = state['revision']
            state['answers'].append(copy.deepcopy(self.answer))

    def test_receipt_checksum_covers_answer_evidence_and_scope(self):
        before = self.flow.get(self.pid)
        receipt = self.flow.answer_audit(self.pid, self.aid)
        expected = receipt.pop('digest')['value']
        raw = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        self.assertEqual(receipt['answer']['id'], self.aid)
        self.assertEqual(receipt['evidence'][0]['source']['saved_sha256'], self.source['sha256'])
        self.assertEqual(receipt['evidence'][0]['locator']['pages'], [7])
        self.assertEqual(receipt['grounding']['status'], 'unrecorded')
        self.assertEqual(self.flow.get(self.pid), before, 'Read-only export must not mutate the project')

    def test_missing_or_foreign_answers_are_rejected(self):
        other = self.flow.create('Other project')['id']
        for pid, aid in [(other, self.aid), (self.pid, 'b' * 32), (self.pid, '../secret')]:
            with self.subTest(pid=pid, aid=aid), self.assertRaises(ValueError):
                self.flow.answer_audit(pid, aid)

    def test_changed_missing_or_symlinked_originals_are_explicit(self):
        original = self.flow.root / self.pid / 'sources' / (self.source['id'] + '.pdf')
        for action, status in [('change', 'changed'), ('remove', 'missing'), ('symlink', 'missing')]:
            if action == 'change': original.write_bytes(b'%PDF-1.4\nChanged')
            elif action == 'remove': original.unlink()
            else: original.symlink_to(Path(self.directory.name) / 'does-not-exist.pdf')
            with self.subTest(action=action):
                receipt = self.flow.answer_audit(self.pid, self.aid)
                self.assertEqual(receipt['source_integrity'][0]['status'], status)
                self.assertEqual(receipt['evidence'][0]['source']['saved_sha256'], self.source['sha256'])

    def test_duplicate_text_is_reported_without_losing_original_evidence_ids(self):
        with self.flow.edit(self.pid) as state:
            duplicate = {**state['answers'][0]['evidence'][0], 'id': 'E2'}
            state['answers'][0]['evidence'].append(duplicate)
        receipt = self.flow.answer_audit(self.pid, self.aid)
        self.assertEqual(receipt['evidence_summary']['identical_text_groups'], [['E1', 'E2']])
        self.assertEqual(receipt['evidence_summary']['cited'], 1)
        self.assertEqual([row['id'] for row in receipt['evidence']], ['E1', 'E2'])

    def test_unknown_metadata_and_server_paths_are_not_exported(self):
        canary = 'synthetic-secret-must-not-export'
        with self.flow.edit(self.pid) as state:
            state['credentials'] = {'api_key': canary}
            answer = state['answers'][0]
            answer['api_key'] = canary
            answer['local_path'] = str(self.flow.root)
            answer['searches'][0]['api_key'] = canary
            answer['coverage']['api_key'] = canary
            answer['evidence'][0]['service_key'] = canary
        exported = json.dumps(self.flow.answer_audit(self.pid, self.aid))
        self.assertNotIn(canary, exported)
        self.assertNotIn(str(self.flow.root), exported)

    def test_historical_hash_mismatch_is_distinct_from_disk_corruption(self):
        with self.flow.edit(self.pid) as state:
            state['answers'][0]['evidence'][0]['source_sha256'] = 'b' * 64
        receipt = self.flow.answer_audit(self.pid, self.aid)
        entry = receipt['source_integrity'][0]
        self.assertEqual(entry['status'], 'match')
        self.assertEqual(entry['answer_hash_status'], 'differs')
        self.assertEqual(entry['answer_sha256'], ['b' * 64])

    def test_incomplete_historical_hashes_are_not_presented_as_fully_matching(self):
        with self.flow.edit(self.pid) as state:
            row = {**state['answers'][0]['evidence'][0], 'id': 'E2'}
            row.pop('source_sha256')
            state['answers'][0]['evidence'].append(row)
        entry = self.flow.answer_audit(self.pid, self.aid)['source_integrity'][0]
        self.assertEqual(entry['status'], 'match')
        self.assertNotEqual(entry['answer_hash_status'], 'match')

    def test_fractional_or_nonpositive_pages_are_not_invented_and_inline_citations_count(self):
        with self.flow.edit(self.pid) as state:
            answer = state['answers'][0]
            answer['content']['sections'][0]['evidence_ids'] = []
            answer['evidence'][0]['positions'] = [[2.5], [True], [0], [-1], [9.0]]
        receipt = self.flow.answer_audit(self.pid, self.aid)
        self.assertEqual(receipt['evidence'][0]['locator']['pages'], [9])
        self.assertTrue(receipt['evidence'][0]['cited'])


    def test_reason_code_whitelist_rejects_untrusted_types_and_unknown_text(self):
        for value in ['invalid_verifier_response', 'synthetic-secret', {'private': 'synthetic-secret'},
                      ['synthetic-secret'], None, 123]:
            with self.subTest(value=value):
                with self.flow.edit(self.pid) as state:
                    state['answers'][0]['grounding'] = {'version': 'grounding/4', 'status': 'withheld',
                                                       'reason_code': value, 'draft_withheld': True}
                receipt = self.flow.answer_audit(self.pid, self.aid)
                self.assertEqual(receipt['grounding']['reason_code'],
                                 value if value == 'invalid_verifier_response' else None)
                self.assertNotIn('synthetic-secret', json.dumps(receipt))


if __name__ == '__main__': unittest.main()
