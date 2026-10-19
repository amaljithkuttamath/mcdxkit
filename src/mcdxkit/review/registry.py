"""Built-in and installed review plug-ins, and which of them are enabled.

Built-ins are on by default. Installed plug-ins are discovered from the ``mcdxkit.review``
entry-point group and are off unless enabled by id: ``--checks default,my_check`` on the CLI,
``MCDXKIT_CHECKS`` for the server. The entry-point name is the plug-in's id, so only the
installed plug-ins a spec names are imported. A plug-in that fails to import or declares a
duplicate or invalid id is recorded as an error, never a crash and never silently enabled.
"""
import re
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Collection, List, Optional, Tuple

from .annotate.cases import Dominance, GoverningCases
from .annotate.similar import SimilarJobs
from .checks.consistency import DuplicateCase, EffectsBelowTop, ServiceAxialAboveStrength
from .checks.magnitude import GrossMagnitude
from .checks.ratios import RatioOutlier
from .classify.names import CaseNames

GROUP = 'mcdxkit.review'
KINDS = {'check': 'run', 'annotator': 'run', 'classifier': 'suggest'}
ID = re.compile(r'[a-z][a-z0-9_]{0,63}')


def builtins() -> Tuple[Any, ...]:
    """Built-in plug-ins in their fixed run order."""
    return (ServiceAxialAboveStrength(), EffectsBelowTop(), DuplicateCase(), RatioOutlier(), GrossMagnitude(),
            GoverningCases(), Dominance(), SimilarJobs(), CaseNames())


@dataclass(frozen=True)
class Entry:
    plugin: Any
    source: str  # 'builtin' or 'entry-point'

    @property
    def id(self) -> str:
        return self.plugin.id


@dataclass(frozen=True)
class Catalog:
    entries: Tuple[Entry, ...]
    # (id, message) for installed plug-ins that could not be registered.
    errors: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    def ids(self, source: Optional[str] = None) -> List[str]:
        return [e.id for e in self.entries if source is None or e.source == source]


def _problem(plugin: Any) -> Optional[str]:
    kind = getattr(plugin, 'kind', None)
    if kind not in KINDS:
        return 'kind must be one of ' + ', '.join(KINDS)
    if not isinstance(getattr(plugin, 'id', None), str) or not ID.fullmatch(plugin.id):
        return 'id must be lower-case letters, digits and underscores'
    if not isinstance(getattr(plugin, 'version', None), str) or not plugin.version:
        return 'version must be a non-empty string'
    if not callable(getattr(plugin, KINDS[kind], None)):
        return f'a {kind} needs a {KINDS[kind]}(view, ctx) method'
    params = getattr(plugin, 'params', None)
    if params is None or not all(isinstance(k, str) for k in dict(params)):
        return 'params must be a mapping with string keys'
    return None


def _entry_points():
    return metadata.entry_points(group=GROUP)


def discover(names: Optional[Collection[str]] = None) -> Catalog:
    """Built-ins plus installed plug-ins. ``names`` limits which entry points are imported
    (by entry-point name); None imports every installed plug-in, for ``checks list``."""
    entries = [Entry(p, 'builtin') for p in builtins()]
    errors = []
    seen = {e.id for e in entries}
    for point in sorted(_entry_points(), key=lambda p: p.name):
        if names is not None and point.name not in names:
            continue
        try:
            loaded = point.load()
            plugin = loaded() if isinstance(loaded, type) else loaded
            # Metadata is read inside the try: a plug-in whose attributes raise is an error too.
            problem = _problem(plugin)
            if problem is None and plugin.id in seen:
                problem = f'duplicate id {plugin.id!r}'
            if problem is None and plugin.id != point.name:
                problem = f'id {plugin.id!r} must match the entry-point name {point.name!r}'
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - plug-in import must not terminate the host
            errors.append((point.name, f'failed to load: {type(exc).__name__}: {exc}'))
            continue
        if problem is not None:
            errors.append((point.name, 'rejected: ' + problem))
            continue
        seen.add(plugin.id)
        entries.append(Entry(plugin, 'entry-point'))
    return Catalog(tuple(entries), tuple(errors))


def parse_spec(spec: Optional[str]) -> Tuple[str, ...]:
    """Validate a --checks value: 'default', 'none', or a comma list of ids and 'default'."""
    if spec is None:
        spec = 'default'
    if not isinstance(spec, str):
        raise ValueError('checks must be a string such as "default", "none" or "default,my_check"')
    words = tuple(w.strip() for w in spec.split(','))
    if not all(words):
        raise ValueError(f'Empty name in checks {spec!r}; use "default", "none" or ids separated by commas')
    if 'none' in words and len(words) > 1:
        raise ValueError('checks "none" cannot be combined with other names')
    if len(set(words)) != len(words):
        raise ValueError(f'Repeated name in checks {spec!r}')
    return words


def select(spec: Optional[str], catalog: Optional[Catalog] = None) -> Tuple[Tuple[Entry, ...], Tuple[Tuple[str, str], ...]]:
    """Enabled entries in run order (built-ins, then installed by id), and load errors for requested ids.

    Raises ValueError for an unknown id so a mistyped check is never silently skipped.
    """
    words = parse_spec(spec)
    if words == ('none',):
        return (), ()
    catalog = catalog or discover(words)
    broken = dict(catalog.errors)
    known = set(catalog.ids())
    wanted = set()
    for word in words:
        if word == 'default':
            wanted.update(catalog.ids('builtin'))
        elif word in known:
            wanted.add(word)
        elif word not in broken:
            raise ValueError(f'Unknown review check {word!r}. Run "mcdxkit checks list" for the available ids.')
    errors = tuple((name, message) for name, message in catalog.errors if name in words)
    return tuple(e for e in catalog.entries if e.id in wanted), errors


def listing(spec: Optional[str] = None) -> List[dict]:
    """Rows for ``mcdxkit checks list``: id, version, kind, source and whether ``spec`` enables it."""
    catalog = discover()
    enabled = {e.id for e in select(spec, catalog)[0]}
    rows = [{'id': e.id, 'version': e.plugin.version, 'kind': e.plugin.kind, 'source': e.source,
             'enabled': e.id in enabled, 'title': getattr(e.plugin, 'title', '')} for e in catalog.entries]
    rows += [{'id': name, 'version': None, 'kind': None, 'source': 'entry-point', 'enabled': False, 'error': message}
             for name, message in catalog.errors]
    return rows
