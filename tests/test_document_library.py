"""A document library works before engineering inputs exist; linking preserves its evidence."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from mcdxkit.documents import DocumentWorkflow
from test_pipeline import report, template


class Retrieval:
    def create_dataset(self, name): return 'dataset'
    def upload(self, dataset, name, raw): return 'source'
    def parse(self, dataset, ids): pass
    def documents(self, dataset): return [{'id': 'source', 'run': 'DONE', 'chunk_count': 1, 'progress': 1}]
    def retrieve(self, dataset, ids, question):
        return [{'id': 'clause', 'document_id': 'source', 'content': 'Include assumptions and calculations in the report.', 'positions': [[1, 0, 200, 0, 100]]}]


class Writer:
    model = 'synthetic/model:free'
    def draft(self, context):
        self.context = context
        return {'sections': [{'heading': 'Required contents', 'body': 'Include assumptions and calculations.', 'evidence_ids': ['E1']}], 'suggestions': [], 'gaps': []}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.flow = DocumentWorkflow(self.root/'library')
        self.project = self.flow.create('Bridge documents')
        self.pid = self.project['id']
        self.raw = self.root/'loads.txt'; self.raw.write_text(report())
        self.template = self.root/'template.mcdx'; template(self.template)

    def ready(self):
        source = self.flow.add_source(self.pid, 'requirements.pdf', b'%PDF-1.4\nSynthetic requirements')
        self.flow.ingest(self.pid, Retrieval()); self.flow.status(self.pid, Retrieval())
        return source

    def test_ask_without_files_persists_citations_and_never_invents_inputs(self):
        source = self.ready(); writer = Writer()
        answer = self.flow.ask(self.pid, 'What must the report include?', Retrieval(), writer)
        self.assertEqual(writer.context['purpose'], 'answer')
        self.assertEqual(writer.context['inputs'], [])
        self.assertIsNone(writer.context['calculation'])
        self.assertEqual(answer['evidence'][0]['source_id'], source['id'])
        restored = DocumentWorkflow(self.root/'library').get(self.pid)
        self.assertEqual(restored['answers'][0], answer)
        self.assertEqual(restored['drafts'], [])
        self.assertFalse((self.flow.root/self.pid/'loads.txt').exists())

    def test_link_then_draft_and_calculate_preserves_library_and_answers(self):
        import zipfile
        self.ready(); writer = Writer()
        answer = self.flow.ask(self.pid, 'Requirements?', Retrieval(), writer)
        before = self.flow.get(self.pid)
        linked = self.flow.attach_calculation(self.pid, self.raw, self.template, cases=[1, 7])
        self.assertEqual(linked['dataset_id'], before['dataset_id'])
        self.assertEqual(linked['sources'], before['sources'])
        self.assertEqual(linked['answers'], [answer])
        draft = self.flow.draft(self.pid, 'Prepare the design basis.', Retrieval(), writer)
        context = writer.context['calculation']
        self.assertEqual(context['selected_cases'], [1, 7])
        self.assertEqual(context['envelope'], {'P': 140, 'Vy': 24, 'Vz': 8, 'My': 190, 'Mz': 850})
        self.assertNotIn(str(self.root), json.dumps(writer.context))
        self.flow.review(self.pid, draft['id'], [], 'Test reviewer', 'Preserve template values')
        result = self.flow.build(self.pid, draft['id'])
        with zipfile.ZipFile(self.flow.artifact(self.pid, result['id'], 'package.zip')) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertEqual(manifest['calculation_context'], context)
            self.assertTrue(manifest['calculation']['calculated'])
        # A second calculation is added beside the first; it does not invalidate the first one's design basis.
        second = self.flow.attach_calculation(self.pid, self.raw, self.template, cases=[1])['calculation']
        self.assertNotEqual(second['id'], linked['calculation']['id'])
        self.assertEqual(self.flow.get(self.pid)['revision'], before['revision'])
        self.assertEqual(self.flow.build(self.pid, draft['id'])['calculation_id'], linked['calculation']['id'])

    def test_unlinked_draft_and_build_have_actionable_errors(self):
        with self.assertRaisesRegex(ValueError, 'Connect'):
            self.flow.draft(self.pid, 'Design basis')
        with self.assertRaisesRegex(ValueError, 'Connect'):
            self.flow.build(self.pid, '0'*32)

    def test_failed_link_leaves_library_usable_and_inputs_absent(self):
        self.ready(); before = self.flow.get(self.pid)
        with self.assertRaises(ValueError):
            self.flow.attach_calculation(self.pid, self.raw, self.template, cases=[999])
        self.assertEqual(self.flow.get(self.pid), before)
        self.assertFalse((self.flow.root/self.pid/'loads.txt').exists())
        self.flow.ask(self.pid, 'Requirements?', Retrieval(), Writer())

    def test_no_partial_pair_and_no_model_suggestions_without_template(self):
        with self.assertRaisesRegex(ValueError, 'both'):
            self.flow.create('Partial', self.raw)
        self.ready()
        class BadWriter(Writer):
            def draft(self, context):
                payload = super().draft(context)
                payload['suggestions'] = [{'variable':'P_a', 'value': 7, 'unit':'kip', 'reason':'Invented', 'evidence_ids':['E1']}]
                return payload
        with self.assertRaises(ValueError):
            self.flow.ask(self.pid, 'Requirements?', Retrieval(), BadWriter())
        self.assertEqual(self.flow.get(self.pid)['answers'], [])

    def test_cli_library_create_and_connect(self):
        from mcdxkit.document_cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['--workspace', str(self.flow.root), 'create', '--title', 'CLI library']), 0)
        pid = json.loads(output.getvalue())['id']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--workspace', str(self.flow.root), 'connect', pid, str(self.raw), '--template', str(self.template), '--cases', '1', '7']), 0)
        self.assertEqual(self.flow.require_calculation(self.flow.get(pid))['options']['cases'], [1, 7])
