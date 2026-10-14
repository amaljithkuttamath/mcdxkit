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

from . import engine
from .document_clients import ModelConfig


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid document project identifier.')
    return value


def text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1–{limit} characters.')
    return value.strip()


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
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / 'projects.sqlite3', timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def edit(self, pid):
        identifier(pid)
        with self.connection() as db:
            try: db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                raise ValueError('Another document operation is running. Retry after it completes.') from None
            row = db.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()
            if not row: raise ValueError('Document project not found.')
            state = json.loads(row[0])
            yield state
            db.execute('UPDATE projects SET data=? WHERE id=?', (json.dumps(state, allow_nan=False), pid))

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
                state.update(self.snapshot_calculation(folder, report, template, cases, input_map, load_source))
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
            raise ValueError('Calculation snapshots already exist; create another project rather than overwrite them.')
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

    def attach_calculation(self, pid, report, template, *, cases=None, input_map=None, load_source='effects'):
        with self.edit(pid) as state:
            self.verify(state)
            if state.get('report_sha256'):
                raise ValueError('A calculation is already connected. Create another project for different calculation files.')
            folder = self.root / pid
            # Reject orphan snapshots as well; never overwrite a previous source.
            if any((folder/name).exists() for name in ('loads.txt', 'template.mcdx')):
                raise ValueError('Calculation snapshots already exist; create another project.')
            try:
                state.update(self.snapshot_calculation(folder, report, template, cases, input_map, load_source))
            except Exception:
                for name in ('loads.txt', 'template.mcdx'):
                    (folder/name).unlink(missing_ok=True)
                raise
            state['revision'] += 1
        return self.get(pid)

    @staticmethod
    def require_calculation(state):
        if not state.get('report_sha256'):
            raise ValueError('Connect a raw report and Mathcad template before preparing or calculating a design package. You can ask the library without them.')

    def verify(self, state):
        folder = self.root / state['id']
        files = [('sources/' + s['id'] + '.pdf', s['sha256']) for s in state['sources']]
        if state.get('report_sha256'):
            files += [('loads.txt', state['report_sha256']), ('template.mcdx', state['template_sha256'])]
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

    def ingest(self, pid, client=None):
        # Commit remote IDs after every operation so a later parse failure is recoverable.
        state = self.get(pid); self.verify(state)
        if not state['sources']: raise ValueError('Add project PDFs before indexing.')
        client = client or ModelConfig.for_workspace(self.root).ragflow()
        with self.edit(pid) as state:
            if not state['dataset_id']:
                state['dataset_id'] = client.create_dataset('mcdxkit-' + pid)
        for sid in [s['id'] for s in state['sources']]:
            with self.edit(pid) as state:
                source = next(s for s in state['sources'] if s['id'] == sid)
                if not source['document_id']:
                    raw = (self.root / pid / 'sources' / (sid + '.pdf')).read_bytes()
                    if digest(raw) != source['sha256']: raise ValueError('Source changed before upload.')
                    source['document_id'] = client.upload(state['dataset_id'], source['name'], raw)
            with self.edit(pid) as state:
                source = next(s for s in state['sources'] if s['id'] == sid)
                if source['status'] in ('uploaded', 'failed'):
                    client.parse(state['dataset_id'], [source['document_id']]); source['status'] = 'parsing'
        return self.get(pid)

    def status(self, pid, client=None):
        with self.edit(pid) as state:
            if state['dataset_id']:
                client = client or ModelConfig.for_workspace(self.root).ragflow()
                docs = {d['id']: d for d in client.documents(state['dataset_id'])}
                for source in state['sources']:
                    doc = docs.get(source['document_id'])
                    if not doc:
                        source['status'] = 'failed'; source['progress'] = 0
                    if doc:
                        run = str(doc.get('run', '')).upper()
                        progress = doc.get('progress', 0)
                        source['progress'] = max(0, min(1, progress)) if type(progress) in (int, float) and math.isfinite(progress) else 0
                        source['status'] = ('ready' if run in ('DONE', '3') and doc.get('chunk_count', 0) > 0
                                            else 'failed' if run in ('FAIL', 'FAILED', '4', 'CANCEL', '2', 'DONE', '3') else 'parsing')
        return self.get(pid)

    def ask(self, pid, question, client=None, writer=None):
        return self.generate(pid, question, client, writer, purpose='answer')

    def draft(self, pid, question, client=None, writer=None):
        return self.generate(pid, question, client, writer, purpose='design_basis')

    def generate(self, pid, question, client, writer, *, purpose):
        question = text(question, 'Question or draft request', 2000)
        with self.edit(pid) as state:
            self.verify(state)
            if purpose == 'design_basis': self.require_calculation(state)
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
            inputs = state['template_inputs'] if purpose == 'design_basis' else {'inputs': []}
            calculation = None
            if purpose == 'design_basis':
                folder = self.root / pid
                found = engine.inspect_report(folder/'loads.txt', template=folder/'template.mcdx', **state['options'])
                calculation = {k: found[k] for k in ('selected_cases', 'load_source', 'envelope', 'governing')}
                calculation.update(report_sha256=state['report_sha256'], template_sha256=state['template_sha256'],
                                   units={'P': 'kip', 'Vy': 'kip', 'Vz': 'kip', 'My': 'kip-in', 'Mz': 'kip-in'},
                                   qualification='Independent component extrema are not concurrent load combinations.')
            context = {'project': state['title'], 'purpose': purpose, 'request': question,
                       'inputs': inputs['inputs'], 'calculation': calculation, 'evidence': evidence}
            content = validate_draft(writer.draft(context), inputs, evidence)
            draft = {'id': secrets.token_hex(16), 'created': time.time(), 'revision': state['revision'],
                     'request': question, 'model': writer.model, 'content': content, 'evidence': evidence, 'review': None,
                     'purpose': purpose, 'calculation_context': calculation}
            collection = state.setdefault('answers' if purpose == 'answer' else 'drafts', [])
            if len(collection) >= 100: raise ValueError('Project contains 100 answers or drafts. Create a new project revision.')
            collection.append(draft)
        return draft

    @staticmethod
    def selected_draft(state, did):
        draft = next((d for d in state['drafts'] if d['id'] == did), None)
        if draft is None: raise ValueError('Draft not found in this project.')
        if draft['revision'] != state['revision']: raise ValueError('Project sources changed; generate and review a new draft.')
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

    def build(self, pid, did):
        with self.edit(pid) as state:
            self.require_calculation(state)
            self.verify(state)
            draft = self.selected_draft(state, did)
            if draft['review'] is None: raise ValueError('Complete the draft review before building a package.')
            if len(state['runs']) >= 100: raise ValueError('Project contains 100 packages. Create a new project revision.')
            content = validate_draft(draft['content'], state['template_inputs'], draft['evidence'])
            overrides = {s['variable']: s['value'] for i, s in enumerate(content['suggestions']) if i in draft['review']['accepted']}
            rid = secrets.token_hex(16)
            folder = self.root / pid / 'runs' / rid; folder.mkdir(parents=True)
            try:
                result = engine.convert(self.root / pid / 'loads.txt', self.root / pid / 'template.mcdx',
                                        folder / 'calculation.mcdx', title=state['title'], overrides=overrides, **state['options'])
                manifest = {'schema': 'mcdxkit-document-package/1', 'project_id': pid, 'title': state['title'],
                            'run_id': rid, 'draft_id': did, 'created': time.time(), 'model': draft['model'],
                            'review': draft['review'], 'draft': content, 'evidence': draft['evidence'],
                            'calculation_context': draft.get('calculation_context'),
                            'sources': [{k: s[k] for k in ('id', 'name', 'sha256', 'bytes')} for s in state['sources']],
                            'report_sha256': state['report_sha256'], 'template_sha256': state['template_sha256'],
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
                shutil.copyfile(self.root / pid / 'loads.txt', sources_dir / 'loads.txt')
                shutil.copyfile(self.root / pid / 'template.mcdx', sources_dir / 'template.mcdx')
                if (file_digest(sources_dir/'loads.txt') != state['report_sha256']
                        or file_digest(sources_dir/'template.mcdx') != state['template_sha256']
                        or result['source_sha256'] != state['report_sha256']
                        or result['template_sha256'] != state['template_sha256']):
                    raise ValueError('Calculation inputs changed during package generation.')
                manifest['files'] = {str(p.relative_to(folder)): file_digest(p) for p in folder.rglob('*') if p.is_file()}
                (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
                with zipfile.ZipFile(folder / 'package.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
                    for p in folder.rglob('*'):
                        if p.is_file() and p.name != 'package.zip': archive.write(p, str(p.relative_to(folder)))
                run = {'id': rid, 'draft_id': did, 'created': manifest['created'], 'sha256': file_digest(folder/'package.zip'),
                       'calculated': True, 'native_execution_verified': False, 'check_summary': result.get('check_summary')}
                state['runs'].append(run)
            except Exception:
                shutil.rmtree(folder)
                raise
        return run

    def artifact(self, pid, rid, name):
        state = self.get(pid)
        run = next((r for r in state['runs'] if r['id'] == rid), None)
        if run is None or name not in ('package.zip', 'report.html', 'report.md', 'manifest.json'):
            raise ValueError('Document artifact not found.')
        folder = self.root / pid / 'runs' / identifier(rid)
        package = folder / 'package.zip'
        if package.is_symlink() or file_digest(package) != run['sha256']: raise ValueError('Package changed after generation.')
        if name != 'package.zip':
            with zipfile.ZipFile(package) as archive:
                if archive.read(name) != (folder/name).read_bytes(): raise ValueError('Report changed after generation.')
        return folder / name


def render_report(manifest, result):
    """Numerical sections are deterministic; model prose remains visibly attributed."""
    lines = ['# ' + manifest['title'], '', '## Review status',
             'Design-basis review recorded. This is not engineering certification. Prime-native execution is unverified.',
             'Reviewer: ' + manifest['review']['reviewer'], 'Review note: ' + manifest['review']['note'], '',
             '## Source-backed narrative (AI draft, reviewed)', 'Model: ' + manifest['model']]
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
             '<p>Design-basis review recorded. This is not engineering certification. Prime-native execution is unverified.</p>',
             '<p><strong>Reviewer:</strong> ' + esc(manifest['review']['reviewer']) + '<br>' + esc(manifest['review']['note']) + '</p>',
             '<h2>Design basis</h2><small>AI-drafted narrative, reviewed · ' + esc(manifest['model']) + '</small>']
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
