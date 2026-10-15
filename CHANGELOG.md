# Changelog

User-visible changes to MCDXKit (formerly gdcalc). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/). Each pull request with a user-visible change adds a line under `Unreleased`; a release moves those lines under its version.

## Unreleased

- Preserve parenthetical applicability and approval exclusions in Ask's statement checks, even when a model reports support. Document drafts now enforce the same pinned DeepSeek provider and price caps as Ask and extraction.
- Unified project workspace with worksheet, conversational Ask, input mapping, requirements and calculated outputs; original PDFs open in a side drawer.
- Project-aware LangChain research with bounded RAGFlow searches, follow-up context, live activity and persisted human clarification. Suggested actions reuse explicit workflow controls.
- Selected-page DeepSeek extraction preserves literal values, conditions and exact citations; reviewed applicability and compatible scalar bindings feed the existing engine.
- Ask plans a main search plus condition/exception searches within a fixed budget, reuses repeated results, reads original neighbouring PDF pages for read-only answers, flags stated numbers missing from cited text, and shows a typed activity timeline (searches, pages read, saved extractions, citation check). A new question no longer briefly shows the previous failed request. Truncated original-page context is labelled and recorded as a gap.
- Ask finishes with an answer when a research tool's budget is used instead of failing with "Research limit reached". Read-only questions no longer save requirements: saving needs the user's request, otherwise Ask offers a **Save for review** button.
- Use your ChatGPT plan for Ask: **Continue with ChatGPT** in Services (local 127.0.0.1 servers only) signs in with OpenAI's documented OAuth flow, and the selected ChatGPT model runs every Ask step through the Responses API. OpenRouter remains selectable; no automatic provider fallback or retry.
- The answer check accepts up to 8 distinct quotes per statement (previously 3), so compound statements are no longer withheld for needing several quotations; withheld answers record a fixed reason code (for example `invalid_verifier_response`) in the answer and its audit receipt.
- The answer check (`grounding/5`) accepts quotes of up to 2,000 characters (previously 400), so answers resting on long engineering sentences are no longer withheld because quoting the whole sentence with its conditions exceeded the limit. New explicit size limits (6,000 quote characters per statement, 20,000 per check) bound the check's quotes; the separate output-token limit still applies. Over-limit or cut-off checks still withhold the draft; quotes are never trimmed and nothing is retried.
- The workspace makes the two-file calculation path primary (raw report and Mathcad template → checked inputs → calculated outputs, with a next-action button and step indicator). Ask is a separate destination available from every view, even before a project or calculation exists.
- Download an audit receipt for any saved Ask answer: model and versions, searches, pages read, each checked statement with quotes, retrieved vs cited evidence, current source-file integrity and a tamper-evident SHA-256 digest (not a signature or proof of truth). Grounding now rejects values transferred to a different scope (for example from new to existing walls), widened beyond their source qualifiers or conditions, or with a lost sign, and checks compound statements clause by clause. The receipt distinguishes a changed stored file from an answer built on an earlier file revision.
- Ask checks every statement of a new answer against its cited passages before publishing (one extra call on the selected model plus mechanical quote, number, unit and limit checks). If any statement can't be confirmed, the draft is withheld and the retrieved passages are shown instead. This reduces, but cannot eliminate, unsupported statements.
- New Ask answers cite evidence inline right after each supported claim (`[E3][E7]`), checked against the section's listed evidence; page references to cited pages no longer trigger the numeric check. Earlier answers are unchanged.
- Read-only AG-UI 1.0 event stream for durable jobs (`/api/jobs/{id}/agui`) using the official `ag-ui-protocol` SDK, with Last-Event-ID replay.
- Requirement cards, review and binding summaries show the extracted unit beside literal values such as "two (2)".
- Durable local chat/batch workers, cancellation fencing, authenticated SSE, CLI submission/status and packaged offline frontend assets.

- View the full calculated worksheet directly from document-package Outputs, with package integrity verification and a sandboxed preview.

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
