# CLI and local server

All commands use the same conversion engine. No Codex or Mathcad installation is needed for Calcpad execution. A compatible private Mathcad template is required for conversion; the public repository does not include one.

## Install

For command-line use:

```bash
uv tool install mcdxkit==0.4.0
mcdxkit --version
mcdxkit setup-engine
```

The one-time engine build needs Git and the .NET 10 SDK. The resulting executable is self-contained. `setup-engine` deliberately refuses to replace an existing engine directory. Reuse a working engine; for an upgrade, build into a new directory and set `MCDXKIT_ENGINE_DIR` rather than deleting the old installation blindly. For editable development use [CONTRIBUTING.md](../CONTRIBUTING.md).

## Inspect before converting

```bash
mcdxkit inspect /path/to/report.gp11t
mcdxkit inspect /path/to/report.gp11t --cases 1,3,7
mcdxkit inspect /path/to/report.gp11t --history /path/to/earlier-results   # learn case naming, list similar past jobs
mcdxkit inspect /path/to/report.gp11t --checks none                        # no review checks
```

Inspection returns cases, selected cases, load basis and an envelope where selection is resolved. Default selection recognizes `STR` case names and excludes service cases. If classification is unresolved, obtain the intended case IDs; do not guess. `case_suggestions` classifies every case name (strength, service, extreme, fatigue, other or unknown) with confidence and evidence and lists `recommended_cases`; it is advisory, so pass reviewed IDs to `--cases` yourself. `--history OUTPUT_DIR` (inspect only) learns naming conventions from completed conversion audits in that directory and lists up to three of its earlier conversions with the closest envelopes in `similar_jobs` (derived from the `similar_job` annotations in `review_checks`) ([similar past jobs](machine-learning.md#similar-past-jobs)). Reports must be GROUP text in the supported kip/in convention, not Excel binaries.

### Reports that start at the summary

The preceding report header is optional. A `.gp11t` or `.txt` file may begin directly with `SUMMARY FOR LOAD CASES AND COMBINATIONS`, followed by the usual `LOAD CASE:` sections and unchanged tables. The parser finds the last summary and reads each case up to the next case marker. It still requires the table labels, directions and units; this does not support unlabelled numeric rows.

Inspection identifies these files with `summary_only: true`. Without the earlier case names, strength/service classification is unavailable: choose the intended cases in the browser's expanded selector, or provide `--cases` in the CLI. Case numbers and loads are read from the summary; no names or classifications are inferred from case order.

```bash
mcdxkit inspect summary.txt
# Example only: use the case IDs appropriate to this report.
mcdxkit convert summary.txt --cases 1,3,7 --template reference.mcdx --output results/design.mcdx
```

Inspection, conversion audits and batch audits include an advisory `review_checks` block (schema `review-checks/1`, replacing the earlier `review_flags`): flags, annotations such as the governing case, case-name suggestions, errors from checks that could not run, and `checks_run` with each check's version and thresholds. A flag asks you to check the GROUP input and never changes the selection or result. See [review checks](machine-learning.md#review-checks).

## Discover and map template inputs

```bash
mcdxkit inspect reference.mcdx
mcdxkit inspect summary.txt --template reference.mcdx --cases 1-6 \
  --map AxialCustom=P --map TransverseCustom=Vz
mcdxkit convert summary.txt --template reference.mcdx --cases 1-6 \
  --map AxialCustom=P --map TransverseCustom=Vz --output results/design.mcdx
```

These variable names are examples; use names detected in your template. `--map` replaces the fixed-head preset completely and is also accepted by `batch`. Supported report components are `P`, `Vy`, `Vz`, `My`, `Mz`. Unmapped inputs keep their template values; downstream formulas calculate from the new mapped inputs. Unknown, duplicate, derived or dimensionally incompatible targets fail. A mapped variable cannot also be changed with `--set`.

Start with `mcdxkit inspect report.txt --template reference.mcdx` to validate the pair before generation. The `template_validation` result distinguishes `ready`, `blocked`, `needs_mapping` and `needs_cases`, with errors and warnings. `--set` overrides also participate in this preflight. These are input compatibility checks; equation translation and execution are checked during preview/conversion.

In the browser, select a template, open **Inputs**, choose the report source beside each detected input, and select the intended cases. The template is checked against each raw report before preview. Changing template, mapping, cases, load basis or overrides requires fresh validation; blocked pairs cannot preview. Review the Changes view before creating outputs. Batch resume includes the mapping in job identity. See the [template contract](../references/template-contract.md) for layout, units and geometry limits.

## Review checks

```bash
mcdxkit checks list                              # id, version, kind, source, on/off
mcdxkit checks list --checks default,my_check    # on/off for a --checks value; --json for rows
```

`--checks` on `inspect`, `convert` and `batch` takes `default` (the built-in checks, annotators and case-name classifier), `none`, or a comma list of ids such as `default,my_check` or `ratio_outlier`. An unknown id is an error (exit 2). Installed plug-ins (Python entry points in the `mcdxkit.review` group, named by their id) are listed but stay off until named; only named plug-ins are imported. A plug-in that fails to load, raises or exceeds its time budget is reported under `review_checks.errors` as `check_error`; the inspection or conversion still completes. A plug-in that timed out is disabled for the rest of the process (`check_error: disabled after timeout`). `convert` prints `open_checks` (flags without a decision; `null` when no check ran successfully, for example with `--checks none`) and `check_errors` (plug-ins that failed). With no review decisions, `.mcdx`, `.cpd` and `.html` outputs are byte-identical with `--checks none` and `--checks default`.

Default loads come from the last summary: axial compression from local pile-top reactions, shears/moments from local pile effects. Use `--load-source reactions` only when local top reactions are the intended basis for all components.

## Convert one report

```bash
mcdxkit convert /path/to/report.gp11t \
  --template /path/to/reference.mcdx \
  --output /path/to/new-results/design.mcdx

# Explicit case selection and an already-reviewed literal input change:
mcdxkit convert /path/to/report.gp11t \
  --template /path/to/reference.mcdx \
  --cases 1,3,7 --set n_z=11 --set n_y=1 \
  --output /path/to/new-results/design-revised.mcdx
```

The count values above illustrate syntax; they are not inferred geometry. Read the [template contract](../references/template-contract.md) before applying overrides. `--title` changes the generated source-page title, not inherited headers.

Four files are published together, with audit JSON written last as the completion marker:

| File | Meaning |
| --- | --- |
| `.mcdx` | Native input/formula package; Prime execution unverified |
| `.cpd` | Executable translated Calcpad worksheet |
| `.html` | Fresh Calcpad result snapshot |
| `.audit.json` | Source selection, hashes, assumptions, calculation evidence and [check results](calculation.md#check-results) |

Existing artifacts are never overwritten. Choose a new basename for a new run. A successfully calculated worksheet may contain failing design checks; `convert` prints `check_summary` (failed checks and governing D/C, or `no checks found`).

## Validate a package

```bash
mcdxkit validate /path/to/design.mcdx
```

This checks ZIP/XML relationships and package integrity. It does not execute Mathcad, validate all PTC schemas or approve the design. Use `mcdxkit <command> --help` for authoritative flags. CLI commands print machine-readable results; inspect process exit status before consuming outputs.

## Batch conversion

```bash
mcdxkit batch /path/to/reports --recursive \
  --template /path/to/reference.mcdx \
  --output-dir /path/to/batch-results --workers 4

mcdxkit batch /path/to/reports --recursive \
  --template /path/to/reference.mcdx \
  --output-dir /path/to/batch-results --workers 4 --resume
```

Explicit files and multiple folders are also accepted. Options apply to every input in the invocation: group compatible reports/templates together. Keep source/template files outside the output directory. Worker count is 1–32; default is up to four. Progress is JSON Lines on stderr; stdout is the summary. `--quiet` suppresses progress.

After the run, a per-report check table (status, check count, failures, governing D/C and check) is printed to stderr and written to a new `check-summary-<timestamp>.csv` in the output directory; earlier tables are kept. Reports are identified by their path relative to the common input folder, and rows are sorted. The stdout summary's `checks` object holds the table path and counts (`reports_with_failures`, `reports_without_checks`, `reports_not_recorded`), not the rows. On resume, outcomes are re-read from the hash-verified `.html`, not from the audit; outputs whose audit lacks calculation region lines show `checks not recorded for this output`. Failing checks do not change the exit code.

Exit codes: 0 = all succeeded or verified/skipped; 1 = one or more jobs failed; 2 = invalid setup. Inspect `batch-manifest.jsonl` for individual failures. Resume validates saved identity, hashes and calculation evidence; corrupted outputs are not silently reused. Do not run two batches writing to the same output directory. Manifest rows of converted or verified jobs carry `open_checks`, the number of review flags without an engineer decision (`null` when no check ran successfully, never 0), and `check_errors`, the number of review plug-ins that failed or timed out. Batch accepts `--checks`; the enabled check ids and versions are part of the job identity, so a new check version converts and reviews again on `--resume` (in a new job folder) instead of reusing the old audit.


## Summary CSV across reports

```bash
mcdxkit summary /path/to/a.gp11t /path/to/b.gp11t -o /path/to/new-results/summary.csv
mcdxkit summary /path/to/batch-results -o /path/to/new-results/summary.csv
```

Writes one CSV row per report and final-summary load case. Report inputs use the same parser and default case selection as `inspect`; `--cases` and `--load-source` apply to every report input. A directory is treated as MCDXKit output (batch or browser): each `.audit.json` is summarized from its `_source` report snapshot after checking the audited SHA-256, using the cases and load basis recorded in that audit. `--cases`/`--load-source` are rejected with a directory, even `--load-source effects`. The snapshot hash is checked before the snapshot is parsed; a symlinked audit, job folder or `_source` folder is rejected. A directory without audits, a missing or changed snapshot, an unresolved case selection or an unreadable report fails the whole command and writes nothing. The output must end in `.csv`, is written as UTF-8 with a byte-order mark (so Excel shows non-ASCII names) and is never overwritten. Overlapping inputs are skipped, not repeated, and listed in `duplicates_skipped` in the JSON result: the same resolved file twice (for example a directory and its subfolder), or a second input with identical source bytes, load basis and selected cases. The same report with a different selection is kept. The browser applies the same rule and requires at least two distinct reports.

| Column | Meaning |
| --- | --- |
| `report`, `origin` | Report filename; the input path or audit path (relative to the directory) it came from |
| `source_sha256` | SHA-256 of the report bytes |
| `load_source` | `effects` or `reactions` basis for Vy/Vz/My/Mz |
| `case_id`, `case_name`, `selected` | Every final-summary case; `selected` marks the cases used for the envelope |
| `P_kip`, `Vy_kip`, `Vz_kip`, `My_kip_in`, `Mz_kip_in` | That case's maximum compression and maximum component magnitudes over all piles |
| `governs` | Measures for which this selected case is the report's maximum (ties list every case) |

Component maxima may come from different piles and cases; they are not concurrent loads. Excluded (for example service) cases are listed but never govern. Report and case names that begin with `=`, `+`, `-`, `@` are prefixed with `'` so spreadsheets do not evaluate them. In the browser, **Download summary** on the Inputs step appears when two or more reports are loaded and includes every report with a resolved case selection, using each report's selected cases and the current load basis.

## Start and stop the browser

```bash
mcdxkit serve --no-open --port 8765 \
  --template /path/to/reference.mcdx \
  --output-dir /path/to/browser-results
```

Open the printed URL. Omit `--template` to select it in the browser; omit `--no-open` to launch the browser automatically. `--port 0` chooses an available port. `GET /healthz` reports service health. Ctrl+C stops the server while retaining completed outputs.

Workflow: Files → Inputs → Changes → Outputs. Each output card shows a worksheet-checks panel with failed count, governing D/C and the failing conditions. Open Report/Template/Output file to inspect content; Results shows separate fresh Calcpad calculations. The original-file view is a reconstruction, not Prime rendering. Inputs and Changes list advisory review checks as "Review: …" items to check; they are never shown as a pass or fail. Inputs greys strength cases that can never govern; they stay selected. Current UI cannot edit arbitrary equations. Saved outputs survive restart; the visible upload queue and temporary previews do not.


## Environment variables

Default template lookup: explicit `--template`, then `MCDXKIT_TEMPLATE`, checkout `assets/local/reference.mcdx`, `$XDG_CONFIG_HOME/mcdxkit/reference.mcdx` (default `~/.config/mcdxkit/reference.mcdx`), then `~/.agents/skills/mcdxkit/assets/local/reference.mcdx`. The older `$CODEX_HOME/skills/mcdxkit/assets/local/reference.mcdx` location remains a last compatibility fallback. No agent installation is required. An explicitly configured missing path is reported as missing rather than silently replaced by another template.

| Variable | Purpose |
| --- | --- |
| `MCDXKIT_TEMPLATE` | Private default template path; `--template` overrides it |
| `MCDXKIT_ENGINE_DIR` | Directory containing the built calculator |
| `MCDXKIT_CALCPAD` | Explicit calculator executable path |
| `PORT` | Server port; default 8765; `--port` overrides |
| `MCDXKIT_HOST` | Default bind host; use loopback for local work |
| `MCDXKIT_PUBLIC_URL` | Exact public origin for hosted mode |
| `MCDXKIT_OUTPUT_DIR` | Server output location; `--output-dir` overrides |
| `MCDXKIT_ACCESS_TOKEN` | Hosting secret, at least 32 characters |
| `MCDXKIT_ACCESS_TOKEN_FILE` | Read the hosting secret from this file; takes precedence |
| `MCDXKIT_CHECKS` | Server review checks, same values as `--checks`; default `default` (built-ins only) |

For a host reachable over the network, follow [deployment instructions](../deploy/README.md). CLI startup does not automatically load `.env`; Compose reads it. `MCDXKIT_PORT`, `MCDXKIT_DOMAIN` and `MCDXKIT_IMAGE` in the example environment are Compose settings, distinct from direct CLI variables.

## Common recovery paths

| Failure | Recovery |
| --- | --- |
| Calculator missing | Run `setup-engine` once or configure the existing executable |
| Engine directory exists | Reuse it or choose a new `--install-dir`; do not overwrite it |
| Template missing | Pass/select a compatible private template |
| Unsupported expression | Report the exact construct and extend the adapter with tests; no cache fallback |
| Pile ID beyond template capacity | Review actual geometry before an explicit override |
| Unknown case names | Supply reviewed `--cases`; `case_suggestions` in `mcdxkit inspect` can suggest them |
| Output exists | Choose another basename, or verified batch resume |
| Summary file exists | Choose a new `-o` filename |
| Port occupied | Use `--port 0` or another port |
| Hosted login/origin error | Check exact HTTPS origin, secret and proxy Host forwarding; do not disable protections |

## Source and standards records

After conversion, `mcdxkit standards init output.audit.json --output engineering-register.json` creates a pending provenance register. Use `mcdxkit standards inspect engineering-register.json --audit output.audit.json` to inspect it. The browser provides **Sources & standards** on generated outputs. See [the register guide](engineering-standards.md) for schema, scope and external review records. Never fabricate citations or attestations; software checks are not engineering approval.

## Document creation

`mcdxkit documents --help` exposes project creation, the workspace template library (`templates add|list|archive|restore`), calculations per project (`connect`, `calculations`, `build PROJECT --all`), upload/index, conversational ask, selected-page extraction, requirement/draft review, build and durable submit/jobs/job/cancel. Use the same `--workspace` directory across commands; the browser uses its output directory plus `.documents`. See [the complete workflow](document-workflow.md) for service setup, review semantics and examples.

To calculate every calculation in a project, one run each:

```bash
mcdxkit documents --workspace ./mcdxkit-output/.documents build PROJECT_ID --all
```

Progress (`Finished N of M calculations`) goes to stderr and the JSON result to stdout. A calculation that already has a verified run for the same inputs and engine is skipped, so running the command again after an interruption finishes the rest without duplicate runs. One calculation's failure stays on its own row and nothing is retried; the command exits 1 if any calculation failed. See [Calculate all](document-workflow.md#calculate-all).


To carry reviewed extracted values into the calculation workflow, use the same review bridge as the browser:

```bash
mcdxkit documents --workspace ./mcdxkit-output/.documents requirement-draft PROJECT_ID \
  --requirements REQUIREMENT_ID --bindings '[]' \
  --reviewer 'Engineer name' --note 'Source and conditions checked' \
  --applicability 'Explain why this provision applies to this structure'
mcdxkit documents --workspace ./mcdxkit-output/.documents build PROJECT_ID DRAFT_ID
```

Each selected requirement must already have a `review-requirement --status reviewed` decision. Empty bindings retain template inputs. Explicit bindings are a JSON list of objects with `requirement`, `variable`, `value` and `unit`; source and template units must match. The returned draft ID is used by `build`. This records the supplied review decision, not engineering certification.
