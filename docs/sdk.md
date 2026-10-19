# Python SDK

The `mcdxkit` Python distribution contains the SDK, `mcdxkit` command and browser server. The supported import surface is `mcdxkit.engine`. The CLI and server call this same engine; no agent host is required.

Install the published SDK from PyPI in an isolated Python environment:

```bash
python -m pip install mcdxkit==0.4.0
```

For source development, install a clone with `python -m pip install -e .`. Installation includes web assets and calculator bridge source, but not a compiled Calcpad runtime, Mathcad, engineering standards or a private template. Run `mcdxkit setup-engine` once with Git and .NET 10 SDK available, or point `MCDXKIT_CALCPAD` at a compatible existing calculator. Conversion requires the [supported template](../references/template-contract.md).

## Inspect, convert and validate

```python
from pathlib import Path
from mcdxkit.engine import inspect_report, convert, validate

report = Path("inputs/report.gp11t")
info = inspect_report(report)
if info["selection_required"]:
    raise ValueError(info["selection_required"])

result = convert(
    report,
    Path("inputs/reference.mcdx"),
    Path("results/design.mcdx"),
    cases=info["selected_cases"],
    load_source="effects",
)
package_checks = validate(result["output"])
print(result["calculated_worksheet"])
```

| Function | Contract |
| --- | --- |
| `inspect_template(template, *, input_map=None)` | Discovers literal scalar inputs, ambiguity, dimensions and the current map without executing equations. |
| `inspect_report(report, *, cases=None, load_source='effects', checks='default', history=None, template=None, input_map=None, overrides=None)` | Returns cases, selected IDs, unresolved selection reason, envelope, `governing` case per component, `case_suggestions`, the advisory `review_checks` block and largest observed pile ID. `history` is an optional `mcdxkit.review.history.AuditHistory(output_dir)` (or any `HistoryStore`) the case-name classifier learns from; audits it cannot read are counted in `case_suggestions.trained_on.history_skipped`. Does not execute the calculator. Explicit invalid selection or an unknown check id raises `ValueError`. |
| `convert(report, template, output, *, cases=None, load_source='effects', title=None, overrides=None, checks='default', review_decisions=None, input_map=None)` | Calculates and returns the audit dictionary with final artifact paths, source hash, selection/envelope, `review_checks`, calculation evidence and `checks`/`check_summary`. Publishes `.mcdx`, `.cpd`, `.html`, `.audit.json`, with audit last. |

| `summarize_report(report, *, cases=None, load_source='effects', origin=None)` | Returns one row dict per final-summary case with per-case loads (kip, kip-in), `selected`, `governs` and `source_sha256`. Unresolved or invalid selection raises `ValueError`. |
| `summarize_audit(audit, *, origin=None)` | Same rows for a completed conversion, re-parsed from its `_source` snapshot after verifying the audited hash, with the audited cases and load basis. |
| `summary_key(rows)` | Identity of one summarized input (source hash, load basis, selected cases), used by the CLI and server to skip identical duplicates. |
| `summary_csv(rows)` | Renders rows as CSV text; columns are listed in [the summary guide](cli.md#summary-csv-across-reports). It does not write files. |
| `validate(worksheet)` | Returns package integrity results. Does not execute Prime or prove engineering compliance. |

`checks` is `'default'` (built-in checks, annotators and the case-name classifier), `'none'`, or a comma list of ids (`'default,my_check'`); see `mcdxkit checks list`. It may also be a plan from `mcdxkit.review.runner.prepare(spec)`, so a long-running caller resolves (and imports installed plug-ins) once. `runner.open_flags(block)` counts flags without a decision (`None` when no check ran successfully) and `runner.check_errors(block)` counts `errors`. `review_checks` has schema `review-checks/1`: `{schema, advisory: true, load_source, checks_run, skipped, flags, annotations, suggestions, errors}`. `checks_run` lists `{id, version, kind, source, params, status}` for every plug-in that ran, with its thresholds in `params`. Each flag is `{rule, case, component, message, value, compared_to, unit, evidence, actions, impact}`; `impact` is `{envelope, selection, magnitude, components}`: whether acting on the flag could change the selected envelope or the case selection, and the relative size of that change for sorting. Annotations of kind `governing` and `dominance` carry the governing case per component and the cases that cannot govern (on the selected cases or, when none is resolved, on a labelled review-only fallback `basis: fallback_non_extreme` that is never a selection). `skipped` lists checks that do not apply to this report and why; `errors` lists `check_error` entries for plug-ins that failed or timed out. An empty `flags` list means no flag was raised by the checks that ran, not that the report was verified. Flags never change selection, envelope or results.

`review_decisions` records the engineer's decisions on raised flags in the audit (`review_checks.decisions`): a list of `{rule, case, component, decision, note, decided_at}` where `decision` is `included_case`, `kept_service` or `will_fix_in_group`, `note` is optional text and `decided_at` is an ISO 8601 date and time (`YYYY-MM-DDTHH:MM[:SS[.fff[fff]]]` with an optional `Z` or `±HH:MM`; a date alone is rejected). `rule` and `decision` are text, `case` an integer or `null`, `component` text or `null`. A decision whose rule ran successfully must match a flag it raised; when the rule failed, timed out, was skipped or is not enabled, the decision is kept with `unverified: true`. Malformed decisions raise `ValueError` before any output is written. When at least one decision exists, the generated worksheet gains one plain-text note outside any math region ("Automated input checks: N run, M items raised, reviewed by the engineer" and "Checks review the GROUP summary for consistency. They do not verify the design."); without decisions the worksheet is unchanged. Decisions never change inputs, cases or results. See [review checks](machine-learning.md#review-checks) for writing a plug-in.

`inspect_report(..., template=reference, input_map={"AxialCustom": "P", "TransverseCustom": "Vz"})` adds `template_inputs`, including the matched selected envelope, and `template_validation` (`ready`, `blocked`, `needs_mapping`, `needs_cases`). The same mapping, unit, layout and pile-capacity checks are shared with generation; `overrides` participates without modifying the template or invoking the calculator. Pass the same `input_map` to `convert`; `None` uses the fixed-head preset, while an explicit map replaces it. Empty maps cannot convert. Custom mappings require reviewed semantics and compatible force/moment units. Unmapped values and downstream formulas stay in the template. Summary-only reports require explicit `cases`; the source pages grow with the number selected. The batch API `mcdxkit.batch.convert_batch` accepts the same `input_map` and includes it in resume identity.

Paths accept strings or `pathlib.Path`. `cases` is a list of integer case IDs, not the CLI's comma-separated string. `load_source` is `effects` or `reactions`; see [load semantics](cli.md). `overrides` is a mapping of supported literal template input names to numeric values in that input's existing units, subject to the template contract. Never derive geometry from a desired pass result.

Inputs, templates and existing outputs are not overwritten. Each concurrent conversion must use a distinct output basename. Output storage must support hard links. Source parsing and invalid inputs generally raise `ValueError`; filesystem and calculator failures propagate their corresponding exceptions. Catch exceptions at the application boundary and record a failed job; do not replace results with zero or stale files. A successful conversion can contain failing engineering checks; read `result["check_summary"]["failed"]` and treat `status == "no checks found"` as unknown, not passed.

The SDK is pre-1.0. Pin the package version in integrations; treat other module functions as internal and audit dictionaries as extensible (ignore unfamiliar keys). Do not infer engineering approval from successful return or Prime-native execution from a Calcpad calculation. Large-scale users can use the [batch CLI](cli.md#batch-conversion) for bounded workers, manifests and verified resume.

## Distribution checks

CI tests the installed package with synthetic reports/templates and the real calculator. Release CI installs the actual wheel and runs the full suite before uploading that same distribution to PyPI. See [release setup](releasing.md) and [testing standards](testing.md).
