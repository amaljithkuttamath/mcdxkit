"""Versioned engineering provenance records; no built-in code clauses or rule evaluator.

Rule outcomes link to checks already calculated by the worksheet. Review records are
external attestations, not authenticated professional approval. This module never executes
expressions, edits input values or downloads standards.
"""
import argparse
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

SCHEMA = 'engineering-register/1'
BASIS_FIELDS = ('owner', 'jurisdiction', 'governing_code', 'code_edition', 'owner_amendments',
                'design_method', 'material_grade', 'section_reference', 'geotechnical_basis',
                'units_axes', 'load_source')
MEASURES = (('P_a', 'P', 'force', 'kip'), ('V_u', 'Vy', 'force', 'kip'),
            ('V_z', 'Vz', 'force', 'kip'), ('M_uy', 'My', 'moment', 'kip-in'),
            ('M_uz', 'Mz', 'moment', 'kip-in'))
# Exact unit definitions/conversions, not design factors or safe ranges.
UNITS = {'kip': ('force', 4448.2216152605), 'lbf': ('force', 4.4482216152605),
         'N': ('force', 1), 'kN': ('force', 1000),
         'kip-in': ('moment', 112.9848290276167), 'N-m': ('moment', 1),
         'kN-m': ('moment', 1000), 'kN-mm': ('moment', 1),
         'm': ('length', 1), 'mm': ('length', .001), 'in': ('length', .0254),
         'ft': ('length', .3048), 'ksi': ('stress', 6894757.293168),
         'MPa': ('stress', 1000000), '1': ('dimensionless', 1)}
MAX_BYTES = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def audit_hash(audit):
    return digest({k: v for k, v in audit.items() if k != 'standards_register'})


def content_hash(register):
    """Any substantive edit invalidates recorded reviews, conservatively for all rules."""
    body = copy.deepcopy(register)
    for rule in body.get('rules', []):
        rule.pop('review', None)
    return digest(body)


def from_audit(audit):
    values = []
    for symbol, component, dimension, unit in MEASURES:
        if component not in audit.get('envelope', {}):
            continue
        values.append({'id': symbol, 'component': component, 'quantity': audit['envelope'][component],
                       'unit': unit, 'dimension': dimension, 'source_id': 'report', 'source_version': '1',
                       'source_locator': {'case_ids': audit.get('cases', []),
                                          'table': 'final local summary',
                                          'load_source': audit.get('load_source'),
                                          'source_rows': [{k: c.get(k) for k in ('id', 'source_rows')}
                                                          for c in audit.get('source_cases', [])]},
                       'transformations': ['Maximum compression' if component == 'P' else 'Maximum absolute component',
                                           'Independent component envelope; not concurrent loads'],
                       'template_mapping': ', '.join(k for k,v in audit['input_map'].items() if v==component) or None
                       if 'input_map' in audit else symbol if component != 'Vz' else None})
    return {'schema': SCHEMA, 'revision': 1, 'calculation_sha256': audit_hash(audit),
            'project_basis': {k: audit.get('load_source') if k == 'load_source' else None for k in BASIS_FIELDS},
            'sources': [{'id': 'report', 'kind': 'analysis_report', 'version': '1',
                         'reference': audit.get('source'), 'edition': None, 'sha256': audit.get('source_sha256')},
                        {'id': 'template', 'kind': 'worksheet', 'version': '1',
                         'reference': 'Original template snapshot', 'edition': None,
                         'sha256': audit.get('template_sha256')}],
            'values': values, 'rules': [],
            'overrides': [{'variable': name, 'quantity': value, 'source_id': 'template', 'reason': None,
                           'review_state': 'awaiting_engineering_review'}
                          for name, value in audit.get('overrides', {}).items()]}


def validate(register):
    if not isinstance(register, dict) or register.get('schema') != SCHEMA:
        raise ValueError('Unsupported engineering register schema')
    if type(register.get('revision')) is not int or register['revision'] < 1:
        raise ValueError('Register revision must be a positive integer')
    if not isinstance(register.get('project_basis'), dict):
        raise ValueError('Register project_basis must be an object')
    for name in BASIS_FIELDS:
        value = register['project_basis'].get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 2000):
            raise ValueError('Project basis entries must be text or null')
    for name, limit in (('sources', 100), ('values', 500), ('rules', 200)):
        rows = register.get(name)
        if not isinstance(rows, list) or len(rows) > limit or not all(isinstance(r, dict) for r in rows):
            raise ValueError(f'{name} must be a list of at most {limit} records')
        ids = [row.get('id') for row in rows]
        if any(not isinstance(i, str) or not i or len(i) > 100 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError(f'{name} need unique, nonempty text IDs')
        for row in rows:
            for key in ('version', 'source_id', 'source_version', 'reference', 'edition', 'sha256',
                        'unit', 'dimension', 'component', 'clause'):
                if row.get(key) is not None and (not isinstance(row[key], str) or len(row[key]) > 2000):
                    raise ValueError(f'{name}.{key} must be text or null')
    for value in register['values']:
        if type(value.get('quantity')) not in (int, float) or not math.isfinite(value['quantity']):
            raise ValueError('Value quantities must be finite numbers')
        if not isinstance(value.get('transformations'), list):
            raise ValueError('Values need a transformation history list')
    for rule in register['rules']:
        if not isinstance(rule.get('version'), str) or not rule['version']:
            raise ValueError('Rules need an explicit version')
        if not isinstance(rule.get('applicability'), dict) or not isinstance(rule.get('check'), dict):
            raise ValueError('Rules need applicability and check objects')
        if not isinstance(rule.get('value_ids'), list) or not all(isinstance(x, str) for x in rule['value_ids']):
            raise ValueError('Rule value_ids must be a list of text IDs')
        if rule.get('review') is not None and not isinstance(rule['review'], dict):
            raise ValueError('Rule review must be an object or null')
    if not isinstance(register.get('overrides'), list) or not all(isinstance(x, dict) for x in register['overrides']):
        raise ValueError('Overrides must be a list of records')
    seen = set()
    for override in register['overrides']:
        name = override.get('variable')
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError('Overrides need unique variable names')
        seen.add(name)
        if type(override.get('quantity')) not in (int, float) or not math.isfinite(override['quantity']):
            raise ValueError('Override quantities must be finite numbers')
        if override.get('reason') is not None and not isinstance(override['reason'], str):
            raise ValueError('Override reasons must be text or null')
        if override.get('review_state') not in ('reviewed', 'awaiting_engineering_review', 'changes_requested'):
            raise ValueError('Unknown override review state')
    # Check nested data is finite JSON and keep local/HTTP inspection bounded.
    try:
        encoded = json.dumps(register, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('Register must contain finite JSON data') from exc
    if len(encoded) > MAX_BYTES:
        raise ValueError('Register exceeds 1 MiB')
    return register


def _reviewed(rule, fingerprint):
    review = rule.get('review') or {}
    if review.get('decision') != 'reviewed' or review.get('content_sha256') != fingerprint:
        return False
    if not all(isinstance(review.get(k), str) and review[k].strip() for k in ('reviewer', 'reason', 'reviewed_at')):
        return False
    try:
        stamp = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
        return stamp.tzinfo is not None
    except ValueError:
        return False


def _value_issue(value, sources, audit):
    source = sources.get(value.get('source_id'))
    if not source or not source.get('reference') or source.get('version') != value.get('source_version'):
        return 'missing_basis', 'Missing source reference or source version mismatch'
    unit = UNITS.get(value.get('unit'))
    if not unit or unit[0] != value.get('dimension'):
        return 'unsupported', 'Unit/dimension pair is not supported'
    component = value.get('component')
    mapped = next((d for d in MEASURES if d[0] == value['id']), None)
    if mapped and component != mapped[1]:
        return 'calculation_error', 'Mapped input component was changed or removed'
    if component:
        definition = next((d for d in MEASURES if d[1] == component), None)
        if definition is None or component not in audit.get('envelope', {}):
            return 'unsupported', 'Component has no audited input value'
        if source.get('sha256') != audit.get('source_sha256'):
            return 'calculation_error', 'Value source does not match the calculated report'
        expected = audit['envelope'][component] * UNITS[definition[3]][1]
        if unit[0] != definition[2] or not math.isclose(value['quantity'] * unit[1], expected, rel_tol=1e-9, abs_tol=1e-9):
            return 'calculation_error', 'Quantity differs from the audited input'
    return None


def assess(register, audit):
    """Inspect declared provenance against an audit; never evaluate a rule expression."""
    validate(register)
    sources = {r['id']: r for r in register['sources']}
    values = {v['id']: v for v in register['values']}
    basis = register['project_basis']
    missing = [key for key in BASIS_FIELDS if not basis.get(key)]
    fingerprint = content_hash(register)
    stale = register.get('calculation_sha256') != audit_hash(audit)
    overrides_pending = any(not o.get('reason') or o.get('review_state') != 'reviewed' for o in register['overrides'])
    value_issues = {v['id']: _value_issue(v, sources, audit) for v in values.values()}
    declared_overrides = {o.get('variable'): o.get('quantity') for o in register['overrides']
                          if isinstance(o.get('variable'), str)}
    override_mismatch = declared_overrides != audit.get('overrides', {})

    def state(rule):
        if stale or override_mismatch or not audit.get('calculation', {}).get('calculated'):
            return 'calculation_error', 'Calculation evidence is missing or belongs to another revision'
        if missing:
            return 'missing_basis', 'Missing project basis: ' + ', '.join(missing)
        if basis['load_source'] != audit.get('load_source'):
            return 'calculation_error', 'Project load basis differs from the calculated load source'
        source = sources.get(rule.get('source_id'))
        if not source or not all(source.get(k) for k in ('reference', 'edition', 'version')) or not rule.get('clause'):
            return 'missing_basis', 'Rule needs an authority, edition, clause and version'
        if source['version'] != rule.get('source_version'):
            return 'missing_basis', 'Rule source version differs from the current source record'
        for key, expected in rule['applicability'].items():
            if key not in BASIS_FIELDS:
                return 'unsupported', 'Unsupported applicability field: ' + key
            if basis.get(key) != expected:
                return 'not_applicable', 'Project basis does not match applicability: ' + key
        if not rule['value_ids'] or any(key not in values for key in rule['value_ids']):
            return 'missing_basis', 'Rule must link existing value records'
        for key in rule['value_ids']:
            if value_issues[key]:
                return value_issues[key]
            if values[key]['dimension'] != rule.get('dimension'):
                return 'unsupported', 'Rule dimension differs from the linked value'
        if overrides_pending or not _reviewed(rule, fingerprint):
            return 'awaiting_engineering_review', 'Review missing, changes requested, or content changed since review'
        matched = [c for c in audit.get('checks', []) if c.get('region_id') == rule['check'].get('region_id')
                   and c.get('expression') == rule['check'].get('expression')]
        if len(matched) != 1:
            return 'unsupported', 'No unique calculated worksheet check matches this rule'
        result = matched[0]
        if type(result.get('passed')) is not bool or type(result.get('result')) is not int or result['result'] != int(result['passed']):
            return 'calculation_error', 'Calculated check evidence is inconsistent'
        return ('pass' if result['passed'] else 'fail'), 'Outcome read from the calculated worksheet; external review recorded'

    rows = []
    for rule in register['rules']:
        status, reason = state(rule)
        rows.append({'id': rule['id'], 'version': rule['version'], 'state': status, 'reason': reason,
                     'value_ids': rule['value_ids'], 'source_id': rule.get('source_id'), 'clause': rule.get('clause')})
    overall = 'calculation_error' if stale or override_mismatch or not audit.get('calculation', {}).get('calculated') else 'missing_basis' if missing else 'awaiting_engineering_review'
    # A linked numerical pass is never an overall compliance or professional-approval badge.
    return {'schema': SCHEMA, 'revision': register['revision'], 'content_sha256': fingerprint,
            'status': overall, 'missing_basis': missing, 'rules': rows,
            'values': [{'id': v['id'], 'state': (value_issues[v['id']] or ('awaiting_engineering_review', 'No automatic value approval'))[0],
                        'reason': (value_issues[v['id']] or ('awaiting_engineering_review', 'No automatic value approval'))[1]}
                       for v in values.values()],
            'engineering_approval_verified': False, 'native_execution_verified': False}


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Register/audit exceeds 1 MiB')
    try:
        value = json.loads(raw)
    except RecursionError as exc:
        raise ValueError('JSON nesting exceeds the inspection limit') from exc
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mcdxkit standards', description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init', help='Create a pending register from an existing audit')
    init.add_argument('audit', type=Path); init.add_argument('--output', required=True, type=Path)
    inspect = sub.add_parser('inspect', help='Inspect register lineage and declared review evidence')
    inspect.add_argument('register', type=Path); inspect.add_argument('--audit', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        audit = read_json(args.audit)
        if args.action == 'init':
            register = from_audit(audit); validate(register)
            with args.output.open('x', encoding='utf-8') as stream:
                json.dump(register, stream, indent=2, allow_nan=False); stream.write('\n')
            print(str(args.output))
        else:
            print(json.dumps(assess(read_json(args.register), audit), indent=2, allow_nan=False))
        return 0  # Inspection succeeded; review/compliance outcome is in the returned states.
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print('mcdxkit standards: ' + str(exc), file=sys.stderr)
        return 2
