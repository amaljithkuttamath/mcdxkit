"""Independent checks of evaluation bookkeeping and request isolation."""
import unittest

from evaluate_grounding import evaluate


class BenchmarkTests(unittest.TestCase):
    def test_questions_are_isolated_and_fixed_expectations_are_scored(self):
        cases = [
            {'id': 'positive', 'source': 'Settlement shall not exceed 3 inches.',
             'claim': 'Settlement shall not exceed 3 inches.', 'expected': 'supported'},
            {'id': 'negative', 'source': 'A drainage plan is required.',
             'claim': 'The design is approved.', 'expected': 'unsupported'},
        ]
        calls = []

        def verify(statements, evidence):
            self.assertEqual(len(statements), 1)
            self.assertEqual(len(evidence), 1)
            calls.append(evidence[0]['text'])
            supported = 'Settlement' in evidence[0]['text']
            return {'checks': [{'id': statements[0]['id'], 'verdict': 'supported' if supported else 'unsupported',
                                'evidence_ids': ['E1'] if supported else [],
                                'quotes': [{'evidence_id': 'E1', 'text': evidence[0]['text']}] if supported else []}]}

        result = evaluate(cases, verify)
        self.assertTrue(result['passed'])
        self.assertEqual(result['supported_published'], 1)
        self.assertEqual(result['unsafe_publications'], 0)
        self.assertEqual(calls, [case['source'] for case in cases])

    def test_provider_errors_cannot_pass_negative_cases(self):
        case = {'id': 'negative', 'source': 'A drainage plan is required.',
                'claim': 'The design is approved.', 'expected': 'unsupported'}
        calls = []

        def fail(*args):
            calls.append(args)
            raise RuntimeError('Synthetic provider error; do not export this string')

        result = evaluate([case], fail)
        self.assertFalse(result['passed'])
        self.assertEqual(result['errors'], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['cases'][0]['error'], 'RuntimeError')
        self.assertNotIn('Synthetic provider error', str(result))

    def test_missing_or_duplicate_checks_fail_even_negative_cases(self):
        case = {'id': 'negative', 'source': 'A drainage plan is required.',
                'claim': 'The design is approved.', 'expected': 'unsupported'}
        for checks in [[], [{'id': 'S1'}, {'id': 'S1'}]]:
            with self.subTest(checks=checks):
                result = evaluate([case], lambda *_args, checks=checks: {'checks': checks})
                self.assertFalse(result['passed'])
                self.assertEqual(result['errors'], 1)
