# Machine learning

mcdxkit ships a case-name classifier and a set of advisory review checks, both implemented in the package itself with the standard library. They run offline, need no extra dependencies or downloads, and only advise: they never change inputs, case selections, overrides, worksheets or results.

## Case classifier

GROUP case names drive the default selection: names starting with `STR` are strength cases. Other conventions (`Strength I`, `ULS-1`, `Str1`, `Extreme Event II`, project-specific names) stop at "Case classification unavailable".

`mcdxkit inspect` (`case_suggestions`) and **Suggest cases** in the Inputs step classify each final-summary case as `strength`, `service`, `extreme`, `fatigue` or `other`. The classifier is the built-in `case_names` plug-in (`mcdxkit.review.classify.names`).

- **Stage 1, regex:** `STR`/`SER` prefixes and AASHTO long names (`Strength I`, `Service I`) are classified directly with confidence 1.
- **Stage 2, model:** multinomial naive Bayes over character 2-4 grams and whole words of the normalized name, with Laplace smoothing.
- **Training data:** a seed corpus of AASHTO LRFD limit-state names and common abbreviations, plus the engineer's own history. Every completed conversion's audit records the final-summary case names (`case_names`) and which were selected; selected names are learned as strength and unselected names as not strength, weighted 3× over the seed. History comes from a `HistoryStore` (`mcdxkit.review.history`): the browser reads its output directory, the CLI `inspect --history DIR`, the SDK any store (for example `MemoryHistory` in tests). The audits are read once, before any plug-in runs. Unreadable, dangling, oversized or malformed audits are skipped and counted: the suggestion `basis` (and `case_suggestions.trained_on`) reports `history_skipped`, so a damaged history is visible rather than silently ignored.
- **Abstention:** a case below 80% posterior probability is reported as `unknown` (suggestion `value: null`) and listed in `unresolved_case_ids`.
- **Explanation:** each case lists its confidence, the stage and the name fragments that most favored the predicted class, in a fixed order (ties broken alphabetically, independent of `PYTHONHASHSEED`).
- **Safety:** only `strength` cases are recommended, and the recommended set is checked with the same `select` rules as a manual selection. A set the template cannot use, for example one containing tension, is returned with `selection_issue` and cannot be applied in the browser. Extreme-event cases are classified but never recommended; include them deliberately when the design requires them.

`mcdxkit learn` and the `/api/suggest-cases` endpoint were removed: suggestions are part of every inspection, and **Suggest cases** reads them from `/api/inspect`.

## Review checks

**Advisory only, never applied without your click.** Review checks, case-name suggestions and dominance hints never change inputs, case selection, overrides, worksheets or results. The only way a suggestion changes anything is the explicit **Use cases …** button. Tests check that the `.mcdx`, `.cpd` and `.html` bytes and the audit (apart from `review_checks`) are identical with `checks='default'` and `checks='none'`.

`inspect` (CLI, SDK and browser), `convert` and batch audits return a `review_checks` block (schema `review-checks/1`) from the `mcdxkit.review` package. It replaces the earlier `review_flags` key, which in turn replaced the magnitude-based `anomalies` a planted-error validation found misleading (below). The browser shows flags that could change the envelope or selection as review cards on the Inputs step, where the engineer can add the case, keep it as is or leave it; other results are listed under "What was checked" (see the [CLI and server guide](cli.md#start-and-stop-the-browser)). A flag means "check the GROUP input for this case"; it is never a pass or fail, and no flag is not a verification.

```json
"review_checks": {
  "schema": "review-checks/1", "advisory": true, "load_source": "effects",
  "checks_run": [{"id": "ratio_outlier", "version": "2", "kind": "check", "source": "builtin",
                  "params": {"z_threshold": 5.63, "mad_floor": 0.02, "negligible": 0.01, "min_cases": 3},
                  "status": "ok"}],
  "skipped": [],
  "flags": [{"rule": "service_axial_above_strength", "case": 3, "component": "P",
             "message": "Case 3 SER-IX axial load 304.2 exceeds every STR case (largest 256, case 2). ...",
             "value": 304.23, "compared_to": 256.03, "unit": "kip",
             "evidence": {"service": 304.23, "strength_max": 256.03, "strength_case": 2},
             "actions": ["show_in_report", "mark_reviewed"],
             "impact": {"envelope": true, "selection": true, "magnitude": 0.188, "components": ["P"]}}],
  "annotations": [{"annotator": "dominance", "kind": "dominance", "case": null, "component": null,
                   "data": {"basis": "selected", "note": "Selected cases.", "cases": [2, 4, 5],
                            "dominated": [{"case": 4, "dominated_by": [2]}]}}],
  "suggestions": [{"classifier": "case_names", "case": 3, "kind": "limit_state", "value": "service",
                   "confidence": 1.0, "evidence": ["SER"], "basis": {"stage": "regex", "seed": 355, "history": 0,
                             "history_skipped": 0}}],
  "errors": []
}
```

- A check that does not apply to a report raises `NotApplicable` and is listed in `skipped` with its reason, for example a report with fewer than three cases or the `reactions` load source; it is not reported as clean.
- `impact` says whether acting on the flag could change the selected envelope or the case selection, with `magnitude` the relative size of that change (0.188 = 18.8%) for sorting. A UI can show prominent cards only for items with impact.
- If a plug-in raises (including `SystemExit`, `KeyboardInterrupt` or `GeneratorExit`), exceeds its time budget (5 s per report) or returns the wrong types, `errors` gets `{type: "check_error", id, version, message}`, its `checks_run` status is `error`, and inspection or conversion continues. Advisory code never fails a conversion. Python cannot kill a thread, so a timed-out plug-in is abandoned (daemon thread) and its late results are discarded; it is then disabled for the rest of the process, and later runs record `check_error` with the message `disabled after timeout`.
- `convert(..., review_decisions=[...])` records engineer decisions (`included_case`, `kept_service` or `will_fix_in_group`, with `note` and `decided_at`) under `review_checks.decisions`. A decision whose rule ran successfully must match a flag that rule raised. If the rule did not run successfully in this conversion (it raised, timed out, was skipped or is not enabled), the decision cannot be checked: it is kept with `unverified: true` instead of failing the conversion, and is not counted as reviewed in the worksheet note. Only when at least one decision exists does the worksheet gain a plain-text note on the last generated input page: "Automated input checks: N run, M items raised, reviewed by the engineer" (or "K reviewed by the engineer" when only some flags have a decision) and "Checks review the GROUP summary for consistency. They do not verify the design." It sits outside every math region, so the calculation is unchanged. Batch manifest rows and CLI `convert` output count undecided flags as `open_checks` and plug-in failures as `check_errors`. `open_checks` is `null`, not 0, when no check ran successfully (`--checks none`, or every check failed), so "nothing ran" never reads as "nothing to check".

### Plug-ins

| Id | Kind | Version | Module |
| --- | --- | --- | --- |
| `service_axial_above_strength` | check | 2 | `review/checks/consistency.py` |
| `effects_below_top` | check | 1 | `review/checks/consistency.py` |
| `duplicate_case` | check | 1 | `review/checks/consistency.py` |
| `ratio_outlier` | check | 2 | `review/checks/ratios.py` |
| `gross_magnitude` | check | 2 | `review/checks/magnitude.py` |
| `governing` | annotator | 1 | `review/annotate/cases.py` |
| `dominance` | annotator | 2 | `review/annotate/cases.py` |
| `case_names` | classifier | 1 | `review/classify/names.py` |

Built-ins are on by default (`--checks default`). Installed plug-ins are discovered from the `mcdxkit.review` entry-point group and stay off until named: `--checks default,my_check` on the CLI, `MCDXKIT_CHECKS=default,my_check` for the server (resolved once at startup). The entry-point name is the plug-in's id, and only the installed plug-ins a `--checks` value names are imported; `mcdxkit checks list` imports them all to show each id, version, kind, source and whether it is on. A plug-in that fails to import, raises while its metadata is read, declares invalid metadata, reuses an id or whose id differs from its entry-point name is listed as an error, not loaded.

A plug-in imports only `mcdxkit.review.api` (standard library only). There is no base class or Protocol to inherit: any object (or class, instantiated without arguments) that declares `kind` (`check`, `annotator` or `classifier`), `id`, `version`, `title` and `params` (its thresholds, recorded in every audit). A check's `run(view, ctx)` yields `Flag` objects whose `rule` is its id; an annotator yields `Annotation`; a classifier's `suggest(view, ctx)` yields `Suggestion`. The `ReportView` is frozen: cases with the load-source ranges used by the envelope (`minimum`, `maximum`), both numeric tables (`pile_top`, `along_pile`), pile ids and source rows, the selected case ids, the load source and units. `ctx.history` is a read-only `HistoryStore` (`case_examples()` returning `(name, selected)` pairs) or `None`. The registry validates these attributes when it loads the plug-in.

```toml
[project.entry-points."mcdxkit.review"]
pile_count = "my_package.checks:PileCount"
```

Every registered plug-in must pass the shared contract tests in `tests/test_review_plugins.py`: deterministic order and output, no mutation of the view, no file or socket I/O while running, and no flags on the clean fixtures. Each built-in check also has a planted-error fixture it must flag.

### Rules

Each rule is a physical or labelling invariant. The only tolerance is 1% for rounding of GROUP's printed values.

| Rule | Flags | Notes |
| --- | --- | --- |
| `service_axial_above_strength` | A service case whose maximum axial load exceeds every strength case | Axial load only: service lateral loads legitimately exceed strength (AASHTO LRFD Table 3.4.1-1 uses 1.2 TU in Service I against 0.5 in Strength), and comparing lateral components false-alarmed on 12% of the harder corpus. Skipped when no strength case has compression. Recognizes `STR`/`SER` prefixes and AASHTO names such as `Strength I` and `Service I` (the review's own `LIMIT_STATE` pattern). Default case selection is unchanged and still recognizes only the `STR`/`SER` prefixes. |
| `effects_below_top` | The largest magnitude along the pile is below the pile-top reaction for the same quantity | Effects load source only. Pairs (local column → effects column): `LAT. y` 1 → `SHEAR y-DIR` 4, `LAT. z` 2 → `SHEAR z-DIR` 5, `MOM y` 4 → `MOMENT y-DIR` 3, `MOM z` 5 → `MOMENT z-DIR` 2. Compared by magnitude, so it holds whether the effects table uses the same moment sign as the pile-top table (the `tests/test_pipeline.py` fixtures) or the opposite sign (the validation generator); both are tested. GROUP's own conventions are still to be confirmed on real reports. |
| `duplicate_case` | A case whose numeric tables are identical to an earlier case | Usually a copied case. |

### Statistics

- **`ratio_outlier`:** per case, log10 of the lever arms `|Mz|/|Vy|` and `|My|/|Vz|` at the pile top and, for the effects source, the along-pile/top ratios for Mz, My, Vy and Vz. A consistent model keeps these near-constant across cases, while one wrong cell moves them. Each ratio is scored with the Iglewicz-Hoaglin modified z-score `0.6745 (x - median) / MAD` across the report's cases, and the case's largest |z| is flagged above 5.63. The MAD is floored at 0.02 decades (about 5%); in the study this floor made the threshold portable between corpora (3.8 and 4.6), where a 0.001 floor moved it from 4.4 to 16.9. A ratio is undefined when its denominator is below 1% of the report's median for that quantity. Needs 3 defined values per ratio; with fewer, the score cannot exceed 0.67.
- **`gross_magnitude`:** maximum axial load (`P`) and the pile-top lateral resultant `hypot(|Vy|, |Vz|)` (`V`) more than 0.95 decades (about 9×) from the report's median, worded "possible unit slip". Values below 1% of the median, such as a gravity case's 0.02 kip shear beside 15 kip wind cases, are not compared, so a 1/1000 slip downwards is not detected. Needs at least 3 cases.
- **No kip-ft hint.** The earlier kip-ft/kip-in (12×) hint was dropped on purpose: it was wrong for all 17 of 17 moment-unit slips it flagged, because a cap-moment unit slip changes the axial spread across piles rather than making pile moments 12× larger. The lb/kip hint was right for only 57 of 143, so the wording is now only "possible unit slip".

5.63 is the 97.5th percentile of the per-report maximum ratio score, and 0.95 the 97.5th percentile of the magnitude score, on 300 clean reports of the harder synthetic corpus. The 95th percentile, 4.64, shipped first; 5.63 trades 3 points of recall for half the false alarms (table below). Both should be recalibrated on real clean reports, for example with the study's `check_my_reports.py`.

### Case dominance

The `dominance` annotation lists strength cases that can never govern: another considered case is at least as large in every envelope component (P, Vy, Vz, My, Mz). In the harder corpus 51% of strength cases were dominated (median 6 strength cases per report). The browser captions each selected case with the peaks it governs ("governs no peak" for these) and names them under the governing cases; rows are never dimmed. They stay selected; it is a hint about where to look, not a selection.

`basis` is `selected` when a case selection is in use (inspect with a resolved selection, and every conversion audit). When no selection is resolved, inspect uses `fallback_non_extreme`: all cases except names starting with `ext`, `extreme`, `eq` or `seis`. This fallback applies only to the dominance hint. It is labelled as a fallback in the API, audit and UI, and is never used as, or shown as, a case selection; `select` still fails explicitly with "Case classification unavailable". In the study, this basis overshot the true strength envelope by a median of 0% (90th percentile 71%), against 24% (400%) for all cases.

### Evidence

Planted-error validation on synthetic GROUP-format reports from a simplified pile-group model. "Harmful" means the error changes the default STR envelope by more than 5%; reports mcdxkit already refuses are excluded. Each corpus has 400 clean reports and 150 reports for each of 8 planted errors (factor typo, cap-moment unit slip, lateral force in lb, y/z axis swap, STR labelled SER, dropped load, one-cell ×10 or ÷10 edit, effects copied from another case).

Round 2 used a harder corpus: thermal load with AASHTO factors, 0.9D minimum strength cases, extreme-event cases, random combination subsets, four naming styles, batter piles, soil and stiffness scatter and 4-significant-figure output. Study results (harmful recall at the false-alarm rate on clean reports):

| Detector | Round 1 corpus | Harder corpus |
| --- | --- | --- |
| Previous `anomalies` (magnitude MAD, z ≥ 3.5) | 46% at 10.5% | 52% at 22.5% |
| Rules + ratio z | 54% at 5.0% | 55% at 4.8% |
| Rules + ratio z + gross magnitude | 64% at 5.0% | **58% at 3.5%** |
| LOF on 400 past reports (not shipped) | 68% at 7.2% | 51% at 5.8% |

The 58% at 3.5% row used a ratio threshold of 5.63 (2.5% calibration) together with the 0.95 magnitude threshold. Re-running the review checks (with the negligible-value floor) on the same corpora at both ratio thresholds gave:

| Configuration | Round 1 corpus | Harder corpus |
| --- | --- | --- |
| Ratio threshold 5.63 (**shipped**, `ratio_outlier` version 2) | 61% at 4.8% | 57% at 3.5% |
| Ratio threshold 4.64 (previously shipped, for comparison) | 61% at 4.8% | 60% at 7.0% |

On the harder corpus the 4.64 configuration catches: lateral force in lb 100%, one-cell edit 100%, copied effects 100%, STR labelled SER 63%, dropped load 19%, axis swap 12%, factor typo 10%.

### Limits

- The generators are simplified models, not GROUP. The number that matters next is the false-positive rate on real clean reports.
- A sign flip that keeps each minimum at or below its maximum leaves every magnitude, and so the envelope, unchanged; no check can see it. A flip that inverts the extrema is already refused by the parser.
- Dropped loads, axis swaps and factor typos are input-definition errors that output-only checks mostly cannot recover (no detector at about 6% false alarms got past 31% on any of them). Checking GROUP's input echo against the code's factor table would; that needs a real report to learn the echo format.
- History models (LOF), sibling-case statistics and a numeric case picker were evaluated and are deliberately not built. When case names are opaque, only the engineer can select the cases.
- The statistics compare cases within one report. Reports with fewer than three cases get only the rules.

## Governing cases

`inspect` also returns `governing`, built from the `governing` annotator: for each envelope component, the selected case that produces the peak, the runner-up case and the lead ratio. The browser shows it under each value and notes leads of 1.5× or more. With `--checks none` (or without the `governing` annotator) it is `null`.
