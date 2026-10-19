"""Sign in with ChatGPT for a local, single-user MCDXKit server (OpenAI OSS/local flow).

Follows the documented flow: authorization code + PKCE, dynamic client registration
(`dynamic_agent_client`), a persistent opaque host ID, an exact 127.0.0.1 loopback
callback, OpenID Connect ID-token validation and the ChatGPT plan-usage scope.

Tokens stay in owner-only files under the workspace and are never returned to the
browser, logged or exported. A new sign-in replaces nothing until it is validated.

Concurrency: every read-modify-write of the account registry, and every change to one
account's tokens (sign-in, refresh, sign-out), happens under a short cross-process
SQLite lease. The lease row is committed before any network call, so no database
transaction spans network I/O. The only network call made while a lease is held is one
token-endpoint request, bounded by NETWORK_DEADLINE, well inside LEASE_SECONDS.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ISSUER = 'https://auth.openai.com'
ISSUER_HOST = 'auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
DISCOVERY = ISSUER + '/.well-known/openid-configuration'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
DYNAMIC_CLIENT = 'dynamic_agent_client'
APP_NAME = 'MCDXKit'
CALLBACK_PATH = '/auth/callback'
USAGE_URL = 'https://chatgpt.com/settings/usage'
ATTEMPT_SECONDS = 600
START_SECONDS = 60
REFRESH_MARGIN = 120            # refresh this many seconds before access-token expiry
NETWORK_TIMEOUT = 15            # per connect/read on auth and catalog requests
NETWORK_DEADLINE = 20           # whole-body deadline checked while reading
MAX_AUTH_BYTES = 256 * 1024     # discovery, JWKS, token and model-list responses
LEASE_SECONDS = 120             # > one bounded token request (worst case ~NETWORK_DEADLINE + NETWORK_TIMEOUT)
LEASE_WAIT = 45                 # wait for another holder before reporting "in progress"
JWKS_SECONDS = 600
DISABLED_ENV = 'MCDXKIT_CHATGPT_DISABLED'
TERMINAL_REFRESH = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                    'refresh_token_invalidated', 'refresh_token_reused'}
ALGORITHMS = ('RS256', 'PS256', 'ES256')
_CLIENT_ID = re.compile(r'[A-Za-z0-9_.:-]{1,200}')
_lock = threading.RLock()       # in-process guard for attempt maps and caches
_cache: dict = {}               # discovery and JWKS, process-local


class ChatGPTError(ValueError):
    """A user-facing failure. Messages never contain tokens or authorization URLs."""


def disabled():
    """Shared/network servers start their workers with ChatGPT disabled for this workspace."""
    return os.environ.get(DISABLED_ENV) == '1'


def require_enabled():
    if disabled():
        raise ChatGPTError('ChatGPT plan use is available only on a local MCDXKit server (127.0.0.1). '
                           'This server is shared; choose OpenRouter. Nothing was sent to ChatGPT.')


def loopback_origin(origin):
    """Return the origin when it is exactly http://127.0.0.1:<port>, else None (fail closed)."""
    try:
        parts = urlsplit(origin or '')
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not port or not 0 < port < 65536:
        return None
    if parts.netloc != f'127.0.0.1:{port}' or parts.path not in ('', '/') or parts.query or parts.fragment:
        return None
    return f'http://127.0.0.1:{port}'


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class _Reply:
    def __init__(self, status_code, headers, content):
        self.status_code, self.headers, self.content = status_code, headers, content

    def json(self):
        try: value = json.loads(self.content)
        except (ValueError, UnicodeError): return {}
        return value if isinstance(value, dict) else {}


def _call(method, url, *, limit=MAX_AUTH_BYTES, **kwargs):
    """One bounded request: no env proxies, no redirects, capped size and total time."""
    import httpx
    deadline = time.monotonic() + NETWORK_DEADLINE
    try:
        with httpx.Client(timeout=NETWORK_TIMEOUT, follow_redirects=False, trust_env=False) as http, \
                http.stream(method, url, **kwargs) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit: raise ChatGPTError('ChatGPT returned an unexpectedly large response. Nothing changed.')
                if time.monotonic() > deadline: raise ChatGPTError('ChatGPT sign-in services did not respond in time. Nothing changed.')
            return _Reply(response.status_code, response.headers, bytes(body))
    except httpx.HTTPError:
        raise ChatGPTError('Could not reach ChatGPT sign-in services. Check the connection and try again; nothing changed.') from None


def _request_id(response):
    value = response.headers.get('x-request-id') or response.headers.get('openai-request-id') or ''
    return value if re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value) else ''


def _error_code(response):
    body = response.json()
    error = body.get('error')
    code = error.get('code') if isinstance(error, dict) else error
    return code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code) else ''


def _official(url):
    """https on the OpenAI issuer host only (no userinfo, port or query)."""
    try: parts = urlsplit(url if isinstance(url, str) else '')
    except ValueError: return False
    return parts.scheme == 'https' and parts.netloc == ISSUER_HOST and not parts.query and not parts.fragment


def discovery():
    with _lock:
        cached = _cache.get('discovery')
        if cached and cached[0] + JWKS_SECONDS > time.time(): return cached[1]
    value = _call('GET', DISCOVERY, headers={'Accept': 'application/json'}).json()
    if value.get('issuer') != ISSUER or not _official(value.get('jwks_uri')):
        raise ChatGPTError('ChatGPT sign-in configuration could not be verified. Nothing changed.')
    with _lock: _cache['discovery'] = (time.time(), value)
    return value


def _signing_keys(force=False):
    import jwt
    with _lock:
        cached = _cache.get('jwks')
        if cached and not force and cached[0] + JWKS_SECONDS > time.time(): return cached[1]
    data = _call('GET', discovery()['jwks_uri'], headers={'Accept': 'application/json'}).json()
    try: keys = jwt.PyJWKSet.from_dict(data)
    except jwt.PyJWTError: raise ChatGPTError('ChatGPT signing keys could not be read. Nothing changed.') from None
    with _lock: _cache['jwks'] = (time.time(), keys)
    return keys


def verify_id_token(token, client_id, nonce):
    """Signature (OpenAI JWKS via discovery), issuer, audience, expiry and nonce, using PyJWT."""
    import jwt
    try:
        kid = jwt.get_unverified_header(token).get('kid')
        keys = _signing_keys()
        try: key = keys[kid]
        except KeyError: key = _signing_keys(force=True)[kid]    # refresh once for an unfamiliar kid
        if key.algorithm_name not in ALGORITHMS: raise jwt.InvalidAlgorithmError('Unexpected key algorithm')
        claims = jwt.decode(token, key.key, algorithms=[key.algorithm_name], audience=client_id, issuer=ISSUER,
                            leeway=5, options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})
    except (jwt.PyJWTError, KeyError):
        raise ChatGPTError('The ChatGPT ID token could not be verified. Nothing changed.') from None
    if not isinstance(claims.get('nonce'), str) or not hmac.compare_digest(claims['nonce'], nonce):
        raise ChatGPTError('The ChatGPT ID token did not match this sign-in attempt. Nothing changed.')
    if not isinstance(claims.get('sub'), str) or not claims['sub']:
        raise ChatGPTError('The ChatGPT ID token has no subject. Nothing changed.')
    return claims


def _plan(record):
    return bool(record) and PLAN_SCOPE in (record.get('scopes') or [])


class ChatGPTAccounts:
    """Registrations, credentials and pending sign-in attempts for one workspace."""

    # Pending attempts live in the server process only; a restart safely invalidates them.
    _pending: dict[str, dict] = {}
    _starts: dict[str, dict] = {}
    _last_result: dict[str, str] = {}

    def __init__(self, root):
        self.dir = Path(root) / '.chatgpt'
        if self.dir.is_symlink(): raise ChatGPTError('ChatGPT credential storage must not be a symbolic link.')
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    # ---- storage -------------------------------------------------------------------
    def _write(self, name, value):
        fd, temporary = tempfile.mkstemp(prefix='.' + name + '-', dir=self.dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.dir / name)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _read(self, name, default):
        path = self.dir / name
        if path.is_symlink(): raise ChatGPTError('ChatGPT credential files must not be symbolic links.')
        if not path.exists(): return default
        try:
            with path.open(encoding='utf-8') as stream: return json.loads(stream.read(1024 * 1024))
        except (ValueError, UnicodeError):
            raise ChatGPTError('Saved ChatGPT sign-in data is unreadable. Sign out and continue with ChatGPT again.') from None

    def host_id(self):
        """Opaque, persistent per-host identifier, kept apart from account records.

        Created by hard-linking a complete temporary file, so a concurrent creator either
        wins or reads the winner's complete value; it never fails or sees a partial file."""
        path = self.dir / 'host-id'
        for _ in range(2):
            if path.exists() and not path.is_symlink():
                value = path.read_text(encoding='utf-8').strip()
                if re.fullmatch(r'urn:uuid:[0-9a-f-]{36}', value): return value
                raise ChatGPTError('The saved ChatGPT host identifier is invalid; restore it rather than creating a new one.')
            fd, temporary = tempfile.mkstemp(prefix='.host-id-', dir=self.dir)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write('urn:uuid:' + str(uuid.uuid4())); stream.flush(); os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                try: os.link(temporary, path)
                except FileExistsError: pass            # another process won; read its value
            finally:
                Path(temporary).unlink(missing_ok=True)
        raise ChatGPTError('The ChatGPT host identifier could not be created.')

    def registry(self):
        value = self._read('accounts.json', {'active': None, 'accounts': []})
        if not isinstance(value, dict) or not isinstance(value.get('accounts'), list):
            raise ChatGPTError('Saved ChatGPT accounts are invalid.')
        return value

    @contextmanager
    def _registry_update(self):
        """Cross-process read-modify-write of accounts.json."""
        with self.lease('registry'):
            registry = self.registry()
            yield registry
            self._write('accounts.json', registry)

    def account(self, rid):
        found = next((a for a in self.registry()['accounts'] if a['id'] == rid), None)
        if found is None: raise ChatGPTError('ChatGPT account not found on this computer.')
        return found

    def tokens(self, rid):
        if not re.fullmatch(r'[0-9a-f]{16}', rid or ''): raise ChatGPTError('Invalid ChatGPT account.')
        return self._read(f'tokens-{rid}.json', None)

    @contextmanager
    def lease(self, key, wait=LEASE_WAIT):
        """Cross-process lease. Acquisition and release are separate short transactions."""
        owner = secrets.token_hex(16)
        db = sqlite3.connect(self.dir / 'locks.sqlite3', timeout=5)
        try:
            with db: db.execute('CREATE TABLE IF NOT EXISTS leases (account TEXT PRIMARY KEY, owner TEXT, expires REAL)')
            deadline = time.monotonic() + wait
            while True:
                with db:
                    db.execute('DELETE FROM leases WHERE expires<?', (time.time(),))
                    if db.execute('INSERT OR IGNORE INTO leases VALUES (?,?,?)', (key, owner, time.time() + LEASE_SECONDS)).rowcount: break
                if time.monotonic() > deadline:
                    raise ChatGPTError('Another ChatGPT sign-in update is in progress. Try again shortly.')
                time.sleep(0.2)
            try: yield
            finally:
                with db: db.execute('DELETE FROM leases WHERE account=? AND owner=?', (key, owner))
        finally:
            db.close()

    # ---- public status -------------------------------------------------------------
    def status(self):
        registry = self.registry()
        accounts = []
        for item in registry['accounts']:
            saved = self.tokens(item['id'])
            accounts.append({'id': item['id'], 'label': item['label'], 'email': item.get('email'),
                             'name': item.get('name'), 'signed_in': bool(saved and saved.get('refresh_token')),
                             'plan_usage': _plan(saved), 'active': item['id'] == registry['active'],
                             'welcome_pending': not item.get('welcomed')})
        return {'accounts': accounts, 'active': registry['active'], 'usage_url': USAGE_URL,
                'last_result': self._last_result.get(str(self.dir))}

    def select(self, rid):
        with self._registry_update() as registry:
            if not any(a['id'] == rid for a in registry['accounts']): raise ChatGPTError('ChatGPT account not found on this computer.')
            registry['active'] = rid
        return self.status()

    def acknowledge(self, rid):
        with self._registry_update() as registry:
            for item in registry['accounts']:
                if item['id'] == rid: item['welcomed'] = True
        return self.status()

    # ---- sign-in -------------------------------------------------------------------
    def begin(self, origin, rid=None, enable_plan=False, return_to=None):
        """Create a pending attempt; returns a one-time start handle (no URL or token).

        `return_to` is a server-validated {'project', 'view'} pair, never a URL."""
        require_enabled()
        base = loopback_origin(origin)
        if base is None: raise ChatGPTError('Sign in with ChatGPT is available only when MCDXKit runs on this computer (127.0.0.1).')
        params = {'response_type': 'code', 'redirect_uri': base + CALLBACK_PATH, 'scope': SCOPES, 'resource': RESOURCE,
                  'ext_agent_host_id': self.host_id()}
        if rid:
            account = self.account(rid)
            params['client_id'] = account['client_id']
            # login_hint only: id_token_hint would put ID-token data in browser history and logs.
            if account.get('email'): params['login_hint'] = account['email']
            # Documented way to re-request plan permission after a decline; never on ordinary sign-in.
            if enable_plan: params['prompt'] = 'consent'
        else:
            params.update(client_id=DYNAMIC_CLIENT, agent_name_hint=APP_NAME)
        verifier = secrets.token_urlsafe(64)
        state, nonce, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        params.update(state=state, nonce=nonce, code_challenge_method='S256',
                      code_challenge=_b64url(hashlib.sha256(verifier.encode()).digest()))
        handle = secrets.token_urlsafe(32)
        now = time.time()
        with _lock:
            for store, life in ((self._pending, ATTEMPT_SECONDS), (self._starts, START_SECONDS)):
                for key in [k for k, v in store.items() if v['created'] + life < now]: del store[key]
            if len(self._pending) >= 8: raise ChatGPTError('Too many sign-in attempts are open. Finish or wait for them to expire.')
            self._pending[state] = {'created': now, 'verifier': verifier, 'nonce': nonce, 'account': rid,
                                    'client_id': params['client_id'], 'redirect_uri': params['redirect_uri'],
                                    'binding': hashlib.sha256(binding.encode()).hexdigest(), 'dir': str(self.dir),
                                    'return_to': return_to}
            self._starts[handle] = {'created': now, 'url': AUTHORIZE + '?' + urlencode(params), 'binding': binding}
        return handle

    def start(self, handle):
        """Consume a start handle; returns (authorize URL, binding cookie value)."""
        with _lock:
            item = self._starts.pop(handle or '', None)
        if item is None or item['created'] + START_SECONDS < time.time():
            raise ChatGPTError('This sign-in link expired. Choose Continue with ChatGPT again.')
        return item['url'], item['binding']

    def complete(self, query, binding):
        """Handle the loopback callback. Returns (outcome, validated return context or None)."""
        state = query.get('state') or ''
        with _lock:
            attempt = self._pending.pop(state, None)    # one-time, even on failure
        return_to = attempt.get('return_to') if attempt and attempt['dir'] == str(self.dir) else None
        try:
            require_enabled()
            if attempt is None or attempt['dir'] != str(self.dir) or attempt['created'] + ATTEMPT_SECONDS < time.time():
                raise ChatGPTError('Sign-in could not be verified. Start again from Services.')
            if not binding or not hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), attempt['binding']):
                raise ChatGPTError('Sign-in was started in a different browser session. Start again from Services.')
            if query.get('error'):
                if query['error'] == 'access_denied':
                    raise ChatGPTError('ChatGPT sign-in was cancelled or permission was declined. Nothing changed.')
                raise ChatGPTError('ChatGPT sign-in could not be completed. Nothing changed.')
            code = query.get('code')
            if not code: raise ChatGPTError('The ChatGPT callback contained no authorization code. Nothing changed.')
            returned = query.get('client_id')
            if attempt['client_id'] == DYNAMIC_CLIENT:
                if not returned or returned == DYNAMIC_CLIENT or not _CLIENT_ID.fullmatch(returned):
                    raise ChatGPTError('ChatGPT registration did not complete (no issued client ID). Nothing changed.')
                client_id = returned
            else:
                if returned and returned != attempt['client_id']:
                    raise ChatGPTError('ChatGPT returned a different app registration than the selected account. Nothing changed.')
                client_id = attempt['client_id']
            record = self._exchange(client_id, code, attempt)
            outcome = 'connected' if _plan(record) else 'plan_not_enabled'
            self._last_result[str(self.dir)] = outcome
            return outcome, return_to
        except ChatGPTError as exc:
            self._last_result[str(self.dir)] = str(exc)
            return 'failed', return_to

    def _exchange(self, client_id, code, attempt):
        # Network work first, without any lease; nothing is written until validation passes.
        response = _call('POST', TOKEN, data={'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                                              'code_verifier': attempt['verifier'], 'redirect_uri': attempt['redirect_uri'],
                                              'resource': RESOURCE}, headers={'Accept': 'application/json'})
        if response.status_code != 200:
            if _error_code(response) == 'invalid_grant':
                raise ChatGPTError('The authorization code was not accepted. Start sign-in again.')
            raise ChatGPTError(f'ChatGPT token exchange failed (HTTP {response.status_code}'
                               + (f', request {_request_id(response)}' if _request_id(response) else '') + '). Nothing changed.')
        tokens = self._token_response(response)
        if not tokens.get('id_token'): raise ChatGPTError('ChatGPT returned no ID token. Nothing changed.')
        tokens['scopes'] = tokens['scopes'] or []       # absent scopes grant nothing
        claims = verify_id_token(tokens['id_token'], client_id, attempt['nonce'])
        email = claims.get('email') if isinstance(claims.get('email'), str) else None
        name = claims.get('name') if isinstance(claims.get('name'), str) else None
        with self._registry_update() as registry:
            if attempt['account']:
                account = next((a for a in registry['accounts'] if a['id'] == attempt['account']), None)
                if account is None or account['client_id'] != client_id:
                    raise ChatGPTError('The selected ChatGPT account changed during sign-in. Nothing changed.')
                if account['subject'] != claims['sub'] or account['issuer'] != claims['iss']:
                    raise ChatGPTError('You signed in with a different ChatGPT account than the one selected. Nothing changed.')
            else:
                account = next((a for a in registry['accounts'] if a['client_id'] == client_id), None)
                if account is not None and account['subject'] != claims['sub']:
                    raise ChatGPTError('This app registration belongs to another ChatGPT account. Nothing changed.')
                if account is None:
                    labels = {a['label'] for a in registry['accounts']}
                    label = base = (email or name or 'ChatGPT account')[:120]
                    counter = 2
                    while label in labels: label = f'{base} ({counter})'; counter += 1
                    account = {'id': secrets.token_hex(8), 'label': label, 'issuer': claims['iss'], 'subject': claims['sub'],
                               'client_id': client_id, 'created': time.time(), 'welcomed': False}
                    registry['accounts'].append(account)
            account.update(email=email or account.get('email'), name=name or account.get('name'), updated=time.time())
            record = {'issuer': claims['iss'], 'subject': claims['sub'], 'client_id': client_id, **tokens}
            # Only now, after validation, do these credentials replace anything and become active.
            with self.lease(account['id']):
                self._write(f'tokens-{account["id"]}.json', record)
            registry['active'] = account['id']
        return record

    @staticmethod
    def _token_response(response):
        body = response.json()
        access, refresh, kind, expires = (body.get(k) for k in ('access_token', 'refresh_token', 'token_type', 'expires_in'))
        if not isinstance(access, str) or not access or not isinstance(kind, str) or kind.lower() != 'bearer' \
                or type(expires) is not int or not 0 < expires <= 86400:
            raise ChatGPTError('ChatGPT returned an incomplete token response.')
        scope = body.get('scope')
        now = time.time()
        result = {'access_token': access, 'token_type': 'Bearer', 'expires_at': now + expires, 'saved_at': now,
                  'scopes': sorted(set(scope.split())) if isinstance(scope, str) else None}
        if isinstance(refresh, str) and refresh: result['refresh_token'] = refresh
        if isinstance(body.get('id_token'), str): result['id_token'] = body['id_token']
        earliest = body.get('earliest_refresh_at')
        if type(earliest) in (int, float): result['earliest_refresh_at'] = float(earliest)
        return result

    # ---- use, refresh and sign-out -------------------------------------------------
    @staticmethod
    def _usable(saved):
        if not saved or not saved.get('refresh_token'):
            raise ChatGPTError('Not signed in to ChatGPT. Choose Continue with ChatGPT in Services.')
        if not _plan(saved):
            raise ChatGPTError("ChatGPT plan use isn't enabled for this account. Enable it from Services or choose another provider.")
        return saved

    def access_token(self, rid):
        """A current access token for inference or the model catalog; plan scope checked on every path."""
        require_enabled()
        saved = self._usable(self.tokens(rid))
        if saved['expires_at'] - time.time() <= REFRESH_MARGIN:
            saved = self._usable(self.refresh(rid))     # a refresh may narrow the grant
        return saved['access_token']

    def refresh(self, rid):
        account = self.account(rid)
        with self.lease(rid):
            saved = self.tokens(rid)        # another process may have refreshed, or signed out, while we waited
            if not saved or not saved.get('refresh_token'):
                raise ChatGPTError('Not signed in to ChatGPT. Choose Continue with ChatGPT in Services.')
            if saved['expires_at'] - time.time() > REFRESH_MARGIN: return saved
            if saved.get('earliest_refresh_at') and time.time() < saved['earliest_refresh_at'] and saved['expires_at'] > time.time():
                return saved
            # The single network call made under a lease; omit scope to retain the grant.
            response = _call('POST', TOKEN, data={'grant_type': 'refresh_token', 'client_id': account['client_id'],
                                                  'refresh_token': saved['refresh_token'], 'resource': RESOURCE},
                             headers={'Accept': 'application/json'})
            if response.status_code != 200:
                code = _error_code(response)
                if code in TERMINAL_REFRESH:
                    (self.dir / f'tokens-{rid}.json').unlink(missing_ok=True)
                    raise ChatGPTError('Your ChatGPT sign-in expired or was disconnected. Choose Continue with ChatGPT again.')
                if code == 'invalid_client':
                    raise ChatGPTError('ChatGPT rejected this app registration (invalid_client). Sign in again to register MCDXKit.')
                raise ChatGPTError(f'ChatGPT sign-in refresh failed temporarily (HTTP {response.status_code}). Credentials were kept; try again.')
            fresh = self._token_response(response)
            # Replace access token, expiry, scopes and the rotating refresh token together.
            record = {**saved, **{k: v for k, v in fresh.items() if k != 'id_token'}}
            if fresh['scopes'] is None: record['scopes'] = saved.get('scopes')
            record.setdefault('refresh_token', saved['refresh_token'])
            self._write(f'tokens-{rid}.json', record)
            return record

    def disconnect(self, rid):
        """Sign out: disable locally first (under the lease), then revoke with the captured token.

        Removing the token file under the account lease means a refresh cannot resurrect it: a
        refresh in progress finishes first and its result is removed; a later one finds no file.
        Requests already in flight may still complete; no new request can start."""
        account = self.account(rid)
        with self.lease(rid):
            saved = self.tokens(rid)
            (self.dir / f'tokens-{rid}.json').unlink(missing_ok=True)
        refresh_token = saved.get('refresh_token') if saved else None
        revoked = not refresh_token
        if refresh_token:
            for attempt in range(2):        # bounded: at most 2 × (discovery + revocation) requests
                try:
                    endpoint = discovery().get('revocation_endpoint')
                    if not _official(endpoint): break
                    response = _call('POST', endpoint, data={'token': refresh_token, 'token_type_hint': 'refresh_token',
                                                             'client_id': account['client_id']})
                    if response.status_code == 200: revoked = True; break
                    if response.status_code < 500: break
                except ChatGPTError:
                    pass
                if attempt == 0: time.sleep(1)
        return {**self.status(), 'revoked': revoked,
                'message': None if revoked else
                'Signed out on this computer, but remote revocation was not confirmed. You can disconnect MCDXKit in ChatGPT Settings.'}

    def models(self, rid):
        """Account-visible models (visibility "list"), in server order. Listing is not proof of inference."""
        token = self.access_token(rid)
        response = _call('GET', RESOURCE + '/models', limit=2 * 1024 * 1024, headers={'Authorization': 'Bearer ' + token})
        if response.status_code != 200:
            raise ChatGPTError(f'ChatGPT model list failed (HTTP {response.status_code}'
                               + (f', request {_request_id(response)}' if _request_id(response) else '') + ').')
        items = response.json().get('models')
        if not isinstance(items, list): raise ChatGPTError('ChatGPT returned an invalid model list.')
        models = []
        for item in items[:500]:
            if not isinstance(item, dict) or item.get('visibility') != 'list': continue
            slug, name = item.get('slug'), item.get('display_name')
            if isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,100}', slug):
                models.append({'slug': slug, 'display_name': name[:120] if isinstance(name, str) and name.strip() else slug})
        return {'models': models[:200], 'note': 'Listed for this account; a model is confirmed only after a request completes.'}
