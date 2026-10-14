# Source-backed design packages

Use **Design packages** in the browser, `mcdxkit documents`, or `DocumentWorkflow` in Python. This workflow uses RAGFlow for PDF ingestion/retrieval and a specific OpenRouter `:free` model for cited narrative and scalar input proposals. Existing MCDXKit preflight and CalcpadCE perform all conversions/calculations. Native Mathcad execution is not verified by this workflow.

## Browser

Open **Service settings** to enter the RAGFlow URL/API key, installed embedding model and OpenRouter API key plus a specific `provider/model:free`. An optional RAGFlow reranker ID can be saved too. Blank key fields preserve existing keys; changing the RAGFlow endpoint requires entering its key again. Saving validates the configuration, not the service connection; indexing/drafting checks connectivity.

Browser settings override environment defaults for this workspace and are read by the CLI too. Keys are stored in `output-dir/.documents/.credentials.json` with owner-only permissions on POSIX, never returned by the API, put in browser storage, or included in generated packages. This is a local plaintext credential file, not a secret vault: protect the server account and backups. All authenticated users of this trusted shared workspace can update settings. Use HTTPS in network mode; keep environment/secret-file deployment for centrally managed credentials. Remove the credential file locally to restore environment defaults.

1. In the calculation workspace, select a report, template, cases and load mapping. Choose **Create design package** on that report's Inputs card. Alternatively open **Design packages → New design package** and select the report/template there. Explicit overrides from the conversion screen are not silently imported; use a revised template or review document-backed proposals.
2. Add source PDFs. Files are stored locally before indexing. Open any source to inspect its original PDF. Each source has a content hash; identical bytes are deduplicated even if filenames differ. Different revisions are retained as separate sources, not silently superseded. Select the applicable revisions for a new project; there is no automatic precedence adjudication.
3. **Index sources** submits PDFs to the configured RAGFlow instance. **Refresh status** reports its actual processing state. All sources must be indexed before drafting. No estimated completion percentages are invented.
4. Describe the design basis to prepare, then **Create cited draft**. This sends bounded retrieved excerpts, project title, request and template scalar definitions to OpenRouter. It does not send the entire PDFs or raw load report to OpenRouter. RAGFlow receives the full PDFs. Only a specifically configured `provider/model:free` is accepted; random routers and paid-model fallback are disabled. Provider training is denied in the request; requests may fail if no eligible free provider exists. Retention policies remain provider-specific.
5. Read the draft and original cited pages. Check applicable editions, units, exceptions and scope. Accept only the proposed inputs that apply. Unchecked suggestions are not applied. Save reviewer and review note, including unresolved information. This records design-basis acceptance, not professional certification.
6. **Calculate & create package** executes the reviewed inputs through the existing engine. Read the report or download the ZIP. A completed calculation can contain failing design checks; completion is not a claim of safety.

The package contains `report.md`, `report.html`, `calculation.mcdx`, `calculation.cpd`, `calculation.html`, `calculation.audit.json`, original PDFs and template/load snapshots, and `manifest.json`. Numerical results and check summaries come directly from the engine. AI-authored prose is labelled separately. Source hashes, retrieved passages/locations, model identity, input decisions and reviewer notes remain with the package. Progress indicators are never inserted in these files.

## CLI

Configure services following [local deployment](../deploy/ragflow.md). The CLI and browser share a project store when pointed at the same `output-dir/.documents` directory.

```bash
mcdxkit documents config
mcdxkit documents --workspace ./results/.documents create loads.gp11t \
  --template design.mcdx --title 'Foundation design basis' --cases 1 7
# Use the returned project ID below.
mcdxkit documents --workspace ./results/.documents add PROJECT specification.pdf drawings.pdf
mcdxkit documents --workspace ./results/.documents ingest PROJECT
mcdxkit documents --workspace ./results/.documents status PROJECT
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
project = workflow.create('Design basis', 'loads.txt', 'template.mcdx', cases=[1, 7])
# add_source, ingest, status, draft, review and build use the same contracts as the CLI.
```

`document_clients.py` owns bounded HTTP adapters and secret configuration. `documents.py` owns immutable source snapshots, SQLite project state, structured draft validation, review and package assembly. `document_cli.py` and the HTTP/browser adapters share it. The calculation engine imports neither model client nor authoring service. RAGFlow owns OCR, chunks and retrieval; we do not recreate them. No LangChain or Deep Agents runtime is needed for the fixed workflow.

SQLite serializes mutations across CLI/server processes. This remains a single trusted-team workspace, not tenant-isolated hosting. Back up the whole document directory plus RAGFlow's deployment configuration and persistent volumes. Never commit these directories. Changed source bytes block generation; adding a source invalidates older draft reviews. Existing package exports remain immutable and independently inspectable. A new draft is a new record; review changes never rewrite an already-issued package.

Limits: 100 PDFs / 2 GiB per project; 256 MiB per PDF; 100 drafts and 100 packages; 12 retrieved passages of at most 6,000 characters per draft. Large files must be split into labelled parts. Citation validation establishes source identity, not factual entailment or engineering correctness. PDF page positions are shown only when the parser supplies them. Formula authoring, automatic code-edition resolution, unrestricted agents, native Prime execution, automatic retries and offline LLM drafting are not implemented. Existing calculation and stored-source/package inspection remain local/offline; this drafting adapter requires OpenRouter network access.

## Validation

Synthetic lifecycle tests execute the real calculator. Protocol tests exercise real HTTP against controlled RAGFlow/OpenRouter response fixtures, including HTTP-success business failures, redirect refusal and rate limits. These are separate from live-service evaluation. Before operational use, run the pilot PDFs against a configured RAGFlow instance and chosen free model; verify citations, redline revisions, qualified values, missing evidence and drawing/table extraction. A passing mock/protocol test is not evidence of parser/model quality.
