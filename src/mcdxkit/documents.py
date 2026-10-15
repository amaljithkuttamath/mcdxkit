"""Durable, source-backed authoring. AI proposes prose/inputs; the shared engine calculates."""
import hashlib
import html
import json
import math
import re
import secrets
import shutil
import sqlite3
import time
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import batch, engine
from .review import runner as review_runner
from .document_clients import ModelConfig


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


LEGACY_CALCULATION = 'primary'     # never a valid identifier(), so it cannot collide with a new calculation
MAX_CALCULATIONS = 50


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid document project identifier.')
    return value


def text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1–{limit} characters.')
    return value.strip()


def short(name, limit=80):
    """Bound user-supplied names inside fixed-length activity messages."""
    return name if len(name) <= limit else name[:limit-1] + '…'


def validate_draft(payload, template_inputs, evidence):
    """Enforce evidence identity and scalar/unit boundaries, not engineering truth."""
    if not isinstance(payload, dict): raise ValueError('Draft must be an object.')
    sections, suggestions, gaps = (payload.get(k) for k in ('sections', 'suggestions', 'gaps'))
    if not isinstance(sections, list) or not 1 <= len(sections) <= 12:
        raise ValueError('Draft needs 1–12 cited sections.')
    if not isinstance(suggestions, list) or len(suggestions) > 50 or not isinstance(gaps, list) or len(gaps) > 30:
        raise ValueError('Invalid draft suggestions or gaps.')
    known = {e['id'] for e in evidence}
    inputs = {i['variable']: i for i in template_inputs['inputs']}
    def citations(row):
        refs = row.get('evidence_ids')
        if not isinstance(refs, list) or not refs or len(refs) > 12 or any(not isinstance(r, str) or r not in known for r in refs):
            raise ValueError('Draft contains missing or unknown evidence citations.')
        return list(dict.fromkeys(refs))
    clean_sections = []
    for row in sections:
        if not isinstance(row, dict): raise ValueError('Invalid draft section.')
        clean_sections.append({'heading': text(row.get('heading'), 'Heading', 160),
                               'body': text(row.get('body'), 'Section', 8000), 'evidence_ids': citations(row)})
    clean_suggestions, seen = [], set()
    for row in suggestions:
        if not isinstance(row, dict): raise ValueError('Invalid draft input.')
        variable = text(row.get('variable'), 'Variable', 100)
        spec = inputs.get(variable)
        if spec is None or spec.get('component') or spec.get('status') == 'ambiguous' or variable in seen:
            raise ValueError('Suggestions must name unique unmapped template inputs; loads remain sourced from the raw file.')
        value = row.get('value')
        if type(value) not in (float, int) or not math.isfinite(value): raise ValueError('Suggested value must be finite.')
        if row.get('unit') != spec['unit']: raise ValueError('Suggested unit must exactly match the template unit; no implicit conversion.')
        clean_suggestions.append({'variable': variable, 'value': value, 'unit': spec['unit'],
                                  'reason': text(row.get('reason'), 'Reason'), 'evidence_ids': citations(row)})
        seen.add(variable)
    return {'sections': clean_sections, 'suggestions': clean_suggestions, 'gaps': [text(g, 'Gap') for g in gaps]}


class DocumentWorkflow:
    """One trusted workspace, shared by CLI and server. SQLite serializes mutations."""
    def __init__(self, root, *, guard=None, publish=None, progress=None, activity=None):
        self.root = Path(root).resolve()
        self.guard = guard
        self.publish = publish
        self.progress = progress or (lambda message: None)
        self.activity = activity
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS extraction_attempts (
                project TEXT NOT NULL, identity TEXT NOT NULL, owner TEXT NOT NULL,
                status TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(project,identity))''')

    def event(self, kind, message, **detail):
        """Observed, typed activity. Plain progress callbacks receive only the message."""
        if self.activity is not None: self.activity(kind, message, detail)
        else: self.progress(message)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / 'projects.sqlite3', timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def edit(self, pid, *, result=None):
        identifier(pid)
        with self.connection() as db:
            try: db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                raise ValueError('Another document operation is running. Retry after it completes.') from None
            row = db.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()
            if not row: raise ValueError('Document project not found.')
            state = json.loads(row[0])
            yield state
            if self.guard is not None: self.guard(db)
            db.execute('UPDATE projects SET data=? WHERE id=?', (json.dumps(state, allow_nan=False), pid))
            if result is not None and self.publish is not None: self.publish(db, result)

    def get(self, pid):
        identifier(pid)
        with self.connection() as db:
            row = db.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()
        if not row: raise ValueError('Document project not found.')
        return json.loads(row[0])

    def listing(self):
        with self.connection() as db:
            records = [json.loads(r[0]) for r in db.execute('SELECT data FROM projects ORDER BY rowid DESC LIMIT 100')]
        return [{k: p[k] for k in ('id', 'title', 'created')} for p in records]

    def create(self, title, report=None, template=None, *, cases=None, input_map=None, load_source='effects'):
        title = text(title, 'Project title', 160)
        if (report is None) != (template is None):
            raise ValueError('Provide both the raw report and Mathcad template, or neither to start a library.')
        pid = secrets.token_hex(16)
        folder = self.root / pid
        folder.mkdir()
        try:
            state = {'id': pid, 'title': title, 'created': time.time(), 'revision': 0, 'sources': [],
                     'dataset_id': None, 'answers': [], 'drafts': [], 'runs': []}
            if report is not None:
                state['calculations'] = [self.new_calculation(folder, report, template, cases, input_map, load_source)]
            with self.connection() as db:
                db.execute('INSERT INTO projects VALUES (?,?)', (pid, json.dumps(state)))
            return state
        except Exception:
            shutil.rmtree(folder)
            raise

    @staticmethod
    def snapshot_calculation(folder, report, template, cases, input_map, load_source):
        if report is None or template is None:
            raise ValueError('Provide both the raw report and Mathcad template.')
        report, template = Path(report), Path(template)
        if report.stat().st_size > 16*1024*1024 or template.stat().st_size > 32*1024*1024:
            raise ValueError('Report/template exceeds the conversion upload limit.')
        if any((folder/name).exists() for name in ('loads.txt', 'template.mcdx')):
            raise ValueError('Calculation snapshots already exist here; they are never overwritten.')
        # Inspect the exact stored bytes, not a mutable external source path.
        with tempfile.TemporaryDirectory(dir=folder, prefix='.inputs-') as staging:
            raw, model = Path(staging)/'loads.txt', Path(staging)/'template.mcdx'
            shutil.copyfile(report, raw); shutil.copyfile(template, model)
            found = engine.inspect_report(raw, template=model, cases=cases, input_map=input_map, load_source=load_source)
            if found.get('template_validation', {}).get('status') != 'ready':
                raise ValueError('Resolve case selection and template input mapping in the calculation workspace first.')
            result = {'report_sha256': file_digest(raw), 'template_sha256': file_digest(model),
                      'report_name': report.name, 'template_name': template.name,
                      'template_inputs': engine.inspect_template(model, input_map=input_map),
                      'options': {'cases': found['selected_cases'], 'input_map': input_map, 'load_source': load_source}}
            raw.rename(folder/'loads.txt'); model.rename(folder/'template.mcdx')
            return result

    @staticmethod
    def new_calculation(folder, report, template, cases, input_map, load_source, name=None, template_id=None):
        """Snapshot one report/template pair into its own folder under the project."""
        cid = secrets.token_hex(16)
        target = folder / 'calculations' / cid
        target.mkdir(parents=True)
        try:
            snapshot = DocumentWorkflow.snapshot_calculation(target, report, template, cases, input_map, load_source)
        except Exception:
            shutil.rmtree(target)
            raise
        label = text(name, 'Calculation name', 160) if name else f"{snapshot['report_name']} + {snapshot['template_name']}"
        return {'id': cid, 'name': label, 'created': time.time(), 'template_id': template_id, **snapshot}

    def attach_calculation(self, pid, report, template, *, cases=None, input_map=None, load_source='effects', name=None, template_id=None):
        """Add a calculation. A project holds many; each is an immutable report/template snapshot.

        Adding one leaves `revision` unchanged: revision identifies the reference-document library, so
        existing answers, reviewed design bases and other calculations stay valid."""
        with self.edit(pid) as state:
            self.verify(state)
            if len(self.calculations(state)) >= MAX_CALCULATIONS:
                raise ValueError(f'Project limit: {MAX_CALCULATIONS} calculations. Create another project for more.')
            calculation = self.new_calculation(self.root / pid, report, template, cases, input_map, load_source, name, template_id)
            state.setdefault('calculations', []).append(calculation)
        return {**self.public(self.get(pid)), 'calculation': calculation}

    @staticmethod
    def public(state):
        """The project as the browser sees it: every calculation in one list, including a legacy one."""
        return {**state, 'calculations': DocumentWorkflow.calculations(state)}

    @staticmethod
    def calculations(state):
        """Every calculation in a project, oldest first. A project saved before projects held several
        calculations keeps its single snapshot at the project root; it is presented as calculation
        `primary` and its files are never moved."""
        rows = list(state.get('calculations') or [])
        if state.get('report_sha256'):
            rows.insert(0, {'id': LEGACY_CALCULATION, 'name': f"{state.get('report_name')} + {state.get('template_name')}",
                            'created': state['created'], 'template_id': None,
                            **{k: state.get(k) for k in ('report_sha256', 'template_sha256', 'report_name', 'template_name',
                                                         'template_inputs', 'options')}})
        return rows

    @staticmethod
    def require_calculation(state, cid=None):
        """The selected calculation. With exactly one, an omitted ID means that one; with several, the caller
        must choose. A calculation is never guessed."""
        rows = DocumentWorkflow.calculations(state)
        if not rows:
            raise ValueError('Connect a raw report and Mathcad template before preparing or calculating a design package. You can ask the library without them.')
        if cid is None:
            if len(rows) == 1: return rows[0]
            raise ValueError('This project has several calculations. Choose which calculation to use.')
        found = next((c for c in rows if c['id'] == cid), None)
        if found is None: raise ValueError('Calculation not found in this project.')
        return found

    def template_usage(self):
        """Calculations per template SHA-256 across this workspace's projects."""
        counts: dict[str, int] = {}
        with self.connection() as db:
            for (data,) in db.execute('SELECT data FROM projects'):
                for calc in self.calculations(json.loads(data)):
                    counts[calc['template_sha256']] = counts.get(calc['template_sha256'], 0) + 1
        return counts

    @staticmethod
    def draft_calculation(state, draft):
        """The calculation a design-basis draft belongs to. Drafts saved before projects held several
        calculations have no ID; on a project with a legacy calculation they belong to it."""
        return draft.get('calculation_id') or (LEGACY_CALCULATION if state.get('report_sha256') else None)

    @staticmethod
    def calculation_identity(calculation):
        """batch.py's job identity for calculating retained template inputs (no design basis, no overrides).

        It covers the immutable snapshot, its options, the review checks and the engine revision, so a
        recorded run matches only when the same inputs would be calculated by the same engine."""
        settings = json.loads(json.dumps({'cases': calculation['options'].get('cases'), 'load_source': calculation['options'].get('load_source'),
                                          'input_map': calculation['options'].get('input_map'), 'overrides': {}, 'checks': 'default'}, allow_nan=False))
        return batch.identity(calculation['id'], calculation['report_sha256'], calculation['template_sha256'], settings,
                              review_runner.prepare('default').identity)

    def calculation_folder(self, pid, calculation):
        if calculation['id'] == LEGACY_CALCULATION: return self.root / pid
        return self.root / pid / 'calculations' / identifier(calculation['id'])

    def verify(self, state):
        folder = self.root / state['id']
        files = [('sources/' + s['id'] + '.pdf', s['sha256']) for s in state['sources']]
        for calc in self.calculations(state):
            prefix = '' if calc['id'] == LEGACY_CALCULATION else f"calculations/{identifier(calc['id'])}/"
            files += [(prefix + 'loads.txt', calc['report_sha256']), (prefix + 'template.mcdx', calc['template_sha256'])]
        for name, expected in files:
            path = folder / name
            if path.is_symlink() or not path.is_file() or file_digest(path) != expected:
                raise ValueError('Project source files changed; restore the original or create a new project revision.')

    def add_source(self, pid, name, raw):
        if not isinstance(name, str) or not name.lower().endswith('.pdf') or len(name) > 200 or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):
            raise ValueError('Choose a PDF filename without directory separators or control characters.')
        if not raw.startswith(b'%PDF-') or len(raw) > 256*1024*1024:
            raise ValueError('Use a PDF no larger than 256 MiB; split larger documents into named parts.')
        sha = digest(raw)
        with self.edit(pid) as state:
            self.verify(state)
            for source in state['sources']:
                if source['sha256'] == sha: return source
            if len(state['sources']) >= 100 or sum(s['bytes'] for s in state['sources']) + len(raw) > 2*1024*1024*1024:
                raise ValueError('Project limit: 100 PDFs / 2 GiB. Create another project for additional sources.')
            sid = secrets.token_hex(16)
            folder = self.root / pid / 'sources'; folder.mkdir(exist_ok=True)
            with (folder / (sid + '.pdf')).open('xb') as f: f.write(raw)
            source = {'id': sid, 'name': name, 'sha256': sha, 'bytes': len(raw), 'document_id': None, 'status': 'uploaded', 'progress': 0}
            state['sources'].append(source); state['revision'] += 1
            return source

    def source_path(self, pid, sid):
        state = self.get(pid)
        if not any(s['id'] == sid for s in state['sources']): raise ValueError('Source not in this project.')
        self.verify(state)
        return self.root / pid / 'sources' / (identifier(sid) + '.pdf')

    def extract(self, pid, sid, pages, question, extractor=None, *, neighbors=False):
        from .extraction import parse_pages, read_pages, validate_extraction, RequirementExtractor, SCHEMA_VERSION
        question = text(question, 'Extraction request', 2000)
        pages = parse_pages(pages)
        state = self.get(pid)
        path = self.source_path(pid, sid)
        source = next(s for s in state['sources'] if s['id'] == sid)
        extractor = extractor or RequirementExtractor(ModelConfig.for_workspace(self.root).router_key)
        identity = digest(json.dumps([sid, source['sha256'], pages, question, extractor.model, SCHEMA_VERSION, neighbors]).encode())
        owner = secrets.token_hex(16)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.guard is not None: self.guard(db)
            latest = json.loads(db.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()[0])
            cached = next((r for r in latest.get('extractions', []) if r['identity'] == identity), None)
            if cached: return cached
            attempt = db.execute('SELECT status FROM extraction_attempts WHERE project=? AND identity=?', (pid, identity)).fetchone()
            if attempt and attempt[0] == 'running':
                raise ValueError('This extraction is already running or its outcome is uncertain after interruption. Inspect saved results; it was not charged again. Use a new, narrower request only if you intend another provider call.')
            db.execute('INSERT OR REPLACE INTO extraction_attempts VALUES (?,?,?,?,?)', (pid, identity, owner, 'running', time.time()))
        try:
            # The durable claim precedes provider I/O; no write lock spans it.
            self.progress('Reading selected original PDF pages')
            selected = read_pages(path, pages, neighbors=neighbors)
            self.progress('Extracting values and conditions from selected pages')
            payload, metadata = extractor.extract({'request': question, 'source': source['name'], 'pages': selected})
            content = validate_extraction(payload, selected)
            record = {'id': secrets.token_hex(16), 'identity': identity, 'created': time.time(),
                      'source_id': sid, 'source_name': source['name'], 'source_sha256': source['sha256'],
                      'pages': [p['page'] for p in selected], 'request': question, 'model': extractor.model, 'metadata': metadata,
                      'facts': [{**f, 'id': secrets.token_hex(16), 'review': None} for f in content['facts']], 'gaps': content['gaps']}
            with self.edit(pid, result=record) as current:
                self.verify(current)
                records = current.setdefault('extractions', [])
                if len(records) >= 100: raise ValueError('Project contains 100 extraction runs; create a new revision.')
                records.append(record)
        except Exception:
            # A subsequent explicit request can retry a reported failure. Process
            # interruption leaves the claim running/uncertain, with no auto-expiry.
            with self.connection() as db:
                db.execute("UPDATE extraction_attempts SET status='failed',updated=? WHERE project=? AND identity=? AND owner=?", (time.time(), pid, identity, owner))
            raise
        with self.connection() as db:
            db.execute("UPDATE extraction_attempts SET status='succeeded',updated=? WHERE project=? AND identity=? AND owner=?", (time.time(), pid, identity, owner))
        return record

    @staticmethod
    def extraction_offer(state, aid):
        answer = next((a for a in state['answers'] if a['id'] == aid), None)
        offer = answer.get('extraction_offer') if answer else None
        if not offer: raise ValueError('This answer has no proposal to save requirements.')
        source = next((s for s in state['sources'] if s['id'] == offer['source_id']), None)
        if source is None or source['sha256'] != offer['source_sha256']:
            raise ValueError('The proposed source changed; ask again.')
        return offer

    def accept_extraction_offer(self, pid, aid):
        """The user's explicit confirmation of an Ask proposal. Scope is server-owned: the saved
        answer's resolved source and pages, never client-supplied pages. Same durable, no-retry
        extraction (and identity) as an extraction requested directly in Ask."""
        offer = self.extraction_offer(self.get(pid), aid)
        config = ModelConfig.for_workspace(self.root)
        extractor = self.ask_extractor(config, self.chatgpt_account(config))
        return self.extract(pid, offer['source_id'], offer['pages'], offer['question'], extractor, neighbors=True)

    def chatgpt_account(self, config):
        """The active ChatGPT registration when ChatGPT is the selected provider; never a fallback."""
        if config.provider != 'chatgpt': return None
        from .chatgpt import ChatGPTAccounts, require_enabled
        require_enabled()       # shared/network workers: fail before any claim-time network request
        active = ChatGPTAccounts(self.root).registry()['active']
        if not active: raise ValueError('Continue with ChatGPT in Services, or choose OpenRouter, before asking.')
        return active

    def ask_extractor(self, config, account):
        """Ask extraction uses the selected chat provider's model; None keeps the DeepSeek default."""
        if config.provider != 'chatgpt': return None
        from .chatgpt_responses import ChatGPTExtractor
        return ChatGPTExtractor(self.root, account, config.chatgpt_model)

    @staticmethod
    def requirement(state, rid):
        for run in state.get('extractions', []):
            for fact in run['facts']:
                if fact['id'] == rid: return run, fact
        raise ValueError('Requirement not found in this project.')

    def review_requirement(self, pid, rid, status, reviewer, note):
        if status not in ('reviewed', 'rejected'): raise ValueError('Choose reviewed or rejected.')
        decision = {'status': status, 'reviewer': text(reviewer, 'Reviewer', 160),
                    'note': text(note, 'Review note', 2000), 'id': secrets.token_hex(16), 'time': time.time()}
        with self.edit(pid) as state:
            self.verify(state)
            _, fact = self.requirement(state, rid)
            fact.setdefault('review_history', []).append(decision)
            fact['review'] = decision
        return fact

    def requirement_draft(self, pid, requirements, bindings, reviewer, note, applicability, calculation=None):
        reviewer, note = text(reviewer, 'Reviewer', 160), text(note, 'Review note', 2000)
        applicability = text(applicability, 'Applicability decision', 2000)
        if (not isinstance(requirements, list) or not 1 <= len(requirements) <= 30
                or any(not isinstance(r, str) for r in requirements) or len(set(requirements)) != len(requirements)):
            raise ValueError('Select 1–30 unique reviewed requirements.')
        if not isinstance(bindings, list) or len(bindings) > 50: raise ValueError('Invalid input bindings.')
        with self.edit(pid) as state:
            self.verify(state); calc = self.require_calculation(state, calculation)
            evidence, sections, decisions, references = [], [], [], {}
            for rid in requirements:
                run, fact = self.requirement(state, rid)
                if not fact['review'] or fact['review']['status'] != 'reviewed':
                    raise ValueError('Complete each requirement review before using it as a design basis.')
                refs = []
                for quote in fact['evidence'] + fact['conditions']:
                    eid = f'E{len(evidence)+1}'; refs.append(eid)
                    evidence.append({'id': eid, 'source_id': run['source_id'], 'source_name': run['source_name'],
                                     'source_sha256': run['source_sha256'], 'document_id': None, 'chunk_id': '',
                                     'positions': [[quote['page'], 0, 0, 0, 0]], 'text': quote['quote']})
                # Sections/citations are bounded by the same document contract.
                if len(refs) > 12: raise ValueError('Requirement has more than 12 citations; narrow the extraction before binding.')
                references[rid] = refs
                body = f"{fact['subject']}: {fact['value_text']}\n" + '\n'.join(c['text'] for c in fact['conditions'])
                sections.append({'heading': fact['property'], 'body': body + '\nApplicability: ' + applicability, 'evidence_ids': refs})
                decisions.append({'id': rid, 'review': fact['review'], 'applicability': applicability})
            suggestions = []
            for binding in bindings:
                if not isinstance(binding, dict) or set(binding) != {'requirement', 'variable', 'value', 'unit'}:
                    raise ValueError('Each binding needs requirement, variable, value and unit.')
                if binding['requirement'] not in references: raise ValueError('Binding refers to an unselected requirement.')
                _, fact = self.requirement(state, binding['requirement'])
                if binding['unit'] != fact['unit']:
                    raise ValueError('Binding unit must match the extracted source unit; implicit conversion is not supported.')
                suggestions.append({k: binding[k] for k in ('variable', 'value', 'unit')} | {
                    'reason': note + '\nApplicability: ' + applicability, 'evidence_ids': references[binding['requirement']]})
            content = validate_draft({'sections': sections, 'suggestions': suggestions, 'gaps': [
                'Literal citations verified; extraction completeness and engineering applicability require human review.']},
                calc['template_inputs'], evidence)
            draft = {'id': secrets.token_hex(16), 'created': time.time(), 'revision': state['revision'],
                     'request': note, 'model': 'Reviewed source extraction', 'purpose': 'design_basis',
                     'calculation_id': calc['id'],
                     'content': content, 'evidence': evidence, 'requirement_decisions': decisions,
                     'review': {'accepted': list(range(len(suggestions))), 'reviewer': reviewer, 'note': note,
                                'time': time.time(), 'meaning': 'Design-basis acceptance; not engineering certification.'}}
            if len(state['drafts']) >= 100: raise ValueError('Project contains 100 drafts; create a new revision.')
            state['drafts'].append(draft)
        return draft

    @contextmanager
    def indexing(self, pid):
        # Cross-process lease, not a database transaction around remote upload/parse.
        token = secrets.token_hex(16)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS index_leases (project TEXT PRIMARY KEY, token TEXT, expires REAL)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM index_leases WHERE project=? AND expires<?', (pid, time.time()))
            try: db.execute('INSERT INTO index_leases VALUES (?,?,?)', (pid, token, time.time()+180))
            except sqlite3.IntegrityError:
                raise ValueError('Indexing is already running, or its last request was interrupted. Refresh status before trying again; interrupted leases expire after three minutes.') from None
        def guard(db):
            if self.guard: self.guard(db)
            row = db.execute('SELECT token FROM index_leases WHERE project=?', (pid,)).fetchone()
            if row != (token,): raise ValueError('Indexing ownership changed; no remote result was published.')
            db.execute('UPDATE index_leases SET expires=? WHERE project=? AND token=?', (time.time()+180, pid, token))
        try: yield DocumentWorkflow(self.root, guard=guard, progress=self.progress, activity=self.activity)
        finally:
            with self.connection() as db: db.execute('DELETE FROM index_leases WHERE project=? AND token=?', (pid, token))

    def ingest(self, pid, client=None):
        state = self.get(pid); self.verify(state)
        if not state['sources']: raise ValueError('Add project PDFs before indexing.')
        client = client or ModelConfig.for_workspace(self.root).ragflow()
        with self.indexing(pid) as flow:
            state = flow.get(pid)
            if not state['dataset_id']:
                dataset = client.create_dataset('mcdxkit-' + pid)
                with flow.edit(pid) as current: current['dataset_id'] = dataset
            for sid in [s['id'] for s in flow.get(pid)['sources']]:
                with flow.edit(pid) as current:
                    source = next(s.copy() for s in current['sources'] if s['id'] == sid)
                    dataset = current['dataset_id']
                if not source['document_id']:
                    raw = (self.root / pid / 'sources' / (sid + '.pdf')).read_bytes()
                    if digest(raw) != source['sha256']: raise ValueError('Source changed before upload.')
                    document = client.upload(dataset, source['name'], raw)
                    with flow.edit(pid) as current:
                        found = next(s for s in current['sources'] if s['id'] == sid)
                        found['document_id'] = document
                    source['document_id'] = document
                if source['status'] in ('uploaded', 'failed'):
                    client.parse(dataset, [source['document_id']])
                    with flow.edit(pid) as current:
                        found = next(s for s in current['sources'] if s['id'] == sid)
                        found['status'] = 'parsing'; found['progress'] = 0
        return self.get(pid)

    def status(self, pid, client=None):
        snapshot = self.get(pid)
        if not snapshot['dataset_id']: return snapshot
        client = client or ModelConfig.for_workspace(self.root).ragflow()
        docs = {d['id']: d for d in client.documents(snapshot['dataset_id'])}
        with self.edit(pid) as state:
            if state['dataset_id'] == snapshot['dataset_id']:
                for source in state['sources']:
                    if not source['document_id']:
                        source['status'] = 'uploaded'; source['progress'] = 0
                        continue
                    doc = docs.get(source['document_id'])
                    if not doc:
                        source['status'] = 'failed'; source['progress'] = 0
                    if doc:
                        # Current RAGFlow exposes task state separately from document validity.
                        # Its `status: "1"` means a valid document, not completed ingestion.
                        run = str(doc.get('ingestion_status') or doc.get('run', '')).upper()
                        # RAGFlow can retain a prior attempt's progress while its retry is queued.
                        progress = 0 if run in ('UNSTART', '0', 'CREATED', 'SCHEDULED') else doc.get('progress', 0)
                        source['progress'] = max(0, min(1, progress)) if type(progress) in (int, float) and math.isfinite(progress) else 0
                        chunks = doc.get('chunk_count', 0)
                        source['status'] = ('uploaded' if run in ('UNSTART', '0')
                                            else 'ready' if run in ('DONE', '3', 'COMPLETED') and type(chunks) is int and chunks > 0
                                            else 'failed' if run in ('FAIL', 'FAILED', '4', 'CANCEL', '2', 'DONE', '3', 'COMPLETED', 'STOPPED') else 'parsing')
        return self.get(pid)

    def unconfirmed_path(self, pid, aid):
        # Outside project state on purpose: the browser, exports and audit receipts never receive this text.
        return self.root / identifier(pid) / 'unconfirmed' / (identifier(aid) + '.json')

    def unconfirmed(self, pid, aid):
        """Statements an answer drafted but could not confirm, for the model's context on a recheck only."""
        path = self.unconfirmed_path(pid, aid)
        if path.is_symlink() or not path.is_file(): return []
        try: rows = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): return []
        return [{'text': str(r['text'])[:500], 'reasons': [str(x)[:200] for x in r['reasons']][:6]}
                for r in rows if isinstance(r, dict) and isinstance(r.get('reasons'), list)][:40]

    @staticmethod
    def conversation_history(state, parent):
        history, seen = [], set()
        while parent and len(history) < 6:
            if parent in seen: raise ValueError('Invalid conversation history.')
            seen.add(parent)
            previous = next((a for a in state['answers'] if a['id'] == parent), None)
            if previous is None: raise ValueError('Previous answer not found in this project conversation.')
            body = '\n'.join(s['body'] for s in previous['content']['sections']) or previous.get('assessment', {}).get('explanation', '')
            history.append({'request': previous['request'], 'answer': body[:3000],
                            'clarification': previous.get('clarification'),
                            'qualification': 'Conversation context only; retrieve fresh evidence for this turn.'})
            parent = previous.get('parent_id')
        return list(reversed(history))

    @staticmethod
    def collect_evidence(owned, chunks, evidence):
        rows = []
        for chunk in chunks[:8]:
            source = owned.get(chunk.get('document_id'))
            if source is None: raise ValueError('RAGFlow returned evidence from an unselected document.')
            content = chunk.get('content') or chunk.get('content_with_weight')
            if not isinstance(content, str) or not content.strip(): continue
            content = content[:4000]
            positions = chunk.get('positions', [])
            if not isinstance(positions, list): positions = []
            positions = [p for p in positions[:50] if isinstance(p, list) and len(p) == 5
                         and all(type(v) in (int, float) and math.isfinite(v) for v in p)
                         and int(p[0]) == p[0] and p[0] >= 1]
            # Whitespace-equivalent passages from one source keep their first ID across
            # queries; additional page positions are merged so every citation page stays reachable.
            key = ' '.join(content.split())
            existing = next((e for e in evidence if e['source_id'] == source['id'] and not e.get('kind')
                             and ' '.join(e['text'].split()) == key), None)
            if existing is None:
                if sum(not e.get('kind') for e in evidence) >= 36: break
                existing = {'id': f'E{len(evidence)+1}', 'source_id': source['id'], 'source_name': source['name'],
                            'source_sha256': source['sha256'], 'document_id': source['document_id'],
                            'chunk_id': str(chunk.get('id', ''))[:200], 'positions': positions, 'text': content}
                evidence.append(existing)
            else:
                existing['positions'] = (existing['positions'] + [p for p in positions if p not in existing['positions']])[:50]
            if existing not in rows: rows.append(existing)
        return rows

    @staticmethod
    def workflow_context(state):
        sources = [{k: s[k] for k in ('id', 'name', 'status')} for s in state['sources']]
        facts = [f for run in state.get('extractions', []) for f in run['facts']]
        actions = ['add_files']
        if sources: actions += ['index_documents', 'extract_requirements']
        if facts: actions += ['review_requirements']
        calcs = DocumentWorkflow.calculations(state)
        only = calcs[0] if len(calcs) == 1 else None
        if calcs: actions += ['map_inputs', 'calculate']
        if state['runs']: actions += ['view_outputs']
        runs: dict[str, int] = {}
        for run in state['runs']: runs[run.get('calculation_id') or LEGACY_CALCULATION] = runs.get(run.get('calculation_id') or LEGACY_CALCULATION, 0) + 1
        return {'sources': sources, 'calculation_connected': bool(calcs),
                'calculations': [{'id': c['id'], 'name': c['name'], 'raw_file': c['report_name'], 'template': c['template_name'],
                                  'selected_cases': (c.get('options') or {}).get('cases', []), 'calculated_runs': runs.get(c['id'], 0)}
                                 for c in calcs[-20:]],
                'source_counts': {'total': len(sources), **{status: sum(s['status'] == status for s in sources)
                                                          for status in ('ready', 'uploaded', 'parsing', 'failed')}},
                'raw_file': only and only['report_name'], 'template': only and only['template_name'],
                'selected_cases': (only.get('options') or {}).get('cases', []) if only else [],
                'requirements': {'extracted': len(facts), 'reviewed': sum(bool(f.get('review') and f['review']['status'] == 'reviewed') for f in facts)},
                'calculated_runs': len(state['runs']), 'available_actions': actions,
                'input_policy': 'Calculate retains the current template inputs and selected raw loads. Use the requirement review/binding step to change inputs.'}

    def ask(self, pid, question, client=None, writer=None, *, agent=None, parent=None):
        # Explicit writer injection retains the SDK's deterministic single-pass adapter.
        if writer is not None:
            if parent: raise ValueError('Follow-ups require the document agent.')
            return self.generate(pid, question, client, writer, purpose='answer')
        import inspect
        from .knowledge import KnowledgeAgent, KnowledgeAnswer, VERSION, SEARCH_QUERY_BUDGET, CONTEXT_PAGE_CHARS, audit_citations, canonical_citations, citation_review, safe_clarification
        from .extraction import EvidenceRequestError
        question = text(question, 'Question', 2000)
        state = self.get(pid); self.verify(state)
        history = self.conversation_history(state, parent)
        # The direct parent's unconfirmed statements are model context for a recheck; never shown to the user.
        if parent and (rows := self.unconfirmed(pid, parent)):
            history[-1] = {**history[-1], 'unconfirmed_statements': rows,
                           'unconfirmed_note': 'Drafted in that turn but NOT confirmed in their cited passages, so never shown to the user. '
                                               'They may be wrong. Research each again; state one only where a retrieved passage states it, '
                                               'with the same numbers, units, qualifiers and negation. Otherwise report it in gaps.'}
        ready = [s for s in state['sources'] if s['status'] == 'ready' and s.get('document_id')]
        config = ModelConfig.for_workspace(self.root)
        account = self.chatgpt_account(config)
        agent = agent or KnowledgeAgent(config, progress=self.progress, workspace=self.root, chatgpt_account=account)
        extractor = self.ask_extractor(config, account)
        owned = {s['document_id']: s for s in ready}
        coverage = {'ready': len(ready), 'total': len(state['sources']),
                    'excluded': [s['name'] for s in state['sources'] if s not in ready]}
        evidence, searches, extractions, contexts, cache = [], [], [], [], {}
        offers: list[dict] = []  # Save proposals: read-only until the user confirms.
        def search(query):
            query = text(query, 'Search query', 2000)
            key = ' '.join(query.split()).casefold()
            if key in cache:
                # A repeated query returns the same evidence IDs without another retrieval.
                self.event('search', 'Reused results for a repeated search', query=query[:200], passages=len(cache[key]))
                return cache[key]
            if len(searches) >= SEARCH_QUERY_BUDGET: raise EvidenceRequestError('Search limit reached.')
            self.event('search', f'Searching: {query[:120]}', query=query[:200])
            rows = []
            if ready and state['dataset_id']:
                retriever = client or config.ragflow()
                rows = self.collect_evidence(owned, retriever.retrieve(state['dataset_id'], list(owned), query), evidence)
            searches.append({'query': query, 'passages': len(rows)})
            cache[key] = rows
            names = sorted({row['source_name'] for row in rows})
            self.event('search', f'Found {len(rows)} passages in {len(names)} documents' if rows else 'No matching passages found',
                       query=query[:200], passages=len(rows), sources=names[:5])
            return rows
        def read(evidence_ids):
            from .extraction import resolve_scope, read_pages
            source_id, anchors = resolve_scope(evidence, evidence_ids, max_pages=3)
            if len(contexts) >= 2: raise EvidenceRequestError('Original page context was already read twice in this turn.')
            source = next(s for s in state['sources'] if s['id'] == source_id)
            done = {r['positions'][0][0]: r for r in evidence if r.get('kind') == 'page_context' and r['source_id'] == source_id}
            read_pages_total = {(r['source_id'], r['positions'][0][0]) for r in evidence if r.get('kind') == 'page_context'}
            window = {(source_id, n) for a in anchors for n in (a-1, a, a+1) if n >= 1}
            if len(read_pages_total | window) > 12:
                raise EvidenceRequestError('The page-context budget of 12 pages per turn would be exceeded; answer from the pages already read.')
            path = self.source_path(pid, source_id)  # Integrity failures are not recoverable tool errors.
            self.event('context', f'Reading original pages around p. {", ".join(map(str, anchors))} of {short(source["name"])}',
                       source=source['name'], pages=anchors)
            try: pages = read_pages(path, anchors, context=True)
            except ValueError as exc: raise EvidenceRequestError(str(exc)) from None
            rows = []
            for page in pages:
                row = done.get(page['page'])
                if row is None:
                    # A prefix is never presented as the whole page: truncation is recorded on the
                    # evidence, returned to the agent and saved as a gap.
                    row = {'id': f'E{len(evidence)+1}', 'source_id': source_id, 'source_name': source['name'],
                           'source_sha256': source['sha256'], 'document_id': source.get('document_id'), 'chunk_id': '',
                           'positions': [[page['page'], 0, 0, 0, 0]], 'text': page['text'][:CONTEXT_PAGE_CHARS], 'kind': 'page_context',
                           'truncated': len(page['text']) > CONTEXT_PAGE_CHARS, 'page_characters': len(page['text'])}
                    evidence.append(row)
                rows.append(row)
            numbers = [r['positions'][0][0] for r in rows]
            window = sorted({n for a in anchors for n in (a-1, a, a+1) if n >= 1})
            # Blank neighbours are skipped by the reader; pages past the end of the PDF are not reported.
            omitted = [n for n in window if n not in numbers and n <= max(numbers + anchors)]
            truncated = [r['positions'][0][0] for r in rows if r.get('truncated')]
            contexts.append({'source_id': source_id, 'source_name': source['name'], 'anchor_pages': anchors,
                             'pages': numbers, 'truncated_pages': truncated, 'omitted_pages': omitted,
                             'characters_per_page': CONTEXT_PAGE_CHARS})
            self.event('context', f'Read {len(rows)} original pages from {short(source["name"])}'
                       + (f'; {len(truncated)} truncated' if truncated else ''), source=source['name'], pages=numbers)
            return {'source_name': source['name'], 'anchor_pages': anchors, 'pages': rows, 'omitted_pages': omitted}
        def extract(question, evidence_ids):
            from .extraction import resolve_scope
            source_id, pages = resolve_scope(evidence, evidence_ids)
            if extractions or offers: raise EvidenceRequestError('Only one extraction or save proposal is allowed per conversation turn.')
            # Reuse the domain engine with the parent's cancellation fence. A saved
            # extraction is durable even if subsequent answer formatting fails.
            flow = DocumentWorkflow(self.root, guard=self.guard, progress=self.progress, activity=self.activity)
            run = flow.extract(pid, source_id, pages, question, extractor, neighbors=True)
            extractions.append(run['id'])
            workflow.update(self.workflow_context(self.get(pid)))
            rows, facts = [], []
            for fact in run['facts']:
                refs = []
                for quote in fact['evidence'] + fact['conditions']:
                    row = {'id': f'E{len(evidence)+1}', 'source_id': run['source_id'], 'source_name': run['source_name'],
                           'source_sha256': run['source_sha256'], 'document_id': None, 'chunk_id': '',
                           'positions': [[quote['page'], 0, 0, 0, 0]], 'text': quote['quote'], 'kind': 'extraction_quote'}
                    evidence.append(row); rows.append(row); refs.append(row['id'])
                facts.append({k: fact[k] for k in ('id', 'subject', 'property', 'value_text', 'unit', 'conditions')} | {'evidence_ids': refs})
            self.event('extraction', f'Saved {len(facts)} unreviewed requirements from {short(run["source_name"])}',
                       source=run['source_name'], pages=run['pages'], facts=len(facts), extraction_id=run['id'])
            return {'saved': True, 'extraction_id': run['id'], 'source_name': run['source_name'], 'pages': run['pages'], 'facts': facts, 'evidence': rows, 'gaps': run['gaps'],
                    'review': 'Unreviewed. No calculation inputs changed.'}
        def propose(question, evidence_ids):
            # Read-only: resolve a server-owned scope the user can confirm later with one click.
            from .extraction import resolve_scope
            source_id, pages = resolve_scope(evidence, evidence_ids)
            if offers or extractions: raise EvidenceRequestError('Only one extraction or save proposal is allowed per turn.')
            source = next(s for s in state['sources'] if s['id'] == source_id)
            offers.append({'source_id': source_id, 'source_name': source['name'], 'source_sha256': source['sha256'],
                           'pages': pages, 'question': question, 'evidence_ids': list(dict.fromkeys(evidence_ids))})
            self.event('extraction', f'Prepared values from {short(source["name"])} to save if you confirm',
                       source=source['name'], pages=pages, status='proposed')
            return {'source_name': source['name'], 'pages': pages}
        workflow = self.workflow_context(state)
        context = {'project': state['title'], 'request': question, 'history': history, 'coverage': coverage, 'workflow': workflow,
                   'scope': 'Selected project library only. Source revision precedence and engineering applicability are not verified.',
                   'extraction_policy': 'Reading is the default. Saving writes durable UNREVIEWED records and happens only when the '
                                        "user's current request asks for it (search task 'extract' plus their quoted words). "
                                        'Otherwise extract_requirements prepares a proposal the user confirms.'}
        tools = {'extract': extract}
        # Injected SDK/test agents predating read_context/propose keep their call signature.
        parameters = inspect.signature(agent.answer).parameters
        flexible = any(p.kind is p.VAR_KEYWORD for p in parameters.values())
        if 'read' in parameters or flexible: tools['read'] = read
        if 'propose' in parameters or flexible: tools['propose'] = propose
        try: payload = KnowledgeAnswer.model_validate(agent.answer(context, search, **tools)).model_dump()
        except ValueError as exc:
            if type(exc).__name__ == 'ValidationError': raise ValueError('The document agent returned an invalid answer.') from None
            raise
        if payload['kind'] != 'workflow' and not searches and not extractions: raise ValueError('No current evidence search or extraction was completed.')
        if any(action not in workflow['available_actions'] for action in payload['actions']):
            raise ValueError('The agent proposed a workflow action that is unavailable for this project.')
        # Canonical [E1][E2] citation syntax for every newly saved answer (claims unchanged; stored answers untouched).
        sections = [{**section, 'body': canonical_citations(section['body'])} for section in payload['sections']]
        content = {'sections': sections, 'suggestions': [], 'gaps': payload['gaps']}
        if content['sections']:
            if payload['kind'] == 'research': content = validate_draft(content, {'inputs': []}, evidence)
            elif any(ref not in {e['id'] for e in evidence} for section in content['sections'] for ref in section['evidence_ids']):
                raise ValueError('Workflow answer contains an unknown citation.')
        elif payload['kind'] != 'workflow' and payload['status'] != 'insufficient': raise ValueError('Answer requires supporting evidence.')
        if payload['status'] == 'conflicted' and not payload['conflicts']:
            raise ValueError('A conflicted answer must list the conflicting provisions.')
        draft_audit = audit_citations(content['sections'], evidence)     # rejects unknown/unlisted citations
        unsupported = sum(len(row['values']) for row in draft_audit['unsupported_numbers'])
        self.event('check', f'Citations checked: {unsupported} stated numbers not found in cited text' if unsupported
                   else f'Citations checked: {len(draft_audit["cited"])} cited passages from this turn',
                   status=draft_audit['status'], citations=len(draft_audit['cited']), unsupported=unsupported)
        # Ordinary conversation checks source identity without rewriting Markdown or running a second
        # model over every sentence. Extraction, review and input binding retain their own validation.
        grounding, published, assessment, gaps = citation_review(payload, content['sections'], evidence, coverage, workflow)
        content = {**content, 'sections': published, 'gaps': list(gaps)}
        audit = audit_citations(published, evidence, [assessment['explanation'], *assessment['conflicts'], *gaps,
                                                     *(section['heading'] for section in published)])
        self.event('check', {'citations_checked': 'Answer citations match retrieved project passages',
                             'abstained': 'No supported answer found',
                             'workflow': 'Workflow guidance from project state; no document statements published'}[grounding['status']],
                   status=grounding['status'])
        clarification = safe_clarification(payload['clarification'])
        for entry in contexts:
            if entry['truncated_pages']:
                content['gaps'].append(f"Original page context from {short(entry['source_name'], 120)} pages {', '.join(map(str, entry['truncated_pages']))} "
                                       f"was limited to the first {CONTEXT_PAGE_CHARS} characters per page; later text on those pages was not read.")
        if coverage['excluded']: content['gaps'].append(f"Only {coverage['ready']} of {coverage['total']} documents were searchable. Unindexed documents may change this answer.")
        content['gaps'].append('Project applicability and document revision precedence have not been independently verified.')
        aid = secrets.token_hex(16)
        previous = next((a for a in state['answers'] if a['id'] == parent), None)
        record = {'id': aid, 'conversation_id': (previous.get('conversation_id') or previous['id']) if previous else aid,
                  'parent_id': parent, 'created': time.time(), 'revision': state['revision'], 'request': question,
                  'model': agent.model, 'agent_version': VERSION, 'content': content, 'evidence': evidence,
                  'review': None, 'purpose': 'answer', 'calculation_context': None, 'coverage': coverage, 'searches': searches,
                  'kind': 'workflow' if grounding['status'] == 'workflow' else 'research',
                  'actions': payload['actions'], 'workflow_state': workflow,
                  'clarification': clarification, 'extraction_ids': extractions,
                  'context_reads': contexts, 'audit': audit, 'extraction_offer': offers[0] if offers else None,
                  'grounding': grounding, 'assessment': assessment}
        with self.edit(pid, result=record) as current:
            self.verify(current)
            if current['revision'] != state['revision']: raise ValueError('Project sources changed during research; start a new question.')
            if len(current['answers']) >= 100: raise ValueError('Project contains 100 answers. Create a new project revision.')
            current['answers'].append(record)
        return record

    def draft(self, pid, question, client=None, writer=None, calculation=None):
        return self.generate(pid, question, client, writer, purpose='design_basis', calculation=calculation)

    def generate(self, pid, question, client, writer, *, purpose, calculation=None):
        question = text(question, 'Question or draft request', 2000)
        state = self.get(pid)
        self.verify(state)
        calc = self.require_calculation(state, calculation) if purpose == 'design_basis' else None
        if not state['sources'] or any(s['status'] != 'ready' for s in state['sources']):
            raise ValueError('All project sources must finish indexing before asking or drafting. Refresh indexing status.')
        client = client or ModelConfig.for_workspace(self.root).ragflow()
        writer = writer or ModelConfig.for_workspace(self.root).writer()
        owned = {s['document_id']: s for s in state['sources']}
        chunks = client.retrieve(state['dataset_id'], list(owned), question)
        evidence = []
        for chunk in chunks[:12]:
            source = owned.get(chunk.get('document_id'))
            if source is None: raise ValueError('RAGFlow returned evidence from an unselected document.')
            content = chunk.get('content') or chunk.get('content_with_weight')
            if not isinstance(content, str) or not content.strip(): continue
            positions = chunk.get('positions', [])
            if not isinstance(positions, list): positions = []
            positions = [p for p in positions[:50] if isinstance(p, list) and len(p) == 5 and all(type(v) in (int, float) and math.isfinite(v) for v in p)]
            evidence.append({'id': f'E{len(evidence)+1}', 'source_id': source['id'], 'source_name': source['name'],
                             'source_sha256': source['sha256'], 'document_id': source['document_id'],
                             'chunk_id': str(chunk.get('id', ''))[:200], 'positions': positions,
                             'text': content[:6000]})
        if not evidence: raise ValueError('No supporting evidence found. Narrow the request or add the missing source.')
        inputs = calc['template_inputs'] if calc else {'inputs': []}
        calculation = None
        if calc:
            folder = self.calculation_folder(pid, calc)
            found = engine.inspect_report(folder/'loads.txt', template=folder/'template.mcdx', **calc['options'])
            calculation = {k: found[k] for k in ('selected_cases', 'load_source', 'envelope', 'governing')}
            calculation.update(report_sha256=calc['report_sha256'], template_sha256=calc['template_sha256'],
                               units={'P': 'kip', 'Vy': 'kip', 'Vz': 'kip', 'My': 'kip-in', 'Mz': 'kip-in'},
                               qualification='Independent component extrema are not concurrent load combinations.')
        context = {'project': state['title'], 'purpose': purpose, 'request': question,
                   'inputs': inputs['inputs'], 'calculation': calculation, 'evidence': evidence}
        content = validate_draft(writer.draft(context), inputs, evidence)
        draft = {'id': secrets.token_hex(16), 'created': time.time(), 'revision': state['revision'],
                 'request': question, 'model': writer.model, 'content': content, 'evidence': evidence, 'review': None,
                 'purpose': purpose, 'calculation_context': calculation, **({'calculation_id': calc['id']} if calc else {})}
        with self.edit(pid, result=draft) as current:
            self.verify(current)
            if current['revision'] != state['revision']: raise ValueError('Project sources changed during drafting; try again.')
            collection = current.setdefault('answers' if purpose == 'answer' else 'drafts', [])
            if len(collection) >= 100: raise ValueError('Project contains 100 answers or drafts. Create a new project revision.')
            collection.append(draft)
        return draft

    @staticmethod
    def selected_draft(state, did):
        draft = next((d for d in state['drafts'] if d['id'] == did), None)
        if draft is None: raise ValueError('Draft not found in this project.')
        if draft['revision'] != state['revision']: raise ValueError('Project sources changed; generate and review a new draft.')
        DocumentWorkflow.require_calculation(state, DocumentWorkflow.draft_calculation(state, draft))
        for decision in draft.get('requirement_decisions', []):
            _, fact = DocumentWorkflow.requirement(state, decision['id'])
            if fact['review'] != decision['review']:
                raise ValueError('Requirement review changed; prepare a new design basis before calculating.')
        return draft

    def review(self, pid, did, accepted, reviewer, note):
        reviewer, note = text(reviewer, 'Reviewer', 160), text(note, 'Review note', 2000)
        with self.edit(pid) as state:
            self.verify(state)
            draft = self.selected_draft(state, did)
            if not isinstance(accepted, list) or any(type(i) is not int or i < 0 or i >= len(draft['content']['suggestions']) for i in accepted) or len(set(accepted)) != len(accepted):
                raise ValueError('Select valid unique suggestion indices.')
            draft['review'] = {'accepted': accepted, 'reviewer': reviewer, 'note': note, 'time': time.time(),
                               'meaning': 'Design-basis acceptance; not engineering certification or native execution verification.'}
        return draft

    def build(self, pid, did, calculation=None):
        state = self.get(pid)
        if not self.calculations(state): self.require_calculation(state)     # the actionable error comes first
        if did is not None:
            chosen = self.draft_calculation(state, self.selected_draft(state, did))
            if calculation is not None and chosen is not None and chosen != calculation:
                raise ValueError('This design basis belongs to a different calculation.')
            calculation = chosen if chosen is not None else calculation
        calc = self.require_calculation(state, calculation)
        self.verify(state)
        draft = self.selected_draft(state, did) if did is not None else {
            'model': 'No AI narrative', 'content': {'sections': [], 'suggestions': [], 'gaps': []}, 'evidence': [],
            'review': {'accepted': [], 'reviewer': 'Not recorded', 'note': 'Original template inputs retained.',
                       'status': 'not_recorded', 'meaning': 'Calculation only; no design-basis review recorded.'}}
        if draft['review'] is None: raise ValueError('Complete the draft review before building a package.')
        if len(state['runs']) >= 100: raise ValueError('Project contains 100 packages. Create a new project revision.')
        content = validate_draft(draft['content'], calc['template_inputs'], draft['evidence']) if did is not None else draft['content']
        inputs_dir = self.calculation_folder(pid, calc)
        overrides = {s['variable']: s['value'] for i, s in enumerate(content['suggestions']) if i in draft['review']['accepted']}
        rid = secrets.token_hex(16)
        folder = self.root / pid / 'runs' / rid; folder.mkdir(parents=True)
        try:
            result = engine.convert(inputs_dir / 'loads.txt', inputs_dir / 'template.mcdx',
                                    folder / 'calculation.mcdx', title=state['title'], overrides=overrides, **calc['options'])
            manifest = {'schema': 'mcdxkit-document-package/1', 'project_id': pid, 'title': state['title'],
                        'run_id': rid, 'draft_id': did, 'created': time.time(), 'model': draft['model'],
                        'review': draft['review'], 'draft': content, 'evidence': draft['evidence'],
                        'narrative_origin': 'reviewed_extraction' if draft.get('requirement_decisions') else 'ai' if content['sections'] else 'none',
                        'calculation_context': draft.get('calculation_context'),
                        'sources': [{k: s[k] for k in ('id', 'name', 'sha256', 'bytes')} for s in state['sources']],
                        'calculation_id': calc['id'], 'calculation_name': calc['name'],
                        'report_sha256': calc['report_sha256'], 'template_sha256': calc['template_sha256'],
                        'overrides': overrides, 'native_execution_verified': False,
                        'calculation': result['calculation'], 'check_summary': result.get('check_summary')}
            markdown = render_report(manifest, result)
            (folder / 'report.md').write_text(markdown, encoding='utf-8')
            (folder / 'report.html').write_text(render_html(manifest, result), encoding='utf-8')
            # Include original PDFs and inputs so evidence remains inspectable offline.
            sources_dir = folder / 'sources'; sources_dir.mkdir()
            for source in state['sources']:
                destination = sources_dir / (source['id'] + '.pdf')
                shutil.copyfile(self.root / pid / 'sources' / (source['id'] + '.pdf'), destination)
                if file_digest(destination) != source['sha256']: raise ValueError('Source changed during package generation.')
            shutil.copyfile(inputs_dir / 'loads.txt', sources_dir / 'loads.txt')
            shutil.copyfile(inputs_dir / 'template.mcdx', sources_dir / 'template.mcdx')
            if (file_digest(sources_dir/'loads.txt') != calc['report_sha256']
                    or file_digest(sources_dir/'template.mcdx') != calc['template_sha256']
                    or result['source_sha256'] != calc['report_sha256']
                    or result['template_sha256'] != calc['template_sha256']):
                raise ValueError('Calculation inputs changed during package generation.')
            manifest['files'] = {str(p.relative_to(folder)): file_digest(p) for p in folder.rglob('*') if p.is_file()}
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
            with zipfile.ZipFile(folder / 'package.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
                for p in folder.rglob('*'):
                    if p.is_file() and p.name != 'package.zip': archive.write(p, str(p.relative_to(folder)))
            run = {'id': rid, 'draft_id': did, 'calculation_id': calc['id'], 'created': manifest['created'], 'sha256': file_digest(folder/'package.zip'),
                   'calculated': True, 'native_execution_verified': False, 'check_summary': result.get('check_summary')}
            if did is None: run['job_id'] = self.calculation_identity(calc)
            with self.edit(pid, result=run) as current:
                self.verify(current)
                if current['revision'] != state['revision'] or (did is not None and self.selected_draft(current, did) != draft):
                    raise ValueError('Inputs or draft review changed during calculation; calculate again.')
                if len(current['runs']) >= 100: raise ValueError('Project contains 100 packages.')
                current['runs'].append(run)
        except Exception:
            shutil.rmtree(folder)
            raise
        return run

    def calculate_all(self, pid, calculations=None):
        """Calculate several calculations of one project with batch.py's scheduler, job identity and resume.

        Each calculation gets its own run with its calculation_id. A calculation that already has a run with
        the same identity and an intact package is skipped, so resubmitting after an interruption finishes the
        rest without duplicate runs. One calculation's failure is recorded on its row and never marks another
        failed; nothing is retried. Changed snapshot files still block the whole project (see verify).
        Progress is measured: one update when each calculation finishes."""
        state = self.get(pid)
        if calculations is None: chosen = self.calculations(state)
        else:
            if not isinstance(calculations, list) or not calculations or len(calculations) > MAX_CALCULATIONS \
                    or len(set(map(str, calculations))) != len(calculations):
                raise ValueError('Choose one or more distinct calculations to calculate.')
            chosen = [self.require_calculation(state, cid) for cid in calculations]
        if not chosen: self.require_calculation(state)     # the actionable "connect" error
        self.verify(state)
        total = len(chosen); rows = []
        counts = {'total': total, 'succeeded': 0, 'skipped': 0, 'failed': 0}

        def jobs():
            for calc in chosen:
                yield {'job_id': self.calculation_identity(calc), 'calculation_id': calc['id'], 'name': calc['name']}

        def run(job):
            if self.guard is not None:
                # A cancelled job stops before its next calculation instead of recording it as failed.
                with self.connection() as db: self.guard(db)
            row = {**job, 'run_id': None, 'check_summary': None, 'error': None}
            try:
                done = self.verified_run(pid, job['calculation_id'], job['job_id'])
                if done is not None:
                    return {**row, 'status': 'skipped', 'run_id': done['id'], 'check_summary': done.get('check_summary')}
                built = self.build(pid, None, job['calculation_id'])
                return {**row, 'status': 'succeeded', 'run_id': built['id'], 'check_summary': built.get('check_summary')}
            except Exception as exc:
                return {**row, 'status': 'failed', 'error': str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: {exc}'[:500]}

        def record(row):
            rows.append(row); counts[row['status']] += 1
            self.event('step', f"Finished {len(rows)} of {total} calculations", finished=len(rows), total=total)

        self.event('step', f'Finished 0 of {total} calculations', finished=0, total=total)
        # One worker: every run commits to the same project database, and nothing is retried after a lock timeout.
        batch.schedule(jobs(), run, record, workers=1)
        return {'project_id': pid, **counts, 'calculations': rows, 'calculation_engine': 'CalcpadCE', 'native_execution_verified': False}

    def verified_run(self, pid, cid, job_id):
        """The newest run of this calculation with this job identity whose package is intact, or None."""
        for run in reversed(self.get(pid)['runs']):
            if run.get('calculation_id', LEGACY_CALCULATION) == cid and run.get('job_id') == job_id and run.get('calculated'):
                try:
                    self.artifact(pid, run['id'], 'package.zip')
                    return run
                except (ValueError, OSError):
                    continue
        return None

    def artifact(self, pid, rid, name):
        state = self.get(pid)
        run = next((r for r in state['runs'] if r['id'] == rid), None)
        if run is None or name not in ('package.zip', 'report.html', 'report.md', 'manifest.json', 'calculation.html', 'calculation.mcdx', 'calculation.cpd', 'calculation.audit.json'):
            raise ValueError('Document artifact not found.')
        folder = self.root / pid / 'runs' / identifier(rid)
        package = folder / 'package.zip'
        if package.is_symlink() or file_digest(package) != run['sha256']: raise ValueError('Package changed after generation.')
        if name != 'package.zip':
            with zipfile.ZipFile(package) as archive:
                if archive.read(name) != (folder/name).read_bytes(): raise ValueError('Report changed after generation.')
        return folder / name

    def answer_audit(self, pid, aid):
        """Read-only audit receipt for one saved answer (schema ANSWER_RECEIPT). Never writes, refreshes
        the answer or calls a model; reads only this project's record and hashes the PDFs it references."""
        if not isinstance(aid, str) or not re.fullmatch('[0-9a-f]{32}', aid): raise ValueError('Answer not found in this project.')
        state = self.get(pid)
        answer = next((a for a in state['answers'] if a['id'] == aid), None)
        if answer is None: raise ValueError('Answer not found in this project.')
        # One entry per evidence record ('' = that record has no saved hash), so a missing hash is never hidden
        # by another record for the same source.
        recorded: dict[str, list[str]] = {}
        for row in answer.get('evidence') or []:
            if isinstance(row.get('source_id'), str):
                saved = row.get('source_sha256')
                recorded.setdefault(row['source_id'], []).append(saved if isinstance(saved, str) and saved else '')
        return answer_receipt(state, answer, self.source_integrity(pid, state, recorded))

    def source_integrity(self, pid, state, recorded):
        """For each source the answer referenced: the hash recorded with the answer's evidence vs the project
        register, and the current stored file vs the register. Hashes only those files; never repairs anything."""
        sources = {s['id']: s for s in state['sources']}
        result = []
        for sid in sorted(recorded):
            answer_hashes = sorted(set(h for h in recorded[sid] if h))
            counts = {'evidence_records': len(recorded[sid]), 'records_without_hash': sum(1 for h in recorded[sid] if not h)}
            source = sources.get(sid)
            if source is None or not re.fullmatch('[0-9a-f]{32}', sid):
                result.append({'source_id': sid, 'answer_sha256': answer_hashes, **counts, 'status': 'not_in_project',
                               'answer_hash_status': 'not_in_project'}); continue
            path = self.root / pid / 'sources' / (sid + '.pdf')
            if path.is_symlink() or not path.is_file(): status, current = 'missing', None
            else:
                current = file_digest(path)
                status = 'match' if current == source['sha256'] else 'changed'
            # status: current file vs register (corruption/replacement on disk).
            # answer_hash_status: what the answer was built from vs the register (a stale or superseded answer).
            # Any recorded mismatch wins; then records without a hash are reported, never counted as a match.
            answer_status = ('unrecorded' if not answer_hashes else
                             'differs' if answer_hashes != [source['sha256']] else
                             'partially_recorded' if counts['records_without_hash'] else 'match')
            result.append({'source_id': sid, 'name': source['name'], 'saved_sha256': source['sha256'],
                           'answer_sha256': answer_hashes, **counts, 'current_sha256': current,
                           'status': status, 'answer_hash_status': answer_status})
        return result


ANSWER_RECEIPT = 'mcdxkit.answer-receipt/1'


def _provider(model):
    if not isinstance(model, str): return 'unrecorded'
    return 'chatgpt' if model.startswith('chatgpt:') else 'openrouter' if model.endswith(':free') else 'unrecorded'


_RECEIPT_ACTIONS = {'add_files', 'index_documents', 'extract_requirements', 'review_requirements', 'map_inputs', 'calculate', 'view_outputs'}


def _str(value):
    return value if isinstance(value, str) else None


def _strs(values):
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _int(value):
    return value if type(value) is int else None


def _ints(values):
    return [v for v in values if type(v) is int] if isinstance(values, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _page(value):
    """A positive whole original page number, or None (never truncates 3.7 to 3 or accepts 0/negatives)."""
    if type(value) is int: return value if value > 0 else None
    if type(value) is float and math.isfinite(value) and value.is_integer() and value > 0: return int(value)
    return None


def answer_receipt(state, answer, integrity):
    """Whitelisted, versioned receipt. Every nested value is rebuilt from typed fields of this answer's saved
    record and project evidence; nothing else (credentials, environment, paths, service URLs or settings)."""
    content, assessment = _dict(answer.get('content')), _dict(answer.get('assessment'))
    grounding = answer.get('grounding') if isinstance(answer.get('grounding'), dict) else None
    statements = [st for st in (grounding or {}).get('statements') or [] if isinstance(st, dict)]
    sections = [s for s in content.get('sections') or [] if isinstance(s, dict)]
    from .knowledge import WITHHELD_REASONS, citation_ids
    known = {row.get('id') for row in answer.get('evidence') or []}
    registered = {s['id'] for s in state['sources']}
    # Include citations in open questions/conflicts as well as the main answer and historical statement checks.
    extra_text = [*_strs(content.get('gaps')), *_strs(_dict(answer.get('assessment')).get('conflicts')),
                  _str(_dict(answer.get('assessment')).get('explanation')) or '',
                  *(_str(s.get('heading')) or '' for s in sections)]
    cited = ({ref for s in sections for ref in _strs(s.get('evidence_ids'))}
             | {ref for s in sections for ref in citation_ids(_str(s.get('body')) or '') if ref in known}
             | {ref for st in statements for ref in _strs(st.get('evidence_ids'))}
             | {ref for value in extra_text for ref in citation_ids(value) if ref in known})
    used: dict[str, list[str]] = {}
    for statement in statements:
        for ref in _strs(statement.get('evidence_ids')): used.setdefault(ref, []).append(str(statement.get('id')))
    roles = {'page_context': 'original_page_read', 'extraction_quote': 'extraction_quote'}
    evidence, by_text = [], {}
    for row in answer.get('evidence') or []:
        text = _str(row.get('text')) or ''
        text_hash = digest(' '.join(text.split()).encode())
        by_text.setdefault(text_hash, []).append(_str(row.get('id')))
        pages = sorted({page for p in row.get('positions') or [] if isinstance(p, list) and p and (page := _page(p[0])) is not None})
        source_id = _str(row.get('source_id'))
        evidence.append({'id': _str(row.get('id')), 'role': roles.get(row.get('kind'), 'retrieved_passage'), 'cited': row.get('id') in cited,
                         'used_in_statements': used.get(row.get('id'), []),
                         'source': {'id': source_id, 'name': _str(row.get('source_name')), 'saved_sha256': _str(row.get('source_sha256'))},
                         # Server-owned locator: this project, the stored source ID and original PDF page numbers.
                         'locator': {'project_id': state['id'], 'source_id': source_id, 'source_in_project': source_id in registered,
                                     'pages': pages, 'page_available': bool(pages)},
                         'text': text, 'text_sha256': text_hash,
                         'truncated': bool(row.get('truncated')), 'page_characters': _int(row.get('page_characters'))})
    cited_pages: dict[str, set[int]] = {}
    for row in evidence:
        if row['cited']: cited_pages.setdefault(str(row['source']['id']), set()).update(row['locator']['pages'])
    if grounding:
        scope = _dict(grounding.get('scope'))
        # Server-owned code for withheld drafts; only known string codes are exported (a stored dict/list is unhashable).
        reason = _str(grounding.get('reason_code'))
        ledger = {'version': _str(grounding.get('version')) or 'unrecorded', 'status': _str(grounding.get('status')) or 'unrecorded',
                  'method': _str(grounding.get('method')), 'scope': {k: _str(scope.get(k)) for k in ('retrieval', 'precedence', 'approval')},
                            'reason_code': reason if reason in WITHHELD_REASONS else None,
                  'coverage': _str(grounding.get('coverage')), 'draft_withheld': bool(grounding.get('draft_withheld')),
                  'checked_statements': _int(grounding.get('checked_statements')), 'inline_anchored': _int(grounding.get('inline_anchored')),
                  'statements': [{'id': _str(st.get('id')), 'kind': _str(st.get('kind')), 'section': _int(st.get('section')),
                                  'verdict': _str(st.get('verdict')), 'reasons': _strs(st.get('reasons')),
                                  'checks_applied': _strs(st.get('checks_applied')), 'anchored': bool(st.get('anchored')),
                                  'text': _str(st.get('text')), 'evidence_ids': _strs(st.get('evidence_ids')),
                                  'quotes': [{'evidence_id': _str(q.get('evidence_id')), 'text': _str(q.get('text'))}
                                             for q in st.get('quotes') or [] if isinstance(q, dict)]} for st in statements]}
    else:
        ledger = {'version': 'unrecorded', 'status': 'unrecorded', 'statements': [],
                  'note': f"Saved before grounding checks were recorded (agent {_str(answer.get('agent_version')) or 'unrecorded'}); statements were not checked."}
    audit = _dict(answer.get('audit'))
    coverage = answer.get('coverage')
    clarification = answer.get('clarification') if isinstance(answer.get('clarification'), dict) else None
    offer = answer.get('extraction_offer') if isinstance(answer.get('extraction_offer'), dict) else None
    receipt = {
        'schema': ANSWER_RECEIPT,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'project': {'id': state['id'], 'title': state['title'], 'revision_now': state['revision']},
        'answer': {'id': answer['id'], 'created': answer.get('created') if type(answer.get('created')) in (int, float) else None,
                   'conversation_id': _str(answer.get('conversation_id')) or answer['id'],
                   'parent_id': _str(answer.get('parent_id')), 'request': _str(answer.get('request')),
                   'kind': _str(answer.get('kind')) or 'unrecorded',
                   'revision': _int(answer.get('revision')), 'revision_matches_project': answer.get('revision') == state['revision'],
                   'model': _str(answer.get('model')) or 'unrecorded', 'provider': _provider(answer.get('model')),
                   'agent_version': _str(answer.get('agent_version')) or 'unrecorded',
                   'grounding_version': (_str(grounding.get('version')) if grounding else None) or 'unrecorded',
                   'status': _str(assessment.get('status')) or 'unrecorded', 'explanation': _str(assessment.get('explanation')),
                   'conflicts': _strs(assessment.get('conflicts')),
                   'sections': [{'heading': _str(s.get('heading')), 'body': _str(s.get('body')), 'evidence_ids': _strs(s.get('evidence_ids'))}
                                for s in sections],
                   'limitations': _strs(content.get('gaps')),
                   'actions': [a for a in _strs(answer.get('actions')) if a in _RECEIPT_ACTIONS],
                   'clarification': {'question': _str(clarification.get('question')), 'choices': _strs(clarification.get('choices'))}
                                    if clarification else None,
                   'extraction_ids': _strs(answer.get('extraction_ids')),
                   'extraction_offer': {'source_id': _str(offer.get('source_id')), 'source_name': _str(offer.get('source_name')),
                                        'pages': _ints(offer.get('pages')), 'question': _str(offer.get('question'))} if offer else None},
        'grounding': ledger,
        'citation_audit': {'status': _str(audit.get('status')), 'cited': _strs(audit.get('cited')),
                           'unsupported_numbers': [{'heading': _str(u.get('heading')), 'values': _strs(u.get('values'))}
                                                   for u in audit.get('unsupported_numbers') or [] if isinstance(u, dict)],
                           'sections_without_inline_citations': _strs(audit.get('sections_without_inline_citations')),
                           'meaning': _str(audit.get('meaning'))} if audit else None,
        'retrieval': {'coverage': {'ready': _int(coverage.get('ready')), 'total': _int(coverage.get('total')),
                                   'excluded': _strs(coverage.get('excluded'))} if isinstance(coverage, dict) else 'unrecorded',
                      'searches': [{'query': _str(s.get('query')), 'passages': _int(s.get('passages'))}
                                   for s in answer.get('searches') or [] if isinstance(s, dict)],
                      'original_pages_read': [{'source_id': _str(r.get('source_id')), 'source_name': _str(r.get('source_name')),
                                               **{k: _ints(r.get(k)) for k in ('anchor_pages', 'pages', 'truncated_pages', 'omitted_pages')}}
                                              for r in answer.get('context_reads') or [] if isinstance(r, dict)],
                      'meaning': 'Searches return top-ranked passages from searchable documents; they are not a review of every page.'},
        'evidence': evidence,
        'evidence_summary': {
            'retrieved': sum(1 for e in evidence if e['role'] == 'retrieved_passage'),
            'original_pages_read': sum(1 for e in evidence if e['role'] == 'original_page_read'),
            'extraction_quotes': sum(1 for e in evidence if e['role'] == 'extraction_quote'),
            'cited': sum(1 for e in evidence if e['cited']),
            'cited_pages': [{'source_id': sid, 'pages': sorted(pages)} for sid, pages in sorted(cited_pages.items(), key=lambda x: str(x[0]))],
            'identical_text_groups': [ids for ids in by_text.values() if len(ids) > 1],
            'meaning': 'Several statements may rest on the same original page. Identical text from different files is listed, not merged.'},
        'source_integrity': integrity,
    }
    canonical = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    receipt['digest'] = {'algorithm': 'sha256', 'value': digest(canonical),
                         'canonicalization': 'JSON of this object without "digest": sorted keys, separators (",", ":"), UTF-8, ensure_ascii false.',
                         'meaning': 'Detects edits to this exported file. It is not a digital signature and not proof that the answer is true.'}
    return receipt


def narrative_label(manifest):
    if not manifest['draft']['sections']: return 'Calculation only (no source narrative)'
    return 'Reviewed source extraction' if manifest.get('narrative_origin') == 'reviewed_extraction' else 'AI-drafted narrative'


def render_report(manifest, result):
    """Numerical sections are deterministic; model prose remains visibly attributed."""
    lines = ['# ' + manifest['title'], '', '## Review status',
             ('Design-basis review not recorded.' if manifest['review'].get('status') == 'not_recorded' else 'Design-basis review recorded.') +
             ' This is not engineering certification. Prime-native execution is unverified.',
             'Reviewer: ' + manifest['review']['reviewer'], 'Review note: ' + manifest['review']['note'], '',
             '## ' + narrative_label(manifest), 'Model: ' + manifest['model']]
    for section in manifest['draft']['sections']:
        lines += ['', '### ' + section['heading'], section['body'], 'Evidence: ' + ', '.join(section['evidence_ids'])]
    lines += ['', '## Input decisions']
    for i, suggestion in enumerate(manifest['draft']['suggestions']):
        decision = 'Accepted' if i in manifest['review']['accepted'] else 'Not applied'
        lines += [f"- {decision}: {suggestion['variable']} = {suggestion['value']} {suggestion['unit']} — {suggestion['reason']} ({', '.join(suggestion['evidence_ids'])})"]
    lines += ['', '## Unresolved information'] + (['- ' + g for g in manifest['draft']['gaps']] or ['No gaps reported by the drafting model; completeness is not certified.'])
    lines += ['', '## Calculated results (engine output)', 'Engine: CalcpadCE',
              'Selected load cases: ' + json.dumps(result['cases']), 'Load envelope: ' + json.dumps(result['envelope']),
              'Check summary: ' + json.dumps(result.get('check_summary')), 'Checks: ' + json.dumps(result.get('checks')),
              'Full computed worksheet: calculation.html. Executable formulas: calculation.cpd and calculation.mcdx.', '', '## Evidence']
    for evidence in manifest['evidence']:
        pages = sorted({int(p[0]) for p in evidence['positions']})
        lines += ['', f"[{evidence['id']}] {evidence['source_name']} — PDF pages: {pages or 'not supplied by parser'}",
                  'SHA-256: ' + evidence['source_sha256'], 'Packaged original: sources/' + evidence['source_id'] + '.pdf', evidence['text']]
    return '\n'.join(lines) + '\n'


def render_html(manifest, result):
    """Printable report using escaped prose and engine-owned numerical tables."""
    def esc(value): return html.escape(str(value))
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>' + esc(manifest['title']) + '</title>',
             '<style>body{max-width:900px;margin:48px auto;padding:24px;color:#22252b;font:15px/1.65 system-ui}h1{font-size:30px}h2{margin-top:32px;font-size:20px;border-bottom:1px solid #ccd2da;padding-bottom:8px}h3{font-size:16px}p{white-space:pre-wrap}small{color:#526071}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #dde1e7}blockquote{margin:12px 0;padding:16px;background:#f1f4f8;white-space:pre-wrap}code{overflow-wrap:anywhere}a{color:#365d9f}@media print{body{margin:0;padding:0}h2,h3{break-after:avoid}tr,blockquote{break-inside:avoid}}</style>',
             '<header><small>MCDXKit · Design package</small><h1>' + esc(manifest['title']) + '</h1></header>',
             '<p>' + ('No design-basis review recorded.' if manifest['review'].get('status') == 'not_recorded' else 'Design-basis review recorded.') + ' This is not engineering certification. Prime-native execution is unverified.</p>',
             '<p><strong>Reviewer:</strong> ' + esc(manifest['review']['reviewer']) + '<br>' + esc(manifest['review']['note']) + '</p>',
             '<h2>' + esc(narrative_label(manifest)) + '</h2><small>' + esc(manifest['model']) + '</small>']
    for section in manifest['draft']['sections']:
        parts += ['<h3>' + esc(section['heading']) + '</h3><p>' + esc(section['body']) + '</p>',
                  '<p>' + ' '.join('<a href="#' + esc(ref) + '">[' + esc(ref) + ']</a>' for ref in section['evidence_ids']) + '</p>']
    parts += ['<h2>Input decisions</h2><table><tr><th>Input</th><th>Proposed value</th><th>Decision</th></tr>']
    for i, item in enumerate(manifest['draft']['suggestions']):
        decision = 'Accepted' if i in manifest['review']['accepted'] else 'Not applied; template retained'
        parts += ['<tr><td>' + esc(item['variable']) + '</td><td>' + esc(item['value']) + ' ' + esc(item['unit']) + '</td><td>' + decision + '</td></tr>']
    parts += ['</table><h2>Unresolved information</h2>']
    for gap in manifest['draft']['gaps'] or ['No gaps reported by the model; completeness is not certified.']:
        parts += ['<p>' + esc(gap) + '</p>']
    parts += ['<h2>Calculated results</h2><p>Calculated with CalcpadCE. Cases: ' + esc(result['cases']) + '.</p>',
              '<table><tr><th>Load envelope</th><th>Value</th><th>Unit</th></tr>']
    for key, value in result['envelope'].items():
        parts += ['<tr><td>' + esc(key) + '</td><td>' + esc(value) + '</td><td>' + ('kip-in' if key.startswith('M') else 'kip') + '</td></tr>']
    parts += ['</table><p>Independent component extrema are not concurrent load combinations.</p>',
              '<p>Check summary: ' + esc(json.dumps(result.get('check_summary'))) + '</p>',
              '<p><a href="calculation.html">Full calculated worksheet</a> · <a href="calculation.mcdx">Mathcad formulas</a> · <a href="calculation.cpd">Executable Calcpad worksheet</a></p>',
              '<h2>Source evidence</h2>']
    for evidence in manifest['evidence']:
        pages = sorted({int(p[0]) for p in evidence['positions']})
        parts += ['<section id="' + esc(evidence['id']) + '"><h3>' + esc(evidence['id']) + ' · ' + esc(evidence['source_name']) + '</h3>',
                  '<p>PDF pages: ' + esc(pages or 'not supplied by parser') + ' · <a href="sources/' + evidence['source_id'] + '.pdf">Original PDF</a></p>',
                  '<blockquote>' + esc(evidence['text']) + '</blockquote><small>SHA-256: <code>' + esc(evidence['source_sha256']) + '</code></small></section>']
    return '\n'.join(parts) + '</html>'
