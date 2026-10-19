"""HistoryStore adapters: past decisions the case-name classifier learns from, and the past
jobs the similar-jobs annotator compares against.

AuditHistory reads completed conversion audits in ONE output directory, once, when it is
created, so plug-ins never touch the filesystem while they run. It never looks outside that
directory: the workspace is not tenant-isolated, so history must not cross output directories.
MemoryHistory is for tests and for callers with their own store.
"""
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from .api import COMPONENTS, PastJob, frozen_mapping
from .view import AUDIT_UNITS

Example = Tuple[str, bool]
MAX_AUDIT_BYTES = 2 * 1024 * 1024
# Audits from before units were recorded were parsed under these (the parser enforces them).
DEFAULT_UNITS = AUDIT_UNITS
SHA256 = re.compile(r'[0-9a-f]{64}')


class MemoryHistory:
    def __init__(self, examples: Iterable[Example] = (), skipped: int = 0, jobs: Iterable[PastJob] = ()):
        self._examples = tuple((str(name), bool(selected)) for name, selected in examples)
        self._jobs = tuple(jobs)
        # Audits that could not be read; reported in the classifier's basis, never hidden.
        self.skipped = skipped

    def case_examples(self) -> Tuple[Example, ...]:
        return self._examples

    def past_jobs(self) -> Tuple[PastJob, ...]:
        return self._jobs


def newest(paths: Iterable[Path], limit: int) -> Tuple[List[Path], int]:
    """The ``limit`` most recently modified paths, and how many could not be stat'ed
    (dangling symlinks, files removed or unreadable meanwhile); those are skipped."""
    found, skipped = _newest_stats(paths, limit)
    return [path for path, _ in found], skipped


def _newest_stats(paths: Iterable[Path], limit: int) -> Tuple[List[Tuple[Path, os.stat_result]], int]:
    dated, skipped = [], 0
    for path in paths:
        try:
            dated.append((path, path.stat()))
        except OSError:
            skipped += 1
    dated.sort(key=lambda item: item[1].st_mtime, reverse=True)
    return dated[:limit], skipped


def signature(output_dir) -> Optional[Tuple[int, int, int]]:
    """A cheap freshness signal for ``output_dir``: its own mtime, and the number and newest
    mtime of the folders below it (symlinks and ``_source`` snapshots are not entered). Adding,
    replacing or removing an audit at any depth changes its folder's mtime, so nested batch
    output folders are covered too. Directory listings only, no file reads; None when the
    directory cannot be read."""
    try:
        own = os.stat(output_dir).st_mtime_ns
        with os.scandir(output_dir) as entries:
            pending = list(entries)
    except OSError:
        return None
    count = newest_child = 0
    while pending:
        entry = pending.pop()
        try:
            if not entry.is_dir(follow_symlinks=False) or entry.name == '_source':
                continue
            newest_child = max(newest_child, entry.stat(follow_symlinks=False).st_mtime_ns)
            count += 1
            # A folder that cannot be listed is still counted by its own mtime.
            with os.scandir(entry.path) as entries:
                pending.extend(entries)
        except OSError:
            continue
    return own, count, newest_child


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('not a finite number')
    return float(value)


def _job(audit: Any, path: Path, root: Path, mtime: float) -> Optional[PastJob]:
    """A PastJob from one audit's JSON, or None when the audit does not record what is needed.
    The name is relative to ``root``; recorded absolute paths in the audit are never used."""
    try:
        envelope = audit['envelope']
        values = {key: _number(envelope[key]) for key in COMPONENTS}
        cases = tuple(audit['cases'])
        if not cases or len(cases) > 1000 or not all(type(i) is int for i in cases):
            return None
        load_source = audit['load_source']
        if load_source not in ('effects', 'reactions'):
            return None
        units = audit.get('units')
        if units is None:
            units = DEFAULT_UNITS
        elif not isinstance(units, dict) or not all(isinstance(units.get(k), str) for k in DEFAULT_UNITS):
            return None
        digest = audit.get('source_sha256')
        template = audit.get('template_name')
        if not isinstance(template, str) or not template or len(template) > 200 or any(
                c in template for c in '/\\') or any(ord(c) < 32 for c in template):
            template = None
        name = path.with_name(path.name[:-len('.audit.json')] + '.mcdx').relative_to(root).as_posix()
        date = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime('%Y-%m-%d')
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError):
        return None
    return PastJob(name=name[:300], date=date, envelope=frozen_mapping(values), cases=cases,
                   load_source=load_source, units=frozen_mapping({k: units[k][:20] for k in DEFAULT_UNITS}),
                   source_sha256=digest if isinstance(digest, str) and SHA256.fullmatch(digest) else None,
                   template=template)


def read_audits(output_dir, limit: int = 500) -> Tuple[Tuple[Example, ...], Tuple[PastJob, ...], int]:
    """Case-name examples and past jobs from the newest ``limit`` audits under ``output_dir``,
    and the number of audits skipped for the examples.

    Unreadable, dangling, oversized, symlinked, outside-the-directory or malformed audits are
    skipped (and counted): history is advisory, and one damaged file must not stop inspection.
    An audit without a usable envelope still teaches case names, and the reverse.
    """
    examples: List[Example] = []
    jobs: List[PastJob] = []
    root = Path(output_dir)
    if not root.is_dir():
        return (), (), 0
    try:
        base = root.resolve()
    except OSError:
        return (), (), 0
    # _source holds snapshots of a conversion's inputs, never audits of its own.
    candidates = (p for p in root.rglob('*.audit.json') if '_source' not in p.relative_to(root).parts)
    audits, skipped = _newest_stats(candidates, limit)
    for path, stat in audits:
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(base):
                skipped += 1
                continue
            if stat.st_size > MAX_AUDIT_BYTES:
                skipped += 1
                continue
            audit = json.loads(path.read_text(encoding='utf-8'))
            job = _job(audit, path, root, stat.st_mtime) if isinstance(audit, dict) else None
            if job is not None:
                jobs.append(job)
            selected = {str(i) for i in audit['cases']}
            # case_names lists every final-summary case (audits from gdcalc 0.2.0 and earlier
            # only record the selected ones, which still teach the strength class).
            names = audit.get('case_names') or {str(c['id']): c.get('name') for c in audit['source_cases']}
            found = [(name, ident in selected) for ident, name in names.items() if isinstance(name, str)]
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError):
            skipped += 1
            continue
        examples.extend(found)
    return tuple(examples), tuple(jobs), skipped


def audit_examples(output_dir, limit: int = 500) -> Tuple[Tuple[Example, ...], int]:
    """(case name, selected) from the newest ``limit`` audits under ``output_dir``, and the
    number of audits skipped."""
    examples, _, skipped = read_audits(output_dir, limit)
    return examples, skipped


class AuditHistory(MemoryHistory):
    """Case-name decisions and past jobs from the completed conversions in one output directory."""
    def __init__(self, output_dir, limit: int = 500):
        examples, jobs, skipped = read_audits(output_dir, limit)
        super().__init__(examples, skipped, jobs)
