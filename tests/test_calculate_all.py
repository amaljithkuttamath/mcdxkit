"""Calculate all: one run per calculation through batch.py's scheduler, identity and resume."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcdxkit import engine
from mcdxkit.documents import DocumentWorkflow
from test_pipeline import report, template

REAL_CONVERT = engine.convert


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.flow = DocumentWorkflow(self.root / 'workspace')
        self.raw = self.root / 'loads.txt'; self.raw.write_text(report())
        self.template = self.root / 'template.mcdx'; template(self.template)
        self.pid = self.flow.create('Pier 3')['id']
        self.strength = self.attach([1, 7], 'Strength')
        self.service = self.attach([2], 'Service')
        self.calls = []

    def attach(self, cases, name):
        return self.flow.attach_calculation(self.pid, self.raw, self.template, cases=cases, name=name)['calculation']

    def convert(self, fail=None):
        """engine.convert that records each call; `fail` maps a case list to the exception it raises."""
        def wrapped(*args, **kwargs):
            self.calls.append(kwargs['cases'])
            if fail and tuple(kwargs['cases']) in fail: raise fail[tuple(kwargs['cases'])]
            return REAL_CONVERT(*args, **kwargs)
        return patch('mcdxkit.documents.engine.convert', side_effect=wrapped)

    def runs(self):
        return [r['calculation_id'] for r in self.flow.get(self.pid)['runs']]


class CalculateAllTests(Fixture):

    def test_one_failing_calculation_does_not_fail_the_other_and_is_not_retried(self):
        with self.convert({(2,): RuntimeError('synthetic engine failure')}):
            result = self.flow.calculate_all(self.pid)
        self.assertEqual((result['total'], result['succeeded'], result['skipped'], result['failed']), (2, 1, 0, 1))
        rows = {r['calculation_id']: r for r in result['calculations']}
        self.assertEqual(rows[self.strength['id']]['status'], 'succeeded')
        self.assertEqual(rows[self.service['id']]['status'], 'failed')
        self.assertIn('synthetic engine failure', rows[self.service['id']]['error'])
        self.assertIsNone(rows[self.service['id']]['run_id'])
        # Exactly one attempt per calculation; one run, belonging to the calculation that succeeded.
        self.assertEqual(self.calls, [[1, 7], [2]])
        self.assertEqual(self.runs(), [self.strength['id']])
        self.flow.artifact(self.pid, rows[self.strength['id']]['run_id'], 'package.zip')
        self.assertFalse(result['native_execution_verified'])

    def test_resume_after_interruption_calculates_only_the_rest(self):
        # KeyboardInterrupt is not an engine failure: it stops the process mid-batch, as a killed worker would.
        with self.convert({(2,): KeyboardInterrupt()}), self.assertRaises(KeyboardInterrupt):
            self.flow.calculate_all(self.pid)
        self.assertEqual(self.runs(), [self.strength['id']])
        self.calls.clear()
        with self.convert():
            result = self.flow.calculate_all(self.pid)
        self.assertEqual(self.calls, [[2]])
        self.assertEqual({r['calculation_id']: r['status'] for r in result['calculations']},
                         {self.strength['id']: 'skipped', self.service['id']: 'succeeded'})
        self.assertEqual(self.runs(), [self.strength['id'], self.service['id']])
        # Running again changes nothing: every calculation already has a verified run.
        again = self.flow.calculate_all(self.pid)
        self.assertEqual((again['skipped'], again['succeeded']), (2, 0))
        self.assertEqual(len(self.runs()), 2)

    def test_skipped_run_must_match_identity_and_package(self):
        first = self.flow.build(self.pid, None, self.strength['id'])
        self.assertEqual(first['job_id'], self.flow.calculation_identity(self.strength))
        # A tampered package is never reused; the calculation is calculated again into a new run.
        package = self.flow.root / self.pid / 'runs' / first['id'] / 'package.zip'
        package.write_bytes(package.read_bytes() + b'tampered')
        with self.convert():
            result = self.flow.calculate_all(self.pid, [self.strength['id']])
        self.assertEqual(self.calls, [[1, 7]])
        self.assertEqual(result['calculations'][0]['status'], 'succeeded')
        self.assertNotEqual(result['calculations'][0]['run_id'], first['id'])
        # A new engine revision gives a new identity, so an older run is not reused.
        with patch('mcdxkit.batch.calcpad.REVISION', 'another-revision'):
            self.assertNotEqual(self.flow.calculation_identity(self.strength), first['job_id'])

    def test_progress_is_measured_in_finished_calculations(self):
        events = []
        flow = DocumentWorkflow(self.flow.root, activity=lambda kind, message, detail: events.append((message, detail)))
        flow.calculate_all(self.pid)
        self.assertEqual([d for _, d in events], [{'finished': n, 'total': 2} for n in (0, 1, 2)])
        self.assertEqual(events[-1][0], 'Finished 2 of 2 calculations')

    def test_invalid_selection_and_tampered_snapshot(self):
        for bad in ([], [self.strength['id'], self.strength['id']], ['f' * 32], 'all'):
            with self.assertRaises(ValueError): self.flow.calculate_all(self.pid, bad)
        empty = self.flow.create('Library only')['id']
        with self.assertRaisesRegex(ValueError, 'Connect'): self.flow.calculate_all(empty)
        # Changed snapshot files block the whole project, as for a single calculation.
        (self.flow.calculation_folder(self.pid, self.service) / 'loads.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'): self.flow.calculate_all(self.pid)
        self.assertEqual(self.runs(), [])


class CalculateAllJobTests(Fixture):
    """The background job and the CLI use the same shared function."""
    def store(self):
        from mcdxkit.jobs import JobStore
        return JobStore(self.flow.root)

    def execute(self, job):
        from mcdxkit.jobs import execute
        store = self.store(); token = store.claim(job['id'])
        self.assertIsNotNone(token)
        execute(str(self.flow.root), job['id'], token)
        return store.get(job['id'])

    def test_job_pins_calculations_and_resubmit_with_same_key_adds_no_runs(self):
        store = self.store()
        job = store.submit(self.pid, 'calculate_all', {}, 'calc-all-1')
        pinned = [self.strength['id'], self.service['id']]
        self.assertEqual(job['payload'], {'calculations': pinned})
        self.assertEqual(store.submit(self.pid, 'calculate_all', {}, 'calc-all-1')['id'], job['id'])
        # Adding a calculation after submission neither stales nor retargets the job.
        self.attach([1], 'Later')
        with self.convert({(2,): RuntimeError('synthetic engine failure')}):
            done = self.execute(job)
        self.assertEqual(done['state'], 'succeeded')
        self.assertEqual((done['result']['succeeded'], done['result']['failed']), (1, 1))
        self.assertEqual(self.runs(), [self.strength['id']])
        self.assertEqual([a['detail'] for a in done['activity'] if a['detail']],
                         [{'finished': n, 'total': 2} for n in (0, 1, 2)])
        again = store.submit(self.pid, 'calculate_all', {'calculations': pinned}, 'calc-all-1')
        self.assertEqual((again['id'], again['state']), (job['id'], 'succeeded'))
        self.assertIsNone(store.claim(job['id']))
        self.assertEqual(self.runs(), [self.strength['id']])
        # The project now holds three calculations, so the same key with all of them is a different request.
        with self.assertRaisesRegex(ValueError, 'different'):
            store.submit(self.pid, 'calculate_all', {}, 'calc-all-1')

    def test_interrupted_job_is_not_retried_and_a_new_request_resumes(self):
        store = self.store()
        job = store.submit(self.pid, 'calculate_all', {'calculations': [self.strength['id'], self.service['id']]}, 'first')
        token = store.claim(job['id'])
        # The worker saved one calculation, then died.
        self.flow.build(self.pid, None, self.strength['id'])
        with self.flow.connection() as db: db.execute('UPDATE jobs SET updated=0 WHERE id=?', (job['id'],))
        store.recover()
        failed = store.get(job['id'])
        self.assertEqual((failed['state'], failed['uncertain']), ('failed', False))
        self.assertIsNone(store.claim(job['id']))
        with self.assertRaises(ValueError): store.complete(job['id'], token, {})
        with self.convert():
            resumed = self.execute(store.submit(self.pid, 'calculate_all', {}, 'second'))
        self.assertEqual(self.calls, [[2]])
        self.assertEqual({r['calculation_id']: r['status'] for r in resumed['result']['calculations']},
                         {self.strength['id']: 'skipped', self.service['id']: 'succeeded'})
        self.assertEqual(self.runs(), [self.strength['id'], self.service['id']])

    def test_cancelled_job_saves_no_further_runs(self):
        store = self.store()
        job = store.submit(self.pid, 'calculate_all', {}, 'cancel-me')
        token = store.claim(job['id'])
        flow = store.workflow(job['id'], token, publish=False)
        store.cancel(job['id'])
        with self.assertRaisesRegex(ValueError, 'cancel'): flow.calculate_all(self.pid, job['payload']['calculations'])
        self.assertEqual(self.runs(), [])
        self.assertFalse(store.get(job['id'])['uncertain'])

    def test_job_rejects_invalid_payloads(self):
        store = self.store()
        for payload in ({'calculations': []}, {'calculations': ['f' * 32]}, {'calculation': self.strength['id']},
                        {'calculations': [self.strength['id']] * 2}):
            with self.assertRaises(ValueError): store.submit(self.pid, 'calculate_all', payload, 'bad')

    def test_cli_build_all(self):
        from mcdxkit.document_cli import main
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(['--workspace', str(self.flow.root), *args])
            return code, out.getvalue(), err.getvalue()
        with self.convert({(2,): RuntimeError('synthetic engine failure')}):
            code, out, err = run('build', self.pid, '--all')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['failed'], 1)
        self.assertEqual([line for line in err.splitlines() if line.startswith('Finished')],
                         [f'Finished {n} of 2 calculations' for n in (0, 1, 2)])
        code, out, _ = run('build', self.pid, '--all')
        self.assertEqual((code, json.loads(out)['skipped'], json.loads(out)['succeeded']), (0, 1, 1))
        self.assertEqual(self.runs(), [self.strength['id'], self.service['id']])
        code, _, err = run('build', self.pid, '--all', '--calculation', self.strength['id'])
        self.assertEqual(code, 2); self.assertIn('--all', err)


if __name__ == '__main__':
    unittest.main()
