"""Similar past jobs: nearest earlier conversions, scoped to one output directory, advisory only."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_case_names import summary
from test_pipeline import template
from mcdxkit import engine
from mcdxkit.review import runner
from mcdxkit.review.annotate.similar import SimilarJobs
from mcdxkit.review.api import PastJob, frozen_mapping
from mcdxkit.review.history import AuditHistory, MemoryHistory, read_audits
from mcdxkit.service import Session

def jobs(inspected):
    """Similar jobs from an inspection, as the browser derives them from the annotations."""
    return [a['data'] for a in inspected['review_checks']['annotations'] if a['kind'] == 'similar_job']


BASE = {'P': 1000.0, 'Vy': 100.0, 'Vz': 50.0, 'My': 10000.0, 'Mz': 50000.0}


def text(scale=1.0):
    return summary([(1, 'STR-I', 1000 * scale, 50000 * scale), (2, 'SER-I', 800 * scale, 40000 * scale)])


def audit(folder, name, scale=1.0, *, cases=(1,), units=None, load_source='effects', sha=None, mtime=None, **extra):
    path = Path(folder) / name / 'w.audit.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'output': '/somewhere/else/w.mcdx', 'source': 'r.txt', 'cases': list(cases), 'load_source': load_source,
            'envelope': {k: v * scale for k, v in BASE.items()}, 'case_names': {'1': 'STR-I', '2': 'SER-I'},
            'source_sha256': sha or hashlib.sha256(name.encode()).hexdigest(), 'template_name': 'pile.mcdx'}
    if units is not None:
        data['units'] = units
    data.update(extra)
    path.write_text(json.dumps(data))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class SimilarJobsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.report = self.root / 'r.txt'
        self.report.write_text(text())
        self.out = self.root / 'outputs'

    def tearDown(self):
        self.folder.cleanup()

    def similar(self, output_dir=None, report=None):
        inspected = engine.inspect_report(report or self.report, history=AuditHistory(output_dir or self.out))
        self.assertNotIn('similar_jobs', inspected)
        return jobs(inspected)

    def test_ranks_nearest_first_within_threshold(self):
        for name, scale in (('far', 3.0), ('b', 1.2), ('a', 1.05), ('c', 1.1), ('d', 0.7)):
            audit(self.out, name, scale, mtime=1_790_000_000)
        found = self.similar()
        self.assertEqual([j['name'] for j in found], ['a/w.mcdx', 'c/w.mcdx', 'b/w.mcdx'])
        self.assertEqual([j['rank'] for j in found], [1, 2, 3])
        first = found[0]
        self.assertEqual(first['difference_pct'], {k: 5.0 for k in BASE})
        self.assertEqual((first['date'], first['template'], first['cases']), ('2026-09-21', 'pile.mcdx', [1]))
        self.assertAlmostEqual(first['typical_difference_pct'], 5.0)
        self.assertEqual(first['load_source'], 'effects')
        # Outside the threshold nothing is suggested, however few past jobs there are.
        for name in ('a', 'b', 'c', 'd'):
            (self.out / name / 'w.audit.json').unlink()
        self.assertEqual(self.similar(), [])

    def test_identical_envelope_is_distance_zero_and_case_count_breaks_ties(self):
        audit(self.out, 'a-more-cases', 1.0, cases=(1, 3, 5))
        audit(self.out, 'z-same-cases', 1.0, cases=(1,))
        audit(self.out, 'bigger', 1.3, cases=(1,))
        found = self.similar()
        self.assertEqual([j['name'] for j in found], ['z-same-cases/w.mcdx', 'a-more-cases/w.mcdx', 'bigger/w.mcdx'])
        self.assertEqual([(j['distance'], j['typical_difference_pct']) for j in found[:2]], [(0.0, 0.0), (0.0, 0.0)])
        self.assertAlmostEqual(found[2]['typical_difference_pct'], 30.0)

    def test_source_snapshots_are_not_history(self):
        audit(self.out, 'job/_source', 1.0)
        audit(self.out, 'job', 1.1)
        self.assertEqual([j['name'] for j in self.similar()], ['job/w.mcdx'])

    def test_different_units_or_load_source_are_excluded(self):
        audit(self.out, 'si', 1.0, units={'force': 'kN', 'moment': 'kN-m'})
        audit(self.out, 'reactions', 1.0, load_source='reactions')
        audit(self.out, 'recorded', 1.1, units={'force': 'kip', 'moment': 'kip-in'})
        audit(self.out, 'legacy', 1.2)  # no units recorded: the parser's enforced kip and kip-in
        self.assertEqual([j['name'] for j in self.similar()], ['recorded/w.mcdx', 'legacy/w.mcdx'])

    def test_never_crosses_output_directories(self):
        other = self.root / 'other-tenant'
        audit(other, 'secret', 1.0)
        audit(self.out, 'mine', 1.2)
        # A symlinked folder or audit pointing at another directory is not followed either.
        (self.out / 'link').symlink_to(other / 'secret', target_is_directory=True)
        (self.out / 'mine' / 'x.audit.json').symlink_to(other / 'secret' / 'w.audit.json')
        found = self.similar()
        self.assertEqual([j['name'] for j in found], ['mine/w.mcdx'])
        self.assertNotIn('secret', json.dumps(found))
        self.assertNotIn(str(self.root), json.dumps(found))
        self.assertEqual([j['name'] for j in self.similar(other)], ['secret/w.mcdx'])
        session = Session(None, self.out)
        try:
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['mine/w.mcdx'])
        finally:
            session.temp.cleanup()

    def test_session_sees_conversions_written_by_others(self):
        # Batch or CLI conversions into the same output directory, and deletions there, are
        # picked up without a conversion in this session.
        audit(self.out, 'first', 1.1)
        session = Session(None, self.out)
        try:
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['first/w.mcdx'])
            audit(self.out, 'batch-job', 1.05)
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['batch-job/w.mcdx', 'first/w.mcdx'])
            (self.out / 'first' / 'w.audit.json').unlink()
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['batch-job/w.mcdx'])
        finally:
            session.temp.cleanup()

    def test_session_sees_audits_published_into_existing_nested_folders(self):
        # A batch run into a subfolder creates the job folder first and publishes the audit
        # later; the cached history must not miss it.
        audit(self.out, 'first', 1.1)
        (self.out / 'batch' / 'job').mkdir(parents=True)
        session = Session(None, self.out)
        try:
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['first/w.mcdx'])
            path = audit(self.out, 'batch/job', 1.05)
            # Force a distinct folder mtime even on coarse-timestamp filesystems.
            os.utime(path.parent, ns=(path.parent.stat().st_mtime_ns + 10**9,) * 2)
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))],
                             ['batch/job/w.mcdx', 'first/w.mcdx'])
            path.unlink()
            os.utime(path.parent, ns=(path.parent.stat().st_mtime_ns + 10**9,) * 2)
            self.assertEqual([j['name'] for j in jobs(session.inspect(self.report))], ['first/w.mcdx'])
        finally:
            session.temp.cleanup()

    def test_cli_inspect_lists_similar_jobs_with_history(self):
        import contextlib
        import io
        from mcdxkit import generate
        audit(self.out, 'job', 1.1)
        for argv, expected in (([], None), (['--history', str(self.out)], ['job/w.mcdx'])):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(generate.main([str(self.report), '--inspect', *argv]), 0)
            printed = json.loads(out.getvalue())
            self.assertEqual(expected, [j['name'] for j in printed['similar_jobs']] if 'similar_jobs' in printed else None)

    def test_malformed_audits_are_ignored(self):
        audit(self.out, 'good', 1.1)
        bad = {'not-json': 'not json', 'list': '[1, 2]', 'null': 'null', 'nan': None, 'text-envelope': None,
               'bool': None, 'no-cases': None, 'bad-units': None, 'bad-source': None, 'deep': '[' * 100000}
        for name, raw in bad.items():
            (self.out / name).mkdir(parents=True)
            (self.out / name / 'w.audit.json').write_text(raw if raw is not None else '{}')
        audit(self.out, 'nan', 1.0, envelope={**BASE, 'P': float('nan')})
        audit(self.out, 'text-envelope', 1.0, envelope={**BASE, 'Mz': '50000'})
        audit(self.out, 'bool', 1.0, envelope={**BASE, 'Vz': True})
        audit(self.out, 'no-cases', 1.0, cases=())
        audit(self.out, 'bad-units', 1.0, units='kip')
        audit(self.out, 'bad-source', 1.0, load_source='global')
        audit(self.out, 'bad-template', 1.05, template_name='../../etc/passwd')
        found = self.similar()
        self.assertEqual([j['name'] for j in found], ['bad-template/w.mcdx', 'good/w.mcdx'])
        self.assertIsNone(found[0]['template'])
        self.assertEqual(engine.inspect_report(self.report, history=AuditHistory(self.out))['review_checks']['errors'], [])

    def test_current_report_is_excluded(self):
        digest = hashlib.sha256(self.report.read_bytes()).hexdigest()
        audit(self.out, 'same-source', 1.0, sha=digest)
        audit(self.out, 'other', 1.1)
        self.assertEqual([j['name'] for j in self.similar()], ['other/w.mcdx'])

    def test_needs_a_selection_and_history(self):
        audit(self.out, 'a', 1.0)
        self.assertEqual(jobs(engine.inspect_report(self.report)), [])
        unresolved = self.root / 'u.txt'
        unresolved.write_text(summary([(1, 'Case A', 1000, 50000)]))
        self.assertEqual(self.similar(report=unresolved), [])

    def test_in_memory_history(self):
        job = PastJob(name='x/w.mcdx', date='2026-01-02', envelope=frozen_mapping(BASE), cases=(1,),
                      load_source='effects', units=frozen_mapping({'force': 'kip', 'moment': 'kip-in'}))
        from mcdxkit import group_report
        block = runner.review(group_report.parse(text()), [1], history=MemoryHistory(jobs=[job]))
        rows = [a for a in block['annotations'] if a['annotator'] == SimilarJobs.id]
        self.assertEqual([(r['data']['name'], r['data']['distance']) for r in rows], [('x/w.mcdx', 0.0)])

    def test_read_is_bounded(self):
        for i in range(5):
            audit(self.out, f'j{i}', 1.0 + i / 100, mtime=1_790_000_000 + i)
        examples, jobs, skipped = read_audits(self.out, limit=2)
        self.assertEqual(([j.name for j in jobs], skipped), (['j4/w.mcdx', 'j3/w.mcdx'], 0))


class ServiceTests(unittest.TestCase):
    def test_service_inspect_lists_real_conversions_and_outputs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template(root / 'reference.mcdx')
            first, second = root / 'first.txt', root / 'second.txt'
            first.write_text(text())
            second.write_text(text(1.1))
            out = root / 'results'
            session = Session(root / 'reference.mcdx', out)
            try:
                self.assertEqual(jobs(session.inspect(second)), [])
                uploaded = session.register(first, 'report')
                done = session.operation('/api/convert', {'id': uploaded['id']})
                found = jobs(session.inspect(second))
                worksheet = Path(done['output']).relative_to(out.resolve()).as_posix()
                self.assertEqual([(j['name'], j['template'], j['cases']) for j in found],
                                 [(worksheet, 'reference.mcdx', [1])])
                self.assertEqual(found[0]['difference_pct']['P'], -9.1)
                # Inspecting the converted report again does not list itself.
                self.assertEqual(jobs(session.inspect(first)), [])
            finally:
                session.temp.cleanup()
            # Advisory only: the worksheet is byte-identical whether or not similar jobs exist.
            outputs = []
            for name in ('empty', 'with-history'):
                target = root / name / 'w.mcdx'
                if name == 'with-history':
                    audit(root / name, 'past', 1.0)
                    self.assertEqual(len(jobs(engine.inspect_report(second, history=AuditHistory(root / name)))), 1)
                engine.convert(second, root / 'reference.mcdx', target)
                outputs.append([target.with_suffix(s).read_bytes() for s in ('.mcdx', '.cpd', '.html')])
            self.assertEqual(outputs[0], outputs[1])


if __name__ == '__main__':
    unittest.main()


class PlanIdentityTests(unittest.TestCase):
    def test_history_only_plugin_does_not_change_batch_job_identity(self):
        from mcdxkit.review import runner
        plan = runner.prepare('default')
        self.assertIn('similar_jobs', [e.id for e in plan.entries])
        self.assertNotIn('similar_jobs', [pair[0] for pair in plan.identity])
