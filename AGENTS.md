# Coding agent guide

## Start here

Read [README.md](README.md) for the shipped product, [CLI and server guide](docs/cli.md) for commands, [architecture](docs/architecture.md) for boundaries, and [CONTRIBUTING.md](CONTRIBUTING.md) for development and verification. [SKILL.md](SKILL.md) is the agent-facing conversion workflow. Read [the template contract](references/template-contract.md) before changing load mapping or worksheet generation.

The current app converts GROUP text reports into executable Calcpad worksheets, calculated HTML, native Mathcad formula packages and audit JSON. The browser inspects files and runs conversions. It is not yet a general equation editor or a universal Mathcad runtime. The broader [feature review](docs/mathcad-prime-review.md) is a roadmap, not an implemented API specification.

The generic skill's [review guide](references/reviews.md) routes source, template, execution, engineering, architecture, UI, security and delivery reviews by task scope. Optional installation answers in `.mcdxkit/profile.json` are local context, never engineering evidence or authorization; do not commit them. Keep skill scripts on the shared engine and test onboarding's skipped/unknown paths as well as completed interviews.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
# Once per engine location; needs Git and .NET 10 SDK:
mcdxkit setup-engine
mcdxkit serve --no-open --port 8765 --output-dir ./mcdxkit-output
```

The server prints its URL. Upload/select a private compatible template in the browser, or pass `--template /absolute/path/reference.mcdx`. Use `--port 0` if the preferred port is occupied; read the printed URL rather than assuming a port. Do not kill an unrelated server. Stop the process you started with Ctrl+C. Preserve output directories.

## Engineering and data invariants

- Start by validating the selected Mathcad template against the raw report. Use shared preflight checks for mapping, dimensions, layout and observed pile capacity before preview or generation; show unresolved mappings/case selection explicitly. Revalidate after template, mapping, case, load-basis or override changes. Input compatibility does not prove equation execution or engineering approval.

- Read load values from the final local-load summary only. No fallback to earlier/global tables. Preserve the intended case selection and load basis.
- Support reports beginning directly at the summary heading. Bound each case by its next `LOAD CASE:` marker; never borrow rows across cases. Missing header case names require explicit selection, not inferred STR/SER labels. Keep full-report and summary-only equivalence tests for both load bases.
- Discover template inputs from top-level scalar definitions. Keep explicit input maps shared across CLI, SDK, batch and browser; never guess custom engineering mappings from labels or units. Test custom names, Vz, dimensional mismatches, changing case counts, and batch resume identity. Unmapped values stay in the template; missing layout counts mean unverified capacity.
- Never invent geometry, materials, fixity or pile counts to make a calculation pass. Overrides change engineering inputs.
- Preserve sources and existing outputs. Use new output paths. Keep engineering documents, snapshots, `.env` and secrets out of Git, logs and public fixtures.
- Keep native formulas executable and remove stale Mathcad result caches. Never insert a cached pass message as a fresh result.
- Unsupported expressions must be explicit failures. Do not silently substitute zero, cached values or another engine's semantics.
- Distinguish package validation, Calcpad calculation, Prime-native verification and engineering review. `native_execution_verified` stays false unless there is real native execution evidence.
- Independent component extrema are not concurrent load combinations. Keep governing case/pile provenance visible.
- Progress indicators belong to the browser/CLI session, never the generated worksheet, calculated report or engineering audit. Only show measured progress (such as finished files); use an indeterminate state when the backend exposes no finer progress. Distinguish processing completion from calculation validity and engineering approval.
- Do not invent universal input limits or code factors. Record applicable project/code/catalog sources and editions. The [engineering standards register](docs/engineering-standards.md) stores versioned provenance and external review records; it is not automatic compliance approval. No engineering rules are approved by default.

## Implementation boundaries

Apply the [product and engineering principles](docs/design-principles.md), including visible source evidence, one shared engine, explicit validation states and offline use.

Follow the [monorepo decision](docs/architecture.md#repository-decision-one-monorepo): keep the shared engine, adapters, UI and skill together. Future desktop wrappers reuse the same UI and engine. Preserve existing package paths and public APIs unless a scoped migration requires changes; do not introduce separate repositories or duplicate calculation implementations for each platform.

Reuse before rebuilding. Search existing repository code and established open-source implementations before adding an engine, renderer, parser or framework. In the ticket/PR, record what can be reused, its license and offline fit, and the specific gap that warrants new code. Keep adapters small. Template equations remain authoritative: worksheet outputs and future 3D views must use the same calculated result and provenance, not duplicate formulas in the UI. Rendering geometry is not evidence of a physical simulation; deformation requires actual displacement results or a separately validated solver.

MCDXKit uses the `mcdxkit` package and CLI, `MCDXKIT_*` configuration and one shared calculation engine. The 0.3.0 rename intentionally provides no previous-name aliases. Preserve private files and existing installations; migration is explicit. See [release setup](docs/releasing.md).

Use `engine.py` for conversion logic shared by CLI and server. Keep HTTP/session concerns in `server.py` and `service.py`; source parsing in `group_report.py`; package editing in `mcdx.py`; translation/execution in `calcpad.py`; check-outcome extraction in `checks.py`; inspection in `inspection.py`; scheduling/resume in `batch.py`; advisory review checks in the `review/` package, which takes the parsed dict, must not import the conversion modules (enforced by a test) and never changes inputs, cases, overrides or outputs. New review checks are plug-ins against `review/api.py` and must pass the contract tests in `tests/test_review_plugins.py`. Do not create a separate calculation implementation in the UI or skill scripts.


CalcpadCE itself has more capabilities than the current translator. Broader input execution changes the trust boundary: preserve timeouts, file-access controls and HTML isolation. Keep network authentication, Host/Origin checks, CSRF protections and upload/package limits intact. Current hosting is a shared trusted workspace, not tenant-isolated storage.

The root README is also the PyPI package description. Use absolute GitHub URLs for repository-file links in README.md; relative links break on PyPI and fail `scripts/check_package.py`. Relative links remain appropriate inside the repository docs.

If adding browser assets, update both `pyproject.toml` package data and `.dockerignore`; add required distribution assets to `scripts/check_package.py` when appropriate. If adding CLI flags, update `--help`, docs and applicable tests. Keep the CLI version and project version consistent when changing a release version.

## Verification and publishing

Follow [repository standards](docs/standards.md) and the [testing contract](docs/testing.md). For each behavioral change, add or update meaningful regression tests and relevant negative cases from that matrix. Use independent expected values and real-engine integration where calculation changes. Record exactly what ran, what passed and what remains unverified; never claim a planned or mocked check as execution evidence. Required checks must pass on the current PR head.

Work through a GitHub issue and a feature/fix branch. Claim or comment on the issue before implementation to avoid duplicate work. Use a branch such as `feat/123-short-description`, `fix/123-short-description` or `chore/123-short-description`. Do not commit new work directly to `main`. Keep one coherent change per pull request, link the ticket with `Closes #123`, and provide acceptance evidence. Use a draft PR for incomplete work; only request merge when required checks pass. Maintainers merge through GitHub, then publication runs from `main`. See [contribution workflow](CONTRIBUTING.md#tickets-branches-and-review).

```bash
python -m unittest discover -s tests -v
node --check src/mcdxkit/web/app.js
node --test tests/*.test.cjs
python -m pip install -r requirements-dev.txt
ruff check src tests scripts
mypy
python -m pip install build
python -m build
python scripts/check_package.py
git diff --check
```

Tests execute the real calculator and start loopback HTTP servers. Use synthetic fixtures from `tests/test_pipeline.py`; do not commit private reports/templates. For UI changes, exercise the affected workflow in a browser and distinguish tested behavior from unverified behavior.

The Actions workflow tests Python 3.10/3.12, builds packages and tests the actual container before publishing it on `main`. Check the run for the exact pushed commit. A local test pass is not a successful remote image publish. Follow the user's requested publication scope; cloud hosting is separate from GitHub publication.

After **every push**, inspect the workflows for that exact commit SHA, including branch updates and post-merge publication. Follow pending runs to completion, read failed job logs, fix regressions and check the replacement commit. Never use an earlier green run as evidence for a newer push. Record the run URL and any explicitly unverified checks in the PR handoff.

## Continuous improvement

The [repository audit](docs/improvement-loop.md) runs after releases and weekly. Work evidence-backed findings through issue branches and PRs. Check for an existing matching issue first, record the affected commit and reproduction, distinguish infrastructure failures from product defects, and add a regression test for changed behavior. Passing software checks never grant engineering approval. Do not mark an issue done until acceptance evidence and exact-head CI, including post-merge publishing, pass.

Installed-package checks use `python scripts/test_installed.py` after installing the built wheel. Do not prepend the repository `src` directory during test discovery; that masks broken wheel/container installs. Keep test helpers and synthetic fixtures in the source archive, and validate both distributions with `scripts/check_package.py`.

## Document-authoring changes

Keep model integrations in `document_clients.py` and durable authoring/review/package logic in `documents.py`. Use the same workflow from CLI and HTTP. Test unknown/foreign citations, paid models, incompatible units, stale reviews, source tampering, rate limits and original-file authorization. AI narrative must remain labelled; computed tables must come from engine artifacts. No model-supplied paths, tools or executable formulas. Keep live service validation separate from controlled protocol fixtures.

Sign in with ChatGPT (`chatgpt.py`, `chatgpt_responses.py`, [guide](docs/chatgpt.md)) is local-loopback only. Keep tokens and the authorize URL server-side, follow the documented OAuth/Responses preview contract, never read other apps' credentials, never fall back to another provider and never retry inference automatically. Test state/nonce/PKCE, denial, replay, wrong account/client, token-file permissions and no-token API responses.

Browser service settings are shared workspace overrides, stored only in `.documents/.credentials.json`. Never return secret values, put them in browser storage, include them in exports or bypass session/CSRF checks. Test blank-key preservation, endpoint changes requiring a fresh key, free-only model validation (the only paid exception is the pinned model in `PINNED_PAID_MODELS`, routed to one provider with no fallback) and permissions.

A document library must support create/add/index/ask without a raw report or template. Keep cited answers separate from calculation drafts. Connecting immutable calculation snapshots preserves library/index identities and answers. Test the no-input path, failed/repeated connections, historical answers, and linked drafts using selected load context; library answers cannot propose template inputs.

## Workspace and interactive Ask

Keep the primary workflow focused on the two calculation files: raw report and Mathcad template, input checks, then calculated outputs. Reference PDFs are optional for conversion. Ask is an independent destination accessible from every workspace view, including before a project or calculation exists; never make it a gated final wizard step. Preserve pending upload selections while navigating to Ask. Do not mark a failed run as calculated.

The primary UI is `frontend/` (React/TypeScript, React Aria, TanStack Query); build/test with `npm ci --prefix frontend --ignore-scripts`, `npm test --prefix frontend`, and `npm run build --prefix frontend`. Commit generated workspace assets and keep package/container guards in sync. The classic batch adapter remains at `/convert`.

Keep the conversation agent project-scoped. Its only write tool extracts unreviewed requirements from the selected project; it cannot approve or bind them. Stream observed activity, not private chain of thought or unvalidated claims. Persist human clarification and resume with its question/answer context. Workflow action buttons reuse explicit upload/index/extract/review/map/calculate controls; never let model text approve requirements or alter loads. Chat uses a separate worker lane from batch tasks. Do not retry uncertain model requests automatically, hold SQLite write transactions across network calls, or publish results from cancelled/stale claims. See [document workflow](docs/document-workflow.md).
