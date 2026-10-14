"""FastAPI/Uvicorn adapter: local by default, authenticated when deployed."""
import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse

from . import mcdx
from .generate import default_template
from .review import runner as review_runner
from .service import MIB, RequestError, Session

STATIC = Path(__file__).with_name('web')
PDF_ASSETS = {p.relative_to(STATIC / 'pdfjs').as_posix(): p
              for p in (STATIC / 'pdfjs').rglob('*') if p.is_file() and not p.is_symlink()}
LOGIN_DELAY_STEP = 0.1  # seconds added per failed login in the last minute
LOGIN_DELAY_MAX = 1.0
SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
}


def check_configuration(host, public_url, access_token):
    if host not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('Host must be 127.0.0.1 or 0.0.0.0')
    if host == '0.0.0.0' and (not access_token or len(access_token) < 32 or not public_url):
        raise ValueError('Network mode requires MCDXKIT_ACCESS_TOKEN (at least 32 characters) and --public-url.')
    if public_url:
        u = urlsplit(public_url)
        if (not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment
                or u.scheme not in ('http', 'https')):
            raise ValueError('Public URL must be an origin, for example https://calc.example.com')
        if u.scheme != 'https' and u.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('Remote deployments require an HTTPS public URL and a TLS reverse proxy.')


def create_app(*, origin, template=None, output_dir='mcdxkit-output', access_token=None, network=False, checks='default',
               plan=None):
    """Create one workspace. Deploy one process/replica per trusted user or team."""
    state = Session(template, output_dir, checks, plan)
    from .document_services import ServiceManager
    document_services = ServiceManager(state.output_dir / '.documents')
    cookie_key = secrets.token_bytes(32)
    failed_logins = []
    secure = origin.startswith('https:')

    @asynccontextmanager
    async def lifespan(_app):
        yield
        state.temp.cleanup()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.workspace = state

    def authenticated(request):
        if not access_token:
            return True
        value = request.cookies.get('mcdxkit_session', '')
        try:
            timestamp, nonce, signature = value.split('.')
            payload = timestamp + '.' + nonce
            expected = hmac.new(cookie_key, payload.encode(), hashlib.sha256).hexdigest()
            age = time.time() - int(timestamp)
            return 0 <= age < 8 * 3600 and hmac.compare_digest(signature, expected)
        except (ValueError, OverflowError):
            return False

    @app.middleware('http')
    async def boundaries(request, call_next):
        try:
            # Health checks expose only liveness, not credentials, files, or settings.
            if request.url.path != '/healthz':
                if request.headers.get('host') != urlsplit(origin).netloc:
                    raise RequestError('Use the configured mcdxkit URL.', 403)
                if request.headers.get('origin') not in (None, origin) or request.headers.get('sec-fetch-site') == 'cross-site':
                    raise RequestError('Cross-origin requests are disabled.', 403)
                if request.url.path.startswith('/api/') and request.url.path != '/api/login':
                    if not authenticated(request):
                        raise RequestError('Enter the deployment access token to continue.', 401)
                    if request.url.path != '/api/session' and not hmac.compare_digest(request.headers.get('x-mcdxkit-token', ''), state.token):
                        raise RequestError('Session expired. Reload the browser.', 403)
            response = await call_next(request)
        except RequestError as exc:
            response = JSONResponse({'error': str(exc)}, status_code=exc.status)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        if request.url.path == '/documents' or request.url.path.startswith('/pdfjs/'):
            response.headers['Content-Security-Policy'] = SECURITY_HEADERS['Content-Security-Policy'].replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'") .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'") + "; worker-src 'self'; frame-src 'self' blob:"
        return response

    @app.exception_handler(RequestError)
    async def request_error(_request, exc):
        return JSONResponse({'error': str(exc)}, status_code=exc.status)

    async def read_body(request, limit, content_type):
        lengths = request.headers.getlist('content-length')
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise RequestError('A valid Content-Length is required.', 411)
        if int(lengths[0]) > limit:
            raise RequestError('Request exceeds the upload size limit.', 413)
        if request.headers.get('content-type', '').split(';')[0] != content_type:
            raise RequestError('Unsupported content type.', 415)
        data = bytearray()
        async def collect():
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > limit:
                    raise RequestError('Request exceeds the upload size limit.', 413)
        try:
            await asyncio.wait_for(collect(), timeout=30)
        except asyncio.TimeoutError:
            raise RequestError('Upload timed out. Try again.', 408)
        return bytes(data)

    def execute(function, *args):
        # Do not queue unbounded CPU/memory work. Browser batches submit sequentially.
        if not state.lock.acquire(blocking=False):
            raise RequestError('Another file is being processed. Please retry.', 409)
        try:
            return function(*args)
        except RequestError:
            raise
        except (ValueError, OSError, KeyError, IndexError, mcdx.E.XMLSyntaxError, mcdx.zipfile.BadZipFile) as exc:
            raise RequestError(str(exc)) from exc
        finally:
            state.lock.release()

    @app.get('/healthz')
    async def health():
        return {'status': 'ok'}

    @app.get('/api/session')
    async def session():
        return {'token': state.token, 'output_dir': str(state.output_dir), 'network': network,
                'template': state.template.name if state.template and state.template.is_file() else None,
                'limits': {'files': 100, 'report_mib': 16, 'worksheet_mib': 32, 'session_mib': 256}}

    @app.post('/api/login')
    async def login(request: Request):
        try:
            data = json.loads(await read_body(request, 4096, 'application/json'))
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError('Invalid login request.')
        candidate = data.get('access_token') if isinstance(data, dict) else None
        if not isinstance(candidate, str) or not access_token or not hmac.compare_digest(candidate.encode(), access_token.encode()):
            # Slow guessing without a lockout: failures never block the correct token.
            now = time.monotonic()
            failed_logins[:] = [t for t in failed_logins[-31:] if now - t < 60] + [now]
            await asyncio.sleep(min(LOGIN_DELAY_MAX, LOGIN_DELAY_STEP * len(failed_logins)))
            raise RequestError('Incorrect access token.', 401)
        payload = str(int(time.time())) + '.' + secrets.token_hex(16)
        signature = hmac.new(cookie_key, payload.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({'ok': True})
        response.set_cookie('mcdxkit_session', payload + '.' + signature, max_age=8 * 3600,
                            httponly=True, secure=secure, samesite='strict', path='/')
        return response

    @app.post('/api/logout')
    async def logout():
        response = JSONResponse({'ok': True})
        response.delete_cookie('mcdxkit_session', path='/', secure=secure, httponly=True, samesite='strict')
        return response

    @app.post('/api/upload')
    async def upload(request: Request):
        kind = request.query_params.get('kind', '')
        raw = await read_body(request, (16 if kind == 'report' else 32) * MIB, 'application/octet-stream')
        return await run_in_threadpool(execute, state.upload, request.query_params.get('name', ''), kind, raw)

    @app.post('/api/{operation}')
    async def operation(operation: str, request: Request):
        if operation not in ('inspect', 'convert', 'validate', 'preview', 'view', 'diff', 'summary', 'standards', 'template-inputs'):
            raise RequestError('Not found.', 404)
        try:
            # A summary lists up to 100 report IDs with their selected cases.
            data = json.loads(await read_body(request, 1048576 if operation == 'standards' else 65536 if operation == 'summary' else 16384, 'application/json'))
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError('Expected a JSON object.')
        if not isinstance(data, dict):
            raise RequestError('Expected a JSON object.')
        return await run_in_threadpool(execute, state.operation, '/api/' + operation, data)

    @app.get('/api/download/{file_id}')
    async def download(file_id: str):
        entry = state.get(file_id, ['worksheet', 'audit', 'open_worksheet', 'calculated_worksheet'])
        return FileResponse(entry['path'], filename=entry['path'].name, media_type='application/octet-stream')

    def documents():
        from .documents import DocumentWorkflow
        return DocumentWorkflow(state.output_dir / '.documents')

    @app.get('/api/documents')
    async def document_projects():
        from .document_clients import ModelConfig
        return await run_in_threadpool(execute, lambda: {
            'projects': documents().listing(), 'configuration': ModelConfig.for_workspace(documents().root).public()})

    @app.post('/api/documents/{action}')
    async def document_action(action: str, request: Request):
        if action == 'upload':
            raw = await read_body(request, 256 * MIB, 'application/octet-stream')
            return await run_in_threadpool(execute, lambda: documents().add_source(
                request.query_params.get('project'), request.query_params.get('name'), raw))
        if action not in ('configure', 'connect-openrouter', 'models', 'discover', 'start-service', 'create', 'connect', 'calculation', 'ask', 'show', 'ingest', 'status', 'draft', 'review', 'build'):
            raise RequestError('Not found.', 404)
        try:
            data = json.loads(await read_body(request, 16384, 'application/json'))
            if not isinstance(data, dict): raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError('Expected a JSON object.')
        def perform():
            flow = documents()
            if action == 'connect-openrouter':
                with document_services.lock:
                    from .document_services import connect_openrouter
                    return connect_openrouter(flow.root, data)
            if action == 'configure':
                from .document_clients import save_configuration
                return save_configuration(flow.root, data)
            if action in ('discover', 'start-service'):
                if data: raise ValueError('Service discovery/start takes no connection overrides.')
                return document_services.discover() if action == 'discover' else document_services.start()
            if action == 'models':
                from .document_clients import ModelConfig
                if set(data) - {'router_key'}: raise ValueError('Unknown model discovery fields.')
                config = ModelConfig.for_workspace(flow.root)
                result = config.available_models(data.get('router_key', ''))
                result['embedding_models'] = config.available_embeddings(data.get('router_key', ''))['models']
                return result
            pid = data.get('project')
            if action == 'create' and not data.get('id'):
                if data.get('template_id'): raise ValueError('Provide both the report and template, or neither.')
                return flow.create(data.get('title'))
            if action in ('create', 'connect'):
                report = state.get(data.get('id'), ['report'])['path']
                template = state.get(data['template_id'], ['template'])['path'] if data.get('template_id') else state.template
                if template is None: raise ValueError('Select a template first.')
                options = {'cases': state.cases(data.get('cases')), 'input_map': data.get('input_map'), 'load_source': state.basis(data)}
                if action == 'connect': return flow.attach_calculation(pid, report, template, **options)
                return flow.create(data.get('title'), report, template, **options)
            if action == 'calculation':
                project = flow.get(pid); flow.verify(project); flow.require_calculation(project)
                return {'report': state.register(flow.root / pid / 'loads.txt', 'report'),
                        'template': state.register(flow.root / pid / 'template.mcdx', 'template'),
                        'options': project['options']}
            if action == 'show': return flow.get(pid)
            if action == 'ingest': return flow.ingest(pid)
            if action == 'status': return flow.status(pid)
            if action == 'ask': return flow.ask(pid, data.get('question'))
            if action == 'draft': return flow.draft(pid, data.get('request'))
            if action == 'review': return flow.review(pid, data.get('draft'), data.get('accepted'), data.get('reviewer'), data.get('note'))
            return flow.build(pid, data.get('draft'))
        return await run_in_threadpool(execute, perform)

    @app.get('/api/documents/{pid}/sources/{sid}')
    async def document_source(pid: str, sid: str):
        path = await run_in_threadpool(execute, lambda: documents().source_path(pid, sid))
        return FileResponse(path, media_type='application/pdf', filename='source.pdf')

    @app.get('/api/documents/{pid}/runs/{rid}/{name}')
    async def document_artifact(pid: str, rid: str, name: str):
        path = await run_in_threadpool(execute, lambda: documents().artifact(pid, rid, name))
        return FileResponse(path, filename=name, media_type='application/octet-stream')

    @app.get('/documents')
    async def document_page():
        return FileResponse(STATIC / 'documents.html', media_type='text/html')

    @app.get('/api/history')
    async def history():
        return await run_in_threadpool(execute, state.history)

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    @app.get('/fonts/{name}')
    async def font(name: str):
        if name not in ('plex-regular.ttf', 'plex-medium.ttf', 'plex-semibold.ttf'):
            raise RequestError('Not found.', 404)
        return FileResponse(STATIC / 'fonts' / name, media_type='font/ttf')

    @app.get('/pdfjs/{asset:path}')
    async def pdf_asset(asset: str):
        # Lookup only packaged assets; never form a filesystem path from the request.
        path = PDF_ASSETS.get(asset)
        if path is None:
            raise RequestError('Not found.', 404)
        suffix = path.suffix
        media = {'mjs': 'text/javascript', 'js': 'text/javascript', 'wasm': 'application/wasm'}.get(suffix.lstrip('.'), 'application/octet-stream')
        return FileResponse(path, media_type=media)

    @app.get('/{asset}')
    async def asset(asset: str):
        if asset not in ('app.js', 'style.css', 'documents.js', 'documents.css', 'workspace.css'):
            raise RequestError('Not found.', 404)
        return FileResponse(STATIC / asset, media_type='text/javascript' if asset.endswith('.js') else 'text/css')

    return app


class WebServer:
    """Lifecycle wrapper used by the CLI and real-HTTP integration tests."""
    def __init__(self, *, port, template, output_dir, host='127.0.0.1', public_url=None, access_token=None, checks=None):
        check_configuration(host, public_url, access_token)
        # Built-in review checks only, unless MCDXKIT_CHECKS names installed plug-ins.
        checks = checks or os.environ.get('MCDXKIT_CHECKS') or 'default'
        plan = review_runner.prepare(checks)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.bind((host, port))
            self.socket.listen(32)
        except OSError:
            self.socket.close()
            raise
        self.server_port = self.socket.getsockname()[1]
        self.origin = public_url.rstrip('/') if public_url else f'http://127.0.0.1:{self.server_port}'
        app = create_app(origin=self.origin, template=template, output_dir=output_dir, access_token=access_token,
                         network=host != '127.0.0.1', checks=checks, plan=plan)
        self.state = app.state.workspace
        self.stopped = threading.Event()
        self.stopped.set()
        self.server = uvicorn.Server(uvicorn.Config(app, workers=1, access_log=False, log_level='warning',
                                                    proxy_headers=False, limit_concurrency=16,
                                                    timeout_keep_alive=5, timeout_graceful_shutdown=30))

    def serve_forever(self):
        self.stopped.clear()
        try:
            self.server.run(sockets=[self.socket])
        finally:
            self.stopped.set()

    def shutdown(self):
        self.server.should_exit = True
        self.stopped.wait(35)

    def server_close(self):
        self.socket.close()
        self.state.temp.cleanup()


def create_server(*, port=8765, template=None, output_dir='mcdxkit-output', **kwargs):
    return WebServer(port=port, template=template, output_dir=output_dir, **kwargs)


def main(argv=None, prog='mcdxkit serve'):
    parser = argparse.ArgumentParser(prog=prog, description='Open a browser for file/folder conversion and package validation.',
                                     epilog='Advisory review checks: built-ins only, unless MCDXKIT_CHECKS names more '
                                            '(for example MCDXKIT_CHECKS=default,my_check). See "mcdxkit checks list".')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')), help='Listen port (default: PORT or 8765; 0 selects a free port)')
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default=os.environ.get('MCDXKIT_HOST', '127.0.0.1'), help='Default: loopback. Network mode requires authentication and a public URL.')
    parser.add_argument('--public-url', default=os.environ.get('MCDXKIT_PUBLIC_URL'), help='Exact browser origin; HTTPS for remote deployments')
    parser.add_argument('--template', type=Path, default=default_template(), help='Private default template; may also be selected in the browser')
    parser.add_argument('--output-dir', type=Path, default=Path(os.environ.get('MCDXKIT_OUTPUT_DIR', str(Path.cwd() / 'mcdxkit-output'))), help='Persistent generated worksheets and audits')
    parser.add_argument('--no-open', action='store_true', help='Print the URL without opening a browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    token = os.environ.get('MCDXKIT_ACCESS_TOKEN')
    token_file = os.environ.get('MCDXKIT_ACCESS_TOKEN_FILE')
    try:
        if token_file:
            token = Path(token_file).read_text().strip()
        server = create_server(port=args.port, host=args.host, public_url=args.public_url, access_token=token,
                               template=args.template, output_dir=args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'mcdxkit: Cannot start server: {exc}\n')
    print(f'mcdxkit browser: {server.origin}\nOutputs: {server.state.output_dir}\n' +
          ('Files are uploaded to this server.' if args.host == '0.0.0.0' else 'Calculation files are stored on this computer.') +
          '\nDocument indexing sends PDFs to RAGFlow; drafting sends selected evidence to OpenRouter.\nCtrl+C stops the server. Generated outputs are retained.', flush=True)
    if not args.no_open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping mcdxkit. Generated outputs are retained.', flush=True)
    finally:
        server.server_close()
    return 0
