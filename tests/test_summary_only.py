"""Headerless GROUP summaries use the next case marker, never preceding detail values."""
import tempfile
import unittest
from pathlib import Path

from mcdxkit import engine, group_report
from test_pipeline import report


def summary_only():
    marker = 'SUMMARY FOR LOAD CASES AND COMBINATIONS'
    return marker + report().split(marker, 1)[1]


class SummaryOnlyTests(unittest.TestCase):
    def test_headerless_inspection_exposes_cases_without_inventing_classifications(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'summary.txt'
            source.write_text(summary_only())
            found = engine.inspect_report(source, checks='none')
            self.assertTrue(found['summary_only'])
            self.assertEqual(found['cases'], [{'id': 1, 'name': None}, {'id': 2, 'name': None},
                                            {'id': 7, 'name': None}])
            self.assertEqual(found['selected_cases'], [])
            self.assertIsNone(found['envelope'])
            self.assertIn('summary', found['selection_required'])
            selected = engine.inspect_report(source, cases=[1, 7], checks='none')
            self.assertEqual(selected['envelope'], {'P': 140, 'Vy': 24, 'Vz': 8, 'My': 190, 'Mz': 850})

    def test_both_load_bases_preserve_signed_values_and_case_boundaries(self):
        for basis, expected in [('effects', {'P': 140, 'Vy': 24, 'Vz': 8, 'My': 190, 'Mz': 850}),
                                ('reactions', {'P': 140, 'Vy': 25, 'Vz': 9, 'My': 220, 'Mz': 950})]:
            with self.subTest(basis=basis):
                parsed = group_report.parse(summary_only(), basis)
                cases = group_report.select(parsed, [1, 7])
                self.assertEqual(group_report.envelope(cases), expected)
                self.assertEqual(cases[0]['tables']['local'][0], [90, -20, -4, 1, -100, -900])
                self.assertEqual(cases[0]['pile_ids'], [1, 3])

    def test_decorated_markers_allow_horizontal_whitespace_case_and_crlf(self):
        text = summary_only().replace('SUMMARY FOR LOAD CASES AND COMBINATIONS',
            '  ***** summary\tfor load cases and combinations *****  ')
        text = text.replace('LOAD CASE :', '  load\tcase\t:').replace('\n', '\r\n')
        parsed = group_report.parse(text)
        self.assertTrue(parsed['summary_only'])
        self.assertEqual([c['id'] for c in parsed['cases']], [1, 2, 7])

    def test_cursor_ignores_inline_mentions_and_stops_at_next_case(self):
        text = summary_only().replace('LOAD CASE : 2',
            'Comment mentions LOAD CASE : 99 without starting a case\nLOAD CASE : 2')
        parsed = group_report.parse(text)
        self.assertEqual([c['id'] for c in parsed['cases']], [1, 2, 7])
        self.assertEqual(group_report.envelope(group_report.select(parsed, [1]))['P'], 120)

    def test_malformed_case_marker_cannot_be_silently_skipped_or_truncated(self):
        for marker in ('LOAD CASE : 7oops', 'LOAD CASE :\n7', 'LOAD CASE : 0'):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'load case'):
                group_report.parse(summary_only().replace('LOAD CASE : 7', marker))

    def test_missing_row_cannot_be_borrowed_from_next_case_or_global_table(self):
        text = summary_only().replace('MAXIMUM 120 15 8 1 200 500', '')
        with self.assertRaisesRegex(ValueError, 'MAXIMUM'):
            group_report.parse(text)

    def test_last_summary_wins_and_incomplete_final_summary_still_fails(self):
        text = report() + '\n' + summary_only().replace('MAXIMUM 140 18 9 1 220 550',
                                                       'MAXIMUM 150 18 9 1 220 550')
        parsed = group_report.parse(text)
        self.assertFalse(parsed['summary_only'])
        self.assertEqual(group_report.envelope(group_report.select(parsed))['P'], 150)
        with self.assertRaises(ValueError):
            group_report.parse(text + '\nSUMMARY FOR LOAD CASES AND COMBINATIONS\nLOAD CASE : 1\n')

    def test_no_fallback_when_summary_marker_missing(self):
        with self.assertRaisesRegex(ValueError, 'summary'):
            group_report.parse(summary_only().split('\n', 1)[1])


if __name__ == '__main__':
    unittest.main()
