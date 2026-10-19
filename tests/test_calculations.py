"""A project holds many calculations; each is an immutable report/template snapshot with its own runs."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from mcdxkit.documents import DocumentWorkflow, LEGACY_CALCULATION
from test_pipeline import report, template


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.flow = DocumentWorkflow(self.root / 'workspace')
        self.raw = self.root / 'loads.txt'; self.raw.write_text(report())
        self.template = self.root / 'template.mcdx'; template(self.template)
        self.pid = self.flow.create('Pier 3')['id']

    def attach(self, cases, name=None):
        return self.flow.attach_calculation(self.pid, self.raw, self.template, cases=cases, name=name)['calculation']

    def test_two_calculations_keep_their_own_cases_files_and_runs(self):
        first, second = self.attach([1, 7], 'Strength'), self.attach([2], 'Service')
        state = self.flow.get(self.pid)
        self.assertEqual([c['name'] for c in self.flow.calculations(state)], ['Strength', 'Service'])
        for calc in (first, second):
            folder = self.flow.calculation_folder(self.pid, calc)
            self.assertEqual(folder.parent.name, 'calculations')
            self.assertTrue((folder / 'loads.txt').is_file() and (folder / 'template.mcdx').is_file())
        a = self.flow.build(self.pid, None, first['id'])
        b = self.flow.build(self.pid, None, second['id'])
        self.assertEqual((a['calculation_id'], b['calculation_id']), (first['id'], second['id']))
        with zipfile.ZipFile(self.flow.artifact(self.pid, b['id'], 'package.zip')) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['calculation_id'], second['id'])
        self.assertEqual(manifest['calculation']['calculated'], True)
        # Case 2 (service) governs nothing in the strength calculation: the two runs used different loads.
        self.assertNotEqual(self.flow.require_calculation(state, first['id'])['options'],
                            self.flow.require_calculation(state, second['id'])['options'])
        context = self.flow.workflow_context(self.flow.get(self.pid))
        self.assertEqual([c['calculated_runs'] for c in context['calculations']], [1, 1])
        self.assertIsNone(context['raw_file'])          # no single calculation to describe

    def test_calculation_is_never_guessed(self):
        with self.assertRaisesRegex(ValueError, 'Connect'):
            self.flow.build(self.pid, None)
        only = self.attach([1, 7])
        self.assertEqual(self.flow.require_calculation(self.flow.get(self.pid))['id'], only['id'])
        self.attach([2])
        with self.assertRaisesRegex(ValueError, 'Choose which calculation'):
            self.flow.build(self.pid, None)
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.flow.build(self.pid, None, 'f' * 32)

    def test_tampering_with_any_calculation_snapshot_blocks_the_project(self):
        self.attach([1, 7]); second = self.attach([2])
        (self.flow.calculation_folder(self.pid, second) / 'loads.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.flow.verify(self.flow.get(self.pid))

    def test_failed_calculation_leaves_no_partial_folder_or_record(self):
        before = self.flow.get(self.pid)
        with self.assertRaises(ValueError):
            self.attach([999])
        self.assertEqual(self.flow.get(self.pid), before)
        folder = self.flow.root / self.pid / 'calculations'
        self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])

    def test_project_saved_before_multiple_calculations_still_verifies_and_builds(self):
        # Reconstruct the earlier single-calculation layout: files at the project root, fields at top level.
        snapshot = DocumentWorkflow.snapshot_calculation(self.flow.root / self.pid, self.raw, self.template, [1, 7], None, 'effects')
        state = self.flow.get(self.pid); state.update(snapshot)
        with sqlite3.connect(self.flow.root / 'projects.sqlite3') as db:
            db.execute('UPDATE projects SET data=? WHERE id=?', (json.dumps(state), self.pid))
        legacy = self.flow.require_calculation(self.flow.get(self.pid))
        self.assertEqual(legacy['id'], LEGACY_CALCULATION)
        self.assertEqual(self.flow.calculation_folder(self.pid, legacy), self.flow.root / self.pid)
        run = self.flow.build(self.pid, None)
        self.assertEqual(run['calculation_id'], LEGACY_CALCULATION)
        self.flow.artifact(self.pid, run['id'], 'package.zip')
        # A new calculation goes beside it; the legacy files are not moved.
        added = self.attach([2])
        self.assertTrue((self.flow.root / self.pid / 'loads.txt').is_file())
        self.assertEqual([c['id'] for c in self.flow.calculations(self.flow.get(self.pid))], [LEGACY_CALCULATION, added['id']])

    def test_reviewed_basis_saved_before_multiple_calculations_survives_a_second_calculation(self):
        from mcdxkit.jobs import JobStore
        snapshot = DocumentWorkflow.snapshot_calculation(self.flow.root / self.pid, self.raw, self.template, [1, 7], None, 'effects')
        state = self.flow.get(self.pid); state.update(snapshot)
        # A reviewed design basis in the earlier format: no calculation_id.
        state['drafts'] = [{'id': 'a' * 32, 'created': 1, 'revision': state['revision'], 'request': 'Basis', 'model': 'Synthetic',
                            'purpose': 'design_basis', 'content': {'sections': [{'heading': 'Basis', 'body': 'Keep the template inputs.', 'evidence_ids': ['E1']}],
                                                         'suggestions': [], 'gaps': []},
                            'evidence': [{'id': 'E1', 'source_id': 's', 'source_name': 'spec.pdf', 'source_sha256': 'h', 'document_id': None,
                                          'chunk_id': '', 'positions': [[1, 0, 0, 0, 0]], 'text': 'Keep the template inputs.'}],
                            'review': {'accepted': [], 'reviewer': 'Reviewer', 'note': 'Keep inputs', 'time': 1}}]
        with sqlite3.connect(self.flow.root / 'projects.sqlite3') as db:
            db.execute('UPDATE projects SET data=? WHERE id=?', (json.dumps(state), self.pid))
        self.attach([2])
        run = self.flow.build(self.pid, 'a' * 32)
        self.assertEqual(run['calculation_id'], LEGACY_CALCULATION)
        job = JobStore(self.flow.root).submit(self.pid, 'build', {'draft': 'a' * 32}, 'legacy-basis')
        self.assertEqual(job['state'], 'queued')

    def test_adding_a_calculation_does_not_make_queued_jobs_stale(self):
        from mcdxkit.jobs import JobStore
        first = self.attach([1, 7])
        store = JobStore(self.flow.root)
        queued = store.submit(self.pid, 'calculate', {}, 'calc-first')
        ask = store.submit(self.pid, 'ask', {'question': 'Limits?'}, 'ask-first')
        self.attach([2])
        for job in (queued, ask):
            store.validate_snapshot(job['id'], store.claim(job['id']))
        # With two calculations, a calculate job must name one.
        with self.assertRaisesRegex(ValueError, 'Choose which calculation'):
            store.submit(self.pid, 'calculate', {}, 'calc-unnamed')
        named = store.submit(self.pid, 'calculate', {'calculation': first['id']}, 'calc-named')
        self.assertEqual(named['state'], 'queued')

    def test_cli_templates_and_named_calculations(self):
        from mcdxkit.document_cli import main
        def run(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out): self.assertEqual(main(['--workspace', str(self.flow.root), *args]), 0)
            return json.loads(out.getvalue())
        added = run('templates', 'add', str(self.template), str(self.template))
        self.assertEqual(added[0]['id'], added[1]['id'])
        listed = run('templates', 'list')
        self.assertEqual([(t['name'], t['calculations']) for t in listed], [('template.mcdx', 0)])
        first = run('connect', self.pid, str(self.raw), '--library-template', added[0]['id'], '--cases', '1', '7', '--name', 'Strength')
        run('connect', self.pid, str(self.raw), '--template', str(self.template), '--cases', '2')
        calcs = run('calculations', self.pid)
        self.assertEqual([c['name'] for c in calcs], ['Strength', 'loads.txt + template.mcdx'])
        self.assertEqual(first['calculation']['template_id'], added[0]['id'])
        built = run('build', self.pid, '--calculation', calcs[0]['id'])
        self.assertEqual(built['calculation_id'], calcs[0]['id'])
        self.assertEqual(run('templates', 'list')[0]['calculations'], 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--workspace', str(self.flow.root), 'build', self.pid]), 2)
        self.assertIn('Choose which calculation', err.getvalue())


if __name__ == '__main__':
    unittest.main()
