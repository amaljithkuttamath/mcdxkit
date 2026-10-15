"""Independent adversarial publication checks using synthetic specification text."""
import json
import unittest

from mcdxkit.knowledge import GroundingReport, grounding_gate


SOURCE = 'For new retaining walls, total settlement shall not exceed 2 inches over 50 years.'
COVERAGE = {'ready': 1, 'total': 1, 'excluded': []}


class GroundingContract(unittest.TestCase):
    def run_gate(self, body='Total settlement shall not exceed 2 inches over 50 years for new retaining walls [E1].',
                 source=SOURCE, verifier='supported', **overrides):
        section = {'heading': overrides.pop('heading', 'Limit'), 'body': body, 'evidence_ids': ['E1']}
        payload = {'kind': 'research', 'status': 'supported', 'assessment': '', 'conflicts': [], 'gaps': [], **overrides}
        rows = [{'id': 'E1', 'source_id': 's', 'source_name': 'Synthetic.pdf', 'text': source, 'positions': [[7, 0, 0, 0, 0]]}]
        def check(statements, evidence):
            checks = [{'id': s['id'], 'verdict': verifier, 'evidence_ids': ['E1'],
                       'quotes': [{'evidence_id': 'E1', 'text': source}]} for s in statements]
            return {'checks': checks}
        return grounding_gate(verifier if callable(verifier) or verifier is None else check, payload, [section], rows, COVERAGE)

    @staticmethod
    def published(result):
        """Everything the caller saves or shows: the private `_unconfirmed` channel is popped before saving."""
        grounding, *rest = result
        return [{k: v for k, v in grounding.items() if k != '_unconfirmed'}, *rest]

    def assert_withheld(self, result, forbidden=None):
        grounding, sections, assessment, gaps = result
        self.assertEqual(grounding['status'], 'withheld')
        self.assertEqual(sections, [])
        self.assertEqual(assessment['status'], 'insufficient')
        self.assertTrue(gaps)
        if forbidden:
            self.assertNotIn(forbidden, json.dumps(self.published(result)), 'Rejected prose must not leak through explanation or audit')

    def test_supported_scoped_statement_is_kept(self):
        result = self.run_gate()
        self.assertEqual(result[0]['status'], 'grounded')
        self.assertIn('new retaining walls', result[1][0]['body'])

    def test_unsupported_and_uncertain_verdicts_withhold_the_draft(self):
        for verdict in ['unsupported', 'uncertain']:
            with self.subTest(verdict=verdict): self.assert_withheld(self.run_gate(verifier=verdict))

    def test_unavailable_verifier_cannot_publish_unchecked_prose(self):
        self.assert_withheld(self.run_gate(verifier=None))

    def test_failed_verifier_withholds_without_replaying(self):
        calls = []
        def failed(statements, evidence):
            calls.append(True)
            raise TimeoutError('private service response must not be exposed')
        result = self.run_gate(verifier=failed)
        self.assert_withheld(result, 'private service response')
        self.assertEqual(len(calls), 1)

    def test_missing_duplicate_and_foreign_statement_checks_are_rejected(self):
        for ids in [[], ['S1', 'S1'], ['S1', 'S999']]:
            def verify(statements, evidence, ids=ids):
                return {'checks': [{'id': sid, 'verdict': 'supported', 'evidence_ids': ['E1'],
                                    'quotes': [{'evidence_id': 'E1', 'text': SOURCE}]} for sid in ids]}
            with self.subTest(ids=ids): self.assert_withheld(self.run_gate(verifier=verify))

    def test_foreign_ids_and_fabricated_quotes_are_rejected(self):
        for eid, quote in [('E99', SOURCE), ('E1', 'The source allows settlement of seven inches.')]:
            def verify(statements, evidence, eid=eid, quote=quote):
                return {'checks': [{'id': s['id'], 'verdict': 'supported', 'evidence_ids': [eid],
                                    'quotes': [{'evidence_id': eid, 'text': quote}]} for s in statements]}
            with self.subTest(eid=eid): self.assert_withheld(self.run_gate(verifier=verify))

    def test_wrong_value_unit_and_reversed_bound_cannot_pass_a_lying_verifier(self):
        for body in ['Settlement shall not exceed 7 inches over 50 years [E1].',
                     'Settlement shall not exceed 2 feet over 50 years [E1].',
                     'Settlement must be at least 2 inches over 50 years [E1].']:
            with self.subTest(body=body): self.assert_withheld(self.run_gate(body), body)

    def test_same_unit_elsewhere_does_not_support_the_wrong_value_unit_pair(self):
        source = 'The offset is 2 feet. Settlement shall not exceed 3 inches.'
        self.assert_withheld(self.run_gate('Settlement shall not exceed 2 inches [E1].', source))

    def test_signed_value_cannot_lose_its_sign(self):
        self.assert_withheld(self.run_gate('The design groundwater elevation is 2 m [E1].',
                                          'The design groundwater elevation is -2 m.'))

    def test_scope_cannot_be_borrowed_from_a_different_sentence(self):
        self.assert_withheld(self.run_gate('The limit for existing walls is 2 inches over 50 years [E1].',
                                          SOURCE + ' Existing walls require a separate assessment.'))

    # Negation binding. A source chunk without sentence punctuation (a table) is one source sentence, so a negation in
    # one row must not negate the others; flips of a negation that does govern the clause must still be withheld.
    TABLE = ('Table 5-2 Pile Acceptance Criteria Pile Type Method A Method B '
             'Driven Pile Load Test Required with Safety Factor ≥ 2.5 Restrike Factor ≥ 2.0 '
             'Micropile Load Test Required (skin friction excluded) Prohibited '
             'Helical Pile Load Test Required Prohibited')

    def assert_negation_rejected(self, body, source):
        result = self.run_gate(body, source)
        self.assert_withheld(result)
        self.assertTrue(any('negation' in reason for row in result[0]['_unconfirmed'] for reason in row['reasons']),
                        result[0]['_unconfirmed'])

    def test_negation_in_another_table_row_does_not_reject_a_row(self):
        for body in ['Driven piles require a load test with a safety factor ≥ 2.5 under Method A [E1].',
                     'Helical piles require a load test under Method A and are prohibited under Method B [E1].',
                     'Micropiles require a load test with skin friction excluded under Method A [E1].']:
            with self.subTest(body=body):
                grounding, sections, *_ = self.run_gate(body, self.TABLE)
                self.assertEqual(grounding['status'], 'grounded', grounding.get('_unconfirmed'))
                self.assertIn(body.removesuffix(' [E1].'), sections[0]['body'])

    def test_open_list_is_not_a_negation(self):
        for wording in ['including but not limited to', 'including, but not limited to,', 'which includes but is not limited to']:
            with self.subTest(wording=wording):
                source = f'The Contractor shall supply piles, {wording} driven piles, micropiles and helical piles, as shown on the plans.'
                grounding, sections, *_ = self.run_gate('The Contractor shall supply driven piles, micropiles and helical piles '
                                                        'as shown on the plans [E1].', source)
                self.assertEqual(grounding['status'], 'grounded', grounding.get('_unconfirmed'))

    def test_negation_flips_are_still_withheld(self):
        cases = [
            # the parenthetical negation is about this clause's content
            ('Micropiles require a load test that includes skin friction under Method A [E1].', self.TABLE),
            ('Splices are allowed for H-piles [E1].', 'Splices are allowed (not for H-piles).'),
            # a parenthetical with only a qualifier, a number or no content of its own governs the clause
            ('Pile splices are permitted in new piles [E1].', 'Pile splices are permitted (new excluded).'),
            ('Pile splices are permitted for 2 piles per bent [E1].', 'Pile splices are permitted for 2 piles per bent (not 3).'),
            ('Pile splices are permitted [E1].', 'Pile splices are permitted (not).'),
            # an added negation is not excused by an unrelated parenthetical negation
            ('Driven piles do not require a load test with a safety factor ≥ 2.5 [E1].', self.TABLE),
            # main-clause negation, an unbalanced parenthesis, and bare "not limited to" still govern
            ('Piles shall be driven to refusal [E1].', 'Piles shall not be driven to refusal.'),
            ('Piles shall be driven to refusal and jetted [E1].', 'Piles shall be driven to refusal (not jetted.'),
            ('The axial load is limited to 50 kips [E1].', 'The axial load is not limited to 50 kips.'),
            ('Pile length is limited to the plan length [E1].', 'Pile length is not limited to the plan length.'),
            ('Piles, including but not limited to H-piles, shall be spliced [E1].',
             'Piles, including but not limited to H-piles, shall not be spliced.'),
            # an open list is not a closed limit, in either direction
            ('The Contractor shall supply piles limited to driven piles and micropiles as shown on the plans [E1].',
             'The Contractor shall supply piles, including but not limited to driven piles and micropiles, as shown on the plans.'),
            ('The Contractor shall supply piles, including but not limited to driven piles and micropiles, as shown on the plans [E1].',
             'The Contractor shall supply piles limited to driven piles and micropiles as shown on the plans.'),
        ]
        for body, source in cases:
            with self.subTest(body=body): self.assert_negation_rejected(body, source)

    def test_parenthetical_applicability_cannot_be_dropped(self):
        # These predicates refer to the enclosing requirement even though their words do not
        # appear in the claim. A verifier's supported verdict cannot override that exclusion.
        for note in ('not applicable', 'not governing', 'not approved', 'does not apply',
                     'not valid for use', 'excluded from this contract', 'approval has not been issued',
                     'all work excluded', 'this requirement exempt', 'requirements excluded'):
            with self.subTest(note=note):
                self.assert_negation_rejected('Settlement shall not exceed 2 inches [E1].',
                                             f'Settlement shall not exceed 2 inches ({note}).')

    def test_parenthetical_qualification_is_publishable_when_preserved(self):
        result = self.run_gate('Settlement shall not exceed 2 inches (not applicable) [E1].',
                               'Settlement shall not exceed 2 inches (not applicable).')
        self.assertEqual(result[0]['status'], 'grounded')
        self.assertIn('(not applicable)', result[1][0]['body'])

    def test_uncited_prose_cannot_use_non_claim_verdict_to_bypass_grounding(self):
        self.assert_withheld(self.run_gate('The engineer has approved this design.', verifier='not_a_claim'))

    def test_heading_is_checked_too(self):
        # A false heading is never shown: the confirmed body is published under a server label instead.
        heading = 'The settlement limit is 7 inches'
        def verify(statements, evidence):
            return {'checks': [{'id': s['id'], 'verdict': 'unsupported' if s['kind'] == 'heading' else 'supported',
                                'evidence_ids': ['E1'], 'quotes': [{'evidence_id': 'E1', 'text': SOURCE}]} for s in statements]}
        result = self.run_gate(heading=heading, verifier=verify)
        grounding, sections, assessment, _ = result
        self.assertNotIn(heading, json.dumps(self.published(result)))
        self.assertEqual(sections[0]['heading'], '')
        self.assertEqual((grounding['status'], grounding['unconfirmed_statements'], assessment['status']), ('grounded', 1, 'partial'))
        self.assertEqual(grounding['_unconfirmed'], [{'text': heading, 'reasons': ['the check did not find it stated in the cited passages']}])

    def test_only_confirmed_statements_are_published(self):
        body = ('- Total settlement shall not exceed 2 inches over 50 years for new retaining walls [E1].\n'
                '- Differential settlement shall not exceed 9 inches [E1].')
        result = self.run_gate(body)
        grounding, sections, assessment, gaps = result
        shown = json.dumps(self.published(result))
        self.assertIn('Total settlement shall not exceed 2 inches', sections[0]['body'])
        self.assertTrue(sections[0]['body'].startswith('- '))            # list layout survives
        self.assertNotIn('Differential', shown)                         # the wrong value is never shown
        self.assertEqual((grounding['checked_statements'], grounding['unconfirmed_statements']), (2, 1))
        self.assertIn('1 of 2 statements were confirmed', assessment['explanation'])
        failed = [s for s in grounding['statements'] if s['verdict'] != 'supported' and s['verdict'] != 'structural']
        self.assertEqual(len(failed), 1); self.assertNotIn('text', failed[0])
        self.assertEqual(grounding['_unconfirmed'][0]['text'], 'Differential settlement shall not exceed 9 inches [E1].')
        self.assertIn('a number is not in the quoted text', grounding['_unconfirmed'][0]['reasons'])

    def test_a_section_with_no_confirmed_statement_is_dropped_and_nothing_confirmed_is_withheld(self):
        rows = [{'id': 'E1', 'source_id': 's', 'source_name': 'Synthetic.pdf', 'text': SOURCE, 'positions': [[7, 0, 0, 0, 0]]},
                {'id': 'E2', 'source_id': 's', 'source_name': 'Synthetic.pdf', 'text': 'Piles shall be steel H-piles.', 'positions': [[8, 0, 0, 0, 0]]}]
        good = {'heading': 'Limit', 'body': 'Total settlement shall not exceed 2 inches over 50 years for new retaining walls [E1].', 'evidence_ids': ['E1']}
        bad = {'heading': 'Piles', 'body': 'Piles shall be timber [E2].', 'evidence_ids': ['E2']}
        payload = {'kind': 'research', 'status': 'supported', 'assessment': '', 'conflicts': [], 'gaps': []}
        def verify(statements, evidence):
            return {'checks': [{'id': s['id'], 'verdict': 'unsupported' if 'timber' in s['text'] or s['text'] == 'Piles' else 'supported',
                                'evidence_ids': ['E1'], 'quotes': [{'evidence_id': 'E1', 'text': SOURCE}]} for s in statements]}
        grounding, sections, _, _ = grounding_gate(verify, payload, [good, bad], rows, COVERAGE)
        self.assertEqual([s['heading'] for s in sections], ['Limit'])
        self.assertEqual(sections[0]['evidence_ids'], ['E1'])
        # With no confirmed body statement at all, the answer is withheld as before, but the text is kept privately.
        withheld = grounding_gate(verify, payload, [bad], rows, COVERAGE)
        self.assert_withheld(withheld, 'timber')
        self.assertEqual([u['text'] for u in withheld[0]['_unconfirmed']], ['Piles shall be timber [E2].'])

    def test_workflow_kind_does_not_bypass_document_claim_checks(self):
        payload = {'kind': 'workflow', 'status': 'supported', 'assessment': 'The design is approved.', 'conflicts': [], 'gaps': []}
        section = {'heading': 'Approval', 'body': 'The engineer has approved this design.', 'evidence_ids': []}
        result = grounding_gate(None, payload, [section], [], COVERAGE)
        self.assertEqual(result[1], [])
        self.assertNotIn('The design is approved.', json.dumps(result))

    def test_unchecked_gap_prose_is_not_published(self):
        result = self.run_gate(gaps=['The engineer has approved this design.'])
        self.assertNotIn('The engineer has approved this design.', json.dumps(result))

    def test_no_evidence_abstains_without_a_verifier_call_or_model_claims(self):
        def forbidden(*args): raise AssertionError('No reason to call a verifier with no evidence')
        payload = {'kind': 'research', 'status': 'insufficient', 'assessment': 'The design is approved.',
                   'conflicts': [], 'gaps': ['The engineer has approved this design.']}
        result = grounding_gate(forbidden, payload, [], [], COVERAGE)
        self.assertEqual(result[0]['status'], 'abstained')
        self.assertEqual(result[1], [])
        self.assertNotIn('approved this design', json.dumps(result))


    def test_compound_statement_can_provide_four_distinct_supporting_quotes(self):
        sentences = ['Clearance is 2 feet.', 'Width is 3 feet.', 'Height is 4 feet.', 'Length is 5 feet.']
        def verify(statements, evidence):
            return {'checks': [{'id': st['id'], 'verdict': 'supported', 'evidence_ids': ['E1'],
                                'quotes': [{'evidence_id': 'E1', 'text': text} for text in sentences]} for st in statements]}
        result = self.run_gate('Clearance is 2 feet; width is 3 feet; height is 4 feet; length is 5 feet [E1].',
                               ' '.join(sentences), verifier=verify)
        self.assertEqual(result[0]['status'], 'grounded')
        self.assertEqual(len(result[0]['statements'][-1]['quotes']), 4)

    def test_oversized_and_duplicate_quote_reports_fail_closed(self):
        for quotes in [[{'evidence_id': 'E1', 'text': SOURCE}] * 2,
                       [{'evidence_id': 'E1', 'text': f'Value {n}.'} for n in range(9)]]:
            def verify(statements, evidence, quotes=quotes):
                return {'checks': [{'id': st['id'], 'verdict': 'supported', 'evidence_ids': ['E1'],
                                    'quotes': quotes} for st in statements]}
            with self.subTest(count=len(quotes)):
                result = self.run_gate(verifier=verify)
                self.assert_withheld(result)
                self.assertEqual(result[0]['reason_code'], 'invalid_verifier_response')

    def test_failure_reason_is_server_owned_and_excludes_exception_text(self):
        def verify(statements, evidence):
            raise TimeoutError('synthetic-private-canary')
        result = self.run_gate(verifier=verify)
        self.assert_withheld(result, 'synthetic-private-canary')
        self.assertEqual(result[0]['reason_code'], 'provider_unavailable')
        self.assertEqual(self.run_gate(verifier=None)[0]['reason_code'], 'verifier_unavailable')


    def test_long_qualified_source_sentence_is_preserved_as_one_exact_quote(self):
        source = ('For new retaining walls adjacent to the northern approach embankment, with excavation complete '
                  'and the permanent drainage installed, total settlement shall not exceed 2 inches over 50 years, '
                  'considering both short-term and long-term settlement and the combined effects of construction '
                  'sequence, fill placement, groundwater variation, seasonal changes, and the surrounding facilities, '
                  'provided that the design assumptions remain applicable to the selected structure and that '
                  'the supporting ground investigation has been reviewed by the responsible engineer.')
        self.assertGreater(len(source), 400)
        result = self.run_gate(source[:-1] + ' [E1].', source)
        self.assertEqual(result[0]['status'], 'grounded')
        self.assertEqual(result[0]['statements'][-1]['quotes'][0]['text'], source)

    def test_excessively_long_quotes_still_fail_closed_without_truncation(self):
        source = SOURCE + ' Additional qualification.' * 130
        self.assertGreater(len(source), 3000)
        result = self.run_gate(source=source)
        self.assert_withheld(result)
        self.assertEqual(result[0]['reason_code'], 'invalid_verifier_response')


    def test_combined_quote_size_cannot_evade_per_statement_guard(self):
        check = {'id': 'S1', 'verdict': 'supported', 'evidence_ids': ['E1'],
                 'quotes': [{'evidence_id': 'E1', 'text': str(n) + 'x' * 1599} for n in range(4)]}
        with self.assertRaises(ValueError):
            GroundingReport.model_validate({'checks': [check]})

    def test_report_quote_budget_applies_across_individually_bounded_statements(self):
        checks = [{'id': f'S{n}', 'verdict': 'supported', 'evidence_ids': ['E1'],
                   'quotes': [{'evidence_id': 'E1', 'text': str(q) + 'x' * 1099} for q in range(4)]}
                  for n in range(5)]
        for check in checks:
            GroundingReport.model_validate({'checks': [check]})
        with self.assertRaises(ValueError):
            GroundingReport.model_validate({'checks': checks})


if __name__ == '__main__': unittest.main()
