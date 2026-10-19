import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from test_pipeline import report, template

CLI = Path(__file__).resolve().parents[1] / 'scripts/cli.py'


class BatchTests(unittest.TestCase):
    def test_parallel_folder_failure_resume_and_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); raw = root / 'raw'; raw.mkdir()
            for sub in ('a', 'b'):
                (raw/sub).mkdir(); (raw/sub/'same.txt').write_text(report())
            (raw/'bad.txt').write_text('incomplete report')
            ref = root/'reference.mcdx'; template(ref); out = root/'out'
            def run(*extra):
                process = subprocess.run([sys.executable, str(CLI), 'batch', str(raw), '--recursive',
                    '--template', str(ref), '--output-dir', str(out), '--workers', '2', *extra], capture_output=True, text=True)
                return process, json.loads(process.stdout)
            first, data = run()
            self.assertEqual(first.returncode, 1, first.stderr)
            self.assertEqual((data['succeeded'], data['failed']), (2, 1))
            self.assertEqual(len(list(out.glob('*/*.mcdx'))), 2)
            again, data = run('--resume')
            self.assertEqual((data['skipped'], data['failed']), (2, 1))
            (raw/'bad.txt').write_text(report())
            fixed, data = run('--resume')
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual((data['succeeded'], data['skipped'], data['failed']), (1, 2, 0))
            manifest = [json.loads(line) for line in Path(data['manifest']).read_text().splitlines()]
            self.assertTrue(all('source_sha256' in row for row in manifest))
            # report() raises review items; every converted or verified row counts them.
            self.assertTrue(all(row['open_checks'] > 0 and row['check_errors'] == 0
                                for row in manifest if row['status'] != 'failed'))
            # Calculation artifacts are part of the resume contract too.
            rendered = next(out.glob('*/*.html')); saved = rendered.read_bytes(); rendered.write_bytes(b'corrupt')
            broken, data = run('--resume')
            self.assertEqual(broken.returncode, 1); self.assertEqual(data['failed'], 1)
            rendered.write_bytes(saved)
            # Resume must not accept damaged files merely because a manifest says done.
            next(out.glob('*/*.mcdx')).write_bytes(b'corrupt')
            corrupt, data = run('--resume')
            self.assertEqual(corrupt.returncode, 1)
            self.assertEqual(data['failed'], 1)

    def test_python_api_resume_with_tuple_cases(self):
        from mcdxkit.batch import convert_batch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'a.txt'; source.write_text(report())
            ref = root/'reference.mcdx'; template(ref); out = root/'out'
            first = convert_batch([source], ref, out, cases=(1, 7))
            self.assertEqual((first['succeeded'], first['failed']), (1, 0))
            again = convert_batch([source], ref, out, cases=(1, 7), resume=True)
            self.assertEqual((again['skipped'], again['failed']), (1, 0))

    def test_explicit_files_and_output_subtree_exclusion(self):
        from mcdxkit.batch import discover
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); out = root/'out'; out.mkdir()
            source = root/'a.txt'; source.write_text(report())
            (out/'snapshot.txt').write_text(report())
            self.assertEqual(discover([root, source], out, recursive=True), [source.resolve()])

    def test_new_check_version_reruns_review_on_resume(self):
        from unittest import mock
        from mcdxkit.batch import convert_batch
        from mcdxkit.review.checks.ratios import RatioOutlier
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'r.txt'; source.write_text(report())
            ref = root / 'reference.mcdx'; template(ref); out = root / 'out'
            first = convert_batch([source], ref, out)
            self.assertEqual(first['succeeded'], 1)
            self.assertEqual(convert_batch([source], ref, out, resume=True)['skipped'], 1)
            with mock.patch.object(RatioOutlier, 'version', '3'):
                again = convert_batch([source], ref, out, resume=True)
            self.assertEqual((again['succeeded'], again['skipped']), (1, 0))
            rows = [json.loads(line) for line in Path(again['manifest']).read_text().splitlines()]
            self.assertNotEqual(rows[0]['job_id'], rows[-1]['job_id'])
            audit = json.loads(Path(rows[-1]['audit']).read_text())
            self.assertIn({'id': 'ratio_outlier', 'version': '3'},
                          [{k: c[k] for k in ('id', 'version')} for c in audit['review_checks']['checks_run']])
            self.assertEqual(convert_batch([source], ref, out, resume=True, checks='none')['succeeded'], 1)


FIXTURES = Path(__file__).resolve().parent / 'fixtures/review'
# The review fixtures are wider than the template's default pile count.
PILES = {'n_z': 16}


class BatchReviewCountTests(unittest.TestCase):
    """Batch-level advisory review counts: a summary line and two appended table columns."""

    def copy(self, folder, *names):
        raw = folder / 'raw'; raw.mkdir(exist_ok=True)
        for name in names:
            (raw/name).write_text((FIXTURES/name).read_text())
        return raw

    def rows(self, path):
        import csv as csv_module
        with Path(path).open(newline='', encoding='utf-8') as stream:
            reader = csv_module.DictReader(stream)
            return list(reader.fieldnames), sorted(reader, key=lambda r: r['source'])

    def test_counts_over_several_reports_and_appended_columns(self):
        from mcdxkit.batch import CHECK_COLUMNS, convert_batch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = self.copy(root, 'seed505_clean.txt', 'seed505_mislabel_str_as_ser.txt', 'seed529_digit_edit.txt')
            ref = root/'reference.mcdx'; template(ref)
            summary = convert_batch([raw], ref, root/'out', overrides=PILES)
            self.assertEqual((summary['succeeded'], summary['failed']), (3, 0))
            review = summary['review']
            self.assertEqual((review['reports'], review['reports_with_items'], review['items'],
                              review['reports_with_check_errors']), (3, 2, 2, 0))
            self.assertEqual(review['summary'], '3 reports, 2 with items to review')
            names, rows = self.rows(summary['checks']['table'])
            # Earlier columns keep their order; the review columns are appended.
            self.assertEqual(names, list(CHECK_COLUMNS))
            self.assertEqual(names[-2:], ['review_items', 'check_error'])
            self.assertEqual([r['review_items'] for r in rows], ['0', '1', '1'])
            self.assertEqual({r['check_error'] for r in rows}, {'no'})

    def test_nothing_flagged_and_exit_code_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = self.copy(root, 'seed505_clean.txt', 'seed529_clean.txt')
            ref = root/'reference.mcdx'; template(ref)
            process = subprocess.run([sys.executable, str(CLI), 'batch', str(raw), '--template', str(ref),
                                      '--output-dir', str(root/'out'), '--set', 'n_z=16', '--quiet'],
                                     capture_output=True, text=True)
            data = json.loads(process.stdout)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(data['review']['reports_with_items'], 0)
            self.assertIn('2 reports, 0 with items to review', process.stderr)
            self.assertEqual([r['review_items'] for r in self.rows(data['checks']['table'])[1]], ['0', '0'])

    def test_check_error_counted_without_failing_the_batch(self):
        from unittest import mock
        from mcdxkit.batch import convert_batch
        from mcdxkit.review.checks.ratios import RatioOutlier
        def boom(self, view, ctx):
            raise RuntimeError('no')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = self.copy(root, 'seed505_mislabel_str_as_ser.txt')
            ref = root/'reference.mcdx'; template(ref)
            with mock.patch.object(RatioOutlier, 'run', boom):
                summary = convert_batch([raw], ref, root/'out', overrides=PILES)
            # Advisory only: a check that could not run neither fails the job nor changes the exit code.
            self.assertEqual((summary['succeeded'], summary['failed']), (1, 0))
            review = summary['review']
            self.assertEqual((review['reports_with_items'], review['reports_with_check_errors']), (1, 1))
            self.assertEqual(review['summary'],
                             '1 report, 1 with items to review, 1 report where some checks could not run')
            names, rows = self.rows(summary['checks']['table'])
            self.assertEqual([(r['review_items'], r['check_error']) for r in rows], [('1', 'yes')])

    def test_reports_without_checks_are_not_counted_as_clean(self):
        from mcdxkit.batch import convert_batch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = self.copy(root, 'seed505_mislabel_str_as_ser.txt')
            ref = root/'reference.mcdx'; template(ref)
            summary = convert_batch([raw], ref, root/'out', overrides=PILES, checks='none')
            self.assertEqual(summary['review']['reports_checked'], 0)
            self.assertEqual(summary['review']['summary'], '1 report, 0 with items to review, 1 not checked')
            # Neither review column may read as a clean review when no check ran.
            self.assertEqual([(r['review_items'], r['check_error']) for r in self.rows(summary['checks']['table'])[1]],
                             [('', '')])

    def test_failed_conversion_and_all_checks_failing_are_not_clean(self):
        from mcdxkit.batch import check_row, format_review_summary, review_counts
        failed = check_row({'source': 'a.txt', 'status': 'failed'})
        broken = check_row({'source': 'b.txt', 'status': 'succeeded', 'check_summary': None,
                            'open_checks': None, 'check_errors': 2})
        self.assertEqual([(r['review_items'], r['check_error']) for r in (failed, broken)], [('', ''), ('', 'yes')])
        counts = review_counts([failed, broken])
        self.assertEqual((counts['reports_checked'], counts['reports_with_check_errors']), (0, 1))
        self.assertEqual(format_review_summary(counts),
                         '2 reports, 0 with items to review, 1 not checked, 1 failed to convert, 1 report where some checks could not run')
