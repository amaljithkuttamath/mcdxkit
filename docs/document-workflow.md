# Document library and calculated design packages

Use **Document library** in the browser, `mcdxkit documents`, or `DocumentWorkflow` in Python. This workflow uses RAGFlow for PDF ingestion/retrieval and a specific OpenRouter `:free` model for cited narrative and scalar input proposals. Existing MCDXKit preflight and CalcpadCE perform all conversions/calculations. Native Mathcad execution is not verified by this workflow.

## Browser

Opening the library or **Connect services** automatically checks the configured RAGFlow endpoint (or the bundled loopback endpoint), authenticates with installation credentials, and discovers its enabled default embedding model if no explicit model is set. Explicit endpoints and models are preserved. Failed health, stopped/missing Docker, missing deployment, missing service credentials and missing default models have distinct states. **Advanced connection settings** is for administrators connecting a separate server, not the normal engineering workflow. Add an OpenRouter key, choose from the discovered free models and save. Blank keys preserve existing credentials; changing a custom RAGFlow endpoint requires entering its key again.

Automatic discovery does not install Docker, create a RAGFlow tenant/API key or download embedding weights. **Start document search** starts only the installation’s already-prepared Compose stack, without accepting a command or path from the browser. Startup may pull images, runs in the background, and keeps a private local log; failures preserve service volumes and documents. The current deployment scaffolding is not yet a fully managed first-run installer. Administrators must provision the bundle once with its internal key; engineers should not be asked to supply those infrastructure values. `mcdxkit documents discover` runs the same discovery as the browser. `MCDXKIT_RAGFLOW_DIRECTORY` identifies the prepared deployment for runtime diagnostics; it is a server setting, never a browser-supplied filesystem path.

Browser settings override environment defaults for this workspace and are read by the CLI too. Keys are stored in `output-dir/.documents/.credentials.json` with owner-only permissions on POSIX, never returned by the API, put in browser storage, or included in generated packages. This is a local plaintext credential file, not a secret vault: protect the server account and backups. All authenticated users of this trusted shared workspace can update settings. Use HTTPS in network mode; keep environment/secret-file deployment for centrally managed credentials. Remove the credential file locally to restore environment defaults.

1. Open **Documents → Create library** and enter a project title. No report or template is required. You can also start from the calculation workspace's **Create design package** action with its already selected files/cases/mapping.
2. Add source PDFs. Files are stored locally before indexing. Open any source to inspect its original PDF. Identical bytes are deduplicated; different revisions remain separate sources. There is no automatic revision precedence decision.
3. **Index sources**, then **Refresh status**. All sources must finish indexing before asking or drafting. **Ask with citations** answers your question, saves its evidence snapshot, and links to original pages. Earlier answers remain available in **Saved answers**, including after adding documents or connecting calculation files. They are historical answers, not automatically refreshed conclusions.
4. When ready, **Connect calculation files** to the same library: choose the raw report, template, cases and optional explicit input map. Preflight validates the stored snapshots. Existing PDFs, index IDs and answers remain intact; no reindex is needed for this connection. A connected calculation cannot be replaced in place; use another project for different calculation files. Use **Open calculation workflow** to inspect the connected inputs/template in the existing Files → Inputs → Changes → Outputs interface; the project link returns to the same notebook. Then **Create cited draft** prepares the design basis and proposes only unmapped scalar inputs. Asking the library remains available independently.

Asking sends retrieved excerpts, project title and your question to OpenRouter. Design drafting additionally sends template scalar definitions and the selected load summary (units, case/pile provenance and source hashes). It does not send the entire PDFs or raw report to OpenRouter. RAGFlow receives full PDFs. Only a configured `provider/model:free` is accepted; paid/random fallback is disabled. Provider training is denied in the request, but retention policies remain provider-specific. Missing supporting evidence is an error or explicit gap, not permission to invent an answer.

5. Read the draft and original cited pages. Check applicable editions, units, exceptions and scope. Accept only the proposed inputs that apply. Unchecked suggestions are not applied. Save reviewer and review note, including unresolved information. This records design-basis acceptance, not professional certification.
6. **Calculate & create package** executes the reviewed inputs through the existing engine. Read the report or download the ZIP. A completed calculation can contain failing design checks; completion is not a claim of safety.

The package contains `report.md`, `report.html`, `calculation.mcdx`, `calculation.cpd`, `calculation.html`, `calculation.audit.json`, original PDFs and template/load snapshots, and `manifest.json`. Numerical results and check summaries come directly from the engine. AI-authored prose is labelled separately. Source hashes, retrieved passages/locations, model identity, input decisions and reviewer notes remain with the package. Progress indicators are never inserted in these files.

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
# Later, connect calculation files without losing your PDFs or answers.
mcdxkit documents --workspace ./results/.documents connect PROJECT loads.gp11t \
  --template design.mcdx --cases 1 7
mcdxkit documents --workspace ./results/.documents draft PROJECT \
  --request 'Identify applicable material requirements and assumptions for this template.'
mcdxkit documents --workspace ./results/.documents show PROJECT
# Indices are zero-based; omit --accept to retain every existing template input.
mcdxkit documents --workspace ./results/.documents review PROJECT DRAFT \
  --accept 0 --reviewer 'Reviewer name' --note 'Verified applicability and units against the cited source.'
mcdxkit documents --workspace ./results/.documents build PROJECT DRAFT
mcdxkit serve --output-dir ./results
```

Commands print JSON; errors exit 2. `build` prints the package path. `create --input-map '{"AxialCustom":"P"}'` supports custom templates through the same explicit mapping/preflight as normal conversion. Summary-only reports require explicit cases. Missing model configuration does not affect ordinary `convert`.

## SDK and boundaries

```python
from mcdxkit.documents import DocumentWorkflow
workflow = DocumentWorkflow('results/.documents')
project = workflow.create('Bridge project library')
# add_source, ingest, status and ask work without calculation files.
# Later: workflow.attach_calculation(project['id'], 'loads.txt', 'template.mcdx', cases=[1, 7])
# draft, review and build then use the connected calculation.
```

`document_clients.py` owns bounded HTTP adapters and secret configuration. `documents.py` owns immutable source snapshots, SQLite project state, structured draft validation, review and package assembly. `document_cli.py` and the HTTP/browser adapters share it. The calculation engine imports neither model client nor authoring service. RAGFlow owns OCR, chunks and retrieval; we do not recreate them. No LangChain or Deep Agents runtime is needed for the fixed workflow.

SQLite serializes mutations across CLI/server processes. This remains a single trusted-team workspace, not tenant-isolated hosting. Back up the whole document directory plus RAGFlow's deployment configuration and persistent volumes. Never commit these directories. Changed source bytes block generation; adding a source invalidates older draft reviews. Existing package exports remain immutable and independently inspectable. A new draft is a new record; review changes never rewrite an already-issued package.

Limits: 100 PDFs / 2 GiB per project; 256 MiB per PDF; 100 answers, 100 drafts and 100 packages; 12 retrieved passages of at most 6,000 characters per draft. Files over the limit are rejected; the app never splits or merges originals. Citation validation establishes source identity, not factual entailment or engineering correctness. PDF page positions are shown only when the parser supplies them. Formula authoring, automatic code-edition resolution, unrestricted agents, native Prime execution, automatic retries and offline LLM drafting are not implemented. Existing calculation and stored-source/package inspection remain local/offline; this drafting adapter requires OpenRouter network access.

## Validation

Synthetic lifecycle tests execute the real calculator. Protocol tests exercise real HTTP against controlled RAGFlow/OpenRouter response fixtures, including HTTP-success business failures, redirect refusal and rate limits. These are separate from live-service evaluation. Before operational use, run the pilot PDFs against a configured RAGFlow instance and chosen free model; verify citations, redline revisions, qualified values, missing evidence and drawing/table extraction. A passing mock/protocol test is not evidence of parser/model quality.

### Choosing a model in the browser

Open **Connect services**, paste an OpenRouter API key, and leave the field. The server loads the account-filtered model catalog from OpenRouter and offers every compatible free text model with zero reported pricing. Select a model and save. Reopening settings with a saved key refreshes the list; **Refresh models** retries failed or empty results. Discovery does not generate text, send project documents, or save a newly entered key. Saving settings is explicit. Keys never appear in the response, browser storage, or exported files.

The catalog follows [OpenRouter account preferences](https://openrouter.ai/docs/api/api-reference/models/list-models-filtered-by-user-provider-preferences-privacy-settings-and-guardrails). A listed model is not a guarantee of capacity, structured-answer quality, or availability under the drafting request's no-data-collection policy. Requests can still be refused or rate-limited; no paid fallback is enabled.

### One OpenRouter key for search and answers

The browser also lists free text-embedding models from OpenRouter's separate `/embeddings/models` catalog. Choose a search model and an answer model, then save. For the prepared local bundle, MCDXKit provisions its internal RAGFlow account and registers the search model with RAGFlow's existing OpenRouter driver. It does not install Ollama or download local model weights. Original PDFs stay in the project and RAGFlow; indexing sends extracted text to OpenRouter for embeddings. Embeddings follow the account/provider privacy policy, whereas drafting also requests `data_collection: deny`. Hosted search and answers require network access. Catalog discovery is not an execution test.

Existing embedding models are never silently replaced: changing vector models requires a new workspace. A missing/invalid key, absent free models or service errors leave setup incomplete, with no paid fallback. Advanced fields support separately managed deployments. See [deployment setup](../deploy/ragflow.md).
