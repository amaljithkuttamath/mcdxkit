"""Transport-independent browser session and conversion operations."""
import math
import re
import secrets
import tempfile
import threading
import shutil
from pathlib import Path
from . import checks, engine, inspection, standards
from .review import runner as review_runner
from .review.history import AuditHistory, newest


MIB = 1024 * 1024

class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Session:
    def __init__(self, template, output_dir, checks='default', plan=None):
        self.template = Path(template).resolve() if template else None
        self.checks = checks
        # Resolved once: installed plug-ins are imported here, not on every request.
        self.plan = plan if plan is not None else review_runner.prepare(checks)
        self._history = None
        self.output_dir = Path(output_dir).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix='mcdxkit-browser-')
        self.token = secrets.token_urlsafe(32)
        self.files = {}
        self.upload_bytes = 0
        self.upload_count = 0
        self.conversions = 0
        self.previews = 0
        self.lock = threading.Lock()

    def get(self, file_id, kinds):
        if not isinstance(file_id, str) or file_id not in self.files:
            raise RequestError('File is not in this session. Select it again.', 404)
        entry = self.files[file_id]
        if entry['kind'] not in kinds:
            raise RequestError('This operation requires a ' + ' or '.join(kinds) + ' file')
        return entry

    def register(self, path, kind):
        for file_id, entry in self.files.items():
            if entry['path'] == path and entry['kind'] == kind:
                return {'id': file_id, 'name': path.name, 'kind': kind}
        file_id = secrets.token_hex(16)
        self.files[file_id] = {'path': path, 'kind': kind}
        return {'id': file_id, 'name': path.name, 'kind': kind}

    def history(self):
        """Recover complete generated pairs after a restart, without trusting saved paths."""
        rows = []
        for audit in newest(self.output_dir.glob('*/*.audit.json'), 100)[0]:
            output = audit.with_name(audit.name.removesuffix('.audit.json') + '.mcdx')
            if not re.fullmatch('[0-9a-f]{16}', audit.parent.name) or output.is_symlink() or not output.is_file():
                continue
            try:
                metadata = engine.load_audit(audit)
                rows.append({'worksheet': self.register(output, 'worksheet'), 'audit': self.register(audit, 'audit'),
                             'output': str(output), 'created': audit.stat().st_mtime,
                             'envelope': metadata.get('envelope'), 'native_execution_verified': False,
                             # History stays small: the summary only. Full checks are in the
                             # convert response and the downloadable audit.
                             'check_summary': self.check_summary(metadata),
                             **self.calculated_files(output, metadata.get('calculation'))})
            except (ValueError, OSError, AttributeError):
                continue
        return rows

    @staticmethod
    def check_summary(metadata):
        # Audits written before check extraction, or with a malformed summary, are
        # reported as unrecorded (None) rather than re-derived or assumed to pass.
        summary = metadata.get('check_summary')
        return summary if checks.valid_summary(summary) else None

    def calculated_files(self, output, calculation):
        cpd = output.with_suffix('.cpd'); rendered = output.with_suffix('.html')
        if not calculation or not calculation.get('calculated') or not cpd.is_file() or not rendered.is_file():
            return {}
        return {'open_worksheet': self.register(cpd, 'open_worksheet'),
                'calculated_worksheet': self.register(rendered, 'calculated_worksheet'),
                'calculation': {k: calculation[k] for k in ('engine', 'calculated', 'translated_math_regions')}}

    def upload(self, name, kind, raw):
        if not name or len(name) > 200 or name in ('.', '..') or any(c in name for c in '/\\') or any(ord(c) < 32 for c in name):
            raise RequestError('Choose a filename without directory separators or control characters.')
        suffix = Path(name).suffix.lower()
        if kind not in ('report', 'template', 'worksheet') or suffix not in ({'.gp11t', '.txt'} if kind == 'report' else {'.mcdx'}):
            raise RequestError('Use .gp11t or .txt reports, or .mcdx worksheets/templates.')
        state = self
        if state.upload_count >= 100:
            raise RequestError('Session contains 100 uploads. Restart mcdxkit serve to start a new session.', 413)
        if len(raw) > (16 * MIB if kind == 'report' else 32 * MIB):
            raise RequestError('File exceeds the upload limit.', 413)
        if state.upload_bytes + len(raw) > 256 * MIB:
            raise RequestError('Session uploads exceed 256 MiB. Restart the server for a new session.', 413)
        folder = Path(state.temp.name) / secrets.token_hex(16)
        folder.mkdir()
        path = folder / name
        try:
            path.write_bytes(raw)
            details = ({'inspection': self.inspect(path)} if kind == 'report'
                       else {'validation': engine.validate(path)})
        except Exception:
            path.unlink(missing_ok=True)
            folder.rmdir()
            raise
        result = state.register(path, kind)
        state.upload_count += 1
        state.upload_bytes += len(raw)
        return {**result, **details}

    def inspect(self, path, cases=None, load_source='effects'):
        # The case-name classifier learns from completed conversions in the output directory,
        # read once per completed conversion rather than on every inspection.
        if self._history is None or self._history[0] != self.conversions:
            self._history = (self.conversions, AuditHistory(self.output_dir))
        return engine.inspect_report(path, cases=cases, load_source=load_source, checks=self.plan,
                                     history=self._history[1])

    @staticmethod
    def cases(cases):
        if cases is not None and (not isinstance(cases, list) or not cases or len(cases) > 100 or
                                  any(type(i) is not int or i < 1 for i in cases) or len(set(cases)) != len(cases)):
            raise RequestError('Select one or more unique positive load case IDs.')
        return cases

    @staticmethod
    def basis(data):
        basis = data.get('load_source', 'effects')
        if basis not in ('effects', 'reactions'):
            raise RequestError('Load source must be effects or reactions.')
        return basis

    def summary(self, reports, basis):
        """CSV of per-case loads for two or more uploaded reports; nothing is written to the output directory."""
        if not isinstance(reports, list) or not 2 <= len(reports) <= 100 or not all(isinstance(r, dict) for r in reports):
            raise RequestError('Select between 2 and 100 reports for a summary.')
        if not all(isinstance(r.get('id'), str) for r in reports):
            raise RequestError('Each summary report needs a file ID.')
        rows, seen, duplicates = [], set(), []
        for report in reports:
            entry = self.get(report['id'], ['report'])
            found = engine.summarize_report(entry['path'], cases=self.cases(report.get('cases')), load_source=basis)
            # Same bytes, basis and selection would repeat identical rows: skip, as the CLI does.
            key = engine.summary_key(found)
            if key in seen:
                duplicates.append(entry['path'].name)
                continue
            seen.add(key); rows += found
        if len(seen) < 2:
            raise RequestError('Select at least two different reports for a summary.')
        return {'name': 'mcdxkit-summary.csv', 'reports': len(seen), 'rows': len(rows), 'duplicates_skipped': duplicates,
                'csv': engine.summary_csv(rows), 'native_execution_verified': False}

    def operation(self, route, data):
        state = self
        if route == '/api/template-inputs':
            template=state.get(data['template_id'],['template'])['path'] if data.get('template_id') else state.template
            if template is None:raise RequestError('Select a Mathcad template first.')
            return engine.inspect_template(template,input_map=data.get('input_map'))
        if route == '/api/standards':
            output = self.get(data.get('id'), ['worksheet'])['path']
            audit_path = output.with_suffix('.audit.json')
            if not audit_path.is_file():
                raise RequestError('Generate outputs first; this worksheet has no calculation audit.')
            audit = standards.read_json(audit_path)
            # Inspect only artifacts adjacent to the selected session worksheet, never saved paths.
            import hashlib
            for path, expected in ((output, audit.get('sha256')),
                                   (output.with_suffix('.html'), audit.get('calculation', {}).get('html_sha256'))):
                if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise RequestError('Calculation artifacts changed; regenerate before inspecting standards.')
            register = data.get('register') if 'register' in data else standards.from_audit(audit)
            return {'register': register, 'assessment': standards.assess(register, audit)}
        if route == '/api/view':
            if data.get('id') == 'default-template':
                if self.template is None or not self.template.is_file():
                    raise RequestError('Select a template first.')
                return inspection.worksheet(self.template)
            entry = self.get(data.get('id'), ['report', 'worksheet', 'template'])
            if entry['kind'] == 'report':
                raw = entry['path'].read_bytes()
                try: text = raw.decode('utf-8-sig')
                except UnicodeDecodeError: text = raw.decode('cp1252')
                return {'name': entry['path'].name, 'kind': 'report', 'text': text}
            view = inspection.worksheet(entry['path'])
            rendered = entry['path'].with_suffix('.html')
            if entry['kind'] == 'worksheet' and rendered.is_file():
                view['calculated_html'] = rendered.read_text(encoding='utf-8')
                view['calculation_engine'] = 'CalcpadCE'
            return view
        if route == '/api/diff':
            output = self.get(data.get('id'), ['worksheet'])['path']
            original = output.parent / '_source' / 'template.mcdx'
            if not original.is_file():
                raise RequestError('No original template snapshot is available for this file.')
            return inspection.diff(original, output)
        if route == '/api/validate':
            return engine.validate(state.get(data.get('id'), ['worksheet', 'template'])['path'])
        if route == '/api/summary':
            return self.summary(data.get('reports'), self.basis(data))
        entry = state.get(data.get('id'), ['report'])
        cases = self.cases(data.get('cases'))
        basis = self.basis(data)
        if route == '/api/inspect':
            found=self.inspect(entry['path'], cases, basis)
            template=state.get(data['template_id'],['template'])['path'] if data.get('template_id') else state.template
            if template is not None:
                found.update(inspection.template_report(template,found,data.get('input_map'),data.get('overrides')))
            return found
        template = state.get(data['template_id'], ['template'])['path'] if data.get('template_id') else state.template
        if template is None or not template.is_file():
            raise RequestError('Select a compatible .mcdx template before converting.')
        overrides = data.get('overrides', {})
        if not isinstance(overrides, dict) or len(overrides) > 50 or any(
            not isinstance(k, str) or len(k) > 100 or type(v) not in (int, float) or not math.isfinite(v)
            for k, v in overrides.items()
        ):
            raise RequestError('Overrides must be finite numeric template inputs.')
        title = data.get('title')
        if title is not None and (not isinstance(title, str) or len(title) > 160):
            raise RequestError('Title must contain at most 160 characters.')
        if state.conversions >= 100 or (route == '/api/preview' and state.previews >= 100):
            raise RequestError('Session has generated 100 worksheets. Restart the server for a new session.', 413)
        stem = re.sub(r'[^A-Za-z0-9_-]+', '-', entry['path'].stem).strip('-')[:80] or 'worksheet'
        preview = route == '/api/preview'
        folder = (Path(state.temp.name) / 'previews' if preview else state.output_dir) / secrets.token_hex(8)
        output = folder / (stem + '.mcdx')
        sources = folder / '_source'
        sources.mkdir(parents=True)
        try:
            snapshot_report = sources / entry['path'].name
            snapshot_template = sources / 'template.mcdx'
            shutil.copyfile(entry['path'], snapshot_report)
            shutil.copyfile(template, snapshot_template)
            result = engine.convert(snapshot_report, snapshot_template, output, cases=cases,
                                    load_source=basis, title=title, overrides=overrides, checks=self.plan,input_map=data.get('input_map'))
            changes = inspection.diff(snapshot_template, output)
        except Exception:
            shutil.rmtree(folder)
            raise
        if preview:
            state.previews += 1
        else:
            state.conversions += 1
        return {'worksheet': state.register(output, 'worksheet'),
                'audit': state.register(output.with_suffix('.audit.json'), 'audit'),
                'output': str(output), 'envelope': result['envelope'], 'cases': result['cases'], 'input_map':result['input_map'],
                'validation': result['validation'], 'native_execution_verified': False,
                'preview': preview, 'diff': changes, 'review_checks': result['review_checks'],
                'checks': result['checks'], 'check_summary': result['check_summary'],

                **self.calculated_files(output, result['calculation'])}
