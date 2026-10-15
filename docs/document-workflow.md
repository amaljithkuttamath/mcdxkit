# Document library and calculated design packages

Free answer/search preferences are based on the [recorded synthetic model evaluation](model-evaluation.md). Cohere North Mini Code is the default answer model. Refreshing the catalog keeps existing selections and shows tested recommendations only while they remain listed and free. RAGFlow indexing remains separately validated; direct embedding success is not an end-to-end search pass.

Use **Document library** in the browser, `mcdxkit documents`, or `DocumentWorkflow` in Python. This workflow uses RAGFlow for PDF ingestion/retrieval and a specific OpenRouter `:free` model for cited narrative and scalar input proposals. Existing MCDXKit preflight and CalcpadCE perform all conversions/calculations. Native Mathcad execution is not verified by this workflow.

## Browser workspace

Open the server URL. The primary path is a raw report plus a Mathcad template: **Inputs**, **Worksheet** and **Outputs** under *Calculation*. **Requirements** sits under *Reference documents*. **Ask** and **Templates** are separate destinations, reachable from every view, with or without a project, connected calculation or indexed PDFs ([layout](ui-design.md)).

A project holds many calculations. Each calculation is one raw report plus one Mathcad template, with its own load cases, input mapping and calculated runs, stored as an immutable snapshot. The **Calculation** picker in the task bar selects which one Inputs, Worksheet and Outputs show; **+ New calculation** adds another without changing the existing ones. Reference PDFs, requirements and Ask stay project-wide.

**Templates** is the workspace template library. Upload each `.mcdx` once and every project can pick it. Opening a template shows its discovered inputs (variable, template value, unit and any load preset) and its rendered worksheet, before any project or report exists. Identical bytes are stored once; a changed file with the same name becomes a new version beside the old one; **Archive** hides a template from new calculations without deleting it. A calculation copies the template bytes it uses, so archiving or adding versions never changes an existing calculation or its outputs. Templates with custom input names have no load preset: map their inputs explicitly in Inputs. **Sources** opens original PDFs in a side drawer; it does not replace the calculation workspace. `/convert` retains the classic batch interface and `/library` retains the classic document interface during migration.

1. Create a project and **Add files**. PDFs can be added before a report or template. Original bytes stay intact; revisions are separate sources. Add a GROUP report and pick a Mathcad template (from the library, or upload one, which adds it to the library) when calculation is needed, choose cases and input mappings in **Inputs**, and connect the validated snapshots as a calculation. Repeat for every report and template the project needs.
2. Open **Sources → Index documents** to send PDFs to RAGFlow. Use **Refresh index** to check remote parsing status. Ask searches ready documents and explicitly reports excluded documents; a failed source does not block questions about the remaining sources. Indexing is not a review of applicability or completeness.
3. Open **Ask** for document research or workflow guidance. It knows uploaded filenames/index states, requirement review counts and connected calculation state. A bounded LangChain agent can inspect that state and refine RAGFlow searches. It cannot execute shell commands, approve requirements or change inputs. Follow-ups retain conversation context, but each document answer needs fresh retrieved evidence. Original-page citations open in the same source drawer.
4. Ask streams actual activity over authenticated server-sent events: checking uploaded files, searching, assessing evidence and validating citations. This is an operational activity trail, not a raw reasoning transcript. The answer appears after structured-output and citation validation. Stop cancels the operation; reopening the project restores saved answers and activity. A missing decision produces an in-chat question with choices and a free-text reply. The reply continues the saved conversation. Suggested workflow buttons invoke existing explicit controls; they do not mean the model has performed the action.
5. **Extract requirements** selects an original PDF and at most 12 pages. DeepSeek 4.1 Flash through OpenRouter/DeepInfra extracts literal values, units, qualifications and exact page quotes. This path does not require indexing. Quotations are checked against the selected text layer; the model's completeness and interpretation remain subject to human review.
6. In **Requirements**, open each original page and record the evidence review. **Use reviewed selection** records applicability to this calculation and, optionally, explicit compatible scalar bindings. No load variable is silently overwritten; selected raw loads retain their provenance. Evidence-only requirements leave the template values unchanged. Different project numbers or editions require a human applicability decision.
7. **Calculate** runs retained template inputs and selected raw loads through CalcpadCE. **Save basis & calculate** additionally uses the explicitly reviewed requirements/bindings. **Outputs** can also render the downloadable `calculation.mcdx` itself (**Mathcad file**), so the engineer sees the inserted load summary and the input references before opening Prime. **Outputs** shows the calculated worksheet and downloads `.mcdx`, `.cpd` and the complete package. A finished calculation may contain failed checks. Prime-native execution and engineering approval remain separate, unverified claims.

**Services** keeps the OpenRouter key on the server, lists supported free answer models and detects/starts the prepared local RAGFlow deployment. Initial Docker/Compose preparation is an administrator step; see [deployment](../deploy/ragflow.md). Empty key fields preserve existing credentials. The classic `/library` settings expose advanced custom endpoint and embedding configuration. Workspace settings override environment defaults; see the `.env` section below.

Answer requests send the question, recent conversation, project workflow metadata and retrieved excerpts to OpenRouter. Extraction sends selected text pages to OpenRouter/DeepInfra. RAGFlow receives full PDFs; hosted embeddings send their extracted text to the configured embedding provider. Answer models remain explicitly free-only with no paid/provider fallback. Selected-page extraction uses the separately identified paid model. Provider failures are surfaced, not silently repeated.

The package contains `report.md`, `report.html`, `calculation.mcdx`, `calculation.cpd`, `calculation.html`, `calculation.audit.json`, original PDFs and template/load snapshots, and `manifest.json`. Computed tables come from the engine. Progress/activity stays exclusively in workspace operational records, never engineering artifacts. Direct calculations correctly record that no design-basis review was made.

## CLI

Configure services following [local deployment](../deploy/ragflow.md). The CLI and browser share a project store when pointed at the same `output-dir/.documents` directory.

```bash
mcdxkit documents config
mcdxkit documents --workspace ./results/.documents create --title 'Bridge project library'
# Use the returned project ID below.
mcdxkit documents --workspace ./results/.documents add PROJECT specification.pdf drawings.pdf
mcdxkit documents --workspace ./results/.documents ingest PROJECT
mcdxkit documents --workspace ./results/.documents status PROJECT
mcdxkit documents --workspace ./results/.documents ask PROJECT \
  --question 'What must the foundation design report include?'
# Upload templates once to the workspace library; identical files are stored once.
mcdxkit documents --workspace ./results/.documents templates add pile-cap.mcdx abutment.mcdx
mcdxkit documents --workspace ./results/.documents templates list
# Add calculations without losing your PDFs or answers. A project holds many.
mcdxkit documents --workspace ./results/.documents connect PROJECT loads.gp11t \
  --library-template TEMPLATE_ID --cases 1 7 --name 'Pier 3 strength'
mcdxkit documents --workspace ./results/.documents connect PROJECT service.gp11t \
  --template design.mcdx --cases 2
mcdxkit documents --workspace ./results/.documents calculations PROJECT
mcdxkit documents --workspace ./results/.documents draft PROJECT \
  --request 'Identify applicable material requirements and assumptions for this template.'
mcdxkit documents --workspace ./results/.documents show PROJECT
# Indices are zero-based; omit --accept to retain every existing template input.
mcdxkit documents --workspace ./results/.documents review PROJECT DRAFT \
  --accept 0 --reviewer 'Reviewer name' --note 'Verified applicability and units against the cited source.'
mcdxkit documents --workspace ./results/.documents build PROJECT DRAFT
mcdxkit serve --output-dir ./results
```

Commands print JSON; errors exit 2. `build` prints the package path. With several calculations, `draft`, `requirement-draft` and a draft-less `build` need `--calculation ID`; a calculation is never guessed. A design-basis draft remembers its calculation, so `build PROJECT DRAFT` needs no ID. `create --input-map '{"AxialCustom":"P"}'` supports custom templates through the same explicit mapping/preflight as normal conversion. Summary-only reports require explicit cases. Missing model configuration does not affect ordinary `convert`.

## SDK and boundaries

```python
from mcdxkit.documents import DocumentWorkflow
workflow = DocumentWorkflow('results/.documents')
project = workflow.create('Bridge project library')
# add_source, ingest, status and ask work without calculation files.
# Later, add calculations; the result includes the new 'calculation' record.
# first = workflow.attach_calculation(project['id'], 'loads.txt', 'template.mcdx', cases=[1, 7])['calculation']
# workflow.calculations(workflow.get(project['id'])) lists them all.
# draft(..., calculation=ID), build(pid, draft_id, calculation=ID); with one calculation the ID may be omitted.
# mcdxkit.template_library.TemplateLibrary(root) adds, lists, archives and returns verified template paths.
```

`document_clients.py` owns bounded HTTP adapters and secret configuration. `documents.py` owns immutable source and calculation snapshots, SQLite project state, structured draft validation, review and package assembly. `document_cli.py` and the HTTP/browser adapters share it. The calculation engine imports neither model client nor authoring service. RAGFlow owns OCR, chunks and retrieval; we do not recreate them. The conversational `knowledge.py` adapter reuses LangChain `create_agent` with project-scoped bounded project tools. Deep Agents filesystem/subagent tools are not needed.

SQLite serializes mutations across CLI/server processes. This remains a single trusted-team workspace, not tenant-isolated hosting. Back up the whole document directory plus RAGFlow's deployment configuration and persistent volumes. Never commit these directories. Changed source bytes block generation; adding a source invalidates older draft reviews. Existing package exports remain immutable and independently inspectable. A new draft is a new record; review changes never rewrite an already-issued package.

Limits: 100 PDFs / 2 GiB per project; 256 MiB per PDF; 100 answers, 100 drafts and 100 packages; 12 retrieved passages of at most 6,000 characters per draft; Ask uses at most three search calls and six distinct queries, eight passages per query and 36 distinct passages of at most 4,000 characters, plus at most twelve original context pages; each context page returns at most its first 6,000 characters and longer pages are marked truncated. Files over the limit are rejected; the app never splits or merges originals. Citation validation establishes source identity, not factual entailment or engineering correctness. PDF page positions are shown only when the parser supplies them. Formula authoring, automatic code-edition resolution, unrestricted agents, native Prime execution, automatic transport retries and offline LLM drafting are not implemented. Existing calculation and stored-source/package inspection remain local/offline; this drafting adapter requires OpenRouter network access.

## Validation

Synthetic lifecycle tests execute the real calculator. Protocol tests exercise real HTTP against controlled RAGFlow/OpenRouter response fixtures, including HTTP-success business failures, redirect refusal and rate limits. These are separate from live-service evaluation. Before operational use, run the pilot PDFs against a configured RAGFlow instance and chosen free model; verify citations, redline revisions, qualified values, missing evidence and drawing/table extraction. A passing mock/protocol test is not evidence of parser/model quality.

### Choosing a model in the browser

Open **Connect services**, paste an OpenRouter API key, and leave the field. The server loads the account-filtered model catalog from OpenRouter and offers every compatible free text model with zero reported pricing. Select a model and save. Reopening settings with a saved key refreshes the list; **Refresh models** retries failed or empty results. Discovery does not generate text, send project documents, or save a newly entered key. Saving settings is explicit. Keys never appear in the response, browser storage, or exported files.

The catalog follows [OpenRouter account preferences](https://openrouter.ai/docs/api/api-reference/models/list-models-filtered-by-user-provider-preferences-privacy-settings-and-guardrails). A listed model is not a guarantee of capacity, structured-answer quality, or availability under the drafting request's no-data-collection policy. Requests can still be refused or rate-limited; no paid fallback is enabled.

### One OpenRouter key for search and answers

The browser also lists free text-embedding models from OpenRouter's separate `/embeddings/models` catalog. Choose a search model and an answer model, then save. For the prepared local bundle, MCDXKit provisions its internal RAGFlow account and registers the search model with RAGFlow's existing OpenRouter driver. It does not install Ollama or download local model weights. Original PDFs stay in the project and RAGFlow; indexing sends extracted text to OpenRouter for embeddings. Embeddings follow the account/provider privacy policy, whereas drafting also requests `data_collection: deny`. Hosted search and answers require network access. Catalog discovery is not an execution test.

Existing embedding models are never silently replaced: changing vector models requires a new workspace. A missing/invalid key, absent free models or service errors leave setup incomplete, with no paid fallback. Advanced fields support separately managed deployments. See [deployment setup](../deploy/ragflow.md).

### Local `.env` configuration

Create `.env` in the directory where you launch MCDXKit (copy `.env.example` or `deploy/documents.env.example`) and add `OPENROUTER_API_KEY=your-key`. Document settings are read by the CLI, SDK and server without executing the file, expanding variables or searching parent directories. Existing browser-saved settings take precedence over exported process environment, which takes precedence over `.env`. Blank password fields continue to preserve saved keys. Editing `.env` does not replace a key already saved through the browser; update that key in the browser when needed. Dotenv values never mutate the process environment, so unrelated server/network options are unaffected.

`OPENROUTER_API_KEY_FILE` and `MCDXKIT_RAGFLOW_API_KEY_FILE` can point at private files instead. An explicitly exported key or `_FILE` takes priority over either dotenv form; within one source `_FILE` wins. Keep `.env` private; only empty examples belong in Git. The app re-reads document configuration on the next operation, so dotenv edits do not require restarting an already updated server.

## Durable operations and interactive conversations

The server starts supervised chat and batch workers. Chat has its own lane so calculations and indexing do not block interaction. Huey dispatches JSON job IDs; the application SQLite ledger owns status, snapshots and publication. This is one server instance on one local filesystem, not a horizontally scaled queue or tenant boundary. Remote RAGFlow parsing continues independently after submission; refresh its status explicitly.

`POST /api/jobs` accepts `{project, action, payload, request_key}` and returns 202. Actions are `ask`, `extract`, `extract_offer` (`{answer}`: confirm an Ask save proposal), `draft`, `calculate`, `build`, `ingest` and `index_status`. Read with `GET /api/jobs?project=...` or `GET /api/jobs/{id}`; stop with `POST /api/jobs/{id}/cancel`. `GET /api/jobs/{id}/events` streams authenticated activity/result snapshots. Existing session, Origin and CSRF guards apply. This transport is not a complete typed OpenAPI contract.

Operations capture source/review/model identity and reject stale execution. A claim fences publication; new domain results and success commit together. Cancellation terminates the owned worker/calculator process group and prevents subsequent publication. Lost heartbeats become interrupted failures after 60 seconds. An uncertain remote request is never automatically retried; inspect saved state before explicitly resubmitting. Queue acceptance is bounded to 16 pending operations and each child has a 10-minute deadline. Network work runs outside SQLite write transactions.

Ask's model is the provider chosen in Services: an OpenRouter free model, or the signed-in ChatGPT plan on a local server ([details](chatgpt.md)). **Grounding gate.** Before a new answer is published, every heading, body statement and conflict is checked by one additional call on the same provider and model. MCDXKit then confirms allowed evidence IDs, verbatim quotes, a quote for every inline citation, and that digits, recognized number-unit pairs and common minimum/maximum phrases appear in the quotes. The answer is published only if every statement passes; otherwise, or when no verifier is available or it fails, the draft is withheld and a server-authored explanation and a fixed `reason_code` (for example `invalid_verifier_response` or `provider_unavailable`) are saved with the retrieved passages. The check (`grounding/5`) asks for the whole source sentence that states each value, with its subject, qualifiers and every condition. Each statement may be supported by up to 8 distinct quotes from up to 8 evidence IDs. One quote may be at most 2,000 characters, so long engineering sentences fit; a longer sentence may be quoted as consecutive verbatim parts. All quotes for one statement may total at most 6,000 characters, and all quotes in one verifier report at most 20,000. These are size guards, not a token guarantee. The 8,192-token OpenRouter verifier output cap applies independently, and a reply it cuts off is withheld. Oversized, truncated or malformed verifier replies are withheld, never trimmed, repaired or replaced by another provider. The 90,000-character cited-evidence input limit is unchanged. Nothing is retried. The model's free-text assessment and gaps are never shown; explanations and gaps are written by MCDXKit. Workflow guidance without document evidence is server-authored from recorded project state. Each clause of a statement is bound to the full source sentence that states its value: signs must match, it must share that sentence's content and negation, keep every listed qualifier and number-unit condition stated with the value (for example "for new walls", "over 50 years"), and not conflict with it (fixed list such as new vs existing, total vs differential, service vs strength). Clauses that only report disagreement or an unestablished point are exempt. Unlisted qualifiers rely on the verifier; tolerance and exponent formats are refused.

**Audit receipt.** `GET /api/documents/{pid}/answers/{aid}/audit` (authenticated, read-only) downloads a versioned JSON receipt (`mcdxkit.answer-receipt/1`) for one saved answer: model/provider and versions, published text and status, searches and coverage, original pages read, each checked statement with its quotes, every retrieved vs cited passage with source name, saved SHA-256 and page, current source-file integrity (stored file vs register, and the answer's recorded source hashes vs register), whether the project changed since, and a SHA-256 digest of the receipt. The digest detects edits; it is not a signature or proof of truth. Older answers show grounding as unrecorded. Several statements often cite the same page because one clause states a value and its conditions; the receipt shows searched, read and cited evidence separately. The same model can repeat its own mistakes, so this reduces unsupported statements but does not guarantee correctness. Answers saved before this check are labelled as not grounding-checked. Details, limits and the receipt fields: [Ask answer checks and audit receipts](answer-audit.md).

The agent (`knowledge/9`) has at most six model turns; one additional formatting call on the same model may repair an invalid/missing structured answer. It must still pass schema/citation validation. Per turn it may make three `search_documents` calls, each with one main query and up to two supporting condition/exception queries. Six distinct (whitespace/case-normalised) queries may run per turn; a repeated query does not consume that budget and reuses the same evidence IDs without another retrieval. Every passage the server returns is shown to the agent, and only shown passages, original pages and extraction quotes are citeable. Whitespace-equivalent passages from one source keep one evidence ID. `read_context` reads the original PDF pages around up to three current evidence passages from one source (anchor ±1 page, at most two reads and twelve pages per turn, no model call, blank neighbouring pages omitted and reported) so read-only answers can cite adjacent conditions. A page longer than 6,000 characters is returned as a marked prefix; the tool result, saved evidence, context record and answer gaps all state that the rest of that page was not read. Context reads never claim full-section coverage.

Tool budgets (three searches, two context reads, one extraction or save proposal) are enforced inside the tools. A call beyond a budget runs nothing and returns an explicit "closed for this turn" result; closed tools are withdrawn from later model calls, and the sixth model call offers no research tools, only the structured answer. A run therefore ends with a validated answer from the evidence found unless the model keeps requesting tools anyway; then it fails with an error saying so, publishes nothing and is not retried.

Reading is the default; saving requirements is a write. The agent declares the user's intent once on `search_documents` (`task: answer|extract`), before reading evidence, and a direct save also needs `user_request_quote`, the user's own words from the current message (checked verbatim server-side; retrieved text, history and tool output cannot supply it). Without both, `extract_requirements` saves nothing: the server resolves the evidence to a source and pages and stores an `extraction_offer` on the answer. The answer shows **Save for review**; it submits the `extract_offer` job with only the answer ID, and the server runs the same durable extraction (same identity, claim, no-retry rules) from the stored scope. The declared intent is a model judgement, not a keyword list; a misjudged direct save is still unreviewed and cannot change inputs, and every saved run is listed with its request. Unknown, foreign or multi-source evidence IDs, missing page metadata and unreadable text layers are returned to the agent as tool errors before any file read or provider call; they never add evidence. Extraction remains one call per turn.

New research answers place compact citations directly after the claims they support; the canonical form is `[E3][E7]`. The parser also reads `[E3, E7]`, `[E3; E7]`, lower-case or zero-padded IDs and Markdown links `[E3](...)`, and new answers are saved in canonical form. Every inline ID must be listed in that section's `evidence_ids` and be evidence returned in the same turn. Earlier answers with section-level citations only are stored and shown unchanged; the audit lists sections without inline citations rather than inventing claim links. Before an answer is saved, the server checks citation identity (section and inline `[E#]` markers), requires listed conflicts for a `conflicted` status, and matches every number stated in a section literally against that section's cited text. A page reference such as "PDF page 132" is exempt only when every page it names is an original page of that section's cited evidence; the same number used as a value is still checked. Unmatched numbers are saved and shown as "check these values"; the answer is not rewritten and the check is not semantic verification. It does not stream unvalidated answer tokens or private model reasoning. Human clarification is a persisted answer and follow-up, not a live blocked database transaction or an arbitrary tool-permission prompt.

Job activity is typed: `step`, `search` (query, passage count, source names), `context` (source, pages), `extraction` (source, pages, fact count), `check` (citation count, unmatched numbers). Fields are bounded server-side and never include payloads, secrets or model reasoning. Older workspaces gain the nullable `kind`/`detail` columns in place; earlier string-only activity renders as steps. Each activity item carries its durable `seq`. The workspace SSE stream (`/events`, used by the React UI) emits whenever activity grows.

### AG-UI projection

`GET /api/jobs/{id}/agui` streams the same durable job as [AG-UI](https://github.com/ag-ui-protocol/ag-ui) 1.0 events, built and encoded with the official MIT `ag-ui-protocol` SDK (`ag_ui.core` models, `ag_ui.encoder.EventEncoder`). It uses the same session, Origin and job lookup as other job routes. Mapping:

| Job ledger | AG-UI event |
| --- | --- |
| job accepted | `RUN_STARTED` (`runId` = job ID, `threadId` = project ID, `protocolVersion` 1.0) |
| activity row | `ACTIVITY_SNAPSHOT` (`messageId` = `{job}-{seq}`, `activityType` = `mcdxkit.step|search|context|extraction|check`, content = message + bounded detail) |
| succeeded | `RUN_FINISHED` with references only (`record_id`, `conversation_id`, `extraction_ids`, `clarification_requested`) |
| cancelled | `RUN_FINISHED` with the `cancelled` outcome; metadata records whether a remote request may have been charged |
| failed / interrupted | `RUN_ERROR` (`code` `uncertain_remote_request` or `failed`) |

Each SSE message has an `id:` (`run`, the activity `seq`, or `end`); reconnecting with `Last-Event-ID` resumes after that point within the ledger's retained 40 activity rows. The projection is read-only: it does not accept `RunAgentInput`, start or resume runs, expose tool-call arguments, stream answer text or reasoning, or represent clarification as a resumable AG-UI interrupt (follow-ups remain new Ask jobs with `parent`). Runs are threaded by project because a new conversation's ID is its first saved answer, which does not exist when the run starts; `metadata.mcdxkit.conversation_id` carries it once known. `mcdxkit.*` activity types are this producer's vocabulary, which AG-UI leaves open; AG-UI does not define their meaning or how they are displayed. The React workspace still consumes `/events`; moving it to `@ag-ui/core` schemas is a separate change.

```bash
# In one terminal: owns the workers and their lifecycle.
mcdxkit serve --output-dir ./results --no-open
# In another: submit once; reuse the key only for identical inputs/snapshot.
mcdxkit documents --workspace ./results/.documents submit PROJECT ask \
  --payload '{"question":"What is the next step with these files?"}' --request-key my-request-1
mcdxkit documents --workspace ./results/.documents jobs PROJECT
mcdxkit documents --workspace ./results/.documents job JOB
mcdxkit documents --workspace ./results/.documents cancel JOB
# Direct foreground calls remain available without server workers.
mcdxkit documents --workspace ./results/.documents ask PROJECT --parent ANSWER --question 'Find requirements first'
mcdxkit documents --workspace ./results/.documents extract PROJECT SOURCE --pages '131-132,147' --question 'Settlement limits and conditions'
mcdxkit documents --workspace ./results/.documents review-requirement PROJECT REQUIREMENT \
  --status reviewed --reviewer 'Reviewer' --note 'Checked original pages and conditions'
# Omit DRAFT to calculate retained template inputs without asserting basis review.
mcdxkit documents --workspace ./results/.documents build PROJECT
```


Ask can call `extract_requirements(question, evidence_ids)` when the user requests extraction. It reuses the same selected-page engine as the form and CLI (one extraction per turn, at most 12 PDF pages including adjacent context), saves unreviewed values and conditions, and returns source citations. Search, project inspection and extraction use actual activity events. The original PDF opens from inline `[E1]` references or page buttons; multi-page evidence exposes each supplied page. Evidence without parser page coordinates opens the original document and explicitly says the page is unavailable.

A successfully saved extraction remains available in Requirements even if subsequent answer formatting fails. Ask never reviews a requirement, changes an input or runs a calculation autonomously. A durable identity claim prevents concurrent requests from charging twice for the same extraction. Interrupted claims do not expire automatically: inspect saved results first; only submit a new, narrower request if another provider call is intended. Normal reported failures require an explicit retry. Restart the server and workers together after updating package code so their operation protocol versions agree.


The primary extraction entry is natural-language Ask. The agent selects current retrieved evidence IDs; the server resolves original source/page coordinates and expands by one neighboring page in each direction (bounded to 12 pages). A user need not know filenames or page numbers. Unknown/foreign citations, mixed-source selections and missing page metadata cannot become an extraction. Narrowing is about the requested engineering scope. The original selected-page form remains an advanced option in Sources; CLI callers may still specify pages explicitly. See [retrieval research and evaluation plan](retrieval-research.md).
