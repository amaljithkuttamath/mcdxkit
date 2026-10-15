"""Durable local jobs. Huey dispatches IDs; SQLite owns decisions and publication.

Workers use isolated processes so cancellation can stop the owned calculator.
Interrupted remote requests are never automatically repeated (billing may be unknown).
"""
import argparse
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from huey import SqliteHuey
from huey.serializer import Serializer
from huey.registry import Message

from .documents import MAX_CALCULATIONS, DocumentWorkflow, digest, identifier, text

ACTIONS = {'ask', 'draft', 'extract', 'extract_offer', 'build', 'calculate', 'calculate_all', 'ingest', 'index_status'}
TERMINAL = {'succeeded', 'failed', 'cancelled'}
STAGES = {'ask': 'Finding evidence and writing a cited answer', 'draft': 'Preparing a cited design basis',
          'extract': 'Reading selected pages and extracting requirements', 'build': 'Calculating template equations and preparing outputs'}
STAGES['calculate'] = STAGES['build']
STAGES['calculate_all'] = 'Calculating the selected calculations one at a time'
STAGES['extract_offer'] = 'Saving proposed requirements for review'
STAGES.update(ingest='Submitting documents for indexing', index_status='Checking document indexing')
# Observed activity types. They map onto AG-UI event families (step, tool call result,
# activity snapshot) without exposing model reasoning; see docs/document-workflow.md.
ACTIVITY = {'step', 'search', 'context', 'extraction', 'check'}


def activity_detail(detail):
    """Keep only bounded, typed display fields; never payloads, secrets or model text."""
    clean = {}
    for key, value in (detail or {}).items():
        if key in ('query', 'source', 'status', 'extraction_id') and isinstance(value, str): clean[key] = value[:200]
        elif key in ('passages', 'facts', 'citations', 'unsupported', 'finished', 'total') and type(value) is int and 0 <= value < 10000: clean[key] = value
        elif key == 'pages' and isinstance(value, list): clean[key] = [p for p in value if type(p) is int and p > 0][:12]
        elif key == 'sources' and isinstance(value, list): clean[key] = [s[:200] for s in value if isinstance(s, str)][:5]
    return clean


class JobStore:
    def __init__(self, root):
        self.flow = DocumentWorkflow(root)
        self.root = self.flow.root
        with self.flow.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, action TEXT NOT NULL,
                payload TEXT NOT NULL, snapshot TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, owner TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                stage TEXT NOT NULL, result TEXT, error TEXT, uncertain INTEGER NOT NULL DEFAULT 0,
                dispatched REAL NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS job_activity (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
                created REAL NOT NULL, message TEXT NOT NULL, kind TEXT, detail TEXT)''')
            # A job is uncertain only once it may have reached a remote service; `remote` records the
            # first outbound connection. Workspaces created earlier gain the column in place.
            if 'remote' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
                try: db.execute('ALTER TABLE jobs ADD COLUMN remote INTEGER NOT NULL DEFAULT 0')
                except sqlite3.OperationalError as exc:
                    if 'duplicate column' not in str(exc): raise
            # Workspaces created before typed activity gain nullable columns in place.
            columns = {row[1] for row in db.execute('PRAGMA table_info(job_activity)')}
            for column in ('kind', 'detail'):
                if column not in columns:
                    try: db.execute(f'ALTER TABLE job_activity ADD COLUMN {column} TEXT')
                    except sqlite3.OperationalError as exc:
                        if 'duplicate column' not in str(exc): raise

    def snapshot(self, project, action, payload):
        state = self.flow.get(project)
        result = {'revision': state['revision'], 'sources': [(s['id'], s['sha256']) for s in state['sources']]}
        if action == 'build': result['draft'] = self.flow.selected_draft(state, payload['draft'])
        if action in ('build', 'calculate'):
            # Only the calculation this job uses: adding another calculation never makes it stale.
            chosen = self.flow.draft_calculation(state, result['draft']) if action == 'build' else payload.get('calculation')
            calc = self.flow.require_calculation(state, chosen)
            result['calculation'] = {k: calc.get(k) for k in ('id', 'report_sha256', 'template_sha256', 'options')}
        if action == 'calculate_all':
            # Only the pinned calculations: adding another one later never makes this job stale or retargets it.
            result['calculations'] = [{k: calc.get(k) for k in ('id', 'report_sha256', 'template_sha256', 'options')}
                                      for calc in (self.flow.require_calculation(state, cid) for cid in payload['calculations'])]
        from .document_clients import ModelConfig
        from .knowledge import VERSION
        from .extraction import MODEL, SCHEMA_VERSION
        if action == 'extract_offer': result['offer'] = self.flow.extraction_offer(state, payload['answer'])
        if action in ('ask', 'draft', 'extract', 'extract_offer', 'ingest', 'index_status'):
            config = ModelConfig.for_workspace(self.root)
            # Only public routing identity; never store secret values in jobs.
            result['services'] = {k: v for k, v in config.public().items() if k in ('url', 'model', 'embedding', 'reranker', 'provider', 'chatgpt_model')}
            # Switching ChatGPT account after submission makes the job stale (account ID only, never tokens).
            if config.provider == 'chatgpt' and action in ('ask', 'extract_offer'):
                result['chatgpt_account'] = self.flow.chatgpt_account(config)
            result['protocol'] = [VERSION, MODEL, SCHEMA_VERSION]
        if action == 'ask': result['history'] = self.flow.conversation_history(state, payload.get('parent'))
        return digest(json.dumps(result, sort_keys=True, allow_nan=False).encode())

    def submit(self, project, action, payload, request_key):
        identifier(project)
        if action not in ACTIONS or not isinstance(payload, dict): raise ValueError('Unsupported background operation.')
        fields = {'ask': {'question'}, 'draft': {'request'}, 'extract': {'source', 'pages', 'question'}, 'extract_offer': {'answer'}, 'build': {'draft'}, 'calculate': set(), 'calculate_all': set(), 'ingest': set(), 'index_status': set()}[action]
        if set(payload) != fields and not (action == 'ask' and set(payload) == fields | {'parent'}) \
                and not (action in ('calculate', 'draft') and set(payload) == fields | {'calculation'}) \
                and not (action == 'calculate_all' and set(payload) == {'calculations'}):
            raise ValueError('Invalid job fields.')
        if action in ('ask', 'draft'): text(payload[next(iter(fields))], 'Request', 2000)
        if action == 'ask': self.flow.conversation_history(self.flow.get(project), payload.get('parent'))
        if action == 'extract':
            from .extraction import parse_pages
            self.flow.source_path(project, payload['source'])
            payload = {**payload, 'pages': parse_pages(payload['pages'])}
            text(payload['question'], 'Extraction request', 2000)
        if action == 'build':
            draft = self.flow.selected_draft(self.flow.get(project), payload['draft'])
            if not draft['review']: raise ValueError('Review the design basis before calculating.')
        if action in ('calculate', 'draft'):
            # Pin the job to the calculation it was submitted for, so adding another one later cannot retarget it.
            state = self.flow.get(project)
            if action == 'calculate' or self.flow.calculations(state):
                payload = {**payload, 'calculation': self.flow.require_calculation(state, payload.get('calculation'))['id']}
        if action == 'calculate_all':
            # Pin the exact calculations; omitted means every calculation the project holds now.
            state = self.flow.get(project); chosen = payload.get('calculations')
            if chosen is None: chosen = [c['id'] for c in self.flow.calculations(state)] or [self.flow.require_calculation(state)['id']]
            if not isinstance(chosen, list) or not chosen or len(chosen) > MAX_CALCULATIONS or len(set(map(str, chosen))) != len(chosen):
                raise ValueError('Choose one or more distinct calculations to calculate.')
            payload = {'calculations': [self.flow.require_calculation(state, cid)['id'] for cid in chosen]}
        # Only an answer saved in this project with a proposal can be confirmed (unknown/foreign IDs fail).
        if action == 'extract_offer': self.flow.extraction_offer(self.flow.get(project), payload['answer'])
        key = text(request_key, 'Request identity', 160)
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        snapshot = self.snapshot(project, action, payload)
        now = time.time(); jid = secrets.token_hex(16)
        with self.flow.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT id,project,action,payload,snapshot FROM jobs WHERE request_key=?', (key,)).fetchone()
            if previous:
                if previous[1:] != (project, action, encoded, snapshot): raise ValueError('Request identity was already used for different inputs.')
                jid = previous[0]
            else:
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 16:
                    raise ValueError('The workspace has 16 pending jobs. Wait or cancel a queued job.')
                db.execute('INSERT INTO jobs (id,project,action,payload,snapshot,request_key,state,created,updated,stage) VALUES (?,?,?,?,?,?,?, ?,?,?)',
                           (jid, project, action, encoded, snapshot, key, 'queued', now, now, 'Waiting for worker'))
        # Durable queued row is also the outbox. A crash here is repaired by dispatch().
        self.dispatch()
        return self.get(jid)

    def get(self, jid, project=None):
        identifier(jid)
        with self.flow.connection() as db:
            db.row_factory = __import__('sqlite3').Row
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if row is None or (project is not None and row['project'] != project): raise ValueError('Job not found in this project.')
        result = dict(row)
        for key in ('payload', 'result'):
            result[key] = json.loads(result[key]) if result[key] else None
        result['uncertain'] = bool(result['uncertain'])
        with self.flow.connection() as db:
            # seq is the durable, monotonically increasing activity identity used for replay.
            result['activity'] = [{'seq': seq, 'time': created, 'message': message, 'type': kind or 'step',
                                   'detail': json.loads(detail) if detail else {}} for seq, created, message, kind, detail in
                                  db.execute('SELECT seq,created,message,kind,detail FROM job_activity WHERE job=? ORDER BY seq', (jid,))]
        return result

    @staticmethod
    def public(job):
        result = {k: job[k] for k in ('id', 'project', 'action', 'state', 'created', 'updated', 'stage', 'result', 'error', 'uncertain', 'activity')}
        if job['action'] == 'ask': result.update(request=job['payload']['question'], parent=job['payload'].get('parent'))
        return result

    def listing(self, project):
        self.flow.get(project)
        with self.flow.connection() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM jobs WHERE project=? ORDER BY created DESC LIMIT 100', (project,))]
        return [self.public(self.get(jid)) for jid in ids]

    def claim(self, jid):
        token = secrets.token_hex(16)
        with self.flow.connection() as db:
            changed = db.execute("UPDATE jobs SET state='running',owner=?,updated=?,stage=? WHERE id=? AND state='queued'",
                                 (token, time.time(), STAGES[self.get(jid)['action']], jid)).rowcount
        return token if changed else None

    @staticmethod
    def guard(db, jid, token):
        row = db.execute('SELECT state,owner FROM jobs WHERE id=?', (jid,)).fetchone()
        if row != ('running', token): raise ValueError('Job is no longer active or was cancelled; no result was published.')

    def publish(self, db, jid, token, result):
        self.guard(db, jid, token)
        db.execute("UPDATE jobs SET state='succeeded',stage='Complete',result=?,updated=? WHERE id=?",
                   (json.dumps(result, allow_nan=False), time.time(), jid))

    def workflow(self, jid, token, publish=True):
        # Without publish, saved records do not finish the job (calculate_all saves one run per calculation).
        return DocumentWorkflow(self.root, guard=lambda db: self.guard(db, jid, token),
                                publish=(lambda db, result: self.publish(db, jid, token, result)) if publish else None,
                                progress=lambda message: self.stage(jid, token, message),
                                activity=lambda kind, message, detail: self.stage(jid, token, message, kind, detail))

    def stage(self, jid, token, message, kind='step', detail=None):
        if kind not in ACTIVITY: raise ValueError('Unsupported activity type.')
        encoded = json.dumps(activity_detail(detail), allow_nan=False) if detail else None
        with self.flow.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(db, jid, token)
            # Messages may embed bounded user filenames/queries; never fail a run over display length.
            message = text(message[:200] if isinstance(message, str) else message, 'Stage', 200)
            previous = db.execute('SELECT message FROM job_activity WHERE job=? ORDER BY seq DESC LIMIT 1', (jid,)).fetchone()
            if previous != (message,):
                db.execute('INSERT INTO job_activity(job,created,message,kind,detail) VALUES (?,?,?,?,?)',
                           (jid, time.time(), message, kind, encoded))
                db.execute('DELETE FROM job_activity WHERE job=? AND seq NOT IN (SELECT seq FROM job_activity WHERE job=? ORDER BY seq DESC LIMIT 40)', (jid, jid))
            db.execute('UPDATE jobs SET stage=?,updated=? WHERE id=?', (message, time.time(), jid))

    def complete(self, jid, token, result):
        with self.flow.connection() as db:
            db.execute('BEGIN IMMEDIATE'); self.publish(db, jid, token, result)

    def validate_snapshot(self, jid, token):
        job = self.get(jid)
        with self.flow.connection() as db: self.guard(db, jid, token)
        if self.snapshot(job['project'], job['action'], job['payload']) != job['snapshot']:
            raise ValueError('Project inputs or review changed after submission. Start a new job.')
        self.flow.verify(self.flow.get(job['project']))
        return job

    def cancel(self, jid):
        self.get(jid)
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET state='cancelled',stage='Cancelled',updated=?,uncertain=remote WHERE id=? AND state IN ('queued','running')",
                       (time.time(), jid))
        return self.public(self.get(jid))

    def reject_shared_chatgpt(self, jid):
        """On a shared/network worker, fail a queued ChatGPT job before it is claimed (no request was made)."""
        from .chatgpt import disabled
        from .document_clients import ModelConfig
        job = self.get(jid)
        if not disabled() or job['state'] != 'queued' or job['action'] not in ('ask', 'extract_offer'): return False
        if ModelConfig.for_workspace(self.root).provider != 'chatgpt': return False
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET state='failed',stage='Needs attention',error=?,uncertain=0,updated=? WHERE id=? AND state='queued'",
                       ('ChatGPT plan use is available only on a local MCDXKit server (127.0.0.1). Choose OpenRouter in Services. '
                        'Nothing was sent to ChatGPT.', time.time(), jid))
        return True

    def fail(self, jid, token, message):
        # Uncertain only if this job opened a connection: a failure before that sent nothing anywhere.
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET state='failed',stage='Needs attention',error=?,uncertain=remote,updated=? WHERE id=? AND state='running' AND owner=?",
                       (message[:2000], time.time(), jid, token))

    def mark_remote(self, jid, token):
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET remote=1 WHERE id=? AND owner=? AND state='running'", (jid, token))

    def heartbeat(self, jid, token):
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET updated=? WHERE id=? AND owner=? AND state='running'", (time.time(), jid, token))

    def recover(self):
        with self.flow.connection() as db:
            db.execute("UPDATE jobs SET state='failed',stage='Interrupted',error=?,uncertain=remote,updated=? WHERE state='running' AND updated<?",
                       ('Worker interrupted. No automatic retry; a remote request may have completed. Inspect saved results before starting again.', time.time(), time.time()-60))

    def dispatch(self):
        with self.flow.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE state='queued' AND dispatched<?", (time.time()-10,))]
            for jid in ids:
                action = db.execute('SELECT action FROM jobs WHERE id=?', (jid,)).fetchone()[0]
                _, task = queue(self.root, 'chat' if action == 'ask' else 'batch')
                task(jid)
                db.execute('UPDATE jobs SET dispatched=? WHERE id=?', (time.time(), jid))


class JobSerializer(Serializer):
    def _serialize(self, message):
        return json.dumps(message, allow_nan=False).encode()

    def _deserialize(self, data):
        if len(data) > 8192: raise ValueError('Invalid queued job message.')
        message = Message(*json.loads(data))
        if len(message.args) != 1 or message.kwargs or message.on_complete or message.on_error or message.chord_config:
            raise ValueError('Only job IDs are accepted by this queue.')
        identifier(message.args[0])
        return message


def queue(root, channel='batch'):
    # Do not deserialize pickle from a writable queue database.
    if channel not in ('chat', 'batch'): raise ValueError('Invalid worker channel.')
    huey = SqliteHuey('mcdxkit-' + channel, filename=str(root / (channel + '-queue.sqlite3')), results=False,
                     serializer=JobSerializer())
    def run(jid):
        supervise(root, jid)
    run.__module__ = 'mcdxkit.jobs'  # Stable registry identity when launched with python -m.
    return huey, huey.task(name='run', retries=0)(run)


STOP = threading.Event()


def terminate(child):
    if child.poll() is not None: return
    if os.name == 'posix':
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    else:
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True, timeout=5, check=False)
    try: child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        else: child.kill()
        child.wait()


def supervise(root, jid):
    store = JobStore(root)
    if store.reject_shared_chatgpt(jid): return
    token = store.claim(jid)
    if token is None: return
    child = None
    try:
        child = subprocess.Popen([sys.executable, '-m', 'mcdxkit.jobs', '--run', str(root), jid, token],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name == 'posix')
        job = store.get(jid)
        # calculate_all runs its calculations in sequence; each gets the single-calculation allowance.
        deadline = time.monotonic() + 600 * (len(job['payload']['calculations']) if job['action'] == 'calculate_all' else 1)
        while child.poll() is None:
            if STOP.is_set() or store.get(jid)['state'] == 'cancelled' or time.monotonic() > deadline:
                terminate(child); break
            store.heartbeat(jid, token); STOP.wait(0.4)
    finally:
        if child is not None: terminate(child)
        store.fail(jid, token, 'Worker interrupted. No automatic retry; inspect the project before trying again.')


# The job this process is executing. One audit hook per process sees every outbound socket connection
# (urllib, httpx and the SDKs built on them), so no client can reach a service without the job recording it.
WATCH: dict = {'job': None, 'installed': False}


def _watch_network(event, args):
    if event != 'socket.connect' or WATCH['job'] is None: return
    store, jid, token = WATCH['job']; WATCH['job'] = None    # record once; the database write opens no socket
    try: store.mark_remote(jid, token)
    except Exception:
        # Never let a request leave unrecorded: refusing the connection keeps "nothing was sent" true.
        raise RuntimeError('Could not record the outbound request; nothing was sent.') from None


def execute(root, jid, token):
    store = JobStore(root)
    if not WATCH['installed']:
        sys.addaudithook(_watch_network); WATCH['installed'] = True
    WATCH['job'] = (store, jid, token)
    try:
        job = store.validate_snapshot(jid, token)
        action = job['action']
        flow = store.workflow(jid, token, publish=action != 'calculate_all'); payload = job['payload']; pid = job['project']
        if action == 'ask': result = flow.ask(pid, payload['question'], parent=payload.get('parent'))
        elif action == 'draft': result = flow.draft(pid, payload['request'], calculation=payload.get('calculation'))
        elif action == 'extract': result = flow.extract(pid, payload['source'], payload['pages'], payload['question'])
        elif action == 'extract_offer': result = flow.accept_extraction_offer(pid, payload['answer'])
        elif action == 'ingest': result = flow.ingest(pid)
        elif action == 'index_status': result = flow.status(pid)
        elif action == 'calculate_all': result = flow.calculate_all(pid, payload['calculations'])
        else: result = flow.build(pid, payload.get('draft'), payload.get('calculation'))
        # New records publish atomically with the job. Cached extraction has no new record.
        if store.get(jid)['state'] == 'running': store.complete(jid, token, result)
    except Exception as exc:
        # Provider transport errors are sanitized at the HTTP client boundary.
        message = str(exc) if isinstance(exc, ValueError) else 'Operation failed. Inspect the input files and service configuration.'
        store.fail(jid, token, message)
    finally:
        WATCH['job'] = None


def worker(root, parent=None, channel='batch'):
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: STOP.set())
    store = JobStore(root); huey, _ = queue(store.root, channel)
    while not STOP.is_set():
        if parent is not None and os.getppid() != parent: break
        store.recover(); store.dispatch()
        task = huey.dequeue()
        if task is not None: huey.execute(task)
        else: STOP.wait(0.4)


def start_worker(root, channel='batch', chatgpt=True):
    # chatgpt=False (shared/network servers): the worker and every job child it starts inherit
    # MCDXKIT_CHATGPT_DISABLED, so ChatGPT jobs fail validation before any network request.
    from .chatgpt import DISABLED_ENV
    env = None if chatgpt else {**os.environ, DISABLED_ENV: '1'}
    return subprocess.Popen([sys.executable, '-m', 'mcdxkit.jobs', '--worker', str(root), '--parent', str(os.getpid()), '--channel', channel],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name == 'posix', env=env)


def main():
    parser = argparse.ArgumentParser(description='MCDXKit local background worker')
    parser.add_argument('--worker'); parser.add_argument('--parent', type=int)
    parser.add_argument('--channel', choices=['chat', 'batch'], default='batch')
    parser.add_argument('--run', nargs=3, metavar=('WORKSPACE', 'JOB', 'OWNER'))
    args = parser.parse_args()
    if args.run: execute(*args.run)
    elif args.worker: worker(args.worker, args.parent, args.channel)
    else: parser.error('Choose --worker or --run')


if __name__ == '__main__': main()
