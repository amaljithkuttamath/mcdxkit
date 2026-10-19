"""Discover installed document services without asking engineers for deployment details."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .document_clients import HTTPClient, ModelConfig, NoRedirect, RAGFlow, save_configuration

LOCAL_URL = 'http://127.0.0.1:9380'


def health(url):
    # Probe the configured endpoint or the one bundled local endpoint, never scan the network.
    HTTPClient(url, 'url-validation')
    try:
        request = Request(url.rstrip('/') + '/api/v1/system/healthz', headers={'Accept': 'application/json'})
        with build_opener(NoRedirect()).open(request, timeout=3) as response:
            result = json.loads(response.read(65537))
        return isinstance(result, dict) and result.get('status') == 'ok'
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False


def deployment_path():
    return Path(os.environ.get('MCDXKIT_RAGFLOW_DIRECTORY', '.mcdxkit/ragflow')).resolve()


def installation():
    prepared = (deployment_path() / 'compose.json').is_file()
    if not shutil.which('docker'):
        return {'state': 'runtime_missing', 'title': 'Document service needs installation',
                'message': 'The bundled document service needs Docker on this server. No service URL or model name is needed from you.'}
    try:
        result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=4, check=False)
        running = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        running = False
    if not running:
        return {'state': 'runtime_stopped', 'title': 'Docker is stopped',
                'message': 'Start Docker on the computer running MCDXKit, then check again. Your documents remain available to view.'}
    if not prepared:
        return {'state': 'not_installed', 'title': 'Document service is not installed',
                'message': 'This installation has no prepared document-service bundle. The deployment administrator must install it; engineers should not have to supply service URLs or internal keys.'}
    return {'state': 'stopped', 'title': 'Document service is stopped',
            'message': 'The bundled service configuration was found, but the service is not healthy yet. Start the deployment or check its health before indexing.'}


def discover(root):
    config = ModelConfig.for_workspace(root)
    url = config.url or LOCAL_URL
    result = {'configuration': config.public(), 'service': None}
    if not health(url):
        local = url.rstrip('/') == LOCAL_URL
        result['service'] = installation() if local else {'state': 'unavailable', 'title': 'Document service unavailable',
            'message': 'The configured document server is not healthy. Contact the deployment administrator or check Advanced connection settings.'}
        return result
    if not config.rag_key:
        result['service'] = {'state': 'provisioning_required', 'title': 'Document service found',
            'message': 'Add your OpenRouter key and select answer and search models below. Saving creates the bundled service connection automatically.'}
        return result
    client = RAGFlow(url, config.rag_key, config.embedding)
    client.timeout = 5
    try:
        defaults = client.call('GET', '/models/default')
        if not isinstance(defaults, dict) or not isinstance(defaults.get('models'), list):
            raise ValueError('The document server returned an unsupported model-discovery response.')
        embedding = config.embedding
        if not embedding:
            candidates = [item for item in defaults['models'] if isinstance(item, dict)
                          and item.get('enable') is True and item.get('model_type') == 'embedding'
                          and isinstance(item.get('model_name'), str) and item['model_name']
                          and isinstance(item.get('model_provider'), str) and item['model_provider']]
            if len(candidates) != 1:
                result['service'] = {'state': 'model_missing', 'title': 'Document service connected',
                    'message': 'Add your OpenRouter key below to finish setup. No local model download is needed.'}
                return result
            default = candidates[0]
            instance = default.get('model_instance')
            embedding = default['model_name'] + ('@' + instance if isinstance(instance, str) and instance else '') + '@' + default['model_provider']
        changes = {}
        if not config.url: changes['url'] = url; changes['rag_key'] = config.rag_key
        if not config.embedding: changes['embedding'] = embedding
        if changes: result['configuration'] = save_configuration(root, changes)
        result['service'] = {'state': 'ready', 'title': 'Document service connected',
            'message': 'Service connection and selected search model detected. Indexing verifies whether this model works through the document service.'}
    except ValueError as error:
        result['service'] = {'state': 'connection_error', 'title': 'Document service needs attention', 'message': str(error)}
    return result


class ServiceManager:
    """Start only this installation's prepared stack; never accept commands or paths from HTTP."""
    def __init__(self, root):
        import threading
        self.root = root
        self.lock = threading.Lock()
        self.starting = False
        self.failed = False

    def discover(self):
        result = discover(self.root)
        with self.lock:
            if result['service']['state'] == 'stopped':
                if self.starting:
                    result['service'] = {'state': 'starting', 'title': 'Starting document search',
                        'message': 'Downloading required container images and starting the bundled services. First startup can take several minutes.'}
                elif self.failed:
                    result['service']['message'] = 'Startup did not finish. Check the local deployment log, then retry. Your documents and service volumes are preserved.'
                result['service']['can_start'] = not self.starting
        return result

    def start(self):
        import threading
        config = ModelConfig.for_workspace(self.root)
        if config.url and config.url.rstrip('/') != LOCAL_URL:
            raise ValueError('This workspace uses a separately managed server. Local startup is disabled.')
        if installation()['state'] != 'stopped':
            raise ValueError('Start Docker and prepare the bundled deployment before starting document search.')
        with self.lock:
            if not self.starting:
                self.starting = True
                self.failed = False
                threading.Thread(target=self._start, daemon=True).start()
        return {'state': 'starting'}

    def _start(self):
        directory = deployment_path()
        try:
            fd = os.open(directory / 'startup.log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as log:
                result = subprocess.run(['docker', 'compose', '-f', str(directory / 'compose.json'), 'up', '-d'],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
            failed = result.returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        with self.lock:
            self.failed = failed
            self.starting = False


def connect_openrouter(root, values):
    """Reuse RAGFlow's native OpenRouter embedding driver; no second model runtime."""
    if not isinstance(values, dict) or set(values) - {'router_key', 'model', 'search_model'}:
        raise ValueError('Unknown OpenRouter connection fields.')
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError('Invalid OpenRouter connection value.')
    config = ModelConfig.for_workspace(root)
    key = values.get('router_key', '').strip() or config.router_key
    model = values.get('model', '').strip()
    search = values.get('search_model', '').strip()
    if model not in {m['id'] for m in config.available_models(key)['models']}:
        raise ValueError('Choose an available free answer model. No paid fallback was used.')
    embeddings = {m['id']: m for m in config.available_embeddings(key)['models']}
    if search not in embeddings:
        raise ValueError('Choose an available free search model. No paid fallback was used.')
    selected = search + '@mcdxkit@OpenRouter'
    if config.embedding and config.embedding != selected:
        raise ValueError('This workspace has an existing search model. Keep it to preserve existing indexes; use a new workspace to change embedding models.')
    if not config.rag_key:
        provision_local(root)
        config = ModelConfig.for_workspace(root)
    client = config.ragflow()
    providers = client.call('GET', '/providers')
    if providers is not None and not isinstance(providers, list):
        raise ValueError('Invalid document-service provider response.')
    if not any(isinstance(p, dict) and p.get('name') == 'OpenRouter' for p in providers or []):
        client.call('PUT', '/providers', {'provider_name': 'OpenRouter'})
    instances = client.call('GET', '/providers/OpenRouter/instances')
    if not isinstance(instances, list): raise ValueError('Invalid document-service instance response.')
    exists = any(isinstance(i, dict) and i.get('instance_name') == 'mcdxkit' for i in instances)
    payload = {'instance_name': 'mcdxkit', 'api_key': key, 'base_url': 'https://openrouter.ai/api/v1',
               'model_info': [{'model_name': search, 'model_type': ['embedding'],
                               'max_tokens': embeddings[search]['context_length'] or 512}]}
    client.call('PUT' if exists else 'POST', '/providers/OpenRouter/instances' + ('/mcdxkit' if exists else ''), payload)
    return save_configuration(root, {'router_key': key, 'model': model, 'embedding': selected})


def provision_local(root):
    """Provision a dedicated account only in the prepared, loopback-only bundle."""
    import base64
    import secrets
    config = ModelConfig.for_workspace(root)
    directory = deployment_path()
    if (config.url and config.url.rstrip('/') != LOCAL_URL) or not all(
            (directory / name).is_file() for name in ('compose.json', 'UPSTREAM.txt')):
        raise ValueError('Automatic account setup is only available for the bundled local service.')
    if config.rag_key: return config.public()
    if not health(LOCAL_URL): raise ValueError('Start document search before connecting OpenRouter.')
    path = directory / 'account.json'
    if path.is_symlink(): raise ValueError('Managed account file must not be a symbolic link.')
    created = False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'email': 'mcdxkit-' + secrets.token_hex(12) + '@localhost.local',
                       'password': secrets.token_urlsafe(36)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        created = True
    account = json.loads(path.read_text())
    if not isinstance(account, dict) or set(account) != {'email', 'password'} or any(
            not isinstance(v, str) or not v or len(v) > 256 for v in account.values()):
        raise ValueError('Invalid managed account file. Restore its private backup.')
    # Reuse the pinned server's public key and OpenSSL. Password never enters argv or logs.
    result = subprocess.run(['docker', 'compose', '-f', str(directory / 'compose.json'),
        'exec', '-T', 'ragflow-cpu', 'openssl', 'pkeyutl', '-encrypt', '-pubin',
        '-inkey', '/ragflow/conf/public.pem', '-pkeyopt', 'rsa_padding_mode:pkcs1'],
        input=base64.b64encode(account['password'].encode()), capture_output=True, timeout=20, check=False)
    if result.returncode != 0: raise ValueError('Could not initialize the bundled service account.')
    credentials = {'email': account['email'], 'password': base64.b64encode(result.stdout).decode()}
    client = RAGFlow(LOCAL_URL, 'initializing', '')
    client.timeout = 20
    if created:
        client.call('POST', '/users', {**credentials, 'nickname': 'MCDXKit'})
    else:
        try:
            client.call('POST', '/auth/login', credentials)
        except ValueError:
            # A previous startup may have stopped after writing the private account file.
            client.call('POST', '/users', {**credentials, 'nickname': 'MCDXKit'})
    session = client.response_authorization.removeprefix('Bearer ').strip()
    if not session: raise ValueError('Document service returned no account session.')
    client.key = session
    tokens = client.call('GET', '/system/tokens')
    if not isinstance(tokens, list): raise ValueError('Invalid document-service credential response.')
    api_key = next((t.get('token') for t in tokens if isinstance(t, dict) and isinstance(t.get('token'), str) and t['token']), '')
    if not api_key:
        result = client.call('POST', '/system/tokens', {'name': 'MCDXKit'})
        api_key = result.get('token') if isinstance(result, dict) else None
    if not isinstance(api_key, str) or not api_key: raise ValueError('Document service returned no internal credential.')
    return save_configuration(root, {'url': LOCAL_URL, 'rag_key': api_key})
