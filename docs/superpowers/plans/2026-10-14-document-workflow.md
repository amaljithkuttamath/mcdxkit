# Source-backed document creation implementation plan

Goal: turn selected raw loads, a compatible Mathcad template and project PDFs into a reviewed, cited design report plus the existing calculated artifact bundle.

Approved architecture: RAGFlow owns parsing/retrieval; OpenRouter free models draft structured prose and input proposals; MCDXKit owns immutable sources, review, preflight and engine execution. No AI-written numerical results. Native Mathcad execution remains unverified. The current shared trusted workspace model is retained.

Reuse: existing engine.inspect_template, inspect_report, convert and preflight; RAGFlow HTTP API and pinned upstream Docker Compose (Apache-2.0); OpenRouter chat completions; standard-library SQLite/HTTP/ZIP. No new parser, vector database wrapper, template runtime or cloud deployment.

## Tasks
- [x] Protocol clients: bounded HTTP requests, explicit business-error checking, document-scoped retrieval, free-only OpenRouter model configuration, no redirect credential forwarding, no automatic paid fallback. Test with local HTTP protocol fixtures, including 429 and malformed responses.
- [x] Persistent workflow: SQLite project records and immutable uploaded source versions; explicit ingestion/status; draft citations checked against owned documents; structured proposals checked against scalar template inputs/units; reviewer acceptance; engine conversion plus HTML/Markdown report and evidence manifest packaged as ZIP. Test missing configuration, forged evidence, unit mismatch, draft/review staleness, restart and artifact integrity.
- [x] Shared CLI and HTTP: document project commands and authenticated/CSRF-protected browser routes use the same workflow. Bound uploads and JSON; no browser-selected server file paths. Service endpoints and keys are configured explicitly through authenticated settings.
- [x] Browser: project creation from selected loads/template, source upload/original view, actual ingestion state, draft/evidence review, generate/download package. Use escaped text and isolated original PDF view. Test through loopback HTTP and actual browser with synthetic data.
- [x] Deployment/docs: pinned upstream setup script, private API network override, configuration examples, local model setup, offline/cloud boundary, CLI/SDK/skill documentation. No credentials or private documents in commits.
- [x] Verification: focused tests then complete Python/JS suites, Ruff, mypy, wheel/sdist asset checks, diff review; record unverified live RAGFlow/OpenRouter behavior when credentials/runtime unavailable.

## Review focus
Reject paid/router drift, unknown citations and foreign document IDs. Preserve qualification text and units. Never apply unreviewed inputs. Recheck evidence and source hashes before generation. Model output is untrusted prose and cannot determine numerical calculation status. No full-PDF upload to OpenRouter: only bounded retrieved excerpts and template context. Existing conversions remain usable without model services.

## Validation record

214 Python tests and 8 JavaScript tests passed locally, including the real calculator and controlled HTTP service fixtures. Browser checks covered PDF.js originals, cited drafts, review, calculation, rendered report, API-key save/reload, blank-key preservation and paid-model rejection. Ruff, mypy and wheel/sdist checks passed. Upstream Compose preparation/config validation passed; Docker daemon and live RAGFlow/OpenRouter service checks remain unavailable on this host. No operational parser/model quality claim is made.
