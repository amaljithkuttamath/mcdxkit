"""Resumable, bounded parallel conversion pipeline around the same core engine."""
import argparse
import csv
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

from . import engine, calcpad
from . import checks as worksheet_checks
from .review import runner as review_runner

from .generate import case_ids, default_template, input_map as parse_input_map


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def discover(inputs, output_dir, *, recursive=False):
    output_dir = Path(output_dir).resolve()
    found = set()
    for value in inputs:
        path = Path(value).resolve()
        if not path.exists():
            raise ValueError(f'Input does not exist: {path}')
        candidates = path.rglob('*') if path.is_dir() and recursive else path.glob('*') if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            candidate = candidate.resolve()
            if candidate.is_relative_to(output_dir):
                continue
            if candidate.suffix.lower() in ('.gp11t', '.txt'):
                found.add(candidate)
            elif not path.is_dir():
                raise ValueError(f'Unsupported report: {path.name}; use .gp11t or .txt')
    return sorted(found)


def identity(source, source_sha256, template_sha256, settings, checks_identity):
    """Stable job ID: the inputs, settings, review checks and the engine revision that computes them.

    A new CalcpadCE revision or translator version gives a new ID, so resume never reuses an older result."""
    value = json.dumps([source, source_sha256, template_sha256, settings, calcpad.REVISION, calcpad.TRANSLATOR_VERSION,
                        checks_identity], sort_keys=True, allow_nan=False)
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def schedule(jobs, run, record, workers=1):
    """Run each job once and record each result as it finishes. Nothing is retried.

    With one worker, jobs run in this process and an exception from `run` stops the schedule; callers
    record per-job failures themselves (calculate_all relies on this to stop when its job is cancelled).
    With several workers, `run` must be a picklable module-level function, and one job's exception
    becomes its own failed record without stopping the others."""
    if workers == 1:
        for job in jobs:
            record(run(job))
        return
    iterator = iter(jobs)
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        pending = {}
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < workers * 2:
                job = next(iterator, None)
                if job is None:
                    exhausted = True
                else:
                    pending[pool.submit(run, job)] = job
            if not pending:
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                job = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {**job, 'status': 'failed', 'error': str(exc), 'native_execution_verified': False}
                record(result)


@contextmanager
def manifest_lock(root):
    # OS locks are released after process death; no stale lock recovery flag needed.
    with (root / '.mcdxkit-batch.lock').open('a+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another batch owns this output directory. Use a separate directory or wait.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _convert_job(job):
    started = time.monotonic()
    row = {k: job[k] for k in ('job_id', 'source', 'source_sha256', 'template_sha256', 'settings', 'output')}
    row['native_execution_verified'] = False
    output = Path(job['output']); audit = output.with_suffix('.audit.json')
    try:
        identity = {k: row[k] for k in ('job_id', 'source_sha256', 'template_sha256', 'settings')}
        metadata = output.parent / 'job.json'
        if output.exists() or audit.exists():
            if not job['resume']:
                raise ValueError('Output already exists. Use --resume or a new output directory.')
            if not output.is_file() or not audit.is_file() or not metadata.is_file():
                raise ValueError('Incomplete output pair. Preserve it for review and use a new output directory.')
            previous = json.loads(metadata.read_text())
            evidence = json.loads(audit.read_text())
            if (previous != identity or evidence['source_sha256'] != job['source_sha256']
                    or evidence['template_sha256'] != job['template_sha256']
                    or evidence['sha256'] != digest(output)):
                raise ValueError('Existing output failed identity/hash checks. It was not overwritten.')
            engine.validate(output)
            calculation = evidence.get('calculation', {})
            if not calculation.get('calculated') or calculation.get('revision') != calcpad.REVISION:
                raise ValueError('Saved output has no verified CalcpadCE calculation')
            artifact = output.with_suffix('.cpd')
            if not artifact.is_file() or digest(artifact) != calculation.get('source_sha256'):
                raise ValueError('Calculated worksheet failed artifact hash checks')
            # Hash and parse the same bytes so check outcomes come only from the verified page.
            rendered = output.with_suffix('.html')
            page = rendered.read_bytes() if rendered.is_file() else None
            if page is None or hashlib.sha256(page).hexdigest() != calculation.get('html_sha256'):
                raise ValueError('Calculated worksheet failed artifact hash checks')
            row.update(status='skipped', sha256=evidence['sha256'], audit=str(audit),
                       check_summary=resumed_checks(page, calculation.get('region_lines')),
                       open_checks=review_runner.open_flags(evidence.get('review_checks')),
                       check_errors=review_runner.check_errors(evidence.get('review_checks')))

        else:
            if Path(job['source']).stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Raw report exceeds 16 MiB')
            source_dir = output.parent / '_source'
            source_dir.mkdir(parents=True, exist_ok=True)
            snapshots = [(Path(job['source']), source_dir / Path(job['source']).name, job['source_sha256']),
                         (Path(job['template']), source_dir / 'template.mcdx', job['template_sha256'])]
            for original, snapshot, expected in snapshots:
                if not snapshot.exists():
                    # The coordinator lock prevents simultaneous batches from writing here.
                    with original.open('rb') as src, snapshot.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                if digest(snapshot) != expected:
                    raise ValueError('Input changed or snapshot is incomplete. Retry in a new output directory.')
            if metadata.exists():
                if json.loads(metadata.read_text()) != identity:
                    raise ValueError('Job identity mismatch. Existing files were not overwritten.')
            else:
                with metadata.open('x') as stream:
                    json.dump(identity, stream, indent=2)
            result = engine.convert(snapshots[0][1], snapshots[1][1], output, **job['settings'])
            row.update(status='succeeded', sha256=result['sha256'], audit=str(audit),
                       cases=result['cases'], envelope=result['envelope'], check_summary=result['check_summary'],
                       open_checks=review_runner.open_flags(result['review_checks']),
                       check_errors=review_runner.check_errors(result['review_checks']))

    except Exception as exc:
        row.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    if row.get('status') in ('succeeded', 'skipped'):
        row.update(calculation_engine='CalcpadCE', calculated=True,
                   open_worksheet=str(output.with_suffix('.cpd')), calculated_worksheet=str(output.with_suffix('.html')))
    row['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return row


def resumed_checks(page, region_lines):
    """Check summary re-read from a verified page; None (not recorded) without region lines."""
    if not isinstance(region_lines, dict) or not all(type(v) is int for v in region_lines.values()):
        return None
    return worksheet_checks.extract(page.decode('utf-8'), region_lines)[1]


# Review columns are appended, so anything parsing the earlier columns keeps working.
CHECK_COLUMNS = ('source', 'status', 'checks', 'passed', 'failed', 'governing_dc', 'governing_check',
                 'failed_checks', 'note', 'review_items', 'check_error')


def source_label(source, base=None):
    """Report path relative to the batch input root, so equal basenames stay distinct."""
    path = Path(source)
    if base is not None and path.is_relative_to(base):
        return path.relative_to(base).as_posix()
    return path.as_posix()


def input_root(inputs):
    """Deepest folder containing every input; files count by their parent folder."""
    folders = [str(p if p.is_dir() else p.parent) for p in (Path(i).resolve() for i in inputs)]
    try:
        return Path(os.path.commonpath(folders))
    except ValueError:  # e.g. inputs on different Windows drives
        return None


def check_row(result, base=None):
    """One table row per report; outcomes come only from validated check summaries."""
    row = dict.fromkeys(CHECK_COLUMNS, '')
    row.update(source=source_label(result['source'], base), status=result['status'])
    # Advisory review: blank when no check ran, so "nothing ran" never reads as nothing to review.
    items, errors = result.get('open_checks'), result.get('check_errors')
    row['review_items'] = '' if items is None else items
    # "no" would read as a clean review when nothing ran (--checks none); failures still show "yes".
    row['check_error'] = 'yes' if errors else '' if items is None else 'no'
    summary = result.get('check_summary')
    if result['status'] == 'failed':
        row['note'] = 'conversion failed'
    elif not worksheet_checks.valid_summary(summary):
        row['note'] = 'checks not recorded for this output'
    elif not summary.get('total'):
        row['note'] = 'no checks found'
    else:
        governing = summary.get('governing')
        row.update(checks=summary['total'], passed=summary['passed'], failed=summary['failed'],
                   failed_checks='; '.join(summary.get('failed_checks', [])),
                   governing_dc=f"{governing['ratio']:.3f}" if governing else '',
                   governing_check=governing['name'] if governing else '',
                   note='FAILURES' if summary['failed'] else 'all checks passed')
    return row


def _cell(value):
    # Worksheet variable names reach this table; stop spreadsheet formula injection.
    value = str(value)
    return "'" + value if value[:1] in ('=', '+', '-', '@', '\t', '\r') else value


def write_check_table(root, rows):
    """Write a new timestamped CSV; earlier tables are never overwritten."""
    stamp = time.strftime('%Y%m%dT%H%M%S')
    for attempt in range(100):
        path = root / (f'check-summary-{stamp}' + (f'-{attempt}' if attempt else '') + '.csv')
        try:
            stream = path.open('x', newline='', encoding='utf-8')
        except FileExistsError:
            continue
        with stream:
            writer = csv.DictWriter(stream, fieldnames=CHECK_COLUMNS)
            writer.writeheader()
            writer.writerows({k: _cell(v) for k, v in row.items()} for row in rows)
        return path
    raise ValueError('Could not create a new check summary table')


def format_check_table(rows):
    """Plain-text per-report table: governing D/C and any failed checks."""
    columns = ('source', 'status', 'checks', 'failed', 'governing_dc', 'governing_check', 'note',
               'review_items', 'check_error')
    widths = {c: max([len(c)] + [len(str(r[c])) for r in rows]) for c in columns}
    lines = ['  '.join(c.upper().ljust(widths[c]) for c in columns)]
    lines += ['  '.join(str(r[c]).ljust(widths[c]) for c in columns) for r in rows]
    lines += [f"{r['source']}: failed {r['failed_checks']}" for r in rows if r['failed_checks']]
    return '\n'.join(line.rstrip() for line in lines)


def review_counts(rows):
    """Batch-level advisory review tally over the per-report table rows."""
    return {'reports': len(rows),
            'reports_checked': sum(1 for r in rows if r['review_items'] != ''),
            'reports_failed': sum(1 for r in rows if r['status'] == 'failed'),
            'reports_with_items': sum(1 for r in rows if r['review_items'] not in ('', 0)),
            'items': sum(r['review_items'] for r in rows if r['review_items'] != ''),
            'reports_with_check_errors': sum(1 for r in rows if r['check_error'] == 'yes')}


def format_review_summary(counts):
    """One plain line: how many reports, how many have something to review, checks that could not run."""
    def reports(count):
        return f'{count} report' + ('' if count == 1 else 's')
    line = f"{reports(counts['reports'])}, {counts['reports_with_items']} with items to review"
    # A report where no check ran is never counted as having nothing to review.
    # Failed conversions are named as such, not folded into "not checked".
    unchecked = counts['reports'] - counts['reports_checked'] - counts['reports_failed']
    if unchecked:
        line += f", {unchecked} not checked"
    if counts['reports_failed']:
        line += f", {counts['reports_failed']} failed to convert"
    if counts['reports_with_check_errors']:
        line += f", {reports(counts['reports_with_check_errors'])} where some checks could not run"
    return line


def convert_batch(inputs, template, output_dir, *, workers=1, recursive=False, resume=False,
                  cases=None, load_source='effects', overrides=None, checks='default', progress=None, check_rows=None, input_map=None):
    """Convert files independently; append durable records and verify outputs on resume.

    The job identity includes the enabled review checks and their versions, so a new check
    version gives a new job (and a fresh review) on resume."""

    if not 1 <= workers <= 32:
        raise ValueError('workers must be between 1 and 32')
    template = Path(template).resolve(); root = Path(output_dir).resolve()
    if not template.is_file():
        raise ValueError('Template not found. Pass --template /path/to/reference.mcdx')
    if template.is_relative_to(root):
        raise ValueError('Keep the reference template outside the batch output directory.')
    plan = review_runner.prepare(checks)
    files = discover(inputs, root, recursive=recursive)
    base = input_root(inputs)
    if not files:
        raise ValueError('No .gp11t or .txt reports found. Use --recursive for subfolders.')
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / 'batch-manifest.jsonl'
    # Normalise once (tuples become lists) so identity matches the JSON saved in job.json.
    settings = json.loads(json.dumps({'cases': cases, 'load_source': load_source, 'overrides': overrides or {}, 'checks': checks}, allow_nan=False))
    if input_map is not None:settings['input_map']=input_map
    template_hash = digest(template)
    counts = {'total': len(files), 'succeeded': 0, 'skipped': 0, 'failed': 0,
              'manifest': str(manifest), 'workers': workers, 'calculation_engine': 'CalcpadCE', 'native_execution_verified': False}
    started = time.monotonic()
    table = []
    with manifest_lock(root), manifest.open('a', encoding='utf-8') as log:
        def record(result):
            result['recorded_at'] = time.time()
            try:
                line = json.dumps(result, allow_nan=False)
            except (TypeError, ValueError):
                # Never drop the manifest line over an unserializable check summary.
                result['check_summary'] = None
                line = json.dumps(result, allow_nan=False)
            log.write(line + '\n'); log.flush(); os.fsync(log.fileno())
            # The durable manifest line is written first; a bad summary only degrades the table row.
            try:
                table.append(check_row(result, base))
            except Exception:
                table.append(check_row({**result, 'check_summary': None}, base))
            counts[result['status']] += 1
            if progress:
                progress(result)

        def jobs():
            for path in files:
                try:
                    if path.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError('Raw report exceeds 16 MiB')
                    source_hash = digest(path)
                    job_id = identity(str(path), source_hash, template_hash, settings, plan.identity)
                    stem = re.sub(r'[^A-Za-z0-9_-]+', '-', path.stem).strip('-')[:80] or 'worksheet'
                    yield {'job_id': job_id, 'source': str(path), 'source_sha256': source_hash,
                           'template': str(template), 'template_sha256': template_hash, 'settings': settings,
                           'output': str(root / job_id / (stem + '.mcdx')), 'resume': resume}
                except (ValueError, OSError) as exc:
                    record({'source': str(path), 'source_sha256': None, 'status': 'failed', 'error': str(exc),
                            'native_execution_verified': False})

        schedule(jobs(), _convert_job, record, workers)
        table.sort(key=lambda r: tuple(str(r[c]) for c in CHECK_COLUMNS))
        if check_rows is not None:
            check_rows.extend(table)
        counts['checks'] = {'table': str(write_check_table(root, table)),
                            'reports_with_failures': sum(1 for r in table if r['failed'] not in ('', 0)),
                            'reports_without_checks': sum(1 for r in table if r['note'] == 'no checks found'),
                            'reports_not_recorded': sum(1 for r in table if r['note'] == 'checks not recorded for this output')}
        # Advisory only: review items never change the exit code.
        counts['review'] = review_counts(table)
        counts['review']['summary'] = format_review_summary(counts['review'])
    counts['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mcdxkit batch', description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path, help='Report files and/or directories')
    parser.add_argument('--template', type=Path, default=default_template())
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--recursive', action='store_true', help='Search all subfolders for reports')
    parser.add_argument('--workers', type=int, default=min(4, os.cpu_count() or 1), help='Parallel worker processes, 1–32 (default: up to 4)')
    parser.add_argument('--resume', action='store_true', help='Skip completed outputs after verifying identity, hashes and package structure')
    parser.add_argument('--cases', type=case_ids)
    parser.add_argument('--load-source', choices=['effects', 'reactions'], default='effects')
    parser.add_argument('--set', action='append', default=[], metavar='VARIABLE=VALUE')
    parser.add_argument('--map', action='append', default=[], metavar='VARIABLE=COMPONENT', help='Explicit template input map; repeat for each variable')
    parser.add_argument('--checks', default='default', metavar='SPEC',
                        help='Advisory review plug-ins: default, none, or a comma list such as default,my_check')
    parser.add_argument('--quiet', action='store_true', help='Suppress per-file progress on stderr')
    args = parser.parse_args(argv)
    try:
        overrides = {}
        for pair in args.set:
            key, value = pair.split('=', 1)
            if key in overrides:
                raise ValueError('Duplicate override: ' + key)
            overrides[key] = float(value)
        def progress(row):
            if not args.quiet:
                print(json.dumps({'source': row['source'], 'status': row['status'], 'error': row.get('error')}), file=sys.stderr, flush=True)
        rows = []
        summary = convert_batch(args.inputs, args.template, args.output_dir, workers=args.workers,
                                recursive=args.recursive, resume=args.resume, cases=args.cases,
                                load_source=args.load_source, overrides=overrides, checks=args.checks, progress=progress,
                                check_rows=rows,input_map=parse_input_map(args.map))
        print(format_check_table(rows), file=sys.stderr)
        print(summary['review']['summary'], file=sys.stderr)
        print('Check summary table: ' + summary['checks']['table'], file=sys.stderr, flush=True)

        print(json.dumps(summary, indent=2))
        return 1 if summary['failed'] else 0
    except (ValueError, OSError) as exc:
        print('mcdxkit: ' + str(exc), file=sys.stderr)
        return 2
