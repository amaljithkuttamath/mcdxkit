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
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import uvicorn
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import mcdx
from .generate import default_template
from .review import runner as review_runner
from .service import MIB, RequestError, Session

STATIC = Path(__file__).with_name('web')
WORKSPACE_ASSETS = {p.name: p for p in (STATIC / 'workspace-dist').glob('*') if p.is_file() and not p.is_symlink()}
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
    from .jobs import JobStore, start_worker
    jobs = JobStore(state.output_dir / '.documents')
    cookie_key = secrets.token_bytes(32)
    failed_logins = []
    secure = origin.startswith('https:')

    @asynccontextmanager
    async def lifespan(_app):
        # Network servers start workers with ChatGPT disabled, so even jobs queued earlier on a
        # local server for this workspace cannot use a saved ChatGPT account here.
        workers = {channel: start_worker(jobs.root, channel, chatgpt=not network) for channel in ('chat', 'batch')}
        async def maintain_workers():
            while True:
                await asyncio.sleep(2)
                for channel, process in workers.items():
                    if process.poll() is not None: workers[channel] = start_worker(jobs.root, channel, chatgpt=not network)
        monitor = asyncio.create_task(maintain_workers())
        try:
            yield
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError): await monitor
            for worker in workers.values():
                if worker.poll() is None: worker.terminate()
                try: await run_in_threadpool(worker.wait, 8)
                except __import__('subprocess').TimeoutExpired:
                    worker.kill(); await run_in_threadpool(worker.wait)
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
                # The ChatGPT OAuth redirect is a cross-site top-level GET; it is accepted only on its exact
                # path and is verified by a one-time state, PKCE and an HttpOnly attempt cookie.
                oauth_return = request.method == 'GET' and request.url.path == '/auth/callback'
                if not oauth_return and (request.headers.get('origin') not in (None, origin) or request.headers.get('sec-fetch-site') == 'cross-site'):
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
        if request.url.path in ('/', '/documents', '/library') or request.url.path.startswith('/pdfjs/'):
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

    @app.post('/api/jobs')
    async def start_job(request: Request):
        try:
            data = json.loads(await read_body(request, 16384, 'application/json'))
            if not isinstance(data, dict) or set(data) != {'project', 'action', 'payload', 'request_key'}:
                raise ValueError('Supply project, action, payload and request_key.')
            if data['action'] in ('ask', 'extract_offer'): await run_in_threadpool(refuse_shared_chatgpt)
            job = await run_in_threadpool(jobs.submit, data['project'], data['action'], data['payload'], data['request_key'])
            return JSONResponse(jobs.public(job), status_code=202, headers={'Location': '/api/jobs/' + job['id']})
        except (ValueError, UnicodeError, RecursionError) as exc: raise RequestError(str(exc)) from exc

    @app.get('/api/jobs')
    async def job_list(project: str):
        try: return await run_in_threadpool(jobs.listing, project)
        except ValueError as exc: raise RequestError(str(exc)) from exc

    @app.get('/api/jobs/{jid}')
    async def job_status(jid: str):
        try: return jobs.public(await run_in_threadpool(jobs.get, jid))
        except ValueError as exc: raise RequestError(str(exc), 404) from exc

    @app.post('/api/jobs/{jid}/cancel')
    async def job_cancel(jid: str, request: Request):
        await read_body(request, 4096, 'application/json')
        try: return await run_in_threadpool(jobs.cancel, jid)
        except ValueError as exc: raise RequestError(str(exc), 404) from exc

    @app.get('/api/jobs/{jid}/events')
    async def job_events(jid: str, request: Request):
        try: await run_in_threadpool(jobs.get, jid)
        except ValueError as exc: raise RequestError(str(exc), 404) from exc
        async def events():
            previous = None; last = time.monotonic()
            while not await request.is_disconnected():
                job = jobs.public(await run_in_threadpool(jobs.get, jid))
                # Typed activity can grow while the stage text repeats; emit both changes.
                activity = job['activity']
                signature = (job['state'], job['stage'], job['error'], len(activity), activity[-1]['time'] if activity else None)
                if signature != previous:
                    yield 'event: job\ndata: ' + json.dumps(job, allow_nan=False) + '\n\n'
                    previous = signature; last = time.monotonic()
                if job['state'] in ('succeeded', 'failed', 'cancelled'): break
                if time.monotonic() - last > 10:
                    yield ': keep-alive\n\n'; last = time.monotonic()
                await asyncio.sleep(0.25)
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/api/jobs/{jid}/agui')
    async def job_agui(jid: str, request: Request):
        """Read-only AG-UI 1.0 events for one durable job; same session/auth as other job routes."""
        from . import agui
        try: await run_in_threadpool(jobs.get, jid)
        except ValueError as exc: raise RequestError(str(exc), 404) from exc
        cursor = request.headers.get('last-event-id')
        if not agui.valid_cursor(cursor): raise RequestError('Invalid Last-Event-ID.')
        async def events():
            position, last, project = cursor, time.monotonic(), None
            while not await request.is_disconnected():
                job = jobs.public(await run_in_threadpool(jobs.get, jid))
                terminal = job['state'] in ('succeeded', 'failed', 'cancelled')
                # Project state is only needed for conversation identity: at start and at the end.
                if project is None or terminal: project = await run_in_threadpool(jobs.flow.get, job['project'])
                for event_id, event in agui.events(job, project, position):
                    yield agui.encode(event_id, event)
                    position, last = event_id, time.monotonic()
                if terminal or position == agui.END: break
                if time.monotonic() - last > 10:
                    yield ': keep-alive\n\n'; last = time.monotonic()
                await asyncio.sleep(0.25)
        return StreamingResponse(events(), media_type=agui.CONTENT_TYPE, headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # Sign in with ChatGPT: local loopback servers only (single-user; never shared network deployments).
    from .chatgpt import CALLBACK_PATH, loopback_origin
    chatgpt_origin = None if network else loopback_origin(origin)

    def refuse_shared_chatgpt():
        """A shared/network server never invokes a ChatGPT account saved in this workspace."""
        if network:
            from .document_clients import ModelConfig
            if ModelConfig.for_workspace(jobs.root).provider == 'chatgpt':
                raise ValueError('This workspace is set to use a ChatGPT plan, which is available only on a local '
                                 'MCDXKit server (127.0.0.1). Choose OpenRouter in Services. Nothing was sent to ChatGPT.')

    def chatgpt():
        if chatgpt_origin is None:
            raise RequestError('Sign in with ChatGPT is available only when MCDXKit runs on this computer (127.0.0.1).', 404)
        from .chatgpt import ChatGPTAccounts
        return ChatGPTAccounts(jobs.root)

    @app.get('/api/chatgpt')
    async def chatgpt_status():
        if chatgpt_origin is None: return {'available': False, 'accounts': [], 'active': None}
        try: return {'available': True, **await run_in_threadpool(lambda: chatgpt().status())}
        except ValueError as exc: raise RequestError(str(exc)) from exc

    @app.post('/api/chatgpt/{action}')
    async def chatgpt_action(action: str, request: Request):
        if action not in ('connect', 'select', 'disconnect', 'models', 'acknowledge'): raise RequestError('Not found.', 404)
        try:
            data = json.loads(await read_body(request, 4096, 'application/json'))
            if not isinstance(data, dict) or set(data) - {'account', 'enable_plan', 'project', 'view'}: raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError('Expected a JSON object.')
        account = data.get('account')
        def perform():
            accounts = chatgpt()
            if account is not None and not isinstance(account, str): raise ValueError('Choose a ChatGPT account.')
            if action == 'connect':
                # Return only to a project in this workspace and a known view; never an arbitrary URL.
                return_to = None
                if data.get('project'):
                    documents().get(data['project'])
                    view = data.get('view') if data.get('view') in ('ask', 'worksheet', 'inputs', 'requirements', 'outputs') else 'ask'
                    return_to = {'project': data['project'], 'view': view}
                # Only a one-time start handle reaches the browser, never the authorize URL.
                return {'start': '/auth/start?handle=' + accounts.begin(chatgpt_origin, account, data.get('enable_plan') is True, return_to)}
            if not account: raise ValueError('Choose a ChatGPT account.')
            if action == 'select': return accounts.select(account)
            if action == 'acknowledge': return accounts.acknowledge(account)
            if action == 'disconnect': return accounts.disconnect(account)
            return accounts.models(account)
        try: return await run_in_threadpool(perform)
        except ValueError as exc: raise RequestError(str(exc)) from exc

    @app.get('/auth/start')
    async def chatgpt_start(handle: str = ''):
        try: url, binding = await run_in_threadpool(lambda: chatgpt().start(handle[:200]))
        except ValueError:
            return RedirectResponse('/?chatgpt=failed', status_code=303)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie('mcdxkit_chatgpt', binding, max_age=600, httponly=True, samesite='lax', path=CALLBACK_PATH)
        return response

    @app.get(CALLBACK_PATH)
    async def chatgpt_callback(request: Request):
        query = {k: v[:4096] for k, v in request.query_params.items() if k in ('code', 'state', 'error', 'client_id', 'scope')}
        binding = request.cookies.get('mcdxkit_chatgpt', '')
        # Failures are recorded server-side for the Services panel; the URL carries only an outcome code.
        outcome, return_to = await run_in_threadpool(lambda: chatgpt().complete(query, binding))
        target = {'chatgpt': {'connected': 'connected', 'plan_not_enabled': 'plan'}.get(outcome, 'failed')}
        if return_to: target = {'project': return_to['project'], 'view': return_to['view'], **target}
        response = RedirectResponse('/?' + urlencode(target), status_code=303)
        response.delete_cookie('mcdxkit_chatgpt', path=CALLBACK_PATH, httponly=True, samesite='lax')
        return response

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

    def templates():
        from .template_library import TemplateLibrary
        return TemplateLibrary(state.output_dir / '.documents')

    @app.get('/api/templates')
    async def template_listing(request: Request):
        archived = request.query_params.get('archived') == '1'
        return await run_in_threadpool(execute, lambda: {
            'templates': templates().listing(archived, documents().template_usage())})

    @app.post('/api/templates/upload')
    async def template_upload(request: Request):
        raw = await read_body(request, 32 * MIB, 'application/octet-stream')
        return await run_in_threadpool(execute, lambda: templates().add(request.query_params.get('name'), raw))

    @app.post('/api/templates/{tid}/{action}')
    async def template_action(tid: str, action: str, request: Request):
        if action not in ('use', 'archive', 'restore'): raise RequestError('Not found.', 404)
        def perform():
            library = templates()
            if action == 'use':
                # Registers the verified library file for the existing inspect/view/connect endpoints.
                return {**state.register(library.path(tid), 'template'), 'template_id': tid}
            return library.archive(tid, action == 'archive')
        return await run_in_threadpool(execute, perform)

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
        if action not in ('configure', 'connect-openrouter', 'models', 'discover', 'start-service', 'create', 'connect', 'calculation', 'ask', 'show', 'ingest', 'status', 'draft', 'review', 'build', 'extract', 'review-requirement', 'requirement-draft', 'run-file'):
            raise RequestError('Not found.', 404)
        try:
            data = json.loads(await read_body(request, 16384, 'application/json'))
            if not isinstance(data, dict): raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError('Expected a JSON object.')
        def perform():
            flow = documents()
            if network and action == 'configure' and data.get('provider') == 'chatgpt':
                raise ValueError('ChatGPT plan use is available only on a local MCDXKit server (127.0.0.1).')
            if action == 'ask': refuse_shared_chatgpt()
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
                library_id = data.get('library_template')
                template = (templates().path(library_id) if library_id else
                            state.get(data['template_id'], ['template'])['path'] if data.get('template_id') else state.template)
                if template is None: raise ValueError('Select a template first.')
                options = {'cases': state.cases(data.get('cases')), 'input_map': data.get('input_map'), 'load_source': state.basis(data)}
                if action == 'connect': return flow.attach_calculation(pid, report, template, name=data.get('name'), template_id=library_id, **options)
                return flow.create(data.get('title'), report, template, **options)
            if action == 'calculation':
                project = flow.get(pid); flow.verify(project)
                calc = flow.require_calculation(project, data.get('calculation'))
                folder = flow.calculation_folder(pid, calc)
                return {'calculation': {k: calc[k] for k in ('id', 'name', 'report_name', 'template_name', 'created')},
                        'report': state.register(folder / 'loads.txt', 'report'),
                        'template': state.register(folder / 'template.mcdx', 'template'),
                        'options': calc['options']}
            if action == 'show': return flow.public(flow.get(pid))
            if action == 'run-file':
                # The generated Mathcad file a run delivers, verified against its package, for the read-only viewer.
                return state.register(flow.artifact(pid, data.get('run'), 'calculation.mcdx'), 'worksheet')
            if action == 'extract': return flow.extract(pid, data.get('source'), data.get('pages'), data.get('question'))
            if action == 'review-requirement': return flow.review_requirement(pid, data.get('requirement'), data.get('status'), data.get('reviewer'), data.get('note'))
            if action == 'requirement-draft': return flow.requirement_draft(pid, data.get('requirements'), data.get('bindings'), data.get('reviewer'), data.get('note'), data.get('applicability'), data.get('calculation'))
            if action == 'ingest': return flow.ingest(pid)
            if action == 'status': return flow.status(pid)
            if action == 'ask': return flow.ask(pid, data.get('question'), parent=data.get('parent'))
            if action == 'draft': return flow.draft(pid, data.get('request'), calculation=data.get('calculation'))
            if action == 'review': return flow.review(pid, data.get('draft'), data.get('accepted'), data.get('reviewer'), data.get('note'))
            return flow.build(pid, data.get('draft'), data.get('calculation'))
        return await run_in_threadpool(execute, perform)

    @app.get('/api/documents/{pid}/answers/{aid}/audit')
    async def answer_audit(pid: str, aid: str):
        """Read-only audit receipt download; same session/token/Origin protections as other /api routes."""
        try: receipt = await run_in_threadpool(documents().answer_audit, pid, aid)
        except ValueError as exc: raise RequestError(str(exc), 404) from exc
        return JSONResponse(receipt, headers={'Content-Disposition': f'attachment; filename="mcdxkit-answer-{aid}-audit.json"'})

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
        return FileResponse(STATIC / 'workspace-dist' / 'index.html', media_type='text/html')

    @app.get('/library')
    async def classic_documents():
        return FileResponse(STATIC / 'documents.html', media_type='text/html')

    @app.get('/api/history')
    async def history():
        return await run_in_threadpool(execute, state.history)

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'workspace-dist' / 'index.html', media_type='text/html')

    @app.get('/convert')
    async def classic_conversion():
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    @app.get('/workspace-assets/{name}')
    async def workspace_asset(name: str):
        path = WORKSPACE_ASSETS.get(name)
        if path is None or path.suffix not in ('.js', '.css'): raise RequestError('Not found.', 404)
        return FileResponse(path, media_type='text/javascript' if path.suffix == '.js' else 'text/css')

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
