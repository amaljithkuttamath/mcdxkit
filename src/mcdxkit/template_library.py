"""Workspace-wide Mathcad template library.

Engineers upload each template once and reuse it across projects. A template is stored by the SHA-256 of
its bytes and never overwritten: a changed file with the same name is a new version listed beside the old
one. Archiving hides a template from new calculations without deleting it. A calculation copies the bytes
it uses into its own snapshot, so nothing here can change a past result.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import mcdx
from .inspection import template_inputs

MAX_TEMPLATES = 500
MAX_TEMPLATE_BYTES = 32 * 1024 * 1024


def template_name(name):
    if (not isinstance(name, str) or not name.lower().endswith('.mcdx') or len(name) > 200 or name in ('.mcdx',)
            or any(c in name for c in '/\\') or any(ord(c) < 32 for c in name) or name.startswith('.')):
        raise ValueError('Choose a .mcdx filename without directory separators or control characters.')
    return name


def template_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Template not found in the library.')
    return value


class TemplateLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / 'projects.sqlite3', timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, name, raw):
        """Validate and store a template. Identical bytes return the existing entry (restored if archived)."""
        name = template_name(name)
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_TEMPLATE_BYTES:
            raise ValueError('Use a Mathcad .mcdx template no larger than 32 MiB.')
        tid = hashlib.sha256(raw).hexdigest()
        folder = self.root / 'templates' / tid
        with tempfile.TemporaryDirectory(dir=self.root, prefix='.template-') as staging:
            path = Path(staging) / name
            path.write_bytes(raw)
            try:
                found = template_inputs(path)      # validates the package; never infers custom mappings
            except (zipfile.BadZipFile, KeyError, mcdx.E.XMLSyntaxError) as exc:
                raise ValueError('Not a readable Mathcad .mcdx template.') from exc
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT data FROM templates WHERE id=?', (tid,)).fetchone()
                if row:
                    record = json.loads(row[0])
                    if record['archived']:
                        record['archived'] = False
                        db.execute('UPDATE templates SET data=? WHERE id=?', (json.dumps(record), tid))
                    return record
                if db.execute('SELECT COUNT(*) FROM templates').fetchone()[0] >= MAX_TEMPLATES:
                    raise ValueError(f'Template library limit: {MAX_TEMPLATES} templates. Archive is not deletion; use another workspace.')
                folder.mkdir(parents=True, exist_ok=True)
                if not (folder / name).exists(): shutil.move(str(path), folder / name)
                record = {'id': tid, 'name': name, 'sha256': tid, 'bytes': len(raw), 'created': time.time(), 'archived': False,
                          'inputs': [{k: i[k] for k in ('variable', 'value', 'unit', 'component', 'status', 'expression')}
                                     for i in found['inputs']],
                          'preset_mapping': found['input_map'], 'mapping_basis': found['mapping_basis'] if found['input_map'] else None,
                          'missing_load_inputs': found['missing_load_inputs'], 'native_execution_verified': False}
                db.execute('INSERT INTO templates VALUES (?,?)', (tid, json.dumps(record)))
                return record

    def listing(self, include_archived=False, usage=None):
        with self.connection() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM templates')]
        # Versions are numbered over every upload, so archiving one never relabels another.
        versions: dict[str, int] = {}
        for row in sorted(rows, key=lambda r: r['created']):
            versions[row['name']] = versions.get(row['name'], 0) + 1
            row['version'] = versions[row['name']]
        rows = [r for r in rows if include_archived or not r['archived']]
        rows.sort(key=lambda r: (r['name'].casefold(), -r['created']))
        return [{**r, 'calculations': (usage or {}).get(r['sha256'], 0)} for r in rows]

    def get(self, tid):
        with self.connection() as db:
            row = db.execute('SELECT data FROM templates WHERE id=?', (template_id(tid),)).fetchone()
        if not row: raise ValueError('Template not found in the library.')
        return json.loads(row[0])

    def path(self, tid):
        """The stored file, checked against its recorded hash before any use."""
        record = self.get(tid)
        path = self.root / 'templates' / record['id'] / record['name']
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('A library template changed on disk; upload the original again.')
        mcdx.validate(path)
        return path

    def archive(self, tid, archived=True):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM templates WHERE id=?', (template_id(tid),)).fetchone()
            if not row: raise ValueError('Template not found in the library.')
            record = json.loads(row[0]); record['archived'] = bool(archived)
            db.execute('UPDATE templates SET data=? WHERE id=?', (json.dumps(record), tid))
        return record
