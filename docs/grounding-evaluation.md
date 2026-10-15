# Ask grounding evaluation — 15 October 2026

The fixed 24-case benchmark passed with `cohere/north-mini-code:free`, `knowledge/9`, and `grounding/5` when each question was tested independently, matching separate Ask turns. All **10 supported statements were published**, all **14 unsupported statements were withheld**, and there were **zero provider errors**. This is a bounded claim-publication test, not a retrieval benchmark, proof of factual accuracy, or engineering approval.

The [public fixtures](../tests/fixtures/grounding/cases-v1.json) cover values, units, signs, scope, minimum/maximum direction, negation, conditions, approval claims, unrelated evidence, and conflicting sources. Expected outcomes were fixed before the live runs; the fixture SHA-256 is `dfec79594debb3c59bbeed94fee116425c92c0cf0734788d96cebbb91f9506e2`. The acceptance rule requires every positive and negative case to pass. Provider errors and malformed/missing checks fail; refusing everything cannot pass.

## Results and failures

| Protocol / version | Supported published | Unsupported published | Outcome |
| --- | ---: | ---: | --- |
| 24 unrelated questions pooled in one request, grounding/2 | 5 / 10 | 1 / 14 | Failed |
| Same pooled stress test, grounding/3 | 8 / 10 | 0 / 14 | Failed |
| One question per request, grounding/3 | 10 / 10 | 0 / 14 | Passed |
| One question per request, grounding/4 | 10 / 10 | 0 / 14 | Passed |
| One question per request, grounding/5 | 10 / 10 | 0 / 14 | Passed |

Grounding/5 additionally admits longer exact supporting sentences with per-statement and whole-report character budgets. It passed a fresh 24-case isolated run. Long-sentence and oversized-quote offline regressions cover this format change separately.

Grounding/4 increases the bounded supporting-quote allowance for compound statements and records safe failure codes. It passed a fresh isolated run with the same frozen fixtures.

The isolated protocol uses the same questions, expected outcomes, prompt and publication gate. Pooling independent questions was an extra stress condition; it is not the production request shape. Both failed runs remain in the [machine-readable history](evaluations/2026-10-15-grounding.json). Gemma comparisons were rate-limited; the Nemotron and Inkling comparisons failed at the provider/model boundary. They receive no accuracy score and are not recommendations.

There was no automatic retry or provider fallback. These are single recorded runs, not a reliability estimate. Model availability and behavior can change. The app's selected model was not changed by the benchmark.

## Reproduce

Install the development environment and configure an OpenRouter key privately using the normal app settings or environment. The test sends only the checked-in synthetic fixture text, never project documents.

```bash
python tests/evaluate_grounding.py --live \
  --workspace /absolute/path/to/output/.documents \
  --model cohere/north-mini-code:free \
  --output /new/path/grounding-result.json
```

The default is one question per request, attempted once, with a minimum four-second interval. `--batch-size 24` reproduces the pooled stress test. Existing output files are never overwritten. Offline tests in `test_grounding.py` exercise malicious or malformed verifier output; `test_grounding_benchmark.py` checks isolation and scoring, including that a provider failure cannot count as a successful rejection.

## Limits

The gate reuses the answering model, so correlated mistakes can survive. The mechanical checks cover a fixed set of literal quantities and qualifiers; they do not establish semantic entailment for arbitrary prose. Synthetic statements do not measure retrieval recall, PDF parsing, drawing interpretation, revision precedence or whether the correct engineering standard applies. Those require separate project-specific evaluations and human review. See the [audit contract](answer-audit.md) for what is recorded and what each check means.
