"""Synthetic source-backed document lifecycle and external protocol contracts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_pipeline import report, template


class DocumentTests(unittest.TestCase):
    def setUp(self):
        config = patch('mcdxkit.document_clients.local_environment', return_value={})
        config.start(); self.addCleanup(config.stop)
        from mcdxkit.documents import DocumentWorkflow
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'loads.txt'; self.raw.write_text(report())
        self.template = self.root / 'template.mcdx'; template(self.template)
        self.flow = DocumentWorkflow(self.root / 'projects')
        self.project = self.flow.create('Example foundation', self.raw, self.template)
        self.pid = self.project['id']

    def evidence_draft(self):
        source = self.flow.add_source(self.pid, 'specification.pdf', b'%PDF-1.4\nsynthetic source')
        class Retrieval:
            def create_dataset(self, name): return 'dataset-1'
            def upload(self, dataset, name, raw): return 'document-1'
            def parse(self, dataset, ids): return None
            def documents(self, dataset): return [{'id': 'document-1', 'run': 'DONE', 'progress': 1, 'chunk_count': 1}]
            def retrieve(self, dataset, ids, question):
                return [{'id': 'chunk-1', 'document_id': 'document-1', 'content': 'Use downdrag of 6 kip for this example.', 'positions': [[1, 0, 100, 0, 100]]}]
        class Writer:
            model = 'test/model:free'
            def draft(self, context):
                return {'sections': [{'heading': 'Design basis', 'body': 'Downdrag follows the supplied example.', 'evidence_ids': ['E1']}],
                        'suggestions': [{'variable': 'P_downdrag', 'value': 6, 'unit': 'kip', 'reason': 'Example source specifies 6 kip.', 'evidence_ids': ['E1']}], 'gaps': []}
        self.flow.ingest(self.pid, Retrieval())
        self.flow.status(self.pid, Retrieval())
        draft = self.flow.draft(self.pid, 'Identify design requirements and downdrag.', Retrieval(), Writer())
        return source, draft

    def test_persists_originals_and_rejects_traversal_and_duplicate_bytes(self):
        from mcdxkit.documents import DocumentWorkflow
        first = self.flow.add_source(self.pid, 'notes.pdf', b'%PDF-1.4\nnotes')
        second = self.flow.add_source(self.pid, 'copy.pdf', b'%PDF-1.4\nnotes')
        self.assertEqual(first['id'], second['id'])
        restored = DocumentWorkflow(self.root / 'projects').get(self.pid)
        self.assertEqual(len(restored['sources']), 1)
        with self.assertRaises(ValueError): self.flow.add_source(self.pid, '../bad.pdf', b'%PDF-1.4')
        with self.assertRaises(ValueError): self.flow.get('../escape')

    def test_reviewed_draft_builds_real_calculation_and_cited_package(self):
        import zipfile
        _, draft = self.evidence_draft()
        with self.assertRaisesRegex(ValueError, 'review'): self.flow.build(self.pid, draft['id'])
        self.flow.review(self.pid, draft['id'], [0], 'Synthetic reviewer', 'Verified against source')
        result = self.flow.build(self.pid, draft['id'])
        package = self.flow.artifact(self.pid, result['id'], 'package.zip')
        with zipfile.ZipFile(package) as z:
            manifest = json.loads(z.read('manifest.json'))
            audit = json.loads(z.read('calculation.audit.json'))
            self.assertTrue(audit['calculation']['calculated'])
            self.assertFalse(manifest['native_execution_verified'])
            self.assertEqual(manifest['review']['accepted'], [0])
            self.assertIn('specification.pdf', z.read('report.md').decode())
            self.assertIn('6', z.read('calculation.cpd').decode())
            self.assertIn('report.html', z.namelist())
        self.assertEqual(self.flow.get(self.pid)['runs'][0]['id'], result['id'])

    def test_forged_citation_wrong_units_and_load_overrides_are_rejected(self):
        from mcdxkit.documents import validate_draft
        _, draft = self.evidence_draft()
        context = self.flow.get(self.pid)
        for mutation in ('citation', 'unit', 'load'):
            payload = json.loads(json.dumps(draft['content']))
            if mutation == 'citation': payload['sections'][0]['evidence_ids'] = ['E999']
            if mutation == 'unit': payload['suggestions'][0]['unit'] = 'MPa'
            if mutation == 'load': payload['suggestions'][0].update(variable='P_a', unit='kip')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_draft(payload, context['template_inputs'], draft['evidence'])

    def test_new_source_invalidates_review_and_source_tampering_blocks_build(self):
        source, draft = self.evidence_draft()
        self.flow.review(self.pid, draft['id'], [], 'Reviewer', 'Keep template input')
        self.flow.add_source(self.pid, 'revision.pdf', b'%PDF-1.4\nrevision')
        with self.assertRaisesRegex(ValueError, 'changed'): self.flow.build(self.pid, draft['id'])
        self.flow.source_path(self.pid, source['id']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'changed'): self.flow.draft(self.pid, 'requirements')

    def test_missing_configuration_has_actionable_error_without_changing_calculation(self):
        from mcdxkit.document_clients import ModelConfig
        with patch.dict('os.environ', {}, clear=True), self.assertRaisesRegex(ValueError, 'RAGFLOW'):
            ModelConfig.from_env().ragflow()
        self.assertEqual(self.project['title'], 'Example foundation')


class ClientTests(unittest.TestCase):
    def test_paid_and_random_models_are_rejected_before_request(self):
        from mcdxkit.document_clients import OpenRouter
        for model in ('paid/model', 'openrouter/auto', 'openrouter/free'):
            with self.subTest(model=model), self.assertRaises(ValueError): OpenRouter('secret', model)

    def test_ragflow_business_errors_fail_even_with_http_success(self):
        from mcdxkit.document_clients import RAGFlow
        client = RAGFlow('http://localhost:9380', 'secret', 'local-embedding')
        with patch.object(client, 'request', return_value={'code': 106, 'message': 'secret backend text'}):
            with self.assertRaisesRegex(ValueError, '106') as caught: client.create_dataset('Example')
            self.assertNotIn('secret backend text', str(caught.exception))


class ProtocolTests(unittest.TestCase):
    """Real HTTP transport against deterministic upstream protocol responses."""
    def setUp(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.requests = []
        self.response = {'code': 0, 'data': {'id': 'dataset-1'}}
        self.code = 200
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append((self.path, dict(self.headers), data))
                self.send_response(owner.code)
                if owner.code == 302: self.send_header('Location', '/unexpected')
                self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(owner.response).encode())
            def log_message(self, *args): pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

    def test_dataset_upload_retrieval_and_free_drafting_protocol(self):
        from mcdxkit.document_clients import RAGFlow, OpenRouter
        rag = RAGFlow(self.url, 'rag-secret', 'bge-m3@Ollama')
        self.assertEqual(rag.create_dataset('example'), 'dataset-1')
        self.assertEqual(json.loads(self.requests[-1][2])['embedding_model'], 'bge-m3@Ollama')
        self.response = {'code': 0, 'data': [{'id': 'document-1'}]}
        self.assertEqual(rag.upload('dataset-1', 'source.pdf', b'%PDF-1.4'), 'document-1')
        self.assertIn(b'name="file"', self.requests[-1][2])
        self.response = {'code': 0, 'data': {'chunks': [{'content': '6 kip', 'document_id': 'document-1'}]}}
        self.assertEqual(rag.retrieve('dataset-1', ['document-1'], 'downdrag')[0]['content'], '6 kip')
        self.assertEqual(json.loads(self.requests[-1][2])['document_ids'], ['document-1'])
        writer = OpenRouter('router-secret', 'example/model:free'); writer.url = self.url
        self.response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"sections": [], "suggestions": [], "gaps": ["missing evidence"]}'}}]}
        self.assertEqual(writer.draft({'evidence': []})['gaps'], ['missing evidence'])
        sent = json.loads(self.requests[-1][2])
        self.assertEqual(sent['model'], 'example/model:free')
        self.assertFalse(sent['provider']['allow_fallbacks'])
        self.assertEqual(sent['provider']['data_collection'], 'deny')
        self.assertNotIn('models', sent)

    def test_no_redirect_credentials_or_automatic_rate_limit_retry(self):
        from mcdxkit.document_clients import RAGFlow
        rag = RAGFlow(self.url, 'private-secret', 'local')
        for status in (302, 429):
            self.code = status
            with self.assertRaisesRegex(ValueError, str(status)):
                rag.create_dataset('example')
        self.assertEqual(len(self.requests), 2)

    def test_truncated_draft_does_not_become_an_accepted_document(self):
        from mcdxkit.document_clients import OpenRouter
        writer = OpenRouter('secret', 'example/model:free'); writer.url = self.url
        self.response = {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}
        with self.assertRaisesRegex(ValueError, 'truncated'): writer.draft({})


class BrowserAssetTests(unittest.TestCase):
    def test_pdf_renderer_and_worker_are_bundled_with_license(self):
        from mcdxkit.server import STATIC
        self.assertTrue((STATIC / 'pdfjs' / 'pdf.mjs').is_file(), 'Original-source viewing needs the actual PDF.js renderer')
        self.assertTrue((STATIC / 'pdfjs' / 'pdf.worker.mjs').is_file())
        self.assertIn('Apache', (STATIC / 'pdfjs' / 'LICENSE').read_text())


class AdditionalBoundaryTests(DocumentTests):
    def test_current_ingestion_states_and_legacy_run_require_completed_nonempty_chunks(self):
        self.evidence_draft()
        self.flow.add_source(self.pid, 'pending.pdf', b'%PDF-1.4\npending synthetic source')
        rows = [({'ingestion_status':'COMPLETED','chunk_count':2}, 'ready'),
                ({'ingestion_status':'COMPLETED','chunk_count':0}, 'failed'),
                ({'ingestion_status':'COMPLETED','chunk_count':'2'}, 'failed'),
                ({'ingestion_status':'FAILED','chunk_count':2}, 'failed'),
                ({'ingestion_status':'STOPPED','chunk_count':2}, 'failed'),
                ({'ingestion_status':'UNSTART','chunk_count':0}, 'uploaded'),
                ({'run':'UNSTART','chunk_count':0}, 'uploaded'),
                ({'run':'0','chunk_count':0}, 'uploaded'),
                ({'ingestion_status':'RUNNING','progress':1,'chunk_count':2}, 'parsing'),
                ({'ingestion_status':'UNKNOWN','run':'DONE','chunk_count':2}, 'parsing'),
                ({'run':'DONE','chunk_count':2}, 'ready')]
        for fields, expected in rows:
            class Status:
                def documents(self, dataset, row=fields): return [{'id':'document-1', **row}]
            with self.subTest(fields=fields):
                state = self.flow.status(self.pid, Status())
                self.assertEqual(state['sources'][0]['status'], expected)
                self.assertEqual(state['sources'][1]['status'], 'uploaded')

    def test_resume_unstarted_document_reuses_upload_and_resets_old_progress(self):
        self.evidence_draft()
        class Resume:
            def __init__(self): self.parsed = []
            def documents(self, dataset):
                return [{'id': 'document-1', 'ingestion_status': 'UNSTART', 'progress': 1, 'chunk_count': 0}]
            def upload(self, *args): raise AssertionError('Resume must reuse the uploaded document')
            def parse(self, dataset, ids): self.parsed.extend(ids)
        client = Resume()
        self.flow.status(self.pid, client)
        state = self.flow.ingest(self.pid, client)
        self.assertEqual(client.parsed, ['document-1'])
        self.assertEqual(state['sources'][0]['document_id'], 'document-1')
        self.assertEqual(state['sources'][0]['status'], 'parsing')
        self.assertEqual(state['sources'][0]['progress'], 0)

    def test_changed_packaged_artifact_is_not_served(self):
        _, draft = self.evidence_draft()
        self.flow.review(self.pid, draft['id'], [], 'Reviewer', 'Keep existing inputs')
        result = self.flow.build(self.pid, draft['id'])
        self.flow.artifact(self.pid, result['id'], 'report.html').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.flow.artifact(self.pid, result['id'], 'report.html')

    def test_calculation_failure_never_publishes_a_package(self):
        _, draft = self.evidence_draft()
        self.flow.review(self.pid, draft['id'], [0], 'Reviewer', 'Example input')
        with patch('mcdxkit.documents.engine.convert', side_effect=ValueError('Unsupported formula')):
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                self.flow.build(self.pid, draft['id'])
        self.assertEqual(self.flow.get(self.pid)['runs'], [])
        self.assertEqual(list((self.root/'projects'/self.pid/'runs').iterdir()), [])

    def test_foreign_and_empty_retrieval_never_produces_draft(self):
        self.evidence_draft()
        class Foreign:
            def retrieve(self, *args): return [{'document_id': 'foreign', 'content': 'Unrelated evidence'}]
        class Empty:
            def retrieve(self, *args): return []
        class Writer:
            model = 'example/model:free'
            def draft(self, *args): raise AssertionError('No model request should occur')
        for client in (Foreign(), Empty()):
            with self.assertRaises(ValueError): self.flow.draft(self.pid, 'Requirements', client, Writer())
        self.assertEqual(len(self.flow.get(self.pid)['drafts']), 1)

    def test_report_escapes_source_text_and_does_not_treat_no_checks_as_pass(self):
        from mcdxkit.documents import render_html
        _, draft = self.evidence_draft()
        self.flow.review(self.pid, draft['id'], [], 'Reviewer', 'Keep inputs')
        result = self.flow.build(self.pid, draft['id'])
        manifest = json.loads(self.flow.artifact(self.pid, result['id'], 'manifest.json').read_text())
        manifest['draft']['sections'][0]['body'] = '<script>alert(1)</script>'
        rendered = render_html(manifest, {'cases': [1,7], 'envelope': {'P':140}, 'check_summary': {'status':'no checks found'}})
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('no checks found', rendered)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        config = patch('mcdxkit.document_clients.local_environment', return_value={})
        config.start(); self.addCleanup(config.stop)

    def test_browser_settings_persist_privately_and_never_return_keys(self):
        from mcdxkit.document_clients import ModelConfig, save_configuration
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            root = Path(folder)
            public = save_configuration(root, {'url': 'http://127.0.0.1:9380', 'rag_key': 'private-rag-key',
                'embedding': 'bge-m3@Ollama', 'router_key': 'private-router-key', 'model': 'example/model:free', 'reranker': ''})
            self.assertNotIn('private-', json.dumps(public))
            self.assertTrue(public['openrouter_configured'])
            self.assertEqual(ModelConfig.for_workspace(root).router_key, 'private-router-key')
            self.assertEqual((root/'.credentials.json').stat().st_mode & 0o777, 0o600)
            save_configuration(root, {'router_key': '', 'model': 'other/model:free'})
            self.assertEqual(ModelConfig.for_workspace(root).router_key, 'private-router-key')
            with self.assertRaisesRegex(ValueError, 'key'):
                save_configuration(root, {'url': 'http://different-host:9380'})
            with self.assertRaises(ValueError): save_configuration(root, {'model': 'paid/model'})
            self.assertEqual(ModelConfig.for_workspace(root).model, 'other/model:free')
