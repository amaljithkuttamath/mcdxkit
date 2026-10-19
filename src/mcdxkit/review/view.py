"""Build the frozen ReportView that plug-ins read from the plain dict group_report.parse() returns.

The review package takes the parsed dict rather than importing the parser, so plug-ins
depend only on review.api. Units are those the parser enforces: kip and kip-in.
"""
from typing import Any, Iterable, Mapping, Optional

from .api import (ALONG_PILE_COLUMNS, COMPONENTS, PILE_TOP_COLUMNS, CaseView, ReportView,
                  frozen_mapping)

UNITS = frozen_mapping({'force': 'kip', 'moment': 'kip-in', 'P': 'kip', 'Vy': 'kip', 'Vz': 'kip',
                        'My': 'kip-in', 'Mz': 'kip-in', 'V': 'kip'})
# The unit system recorded in conversion audits, so history compares like with like.
AUDIT_UNITS = frozen_mapping({key: UNITS[key] for key in ('force', 'moment')})


def _ranges(rows, names):
    minimum, maximum = rows
    if len(minimum) != len(names) or len(maximum) != len(names):
        raise ValueError('Unexpected table width for the review view')
    return frozen_mapping({name: (float(low), float(high))
                           for name, low, high in zip(names, minimum, maximum, strict=True)})


def case_view(case: Mapping[str, Any]) -> CaseView:
    pairs = case['pairs']
    tables = case['tables']
    effects = tables.get('effects')
    return CaseView(
        id=int(case['id']), name=case['name'],
        minimum=frozen_mapping({key: float(pairs[key][0]) for key in COMPONENTS}),
        maximum=frozen_mapping({key: float(pairs[key][1]) for key in COMPONENTS}),
        pile_top=_ranges(tables['local'], PILE_TOP_COLUMNS),
        along_pile=_ranges(effects, ALONG_PILE_COLUMNS) if effects else None,
        pile_ids=tuple(case['pile_ids']),
        source_rows=frozen_mapping({key: tuple(rows) for key, rows in case['source_rows'].items()}))


def build(parsed: Mapping[str, Any], selected: Optional[Iterable[int]] = None) -> ReportView:
    """``selected`` is the case selection in use, if any; empty means unresolved."""
    return ReportView(cases=tuple(case_view(c) for c in parsed['cases']),
                      selected=frozenset(selected or ()), load_source=parsed['load_source'], units=UNITS)
