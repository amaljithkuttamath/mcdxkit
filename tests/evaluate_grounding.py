"""Opt-in live grounding benchmark. Only synthetic public fixture text leaves this process."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from mcdxkit.document_clients import ModelConfig
from mcdxkit.knowledge import KnowledgeAgent, VERSION, GROUNDING_VERSION, claim_units, grounding_gate


def evaluate_batch(cases, verify):
    """Run one bounded verifier request; independently score each publication decision."""
    statements, evidence, tasks = [], [], []
    for index, case in enumerate(cases, 1):
        eid = f'E{index}'
        section = {'heading': 'Requirement', 'body': case['claim'] + f' [{eid}]', 'evidence_ids': [eid]}
        units = [unit for unit in claim_units([section], []) if not unit['structural']]
        mapping = {}
        for unit in units:
            global_id = f'S{len(statements)+1}'
            mapping[global_id] = unit['id']
            statements.append({'id': global_id, 'kind': unit['kind'], 'text': unit['text'], 'allowed_evidence_ids': [eid]})
        row = {'id': eid, 'source_name': 'Synthetic benchmark', 'source_id': eid,
               'text': case['source'], 'positions': [[index, 0, 0, 0, 0]]}
        evidence.append({'id': eid, 'source': row['source_name'], 'pages': [index], 'text': row['text']})
        tasks.append((case, section, row, mapping))
    raw = verify(statements, evidence)
    ids = [check.get('id') for check in raw.get('checks', [])]
    if len(ids) != len(statements) or set(ids) != {item['id'] for item in statements}:
        raise ValueError('The benchmark did not receive exactly one check for each statement')
    results = []
    for case, section, row, mapping in tasks:
        response = {'checks': [{**check, 'id': mapping[check['id']]} for check in raw.get('checks', []) if check.get('id') in mapping]}
        payload = {'kind': 'research', 'status': 'supported', 'assessment': '', 'conflicts': [], 'gaps': []}
        gate = grounding_gate(lambda *_args, response=response: response, payload, [section], [row], {'ready': 1, 'total': 1, 'excluded': []})
        published = gate[0]['status'] == 'grounded' and bool(gate[1])
        expected = case['expected'] == 'supported'
        results.append({'case': case['id'], 'expected_publication': expected, 'published': published,
                        'passed': expected == published, 'grounding': gate[0], 'verifier_checks': response['checks']})
    return {'passed': all(row['passed'] for row in results), 'total': len(results),
            'supported_published': sum(row['published'] and row['expected_publication'] for row in results),
            'supported_total': sum(row['expected_publication'] for row in results),
            'unsafe_publications': sum(row['published'] and not row['expected_publication'] for row in results),
            'cases': results}


def evaluate(cases, verify, batch_size=1, interval=0):
    """Keep independent questions isolated by default, like separate application turns.

    A pooled batch remains available as a stress test. Errors fail a case rather than
    being counted as a successful abstention. Each request is attempted exactly once.
    """
    if batch_size < 1 or not cases:
        raise ValueError('Provide cases and a positive batch size')
    results, started = [], None
    for offset in range(0, len(cases), batch_size):
        batch = cases[offset:offset + batch_size]
        if started is not None and interval:
            time.sleep(max(0, interval - (time.monotonic() - started)))
        started = time.monotonic()
        try:
            results.extend(evaluate_batch(batch, verify)['cases'])
        except Exception as exc:
            results.extend({'case': case['id'], 'expected_publication': case['expected'] == 'supported',
                            'published': False, 'passed': False, 'error': type(exc).__name__} for case in batch)
    return {'passed': all(row['passed'] for row in results), 'total': len(results),
            'supported_published': sum(row['published'] and row['expected_publication'] for row in results),
            'supported_total': sum(row['expected_publication'] for row in results),
            'unsafe_publications': sum(row['published'] and not row['expected_publication'] for row in results),
            'errors': sum('error' in row for row in results), 'cases': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly send public synthetic fixtures to the configured OpenRouter model')
    parser.add_argument('--workspace', type=Path, required=True, help='The local .documents configuration directory; secrets are never exported')
    parser.add_argument('--model', help='Explicit free-model comparison; does not change workspace settings')
    parser.add_argument('--batch-size', type=int, default=1, help='Independent questions per request; default 1 matches separate app turns')
    parser.add_argument('--output', type=Path, required=True, help='New result JSON path (must not exist)')
    args = parser.parse_args()
    if not args.live: parser.error('--live is required; this benchmark makes hosted model requests')
    if not 1 <= args.batch_size <= 24: parser.error('--batch-size must be between 1 and 24')
    if args.output.exists(): parser.error('Output exists; choose a new path to preserve earlier evidence')
    config = ModelConfig.for_workspace(args.workspace)
    if config.provider != 'openrouter': parser.error('This benchmark is scoped to OpenRouter; no silent provider switch')
    if args.model: config = replace(config, model=args.model)
    path = Path(__file__).parent / 'fixtures' / 'grounding' / 'cases-v1.json'
    data = path.read_bytes()
    report = {'schema': 'mcdxkit.grounding-benchmark/1', 'created': datetime.now(timezone.utc).isoformat(),
              'model': config.answer_model, 'agent_version': VERSION, 'grounding_version': GROUNDING_VERSION,
              'fixture_sha256': hashlib.sha256(data).hexdigest(),
              'protocol': 'isolated questions' if args.batch_size == 1 else 'pooled question stress test',
              'batch_size': args.batch_size,
              'acceptance': 'All supported cases published; zero unsupported cases published. Errors and missing results fail.',
              'limits': 'Synthetic fixture benchmark, not a guarantee of factual accuracy or engineering approval.'}
    try:
        report.update(evaluate(json.loads(data), KnowledgeAgent(config, workspace=args.workspace).verify,
                               batch_size=args.batch_size, interval=4))
    except Exception as exc:
        report.update(passed=False, error=type(exc).__name__, reason='Provider or protocol failed; no score claimed and no retry made.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__': sys.exit(main())
