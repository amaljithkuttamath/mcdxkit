# Natural-language requirements research

The engineer asks a question. The system owns document discovery, retrieval, page resolution and citation navigation. It asks the engineer only to resolve genuine scope/applicability ambiguity; source filenames, parser coordinates and page selection are not required primary inputs.

Research below is a design lead, not evidence that MCDXKit is accurate. Benchmark numbers are reported by the cited authors or vendors; none were reproduced here. Accuracy on project documents is established only by the private evaluation described at the end.

## Recent leads (verified by the maintainers on 2026-10-15)

| Source | Idea taken | In MCDXKit (`knowledge/5`) | Not adopted / deferred |
| --- | --- | --- | --- |
| [Perplexity, Rethinking search as code generation](https://www.perplexity.ai/hub/blog/rethinking-search-as-code-generation) (2026-06-01) | Separate composable retrieval/fan-out/filter primitives and explicit intermediate evidence from the model's task strategy. | **Implemented as typed, bounded tools**: `search_documents` (main + ≤2 supporting queries, server-enforced budget, deduplication), `read_context`, `extract_requirements`. Intermediate evidence is persisted with IDs and shown in the timeline. | Model-generated code execution and Perplexity's infrastructure. Arbitrary code would change the trust boundary for private engineering documents. |
| [Perplexity Q2D-Web](https://arxiv.org/abs/2609.08887) (2026-09) | Agent-reformulated questions and complementary retrieval families; evaluate multiple evidence items and hard negatives. | **Partly**: the agent rewrites the question and adds condition/exception queries; RAGFlow hybrid keyword+vector retrieval is reused. | Its web-scale benchmark corpus is private; web results do not establish engineering accuracy. Hard-negative cases belong in our private evaluation set. |
| [VecTree-RAG](https://arxiv.org/abs/2607.23006) (2026-07-25) | Route by vector search, then read source-grounded sections progressively instead of trusting top-k chunks. | **Partly**: `read_context` reads original PDF pages around evidence (±1 page, bounded) for read-only answers as well as extraction. | Section/outline tree. Next candidate (phase 2 below) if evaluation shows boundary-spanning conditions are still missed. |
| [pplx-embed-v2 contextual preview](https://huggingface.co/perplexity-ai/pplx-embed-v2-context-9b-preview) (2026-09-30) | Encode a chunk jointly with its document context. | Not adopted. | Incompatible embeddings require a new index version, an adapter and full reindexing; not a configuration switch. No model download in this increment. |
| [Perplexity multimodal late interaction](https://www.perplexity.ai/pt-PT/hub/blog/multimodal-embeddings-beyond-a-single-vector) (2026-10-07) | Multiple token/patch vectors for text and rendered-page retrieval. | Not adopted. | Candidate for tables and scanned pages; higher storage/scoring cost. Retrieval of a page image does not replace literal extraction checks. |

## Earlier foundations

| Research | Relevant finding | Application and limit |
| --- | --- | --- |
| [Query Rewriting for Retrieval-Augmented LLMs](https://arxiv.org/abs/2305.14283) | Rewriting can bridge user wording and retrievable evidence. | Follow-up resolution and focused technical queries; we did not train their rewriter. |
| [Corrective RAG](https://arxiv.org/html/2401.15884v3) | Retrieval quality should affect subsequent actions. | Bounded refinement, explicit insufficient/conflicted status. No trained evaluator, no public-web fallback. |
| [Self-RAG](https://arxiv.org/html/2310.11511v1) | Selective retrieval and critique. | Prompted relevance/support assessment only; self-assessment does not verify truth. |
| [Lost in the Middle](https://arxiv.org/html/2307.03172v2) | More context does not ensure reliable use. | Deduplicated, capped passages; page context only on demand and within a 12-page budget. |
| [ALCE](https://arxiv.org/abs/2305.14627) | Correctness and citation quality need separate evaluation. | Citation identity and literal-number checks are reported separately from the model's assessment. A valid citation is not proof of entailment. |

## Current pipeline

1. **Pre-retrieval.** The agent resolves follow-ups from conversation context (history is never evidence) and plans one main query plus up to two condition/exception/definition queries per search call. Server limits: three calls, six distinct queries per turn; a repeated query does not consume the budget and reuses its evidence IDs without another retrieval. Every returned passage is shown to the agent; nothing unseen is citeable.
2. **Retrieval.** RAGFlow searches only ready document IDs in the selected project (hybrid keyword+vector, optional configured reranker). Foreign document IDs are rejected. Whitespace-equivalent passages from one source keep a single stable ID with merged page positions. Rank is retrieval order, never a confidence percentage.
3. **Context recovery.** `read_context` maps current evidence IDs to one source's original PDF pages and reads each anchor page with its neighbours in the isolated PDF subprocess (no model call, ≤3 anchors per read, ≤2 reads and ≤12 pages per turn). Pages become citeable evidence that opens the PDF at that page. Pages longer than 6,000 characters are returned as marked prefixes and recorded as gaps; this is page-window context, not section completeness.
4. **Extraction (explicit requests only).** Evidence IDs resolve to original pages; neighbours are expanded within 12 pages; the shared DeepSeek extractor runs once; literal values and condition quotes are checked against those pages. Results remain unreviewed.
5. **Post-retrieval checks before publication.** Citation identity (section and inline markers), listed conflicts for `conflicted`, and literal matching of every stated number against the section's cited text. Unmatched numbers are published as warnings. Unindexed sources are listed as gaps. Nothing here checks meaning, completeness or applicability.
6. **Persistence and UI.** Answer, searches, page reads, extraction IDs, audit and evidence are saved. A typed timeline shows observed steps; citations open the source drawer; review and input binding stay explicit human actions.

## Evaluation before additional architecture

Create a private, reviewer-labelled set of natural-language project questions: direct value, follow-up/pronoun, shared condition, boundary between pages, conflicting revision, table, scanned page, unanswerable question, hard negative (similar wording, wrong element) and mixed project. Keep a held-out split. Record gold source/page, value/unit, mandatory conditions and applicability. Do not commit client PDFs or derived sensitive text.

Measure page recall@k, irrelevant-context rate, value/unit accuracy, condition recall, numeric-claim literal-match rate, supported-claim/citation precision, abstention accuracy, conflict recall, latency and provider cost. Ablate: single query vs fan-out; with vs without `read_context`; reranker on/off. Model self-grading is diagnostic, not an engineering review. The executable version, with gold-label schema, per-stage metrics, first-loss attribution, frozen retrieval replay and the acceptance rule, is the [retrieval evaluation spec](retrieval-evaluation.md).

Deferred candidates, only after evaluation identifies the failure: section/outline-aware context, table/OCR preservation, contextual or late-interaction embeddings (new index version), an opt-in claim-entailment check, and revision-precedence metadata. Avoid GraphRAG, a second vector database or a fleet of agents without measured benefit on project tasks.
