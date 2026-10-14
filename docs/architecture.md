# Architecture

This describes the shipped application. The [Prime feature review](mathcad-prime-review.md) describes the larger product target and gaps.

## Repository decision: one monorepo

Keep the calculation engine, SDK, CLI, HTTP server, shared browser interface and portable skill in this repository. Future Windows/Linux desktop applications belong here too. They are delivery surfaces for the same calculation workflow and should evolve through coordinated changes and releases.

Use the existing paths until a concrete build or dependency requirement justifies extraction:

| Component | Location | Dependency boundary |
| --- | --- | --- |
| Engine and SDK | `src/mcdxkit/engine.py` and calculation/parser modules | Independent of HTTP, browser, desktop and AI integrations |
| CLI and batch | `src/mcdxkit/cli.py`, `src/mcdxkit/batch.py` | Call the shared engine; own arguments, scheduling and progress |
| HTTP adapter | `src/mcdxkit/server.py`, `src/mcdxkit/service.py` | Own sessions, authentication and file access; call the shared engine |
| Shared UI | `src/mcdxkit/web/` | Use the server API; no duplicated calculation logic |
| Portable skill | `SKILL.md`, `references/`, `scripts/` | Instruct agents to use the public CLI/SDK |
| Desktop wrapper (planned) | Future `desktop/` directory | Reuse the shared UI; own window, file dialogs and local backend lifecycle |

The current Python distribution bundles the SDK, CLI, server and web assets. Preserve public imports and CLI compatibility when moving modules. Do not create empty packages or introduce workspace tooling solely to match a proposed folder tree. Optional AI integrations must not become dependencies of ordinary calculation.

### Deliverables and release boundaries

PyPI, container images and future desktop installers are separate artifacts built from a coordinated source revision. Record that revision and the pinned calculator version in release evidence. The current workflows publish PyPI on versioned releases and containers from tested `main` commits; a monorepo decision does not change those triggers.

Desktop installers have not shipped. Their implementation should bundle the backend, shared UI and platform-compatible calculator, without requiring users to install Python or build tools. Validate installed-app startup, offline conversion, paths with spaces/Unicode, owned-process cleanup and preservation of user files during upgrade/uninstall on each supported operating system. Linux container tests do not establish Windows compatibility. Signing and platform release jobs belong in the desktop implementation tickets.

Keep one issue and PR per coherent change, with affected interfaces and tests updated together. Extract a separate repository only when a component has independent maintainers, release needs or external consumers that justify the coordination cost.

## Conversion path

```mermaid
flowchart LR
    CLI[CLI] --> Engine[engine.py]
    Web[Browser] --> HTTP[server.py]
    HTTP --> Session[service.py]
    Session --> Engine
    Batch[batch.py workers] --> Engine
    Engine --> Parse[group_report.py]
    Parse --> Native[mcdx.py]
    Native --> Translate[calcpad.py translator]
    Translate --> Bridge[CalcpadCE subprocess]
    Bridge --> Publish[Validated artifact bundle]
```

| Module | Responsibility |
| --- | --- |
| `cli.py`, `generate.py`, `validate.py` | Commands, arguments, output and exit codes |
| `engine.py` | Transport-independent inspect/convert/validate/summary API and staged publication |
| `group_report.py` | Final summary parsing, case selection and envelopes |
| `mcdx.py` | Package safety checks and constrained native template modifications |
| `calcpad.py` | Strict XML-to-Calcpad translation, subprocess deadline and result validation |
| `checks.py` | Read rendered comparison outcomes and simple D/C ratios from the calculated HTML; no recalculation |
| `calcpad_bridge/` | Small .NET executable driving CalcpadCE; source in, HTML/errors out |
| `setup_engine.py` | Build a pinned engine revision for the current platform |
| `inspection.py` | Read-only document reconstruction and expression differences |
| `service.py` | Session registry, opaque file IDs, uploads/previews/history and conversion lock |
| `server.py` | FastAPI/Uvicorn, authentication, origin checks, limits and static assets |
| `web/` | Browser workflow and file viewer; no independent design solver |
| `batch.py` | Input discovery, bounded process pool, job identity, manifest and resume |
| `review/` | Advisory review checks as plug-ins, shared by inspect, convert, batch and browser: `api.py` (plug-in contract, standard library only), `view.py` (frozen `ReportView` from the parsed dict), `registry.py` (built-ins and `mcdxkit.review` entry points), `runner.py` (isolated, time-budgeted runs; `review-checks/1` report), `history.py` (past audits for the classifier), `checks/`, `annotate/`, `classify/` |
| `summary.py` | `mcdxkit summary` input discovery and exclusive CSV write; rows come from `engine.py` |

### Review dependency rule

`mcdxkit.review` receives the plain dict `group_report.parse()` returns and returns a JSON-ready block. It must not import `mcdx`, `calcpad`, `engine`, `server`, `service`, `cli`, `generate`, `batch`, `inspection` or `group_report`; `tests/test_review_plugins.py` walks every module with `ast` and fails on such an import, absolute or relative. `engine.py` is the only caller that combines review output with conversion: it records `review_checks` in the audit and, only when an engineer decision exists, passes a plain-text note to `mcdx.generate`. Checks never modify inputs, cases, overrides or outputs. Plug-ins run in deterministic order with a per-plug-in time budget; failures become `check_error` entries, never conversion failures.

## Public Python API

```python
from mcdxkit.engine import inspect_report, convert, validate

inspection = inspect_report('report.gp11t', cases=[1, 3, 7])
result = convert(
    'report.gp11t', 'reference.mcdx', 'new-output/design.mcdx',
    cases=[1, 3, 7], load_source='effects', overrides={},
)
package_check = validate(result['output'])
```

Inputs and formula definitions remain in generated files. CalcpadCE executes translated expressions; Python is responsible for orchestration and source interpretation. General Mathcad expression coverage is not implemented. Extending Calcpad support should include explicit type/unit semantics and fixtures for every newly supported construct.

## Storage and consistency

Single conversion stages four artifacts in the destination filesystem and publishes without overwriting. Audit JSON is the completion marker. Hard-link support is required; publication failures roll back only artifacts created by the attempted operation.

Batch identity includes source/template content, settings and engine/translator versions. Resume checks artifacts before skipping work. Browser and batch histories retain private source/template snapshots for comparison. Back up the full output directory, not only the visible worksheet file.

Session file IDs resolve through a server-side registry; clients cannot choose arbitrary server filesystem paths. Temporary uploads and previews are session state. Completed artifacts and audits are durable. The current server has shared workspace state, serialized conversion and no multi-tenant authorization model. A reverse proxy or multiple replicas does not add tenant isolation.

## Execution and trust boundaries

The report parser accepts bounded text inputs. Package inspection checks ZIP expansion and XML/relationship constraints. The translator only generates a restricted Calcpad language subset and rejects unsupported content. The bridge executes generated source with a deadline. Calculation HTML is handled separately from original-file content. Cached Mathcad values are visibly distinct from fresh results.

Do not turn the bridge into a general uploaded-code endpoint by simply bypassing the translator. General worksheets introduce includes, file reads/writes and richer executable content. That requires explicit capabilities, isolated execution/storage and validation of dependencies and output markup.

The HTTP layer checks Host/Origin, request tokens and hosted login state. Network mode requires a strong access token and configured public origin. Hosted mode uploads engineering files to that server. See [deployment](../deploy/README.md) for TLS, persistence and limits.

## Extending the system

- Add source formats behind parsing adapters returning explicit units, cases and provenance. Do not merge different report sections as an undocumented fallback.
- Add template profiles with declared geometry, layout, inputs and supported expressions. Do not loosen the existing profile to accept ambiguous definitions.
- Extend calculation coverage independently from file inspection. Rendering a construct does not prove it calculates correctly.
- Keep CLI, browser and batch on one engine API. A future editable worksheet model must own both displayed expressions and executed expressions.
- Native Prime execution belongs behind a separate Windows adapter with actual version/run evidence. It is absent today; Linux Docker builds do not provide it.
- Distributed hosting needs durable jobs, per-project permissions and isolated storage. The current worker pool is local batch processing.

## Build and publication

`pyproject.toml` defines the Python package and assets. The multi-stage Dockerfile builds the pinned calculator, installs Python dependencies, then runs as UID/GID 10001. `.dockerignore` is an allowlist to prevent private project data entering images.

Actions tests Python versions and package contents before container checks. Only a successful `main` run pushes the exact tested image to GHCR. Pull-request code is tested without registry login or publication. The repository's public source, registry package visibility and a live hosted deployment are separate states.

See [distributed execution boundaries](distributed-architecture.md) for the future API/job/worker/storage contract and its acceptance gates.

## Source-backed document authoring

The [document workflow](document-workflow.md) adds persistent design-basis drafting and review around the existing engine. RAGFlow owns parsing/retrieval; the explicit OpenRouter free model drafts cited prose and input suggestions. SQLite and immutable source snapshots record project/review state; CLI and browser use `documents.py`. Numerical results remain engine-owned. See [deployment](../deploy/ragflow.md).
