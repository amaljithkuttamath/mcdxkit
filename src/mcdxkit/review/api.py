"""Plug-in contract for advisory review checks. Standard library only.

This is the only module a review plug-in needs to import. A plug-in receives a frozen
ReportView of the GROUP final local summary and returns findings; it never sees files,
the worksheet, the calculator or the conversion settings, and it cannot change them.

Three kinds of plug-in, told apart by their ``kind`` attribute:

* ``check``: ``run(view, ctx)`` yields Flag objects whose ``rule`` is the check's ``id``.
* ``annotator``: ``run(view, ctx)`` yields Annotation objects (for example governing cases).
* ``classifier``: ``suggest(view, ctx)`` yields Suggestion objects; ``value=None`` abstains.

A check that does not apply to a report raises NotApplicable(reason); the report lists it
as skipped with that reason instead of presenting it as clean. Every plug-in declares an
``id``, a ``version`` string and ``params`` (its thresholds), which are recorded in the audit.
Plug-ins must be deterministic and must not read files, open sockets or keep state between
runs; the runner gives each one a time budget and records failures as ``check_error``.
"""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Tuple

COMPONENTS = ('P', 'Vy', 'Vz', 'My', 'Mz')
# Columns of GROUP's '* PILE TOP REACTIONS, LOCAL *' table.
PILE_TOP_COLUMNS = ('P', 'Vy', 'Vz', 'Mx', 'My', 'Mz')
# Columns of GROUP's '* EFFECTS FOR LATERALLY LOADED PILE *' table: deflections, moments,
# shears, soil reactions and stress, in the report's y/z directions.
ALONG_PILE_COLUMNS = ('defl_y', 'defl_z', 'Mz', 'My', 'Vy', 'Vz', 'soil_y', 'soil_z', 'stress')
DEFAULT_ACTIONS = ('show_in_report', 'mark_reviewed')
SCHEMA = 'review-checks/1'

Range = Tuple[float, float]


def frozen_mapping(value: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    """Read-only copy of a mapping (types.MappingProxyType over a private dict)."""
    return MappingProxyType(dict(value or {}))


class NotApplicable(Exception):
    """Raised by a check that cannot run on this report; the message is the reason."""


@dataclass(frozen=True)
class CaseView:
    """One final-summary case. Ranges are (minimum, maximum) as printed by GROUP.

    ``minimum``/``maximum`` hold the load source in use (what the envelope reads);
    ``pile_top`` and ``along_pile`` hold both full numeric tables. ``along_pile`` is None
    for the reactions load source, which does not read the effects table.
    """
    id: int
    name: Optional[str]
    minimum: Mapping[str, float]
    maximum: Mapping[str, float]
    pile_top: Mapping[str, Range]
    along_pile: Optional[Mapping[str, Range]]
    pile_ids: Tuple[int, ...]
    source_rows: Mapping[str, Tuple[str, ...]]

    def peak(self, component: str) -> float:
        """Envelope peak: largest axial compression for P, largest magnitude otherwise."""
        if component == 'P':
            return self.maximum['P']
        return max(abs(self.minimum[component]), abs(self.maximum[component]))

    @property
    def label(self) -> str:
        return 'Case ' + str(self.id) + (' ' + self.name if self.name else '')


@dataclass(frozen=True)
class ReportView:
    cases: Tuple[CaseView, ...]
    selected: frozenset
    load_source: str
    units: Mapping[str, str]


@dataclass(frozen=True)
class Impact:
    """What a flag could change if the engineer acts on it, so a UI can show only items that matter.

    ``envelope``: the selected envelope could change (for example, the flagged value would set a
    new peak). ``selection``: the case selection could change (for example, a mislabelled case).
    ``magnitude``: relative size of that change (0.195 = 19.5%), for sorting; 0 when unknown.
    ``components``: the envelope components it would change.
    """
    envelope: bool = False
    selection: bool = False
    magnitude: float = 0.0
    components: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Flag:
    """One item for the engineer to check. Never a pass or fail."""
    rule: str
    case: Optional[int]
    component: Optional[str]
    message: str
    value: Optional[float] = None
    compared_to: Optional[float] = None
    unit: Optional[str] = None
    evidence: Tuple[Tuple[str, Any], ...] = ()
    actions: Tuple[str, ...] = DEFAULT_ACTIONS
    impact: Impact = Impact()


@dataclass(frozen=True)
class Annotation:
    """Context shown beside the inputs, such as the governing case of a component."""
    annotator: str
    kind: str
    case: Optional[int]
    component: Optional[str]
    data: Mapping[str, Any] = field(default_factory=frozen_mapping)


@dataclass(frozen=True)
class Suggestion:
    """A classifier's view of one case. ``value`` None means the classifier abstained."""
    classifier: str
    case: int
    kind: str
    value: Optional[str]
    confidence: float
    evidence: Tuple[str, ...] = ()
    basis: Mapping[str, Any] = field(default_factory=frozen_mapping)


@dataclass(frozen=True)
class PastJob:
    """One completed conversion in the same output directory, as its audit recorded it.

    ``name`` is the worksheet path relative to that output directory (never absolute);
    ``date`` is the audit's modification date (UTC, YYYY-MM-DD); ``envelope`` holds the
    selected-case peaks per component; ``template`` is the template file name, when recorded.
    """
    name: str
    date: str
    envelope: Mapping[str, float]
    cases: Tuple[int, ...]
    load_source: str
    units: Mapping[str, str]
    source_sha256: Optional[str] = None
    template: Optional[str] = None


class HistoryStore(Protocol):
    """Read-only past decisions: (case name, was it selected) from completed conversions.
    A store may also have ``skipped``, the number of past audits it could not read, and
    ``past_jobs()``, the PastJob records of the same output directory."""
    def case_examples(self) -> Tuple[Tuple[str, bool], ...]: ...


@dataclass(frozen=True)
class Context:
    """``source_sha256`` identifies the report under review, when the caller knows it."""
    history: Optional[HistoryStore] = None
    source_sha256: Optional[str] = None
