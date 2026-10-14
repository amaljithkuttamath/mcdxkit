# Changelog

User-visible changes to MCDXKit (formerly gdcalc). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/). Each pull request with a user-visible change adds a line under `Unreleased`; a release moves those lines under its version.

## Unreleased

- Refresh document indexing progress automatically without resetting the open source, question or unsaved review. Stop polling on completion or connection errors and keep manual retry available.

- Resume uploaded but unstarted RAGFlow documents without uploading duplicates; clear old progress when a parsing retry is accepted.

- Read current RAGFlow ingestion states, requiring completed tasks with nonempty chunks before enabling answers. Bound the local Elasticsearch heap so PDF parsing can share an 8 GiB Docker VM.

- Prefer free document models based on recorded synthetic tests: Cohere for answers, with two Nemotron search recommendations. Preserve existing selections and indexes; distinguish direct model tests from RAGFlow execution.

- Read local `.env` document-service settings through python-dotenv, preserving process and browser overrides and keeping credentials out of Git/exports.

- Use one OpenRouter key for free search embeddings and answers through RAGFlow’s native adapter; provision the bundled local service account and preserve existing index models. No Ollama runtime is required.

- Detect document-service health, local runtime state and the configured default search model; reserve infrastructure fields for advanced administration.

- Discover supported free OpenRouter models from the browser after entering a key, with saved-key refresh and explicit connection errors.

- Document libraries can now start without calculation files: add PDFs, index, ask cited questions, then connect a validated report/template while retaining sources and answers.

- Add source-backed design packages: persistent PDF evidence, RAGFlow indexing/retrieval, OpenRouter free-only drafts, explicit input review, and calculated report ZIPs through the shared CLI/browser workflow. Includes browser API-key settings with private server-side storage and pinned local deployment preparation.

- Validate the template against the raw report before preview: expose mapping, unit, layout and pile-capacity problems in Inputs, refresh checks after input changes, and share preflight checks with native generation.

- Discover scalar inputs from the selected Mathcad template and map them to P/Vy/Vz/My/Mz through CLI, SDK, batch and browser; preserve downstream formulas and grow source pages with the selected case count. Validate mapping dimensions and record custom mappings in audits and standards registers.

- Support summary-only GROUP inputs explicitly, read bounded case markers, and guide case selection when header names are absent.

## 0.4.0

- Verify installed SDK origins in release/container tests and include test helpers and synthetic fixtures in source distributions.

- Run synthetic repository audits after releases and weekly, with bounded evidence and deduplicated improvement tickets.
- Add versioned source/standards records in the CLI and browser, with audit linkage, unit checks, stale-review detection and explicit pending engineering review.

### Added

- Advisory review checks in a new `mcdxkit.review` plug-in package: service axial load above strength, effects below the pile top, duplicate cases, ratio outliers and possible unit slips, plus governing and "never governs" case annotations and offline case-name suggestions. Results appear in `inspect`, the audit (`review_checks`, with each check's id, version and thresholds), batch manifests and the browser. Checks never change inputs, case selection or outputs; with no review decisions the `.mcdx`, `.cpd` and `.html` are byte-identical with checks on or off. A crashing or slow check is recorded and never fails a conversion.
- `--checks default|none|id,...` on `inspect`, `convert` and `batch`, `MCDXKIT_CHECKS` for the server, and `mcdxkit checks list`. Installed third-party checks (entry-point group `mcdxkit.review`) stay off unless named.
- Browser-only generation progress and a persistent template shortcut, with clearer sidebar steps and mobile preview navigation.

- Check results: CalcpadCE pass/fail outcomes and demand/capacity ratios are summarised in the audit (`checks`, `check_summary`), the batch check-summary CSV and the Outputs step.
- Agent-neutral skill installer with an optional local workflow interview and task-specific review guidance.

- Multi-report summary export: `mcdxkit summary`, `/api/summary` and a **Download summary** button write one CSV row per report and load case, with governing cases and selection status.
- CI lint and type-check job: `ruff check` (correctness rules) and `mypy`, pinned in `requirements-dev.txt`.

## 0.3.0

### Changed

- Renamed the project from gdcalc to MCDXKit with no compatibility aliases: the package, import, `mcdxkit` command, `MCDXKIT_*` environment variables and engine cache path all changed.
- README rewritten as a concise package front page.

## 0.2.1

### Fixed

- PyPI documentation links and package install instructions.

## 0.2.0

### Added

- First public release: GROUP report conversion to CalcpadCE `.cpd`, calculated `.html`, Mathcad `.mcdx` and `.audit.json`, with the CLI, Python SDK, local browser server, container image, contributor workflow and release pipeline.
