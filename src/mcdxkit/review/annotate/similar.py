"""Similar past jobs: the nearest earlier conversions in the SAME output directory.

Each report is reduced to the log10 magnitudes of its selected-case envelope (P, Vy, Vz,
My, Mz). Distance is the root-mean-square of the five log10 differences, so it is scale-free
and reads as "typically within x%": a distance of log10(1.25) means the components differ by
about 25% in the RMS sense, and identical envelopes are at distance 0. Ties are broken by the
absolute difference in selected-case count, then by name. Past jobs with different units or
a different load source are not comparable and are left out. The report under review (same
source SHA-256) is left out too.

Advisory context only: it reads the history the caller loaded for this output directory,
never the filesystem, and changes nothing.
"""
import math
from typing import Iterator, Mapping

from ..api import COMPONENTS, Annotation, Context, ReportView, frozen_mapping

# Envelope values below this (kip or kip-in) count as this, so near-zero components
# (an unloaded shear direction) do not dominate a scale-free distance.
FLOOR = 1.0
# Typical difference of at most 50% across components.
MAX_DISTANCE = math.log10(1.5)
LIMIT = 3


def _log(value: float) -> float:
    return math.log10(max(abs(value), FLOOR))


def features(envelope: Mapping[str, float]) -> tuple:
    return tuple(_log(envelope[key]) for key in COMPONENTS)


def distance(a: tuple, b: tuple) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) / len(COMPONENTS))


class SimilarJobs:
    """Up to three earlier conversions in this output directory with the closest envelopes."""
    kind = 'annotator'
    id = 'similar_jobs'
    version = '1'
    title = 'Similar past jobs'
    # Needs ctx.history; conversions have none, so batch job identities ignore this plug-in.
    history_only = True
    params = frozen_mapping({'max_distance_log10': round(MAX_DISTANCE, 6), 'floor': FLOOR, 'limit': LIMIT})

    def run(self, view: ReportView, ctx: Context) -> Iterator[Annotation]:
        cases = [c for c in view.cases if c.id in view.selected]
        past = getattr(ctx.history, 'past_jobs', None)
        if not cases or not callable(past):
            return
        envelope = {key: max(c.peak(key) for c in cases) for key in COMPONENTS}
        mine = features(envelope)
        ranked = []
        for job in past():
            if ctx.source_sha256 and job.source_sha256 == ctx.source_sha256:
                continue
            if (job.load_source != view.load_source or not job.units
                    or any(view.units.get(k) != v for k, v in job.units.items())):
                continue
            gap = distance(features(job.envelope), mine)
            if gap <= MAX_DISTANCE:
                ranked.append((gap, abs(len(job.cases) - len(cases)), job.name, job))
        ranked.sort(key=lambda row: row[:3])
        for rank, (gap, _, _, job) in enumerate(ranked[:LIMIT], 1):
            differences = {key: round(100 * (10 ** (_log(job.envelope[key]) - _log(envelope[key])) - 1), 1)
                           for key in COMPONENTS}
            yield Annotation(self.id, 'similar_job', None, None, frozen_mapping({
                'rank': rank, 'name': job.name, 'date': job.date, 'template': job.template,
                'cases': list(job.cases), 'case_count': len(job.cases), 'load_source': job.load_source,
                'distance': round(gap, 6), 'typical_difference_pct': round(100 * (10 ** gap - 1), 1),
                'difference_pct': differences, 'envelope': dict(job.envelope)}))
