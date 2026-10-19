import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcdxkit.document_clients import ModelConfig, RAGFlow, save_configuration
from mcdxkit.document_services import discover, installation


class ServiceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        # Developer credentials must never enter synthetic fixtures or failure output.
        config = patch('mcdxkit.document_clients.local_environment', return_value={})
        config.start(); self.addCleanup(config.stop)

    def test_installed_configuration_discovers_default_model_and_preserves_keys(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'MCDXKIT_RAGFLOW_API_KEY': 'internal-test-key'}, clear=True):
            with patch('mcdxkit.document_services.health', return_value=True), patch.object(RAGFlow, 'call', return_value={'models': [
                {'enable': True, 'model_type': 'embedding', 'model_name': 'bge-m3', 'model_provider': 'Ollama'}]}):
                result = discover(folder)
            self.assertEqual(result['service']['state'], 'ready')
            self.assertEqual(ModelConfig.for_workspace(folder).embedding, 'bge-m3@Ollama')
            self.assertEqual(ModelConfig.for_workspace(folder).url, 'http://127.0.0.1:9380')
            self.assertNotIn('internal-test-key', json.dumps(result))

    def test_preserves_explicit_model_and_endpoint_and_handles_auth_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            save_configuration(folder, {'url': 'http://configured:9380', 'rag_key': 'key', 'embedding': 'chosen@Provider'})
            with patch('mcdxkit.document_services.health', return_value=True), patch.object(RAGFlow, 'call', return_value={'models': []}):
                self.assertEqual(discover(folder)['service']['state'], 'ready')
            self.assertEqual(ModelConfig.for_workspace(folder).embedding, 'chosen@Provider')
            with patch('mcdxkit.document_services.health', return_value=True), patch.object(RAGFlow, 'call', side_effect=ValueError('HTTP 401')):
                self.assertEqual(discover(folder)['service']['state'], 'connection_error')

    def test_never_invents_credentials_or_models(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True), patch('mcdxkit.document_services.health', return_value=True):
            self.assertEqual(discover(folder)['service']['state'], 'provisioning_required')
            self.assertFalse((Path(folder)/'.credentials.json').exists())
            save_configuration(folder, {'rag_key': 'key'})
            with patch.object(RAGFlow, 'call', return_value={'models': []}):
                self.assertEqual(discover(folder)['service']['state'], 'model_missing')
            self.assertEqual(ModelConfig.for_workspace(folder).embedding, '')

    def test_runtime_and_bundle_states_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'MCDXKIT_RAGFLOW_DIRECTORY': folder}, clear=True):
            with patch('mcdxkit.document_services.shutil.which', return_value=None):
                self.assertEqual(installation()['state'], 'runtime_missing')
            with patch('mcdxkit.document_services.shutil.which', return_value='/docker'), patch('mcdxkit.document_services.subprocess.run') as run:
                run.return_value.returncode = 1
                self.assertEqual(installation()['state'], 'runtime_stopped')
                run.return_value.returncode = 0
                self.assertEqual(installation()['state'], 'not_installed')
                (Path(folder)/'compose.json').write_text('{}')
                self.assertEqual(installation()['state'], 'stopped')

    def test_managed_start_is_bounded_to_prepared_local_installation(self):
        from mcdxkit.document_services import ServiceManager
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            manager = ServiceManager(folder)
            with patch('mcdxkit.document_services.installation', return_value={'state':'stopped'}), patch('threading.Thread') as thread:
                self.assertEqual(manager.start()['state'], 'starting')
                manager.start()
                self.assertEqual(thread.call_count, 1)
                thread.return_value.start.assert_called_once()
            with patch('mcdxkit.document_services.discover', return_value={'service': {'state': 'stopped'}}):
                self.assertEqual(manager.discover()['service']['state'], 'starting')
            save_configuration(folder, {'url': 'http://managed-elsewhere:9380'})
            with self.assertRaisesRegex(ValueError, 'separately'): manager.start()

    def test_managed_start_uses_fixed_compose_and_reports_failure(self):
        from mcdxkit.document_services import ServiceManager
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'MCDXKIT_RAGFLOW_DIRECTORY': folder}, clear=True):
            manager = ServiceManager(folder)
            with patch('mcdxkit.document_services.subprocess.run') as run:
                run.return_value.returncode = 1
                manager._start()
                self.assertEqual(run.call_args.args[0], ['docker','compose','-f',str(Path(folder).resolve()/'compose.json'),'up','-d'])
            self.assertTrue(manager.failed)
            self.assertFalse(manager.starting)
            self.assertEqual((Path(folder)/'startup.log').stat().st_mode & 0o777, 0o600)

    def test_openrouter_setup_reuses_native_provider_and_does_not_replace_existing_index_model(self):
        from mcdxkit.document_services import connect_openrouter
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            save_configuration(folder, {'url':'http://127.0.0.1:9380', 'rag_key':'internal', 'embedding':'existing@Provider'})
            with patch.object(ModelConfig, 'available_models', return_value={'models':[{'id':'chat/a:free'}]}), patch.object(ModelConfig, 'available_embeddings', return_value={'models':[{'id':'embed/a:free','context_length':8192}]}), patch.object(RAGFlow, 'call') as call:
                with self.assertRaisesRegex(ValueError, 'existing'):
                    connect_openrouter(folder, {'router_key':'private-router', 'model':'chat/a:free', 'search_model':'embed/a:free'})
                call.assert_not_called()
            self.assertFalse(bool(ModelConfig.for_workspace(folder).router_key))

    def test_openrouter_setup_stores_one_key_after_success_and_preserves_configuration_on_failure(self):
        from mcdxkit.document_services import connect_openrouter
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            save_configuration(folder, {'url':'http://127.0.0.1:9380', 'rag_key':'internal'})
            calls=[]
            def api(client, method, path, data=None):
                calls.append((method,path,data))
                if path == '/providers': return [{'name':'OpenRouter'}]
                if path.endswith('/instances') and method == 'GET': return []
                return None
            catalogs = [patch.object(ModelConfig,'available_models', return_value={'models':[{'id':'chat/a:free'}]}), patch.object(ModelConfig,'available_embeddings', return_value={'models':[{'id':'embed/a:free','context_length':8192}]})]
            with catalogs[0], catalogs[1], patch.object(RAGFlow,'call',api):
                result=connect_openrouter(folder, {'router_key':'private-router','model':'chat/a:free','search_model':'embed/a:free'})
            saved=ModelConfig.for_workspace(folder)
            self.assertEqual(saved.embedding,'embed/a:free@mcdxkit@OpenRouter')
            self.assertEqual(saved.router_key,'private-router')
            self.assertNotIn('private-router',json.dumps(result))
            payload=next(data for method,path,data in calls if method=='POST')
            self.assertEqual(payload['api_key'],'private-router')
            self.assertEqual(payload['base_url'],'https://openrouter.ai/api/v1')
            self.assertEqual(payload['model_info'][0]['model_type'],['embedding'])
            with patch.object(ModelConfig,'available_models',return_value={'models':[]}):
                with self.assertRaises(ValueError): connect_openrouter(folder, {'router_key':'bad','model':'paid/model','search_model':'embed/a:free'})
            self.assertEqual(ModelConfig.for_workspace(folder).router_key,'private-router')

    def test_managed_account_is_private_reused_and_never_created_for_remote_server(self):
        from mcdxkit.document_services import provision_local
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'MCDXKIT_RAGFLOW_DIRECTORY':folder}, clear=True):
            root=Path(folder)/'workspace'
            (Path(folder)/'compose.json').write_text('{}')
            (Path(folder)/'UPSTREAM.txt').write_text('pinned test bundle')
            calls=[]
            def api(client, method, path, data=None):
                calls.append(path)
                if path in ('/users','/auth/login'):
                    client.response_authorization='session-secret'
                    return {}
                if path=='/system/tokens':
                    return [{'token':'ragflow-private'}]
                raise AssertionError(path)
            with patch('mcdxkit.document_services.health',return_value=True), patch('mcdxkit.document_services.subprocess.run') as process, patch.object(RAGFlow,'call',api):
                process.return_value.returncode=0
                process.return_value.stdout=b'encrypted'
                provision_local(root)
                account=(Path(folder)/'account.json').read_bytes()
                provision_local(root)
            self.assertEqual(calls.count('/users'),1)
            self.assertEqual((Path(folder)/'account.json').read_bytes(),account)
            self.assertEqual((Path(folder)/'account.json').stat().st_mode & 0o777,0o600)
            self.assertEqual(ModelConfig.for_workspace(root).rag_key,'ragflow-private')
            remote=Path(folder)/'remote'
            save_configuration(remote,{'url':'https://external.example'})
            with self.assertRaisesRegex(ValueError,'bundled'): provision_local(remote)
