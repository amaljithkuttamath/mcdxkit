"""Exercise the actual loopback HTTP boundary using synthetic engineering inputs."""
import csv
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import urlencode

from test_pipeline import report, template
from test_summary_only import summary_only
from mcdxkit import server as server_module
from mcdxkit.server import create_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        config = mock.patch('mcdxkit.document_clients.local_environment', return_value={})
        config.start(); self.addCleanup(config.stop)
        credentials = mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': '', 'OPENROUTER_API_KEY_FILE': '', 'MCDXKIT_RAGFLOW_API_KEY': '', 'MCDXKIT_RAGFLOW_API_KEY_FILE': ''})
        credentials.start(); self.addCleanup(credentials.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.template = self.root / 'reference.mcdx'
        template(self.template)
        self.server = create_server(port=0, template=self.template, output_dir=self.root / 'results')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.request('GET', '/api/session', authenticated=False)[1]['token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, method, path, data=None, headers=None, authenticated=True):
        h = {'X-MCDXKit-Token': self.token} if authenticated else {}
        h.update(headers or {})
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            h['Content-Type'] = 'application/json'
        elif data is not None:
            h.setdefault('Content-Type', 'application/octet-stream')
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.request(method, path, data, h)
        response = conn.getresponse()
        raw = response.read()
        value = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        conn.close()
        return response.status, value

    def upload(self, name='loads.gp11t', content=None, kind='report'):
        return self.request('POST', '/api/upload?' + urlencode({'name': name, 'kind': kind}),
                            report().encode() if content is None else content)

    def test_chatgpt_signin_routes_require_session_and_preserve_callback_exception_scope(self):
        self.assertEqual(self.request('GET','/api/chatgpt',authenticated=False)[0],403)
        status,body=self.request('GET','/api/chatgpt')
        self.assertEqual(status,200);self.assertTrue(body['available'])
        self.assertEqual(body['accounts'],[])
        self.assertEqual(self.request('POST','/api/chatgpt/connect',{},authenticated=False)[0],403)
        self.assertEqual(self.request('POST','/api/chatgpt/connect',{},headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request('GET','/auth/start?handle=wrong',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        # The OAuth return alone may be cross-site; unknown state still cannot grant an account.
        with mock.patch('mcdxkit.chatgpt._call',side_effect=AssertionError('Unexpected external request')):
            self.assertEqual(self.request('GET','/auth/callback?state=wrong&code=wrong',headers={'Sec-Fetch-Site':'cross-site'})[0],303)
        self.assertEqual(self.request('GET','/api/chatgpt')[1]['accounts'],[])
        self.assertEqual(self.request('POST','/api/chatgpt/connect',{'project':'f'*32,'view':'ask'})[0],400)

    def test_background_jobs_require_session_and_remain_inspectable(self):
        status, project = self.request('POST', '/api/documents/create', {'title': 'Background example'})
        self.assertEqual(status, 200)
        data = {'project': project['id'], 'action': 'ask', 'payload': {'question': 'What is the limit?'}, 'request_key': 'one'}
        status, _ = self.request('POST', '/api/jobs', data, authenticated=False)
        self.assertIn(status, (401, 403))
        status, job = self.request('POST', '/api/jobs', data)
        self.assertEqual(status, 202, job)
        status, restored = self.request('GET', '/api/jobs/' + job['id'])
        self.assertEqual(status, 200)
        self.assertEqual(restored['id'], job['id'])
        self.assertNotIn('owner', restored)
        self.assertNotIn('request_key', restored)
        status, listing = self.request('GET', '/api/jobs?project=' + project['id'])
        self.assertEqual(status, 200)
        self.assertEqual(listing[0]['id'], job['id'])
        status, _ = self.request('POST', '/api/jobs/' + job['id'] + '/cancel', {})
        self.assertEqual(status, 200)
        status, stream = self.request('GET', '/api/jobs/' + job['id'] + '/events')
        self.assertEqual(status, 200)
        self.assertIn(b'event: job', stream)
        self.assertIn(job['id'].encode(), stream)
        self.assertNotIn(b'request_key', stream)
        self.assertIn(self.request('GET', '/api/jobs/' + job['id'] + '/events', authenticated=False)[0], (401, 403))

    def test_agui_stream_preserves_session_guards_terminal_state_and_reconnect(self):
        status, project = self.request('POST', '/api/documents/create', {'title': 'AG-UI boundary'})
        self.assertEqual(status, 200)
        with mock.patch('mcdxkit.jobs.JobStore.dispatch'):
            status, job = self.request('POST', '/api/jobs', {'project':project['id'], 'action':'ask',
                'payload':{'question':'What applies?'}, 'request_key':'agui-boundary'})
            self.assertEqual(status, 202)
            self.assertEqual(self.request('POST', '/api/jobs/'+job['id']+'/cancel', {})[0], 200)
        url='/api/jobs/'+job['id']+'/agui'
        self.assertIn(self.request('GET', url, authenticated=False)[0], (401,403))
        status, body=self.request('GET', url)
        self.assertEqual(status, 200)
        events=[json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith('data: ')]
        self.assertEqual([e['type'] for e in events], ['RUN_STARTED','RUN_FINISHED'])
        self.assertEqual(events[-1]['outcome']['type'], 'cancelled')
        self.assertNotIn(b'request_key', body)
        self.assertEqual(self.request('GET', url, headers={'Last-Event-ID':'end'}), (200,b''))
        self.assertEqual(self.request('GET', url, headers={'Last-Event-ID':'bad-cursor'})[0], 400)
        self.assertEqual(self.request('GET', '/api/jobs/'+'0'*32+'/agui')[0], 404)
        schema=self.server.server.config.app.openapi()
        self.assertIn('/api/jobs/{jid}/agui', schema['paths'])

    def test_service_discovery_is_authenticated_and_rejects_endpoint_overrides(self):
        with mock.patch('mcdxkit.document_services.ServiceManager.discover', return_value={'service': {'state': 'runtime_stopped'}}) as discover:
            status, _ = self.request('POST', '/api/documents/discover', {}, authenticated=False)
            self.assertIn(status, (401, 403)); discover.assert_not_called()
            status, body = self.request('POST', '/api/documents/discover', {})
            self.assertEqual(status, 200, body)
            self.assertEqual(body['service']['state'], 'runtime_stopped')
            status, _ = self.request('POST', '/api/documents/discover', {'url': 'http://untrusted'})
            self.assertEqual(status, 400)
            self.assertEqual(discover.call_count, 1)

    def test_openrouter_connection_requires_session_and_rejects_extra_fields(self):
        with mock.patch('mcdxkit.document_services.connect_openrouter', return_value={'router_key_saved': True}) as connect:
            status, _ = self.request('POST', '/api/documents/connect-openrouter', {}, authenticated=False)
            self.assertIn(status, (401, 403)); connect.assert_not_called()
            status, body = self.request('POST', '/api/documents/connect-openrouter', {'router_key': 'private', 'model':'a/b:free', 'search_model':'c/d:free'})
            self.assertEqual(status, 200)
            self.assertNotIn('private', json.dumps(body))
        status, _ = self.request('POST', '/api/documents/connect-openrouter', {'url':'http://untrusted'})
        self.assertEqual(status, 400)

    def test_model_discovery_requires_session_and_never_persists_key(self):
        with mock.patch('mcdxkit.document_clients.HTTPClient.request', return_value={'data': []}) as request:
            status, body = self.request('POST', '/api/documents/models', {'router_key': 'private-test-key'}, authenticated=False)
            self.assertIn(status, (401, 403))
            request.assert_not_called()
            status, body = self.request('POST', '/api/documents/models', {'router_key': 'private-test-key'})
            self.assertEqual(status, 200, body)
            self.assertEqual(body, {'models': [], 'free_only': True, 'embedding_models': []})
            self.assertEqual(request.call_args_list, [mock.call('GET', '/models/user'), mock.call('GET', '/embeddings/models')])
            self.assertFalse((self.root / 'results/.documents/.credentials.json').exists())
            status, _ = self.request('POST', '/api/documents/models', {'url': 'http://elsewhere', 'router_key': 'private-test-key'})
            self.assertEqual(status, 400)

    def test_document_projects_reuse_uploaded_inputs_and_protect_sources(self):
        _, raw = self.upload()
        status, project = self.request('POST', '/api/documents/create', {'id': raw['id'], 'title': 'Synthetic project'})
        self.assertEqual(status, 200, project)
        pid = project['id']
        status, source = self.request('POST', '/api/documents/upload?project=' + pid + '&name=spec.pdf', b'%PDF-1.4\nSynthetic')
        self.assertEqual(status, 200, source)
        path = '/api/documents/' + pid + '/sources/' + source['id']
        self.assertEqual(self.request('GET', path)[1], b'%PDF-1.4\nSynthetic')
        self.assertEqual(self.request('GET', path, authenticated=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/documents/create', {'id': '/etc/passwd', 'title': 'Forbidden'})[0], 404)
        self.assertEqual(self.request('POST', '/api/documents/show', {'project': '../escape'})[0], 400)
        self.assertEqual(self.request('GET', '/documents')[0], 200)

    def test_answer_audit_requires_session_and_project_ownership_and_is_in_openapi(self):
        from mcdxkit.documents import DocumentWorkflow
        flow = DocumentWorkflow(self.root / 'results' / '.documents')
        pid = flow.create('Audit boundary')['id']
        other = flow.create('Other library')['id']
        aid = 'a' * 32
        with flow.edit(pid) as state:
            state['answers'].append({'id': aid, 'request': 'Synthetic question', 'created': 1,
                                     'evidence': [], 'content': {'sections': [], 'gaps': []}})
        path = f'/api/documents/{pid}/answers/{aid}/audit'
        before = flow.get(pid)
        self.assertEqual(self.request('GET', path, authenticated=False)[0], 403)
        self.assertEqual(self.request('GET', path, headers={'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('GET', f'/api/documents/{other}/answers/{aid}/audit')[0], 404)
        self.assertEqual(self.request('GET', f'/api/documents/{pid}/answers/'+ 'b'*32 + '/audit')[0], 404)
        status, receipt = self.request('GET', path)
        self.assertEqual(status, 200)
        self.assertEqual(receipt['answer']['id'], aid)
        self.assertEqual(receipt['project']['id'], pid)
        self.assertEqual(flow.get(pid), before)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request('GET', path, headers={'X-MCDXKit-Token': self.token})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.getheader('Content-Disposition'), f'attachment; filename="mcdxkit-answer-{aid}-audit.json"')
        finally:
            conn.close()
        schema = self.server.server.config.app.openapi()
        self.assertIn('/api/documents/{pid}/answers/{aid}/audit', schema['paths'])

    def test_document_library_needs_no_inputs_and_connects_existing_uploads(self):
        status, library = self.request('POST', '/api/documents/create', {'title': 'Independent library'})
        self.assertEqual(status, 200, library)
        self.assertNotIn('report_sha256', library)  # Even with a server default template.
        pid = library['id']
        _, source = self.request('POST', '/api/documents/upload?project='+pid+'&name=notes.pdf', b'%PDF-1.4 synthetic')
        self.assertEqual(self.request('POST', '/api/documents/ask', {'project':pid, 'question':'Requirements?'}, authenticated=False)[0], 403)
        status, error = self.request('POST', '/api/documents/draft', {'project':pid, 'request':'Design basis'})
        self.assertEqual(status, 400); self.assertIn('Connect', error['error'])
        _, raw = self.upload()
        status, linked = self.request('POST', '/api/documents/connect', {'project':pid, 'id':raw['id'], 'cases':[1,7]})
        self.assertEqual(status, 200, linked)
        self.assertEqual(linked['sources'][0]['id'], source['id'])
        self.assertEqual(linked['calculation']['options']['cases'], [1,7])
        status, restored = self.request('POST', '/api/documents/calculation', {'project':pid})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored['options']['cases'], [1,7])
        status, inspection = self.request('POST', '/api/inspect', {'id':restored['report']['id'], 'template_id':restored['template']['id'], **restored['options']})
        self.assertEqual(status, 200, inspection)
        self.assertEqual(inspection['envelope']['P'], 140)

        # A second calculation is allowed; the existing one must then be named explicitly.
        status, second = self.request('POST', '/api/documents/connect', {'project':pid, 'id':raw['id'], 'cases':[1]})
        self.assertEqual(status, 200, second)
        status, error = self.request('POST', '/api/documents/calculation', {'project':pid})
        self.assertEqual(status, 400); self.assertIn('Choose which calculation', error['error'])
        status, chosen = self.request('POST', '/api/documents/calculation', {'project':pid, 'calculation':second['calculation']['id']})
        self.assertEqual((status, chosen['options']['cases']), (200, [1]))

    def test_headerless_upload_requires_case_selection_then_calculates(self):
        status, uploaded = self.upload('summary.txt', summary_only().encode())
        self.assertEqual(status, 200, uploaded)
        self.assertTrue(uploaded['inspection']['summary_only'])
        self.assertEqual(uploaded['inspection']['selected_cases'], [])
        self.assertEqual(self.request('POST', '/api/convert', {'id': uploaded['id']})[0], 400)
        status, inspected = self.request('POST', '/api/inspect', {'id': uploaded['id'], 'cases': [1, 7]})
        self.assertEqual(status, 200, inspected)
        self.assertEqual(inspected['envelope'], {'P': 140, 'Vy': 24, 'Vz': 8, 'My': 190, 'Mz': 850})
        status, converted = self.request('POST', '/api/convert', {'id': uploaded['id'], 'cases': [1, 7]})
        self.assertEqual(status, 200, converted)
        self.assertTrue(converted['calculation']['calculated'])
        _, raw = self.request('GET', '/api/download/' + converted['worksheet']['id'])
        dest = self.root / 'summary-output.mcdx';dest.write_bytes(raw)
        from evaluate_fixture import evaluate
        self.assertEqual(evaluate(dest)['P_u'], 145)

    def test_custom_template_selection_and_explicit_mapping_reach_conversion(self):
        from test_template_inputs import custom_template
        other=self.root/'custom.mcdx';custom_template(other)
        status,uploaded=self.upload('custom.mcdx',other.read_bytes(),'template')
        self.assertEqual(status,200,uploaded)
        template_id=uploaded['id']
        status,found=self.request('POST','/api/template-inputs',{'template_id':template_id})
        self.assertEqual(status,200,found);self.assertTrue(found['mapping_required'])
        self.assertIn('AxialCustom',[row['variable'] for row in found['inputs']])
        self.assertEqual(self.request('POST','/api/template-inputs',{},authenticated=False)[0],403)
        self.assertEqual(self.request('POST','/api/template-inputs',{'template_id':'invalid'})[0],404)
        _,source=self.upload('summary.txt',summary_only().encode())
        options={'id':source['id'],'template_id':template_id,'cases':[1,7],
                 'input_map':{'AxialCustom':'P','TransverseCustom':'Vz'}}
        status,found=self.request('POST','/api/inspect',options)
        self.assertEqual(status,200,found)
        self.assertEqual(found['template_inputs']['input_map'],options['input_map'])
        self.assertEqual(found['template_validation']['status'],'ready')
        status,blocked=self.request('POST','/api/inspect',{**options,'input_map':{'M_uy':'P'}})
        self.assertEqual(status,200,blocked)
        self.assertEqual(blocked['template_validation']['status'],'blocked')
        self.assertEqual(list((self.root/'results').glob('*/*.mcdx')),[])
        status,result=self.request('POST','/api/convert',options)
        self.assertEqual(status,200,result)
        _,audit=self.request('GET','/api/download/'+result['audit']['id'])
        self.assertEqual(json.loads(audit)['input_map'],options['input_map'])

    def test_inspect_convert_download_and_validate_native_formulas(self):
        status, uploaded = self.upload()
        self.assertEqual(status, 200, uploaded)
        self.assertEqual(uploaded['inspection']['selected_cases'], [1, 7])
        status, inspected = self.request('POST', '/api/inspect', {'id': uploaded['id'], 'cases': [1]})
        self.assertEqual(inspected['envelope']['P'], 120)
        status, preview = self.request('POST', '/api/preview', {'id': uploaded['id'], 'cases': [1, 7]})
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview['preview'])
        self.assertEqual(len([c for c in preview['diff']['changes'] if c['kind'] == 'changed']), 4)
        self.assertEqual([p.name for p in (self.root / 'results').iterdir()], ['.documents'])
        _, source = self.request('POST', '/api/view', {'id': uploaded['id']})
        self.assertEqual(source['text'], report())
        status, converted = self.request('POST', '/api/convert', {'id': uploaded['id'], 'cases': [1, 7]})
        self.assertEqual(status, 200, converted)
        self.assertFalse(converted['validation']['native_execution_verified'])
        self.assertEqual(converted['validation']['cached_results'], 0)
        self.assertTrue(converted['calculation']['calculated'])
        for key in ('open_worksheet', 'calculated_worksheet'):
            self.assertEqual(self.request('GET', '/api/download/' + converted[key]['id'])[0], 200)
        _, raw = self.request('GET', '/api/download/' + converted['worksheet']['id'])
        dest = self.root / 'download.mcdx'
        dest.write_bytes(raw)
        from evaluate_fixture import evaluate
        self.assertEqual(evaluate(dest)['P_u'], 145)
        _, audit = self.request('GET', '/api/download/' + converted['audit']['id'])
        self.assertEqual(json.loads(audit)['envelope']['P'], 140)
        _, validation = self.request('POST', '/api/validate', {'id': converted['worksheet']['id']})
        self.assertEqual(validation['cached_results'], 0)
        _, view = self.request('POST', '/api/view', {'id': converted['worksheet']['id']})
        self.assertTrue(any('P_u :=' in r.get('expression', '') for r in view['regions']))
        self.assertIn('145', view['calculated_html'])
        self.assertEqual(view['calculation_engine'], 'CalcpadCE')
        _, changes = self.request('POST', '/api/diff', {'id': converted['worksheet']['id']})
        self.assertEqual(changes, converted['diff'])
        self.assertEqual(len(list((self.root / 'results').glob('*/*.mcdx'))), 1)
        # A fresh registry reconstructs download links from complete pairs only.
        self.server.state.files.clear()
        status, history = self.request('GET', '/api/history')
        self.assertEqual(status, 200)
        self.assertEqual(len(history), 1)
        self.assertEqual(self.request('GET', '/api/download/' + history[0]['worksheet']['id'])[1], raw)

    def test_standards_register_is_bound_to_actual_calculated_artifacts(self):
        _, uploaded = self.upload()
        _, converted = self.request('POST', '/api/convert', {'id': uploaded['id'], 'cases': [1, 7]})
        key = converted['worksheet']['id']
        status, result = self.request('POST', '/api/standards', {'id': key})
        self.assertEqual(status, 200, result)
        register = result['register']
        self.assertEqual(register['values'][0]['quantity'], 140)
        self.assertEqual(register['rules'], [])
        self.assertFalse(result['assessment']['engineering_approval_verified'])
        self.assertEqual(self.request('POST', '/api/standards', {'id': key, 'register': register})[0], 200)
        register['values'][0]['source_id'] = []
        self.assertEqual(self.request('POST', '/api/standards', {'id': key, 'register': register})[0], 400)
        self.assertEqual(self.request('POST', '/api/standards', {'id': key}, authenticated=False)[0], 403)
        artifact = self.server.state.get(key, ['worksheet'])['path']
        artifact.with_suffix('.html').write_text('Changed result')
        self.assertEqual(self.request('POST', '/api/standards', {'id': key})[0], 400)

    def test_untrusted_requests_paths_and_sizes_are_rejected(self):
        self.assertEqual(self.request('POST', '/api/upload?name=x.txt&kind=report', b'x', authenticated=False)[0], 403)
        for headers in [{'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.request('GET', '/api/session', headers=headers, authenticated=False)[0], 403)
        self.assertEqual(self.upload('../escape.txt')[0], 400)
        self.assertEqual(self.upload('x.xlsm')[0], 400)
        self.assertEqual(self.upload(content=b'not a report')[0], 400)
        self.assertEqual(self.request('POST', '/api/upload?name=x.txt&kind=report', b'',
                                      {'Content-Length': str(40 * 1024 * 1024)})[0], 413)
        self.assertEqual(self.request('GET', '/api/download/../../reference.mcdx')[0], 404)

    def test_template_upload_duplicate_names_and_invalid_selection(self):
        _, first = self.upload()
        _, second = self.upload()
        self.assertNotEqual(first['id'], second['id'])
        status, t = self.upload('other.mcdx', self.template.read_bytes(), 'template')
        self.assertEqual(status, 200, t)
        self.assertEqual(self.request('POST', '/api/convert', {'id': first['id'], 'cases': []})[0], 400)
        self.assertEqual(self.request('POST', '/api/convert', {'id': first['id'], 'cases': [99]})[0], 400)
        self.assertEqual(self.request('POST', '/api/convert', {'id': first['id'], 'cases': [True]})[0], 400)
        a = self.request('POST', '/api/convert', {'id': first['id'], 'template_id': t['id']})
        b = self.request('POST', '/api/convert', {'id': second['id'], 'template_id': t['id']})
        self.assertEqual(a[0], 200, a)
        self.assertEqual(b[0], 200, b)
        self.assertNotEqual(a[1]['worksheet']['id'], b[1]['worksheet']['id'])

    def test_summary_csv_for_uploaded_reports(self):
        _, first = self.upload('a.txt')
        _, second = self.upload('b.gp11t', report().replace('Max. .2 .3 400 180 14 7', 'Max. .2 .3 400 180 14 9').encode())
        reports = [{'id': first['id'], 'cases': [1, 7]}, {'id': second['id']}]
        status, value = self.request('POST', '/api/summary', {'reports': reports, 'load_source': 'effects'})
        self.assertEqual(status, 200, value)
        self.assertEqual((value['reports'], value['rows']), (2, 6))
        rows = {(r['report'], r['case_id']): r for r in csv.DictReader(io.StringIO(value['csv']))}
        self.assertEqual(rows['b.gp11t', '1']['governs'], 'Vz')
        self.assertEqual(rows['a.txt', '7']['P_kip'], '140')
        self.assertEqual([p.name for p in (self.root / 'results').iterdir()], ['.documents'])
        self.assertEqual(self.request('POST', '/api/summary', {'reports': reports[:1]})[0], 400)
        self.assertEqual(self.request('POST', '/api/summary', {'reports': [reports[0], reports[0]]})[0], 400)
        _, third = self.upload('copy.txt')
        status, value = self.request('POST', '/api/summary', {'reports': reports + [{'id': third['id'], 'cases': [1, 7]}]})
        self.assertEqual(status, 200, value)
        self.assertEqual((value['reports'], value['rows'], value['duplicates_skipped']), (2, 6, ['copy.txt']))
        for bad in (['x'], {'a': 1}, None, 5):
            self.assertEqual(self.request('POST', '/api/summary', {'reports': [reports[0], {'id': bad}]})[0], 400)
        self.assertEqual(self.request('POST', '/api/summary', {'reports': [reports[0], {'id': 'missing'}]})[0], 404)
        self.assertEqual(self.request('POST', '/api/summary', {'reports': [reports[0], {'id': second['id'], 'cases': [99]}]})[0], 400)
        self.assertEqual(self.request('POST', '/api/summary', {'reports': reports, 'load_source': 'global'})[0], 400)
        self.assertEqual(self.request('POST', '/api/summary', {'reports': reports}, authenticated=False)[0], 403)

    def test_browser_configuration_requires_session_and_never_returns_keys(self):
        settings = {'url': 'http://127.0.0.1:9380', 'rag_key': 'test-rag-secret',
                    'embedding': 'test-embedding', 'router_key': 'test-router-secret', 'model': 'test/model:free'}
        self.assertEqual(self.request('POST', '/api/documents/configure', settings, authenticated=False)[0], 403)
        status, value = self.request('POST', '/api/documents/configure', settings)
        self.assertEqual(status, 200, value)
        self.assertNotIn('secret', json.dumps(value))
        status, listed = self.request('GET', '/api/documents')
        self.assertEqual(status, 200)
        self.assertNotIn('secret', json.dumps(listed))
        self.assertTrue(listed['configuration']['openrouter_configured'])
        self.assertEqual(self.request('POST', '/api/documents/configure', {'model': 'paid/model'})[0], 400)
        status, value = self.request('POST', '/api/documents/configure', {'router_key': ''})
        self.assertEqual(status, 200, value)
        self.assertTrue(value['router_key_saved'])

    def test_static_ui_and_missing_template_error(self):
        status, html = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'/workspace-assets/workspace.js', html)
        self.assertEqual(self.request('GET', '/workspace-assets/workspace.js')[0], 200)
        self.assertEqual(self.request('GET', '/workspace-assets/unknown.js')[0], 404)
        self.assertIn(b'webkitdirectory', self.request('GET', '/convert')[1])
        self.assertEqual(self.request('GET', '/app.js')[0], 200)
        self.assertEqual(self.request('GET', '/pdfjs/pdf.mjs')[0], 200)
        self.assertEqual(self.request('GET', '/pdfjs/%2e%2e/server.py')[0], 404)
        self.assertEqual(self.request('GET', '/pdfjs/unlisted.js')[0], 404)
        for name in ('regular', 'medium', 'semibold'):
            status, font = self.request('GET', f'/fonts/plex-{name}.ttf', authenticated=False)
            self.assertEqual(status, 200)
            self.assertTrue(font.startswith(b'\x00\x01\x00\x00'))
        self.assertEqual(self.request('GET', '/fonts/unlisted.ttf')[0], 404)
        self.assertEqual(self.request('GET', '/fonts/%2e%2e%2fserver.py')[0], 404)
        self.assertEqual(self.request('GET', '/../../pyproject.toml')[0], 404)
        self.server.state.template = None
        _, uploaded = self.upload()
        status, error = self.request('POST', '/api/convert', {'id': uploaded['id']})
        self.assertEqual(status, 400)
        self.assertIn('template', error['error'].lower())


class DeploymentTests(unittest.TestCase):
    def test_network_mode_requires_secret_and_https_remote_origin(self):
        from mcdxkit.server import check_configuration
        with self.assertRaises(ValueError): check_configuration('0.0.0.0', None, None)
        with self.assertRaises(ValueError): check_configuration('0.0.0.0', 'https://example.com', 'short')
        with self.assertRaises(ValueError): check_configuration('0.0.0.0', 'http://example.com', 'x' * 32)
        check_configuration('0.0.0.0', 'https://example.com', 'x' * 32)

    def test_cloud_auth_cookie_csrf_and_health(self):
        with tempfile.TemporaryDirectory() as folder:
            server = create_server(port=0, host='0.0.0.0', public_url='https://calc.example.test',
                                   access_token='test-only-' + 'x' * 32, output_dir=folder)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def call(method, route, data=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                h = {'Host': 'calc.example.test', **(headers or {})}
                if data is not None:
                    h['Content-Type'] = 'application/json'; data = json.dumps(data)
                conn.request(method, route, data, h)
                r = conn.getresponse(); body = r.read(); status = r.status; cookie = r.getheader('Set-Cookie')
                conn.close()
                return status, json.loads(body), cookie
            try:
                self.assertEqual(call('GET', '/healthz')[0], 200)
                self.assertEqual(call('GET', '/api/session')[0], 401)
                with mock.patch.object(server_module, 'LOGIN_DELAY_STEP', 0.01), mock.patch.object(server_module, 'LOGIN_DELAY_MAX', 0.05):
                    for _ in range(12):
                        start = time.monotonic()
                        self.assertEqual(call('POST', '/api/login', {'access_token': 'wrong'})[0], 401)
                    self.assertGreaterEqual(time.monotonic() - start, 0.05)
                status, _, cookie = call('POST', '/api/login', {'access_token': 'test-only-' + 'x' * 32})
                self.assertEqual(status, 200)
                for flag in ['HttpOnly', 'Secure', 'SameSite=strict']:
                    self.assertIn(flag, cookie)
                h = {'Cookie': cookie.split(';')[0]}
                status, info, _ = call('GET', '/api/session', headers=h)
                self.assertEqual(status, 200); self.assertTrue(info['network'])
                self.assertEqual(call('GET', '/api/history', headers=h)[0], 403)
                h['X-MCDXKit-Token'] = info['token']
                self.assertEqual(call('GET', '/api/history', headers=h)[0], 200)
                h['Origin'] = 'https://attacker.example'
                self.assertEqual(call('GET', '/api/history', headers=h)[0], 403)
            finally:
                server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
