# Ask retrieval and answer evaluation

Status: specification, not yet implemented. Nothing below has been run. It defines how to measure the Ask pipeline stage by stage, so a retrieval or generation technique is adopted only when it measurably helps on project questions.

The [grounding benchmark](grounding-evaluation.md) already measures one stage: whether the publication gate publishes supported statements and withholds unsupported ones, using 24 synthetic claims with their evidence supplied. It does not measure whether the right page was retrieved, whether conditions were read, or whether the drafted value was correct. The [research notes](retrieval-research.md#evaluation-before-additional-architecture) list the metrics and ablations wanted. This document makes them executable: gold labels, per-stage metrics computed from a recorded trace, first-loss attribution, frozen retrieval replay, an ablation matrix with an acceptance rule, and two corpora.

Passing this evaluation is software evidence about the Ask pipeline. It is not engineering review, and it does not establish revision precedence or applicability.

## Pipeline stages and their cutoffs

The stages below follow `DocumentWorkflow.ask` in `src/mcdxkit/documents.py` and the agent in `src/mcdxkit/knowledge.py` (`knowledge/9`, `grounding/5`). Each stage has a hard cutoff where evidence can be lost, so each gets its own recall.

| # | Stage | Where evidence can be lost |
| --- | --- | --- |
| 0 | Coverage | The gold source is not indexed; it appears in `coverage.excluded` and is never searched. |
| 1 | Pre-retrieval | The agent's queries (one main plus up to two supporting per call, at most six distinct per turn) miss wording that the raw question would have matched, or the reverse. |
| 2 | Retrieval | RAGFlow returns `page_size=12` chunks per query (from `top_k=64`, `similarity_threshold=0.2`, keyword plus vector, optional `rerank_id`). A gold page outside the 12 is lost. |
| 3 | Exposure | `collect_evidence` keeps only the first 8 chunks per query, deduplicates whitespace-equivalent text, and stops at 36 retrieved passages per turn. A gold chunk at rank 9 to 12, or behind the turn cap, was retrieved but never shown to the agent. |
| 4 | Context recovery | `read_context` reads anchor pages ±1, at most 3 anchors per read, 2 reads and 12 pages per turn, and 6,000 characters per page. A condition on an unread page, beyond the window, or past the prefix is lost. |
| 5 | Generation | The draft states the wrong value or unit, omits a mandatory condition, cites the wrong page, or chooses the wrong behaviour (answer, abstain, conflict). |
| 6 | Publication gate | `grounding_gate` withholds a correct draft (over-withholding) or publishes an unsafe one. |
| 7 | End to end | The published answer is correct, cited to the gold page and published. |

## What the saved answer record can and cannot support

The answer record already persists `searches`, `evidence`, `context_reads`, `grounding`, `coverage`, `audit` and `assessment`. Several metrics need facts that the record deliberately or incidentally omits, so the evaluation uses two sources.

**From the saved record** (mechanical, no extra capture):

- Coverage: `coverage.ready`, `coverage.total`, `coverage.excluded`.
- Which pages were shown to the agent: `evidence[*]` with `source_sha256` and `positions[*][0]`; `kind` absent means a retrieved passage, `page_context` means a page read, `extraction_quote` means an extraction quote.
- Context recovery: `context_reads[*]` with `anchor_pages`, `pages`, `truncated_pages`, `omitted_pages`.
- Publication: `grounding.status` (`grounded`, `withheld`, `abstained`, `workflow`), `grounding.reason_code`, per-statement verdicts, published `content.sections` with `evidence_ids`, and `assessment.status`.

**From an evaluation-only trace** (captured by the harness, never written into the product record):

1. **Rank per query.** `searches[*]` stores only `query` and `passages`, and `evidence` is one deduplicated list in first-seen order. Recall@k and first gold rank cannot be computed from a production record. The harness passes its own retriever as `ask(..., client=...)` and logs every raw chunk per query in RAGFlow order, including ranks 9 to 12 that `collect_evidence` drops.
2. **The pre-gate draft.** When the gate withholds, `_withheld` saves only statement IDs, verdicts and server-authored reasons, never the draft text. This is an existing invariant and stays unchanged. The harness wraps `agent.answer` to capture the validated `KnowledgeAnswer` payload (sections, `status`, `conflicts`) and wraps `verify` to capture the statements and passages sent to the gate.
3. **The pre-gate status.** Withholding rewrites `assessment.status` to `insufficient`. Abstention and conflict accuracy are scored on the captured pre-gate `status`; publication metrics use the post-gate record.
4. **Latency.** No timing is saved on the answer. The harness passes `activity=` to `DocumentWorkflow` and timestamps each typed event (`search`, `context`, `extraction`, `check`) plus wall time for the whole `ask`.
5. **Cost.** No usage is recorded. The harness counts retrieval calls, answer model calls and verifier calls, and records provider-reported token usage where the adapter exposes it. OpenRouter is restricted to `:free` models and ChatGPT is a subscription, so cost is reported as calls and tokens, not currency.
6. **The raw-question baseline.** For stage 1 the harness issues one extra `RAGFlow.retrieve` with the unmodified question and production parameters. It is outside the product's search budget and is never added to `evidence`.

Optional later product change, not required for the first slice: save the ordered evidence IDs returned by each search in `searches[*]`, so rank-based metrics can also be computed on ordinary saved answers.

## Gold labels

One JSON file per corpus, schema `mcdxkit.retrieval-gold/1`. Pages are 1-based physical PDF page indices, the same numbering as `positions[*][0]` and `read_pages`, not printed page labels. Sources are identified by SHA-256 and file name, never by RAGFlow document ID, which changes when a library is reindexed.

```json
{
  "schema": "mcdxkit.retrieval-gold/1",
  "corpus": "synthetic-public-v1",
  "sources": [{"name": "spec-rev-b.pdf", "sha256": "…", "pages": 14}],
  "cases": [
    {
      "id": "sw-settlement-new-walls",
      "split": "dev",
      "category": "cross_page_condition",
      "question": "What is the settlement limit for the new retaining walls?",
      "history": [],
      "expected_behaviour": "answer",
      "evidence": [
        {"role": "value", "source_sha256": "…", "pages": [6],
         "quote": "total settlement shall not exceed 2 inches over 50 years"}
      ],
      "value": {"number": 2, "unit": "in", "accepted_units": ["in", "inch", "inches"], "property": "total settlement"},
      "conditions": [
        {"id": "both-terms", "source_sha256": "…", "pages": [7],
         "quote": "calculate both short-term and long-term settlement",
         "must_mention": ["short-term", "long-term"]}
      ],
      "distractors": [{"source_sha256": "…", "pages": [9], "why": "existing-wall limit, different element"}],
      "conflict": null,
      "labelled_by": "reviewer role, not a name", "labelled_on": "2026-10-15"
    }
  ]
}
```

Field rules:

- `category` is one of `direct_value`, `follow_up`, `cross_page_condition`, `table`, `scanned_page`, `unanswerable`, `hard_negative`, `conflicting_revision`. The research list's "shared condition" and "boundary between pages" are both `cross_page_condition`; "mixed project" is a corpus property (several unrelated sources in one library), not a case category.
- `expected_behaviour` is `answer`, `abstain` or `conflict`.
- `evidence` lists the minimal spans that state the answer. Each `quote` is short enough to fall inside one chunk. A long requirement is split into a `value` span and separate `conditions`, each with its own page and quote.
- `conditions` are mandatory qualifiers without which the answer is wrong (scope, time basis, load case, exception). `must_mention` holds normalized key phrases the answer must contain. This is a mechanical proxy for condition coverage, not a check of meaning.
- `distractors` are pages that look relevant but must not support the answer. Required for `hard_negative`, optional elsewhere.
- `conflict` is required for `conflicting_revision`: two or more evidence spans with their sources and the field that differs. The expected behaviour is `conflict` unless the corpus itself states precedence, in which case it is `answer` from the superseding revision and the older revision becomes a distractor.
- `unanswerable` cases have empty `evidence` and `value` and expect `abstain`.
- `follow_up` cases carry the prior turns in `history`. `conversation_history` passes the agent each prior request and its published answer text (up to 3,000 characters), so a prior question alone does not reproduce production. Each `history` entry therefore holds a `request` and a frozen `answer` fixture, and the harness saves those as prior answers before asking the follow-up with `parent=`. Replaying prior turns live instead is allowed only as a separately labelled, noisier protocol, and its queries must also be in the recording. History is never evidence.
- `split` is `dev` or `heldout`, fixed at labelling time, stratified by category, assigned by a deterministic hash of `id`. A held-out case whose content informed a change moves to `dev` and is replaced.

Label lint (automated, before any run): every `sha256` matches a corpus file; every page is within the page count; every `quote` is found, after whitespace collapse and casefolding, in that page's text as extracted by the same `read_pages` path the product uses. A quote that the product's own reader cannot find is a parsing finding to record, not a label to keep silently. `scanned_page` quotes are checked against the reviewer's transcription and flagged as not machine-verifiable.

## Matching rules

All metrics use these definitions.

- **Page hit:** an evidence row or chunk whose source SHA-256 equals the gold `source_sha256` and any of whose `positions[*][0]` pages is in the gold `pages`.
- **Quote hit:** a page hit whose whitespace-collapsed, casefolded text contains the whole normalized gold `quote`. Page and quote hits are scored separately, because the right page can return the wrong chunk.
- **Value match:** a section states a number equal to `value.number`, extracted with the citation audit's own `_numbers` helper in `knowledge.py` (the scorer reuses it rather than adding a second normalizer), and a unit in `accepted_units` is the token immediately after that number, allowing whitespace, compared casefolded. Wrong unit with the right number is a failure.
- **Cited:** the section stating the value cites, through `evidence_ids` or an inline `[E#]` marker, at least one evidence row that is a page hit for a `value` span.
- **Distractor citation:** a section cites a row that is a page hit for a distractor and not for any gold span.

## Metrics per stage

Each metric is computed per case, then aggregated overall, per category and per split. Answer-expected cases (`answer`, `conflict`) contribute to recall metrics; `abstain` cases contribute to behaviour and safety metrics.

| Stage | Metric | Definition | Source |
| --- | --- | --- | --- |
| 0 Coverage | Coverage loss rate | Cases whose gold source is not among ready sources. | Record |
| 1 Pre-retrieval | Raw-question page recall@12 | Value span page-hit in the harness's raw-question top 12. | Trace |
| 1 Pre-retrieval | Query-union page recall@12 | Value span page-hit in the union of all agent queries' top 12. | Trace |
| 1 Pre-retrieval | Fan-out gain / loss | Cases hit by the union but not the raw question, and the reverse. Reported as two counts, not a net. | Trace |
| 2 Retrieval | Page recall@k, k ∈ {1, 3, 5, 8, 12} | Best rank across the turn's queries is ≤ k. | Trace |
| 2 Retrieval | First gold rank, MRR | Lowest 1-based rank of a value page hit across queries; reciprocal averaged, 0 when absent. | Trace |
| 2 Retrieval | Quote recall@12 | As page recall, using quote hits. | Trace |
| 2 Retrieval | Irrelevant-context rate | Share of exposed retrieved passages that hit no gold, condition or conflict page. Diagnostic only: a non-gold page can be legitimately relevant. | Record |
| 3 Exposure | Exposed page recall | Value page hit among `evidence` rows with no `kind` (shown to the agent). Loss reason: `rank_9_to_12`, `turn_cap_36`, or `empty_content`. | Record + trace |
| 4 Context recovery | Condition recall | Conditions whose quote is a quote hit in any exposed row or `page_context` row. Loss reason: `not_read`, `outside_window`, `truncated` (page in `truncated_pages` and quote beyond 6,000 characters), `omitted`, `budget_refused`. | Record |
| 4 Context recovery | Read precision | Share of `page_context` pages that are gold, condition or conflict pages. Diagnostic. | Record |
| 5 Generation | Value/unit accuracy | Pre-gate draft has a value match. | Trace |
| 5 Generation | Draft citation accuracy | Pre-gate value match is cited; distractor citations counted separately. | Trace |
| 5 Generation | Draft condition mention rate | Share of conditions whose `must_mention` phrases all appear in the section stating the value. | Trace |
| 5 Generation | Behaviour accuracy | Pre-gate `status` versus `expected_behaviour`: `supported` or `partial` for answer, `insufficient` or no sections for abstain, `conflicted` with listed conflicts for conflict. Reported as a 3 × 3 confusion matrix. | Trace |
| 6 Gate | Unsafe-publication rate | Published drafts that are unsafe, over all unsafe drafts. Unsafe means: wrong value or unit, a distractor citation, a value stated for an `abstain` case, or `supported` for a `conflict` case. | Trace + record |
| 6 Gate | Over-withholding rate | Withheld or abstained drafts that were correct (value match, cited, all conditions mentioned, right behaviour), over all correct drafts. Reported with `reason_code` counts. | Trace + record |
| 7 End to end | Correct, cited and published rate | Answer cases: `grounding.status` is `grounded`, the published sections have a value match, are cited and mention all conditions, and `assessment.status` is not `conflicted`. Conflict cases: published `conflicted` listing both spans. Abstain cases: no published value for the asked property. | Record |
| 7 End to end | Safe-but-unhelpful rate | Answer or conflict cases that ended `withheld` or `abstained`. Separated from wrong answers. | Record |
| All | Latency | Wall time per `ask`, plus per-stage time from activity events. p50 and p95. | Trace |
| All | Cost | Retrieval calls, answer model calls, verifier calls, tokens where reported. | Trace |

Unsafe-publication and over-withholding are measured here on real drafts produced by the agent over retrieved evidence. The grounding benchmark measures the same gate on fixed synthetic claims. Both are kept; neither substitutes for the other.

## First-loss attribution

Each case gets exactly one outcome label: the earliest stage at which gold evidence disappears. Stages are checked in order, and the first failing check wins.

For `answer` and `conflict` cases, tracking the value span (and, for conflicts, every conflicting span):

1. `lost_coverage`: the gold source is not ready.
2. `lost_query_planning`: no agent query has a value page hit in its top 12, but the raw-question retrieval does.
3. `lost_retriever`: neither the agent queries nor the raw question has a value page hit in the top 12.
4. `lost_exposure`: some query has the hit in its top 12, but no exposed `evidence` row is a page hit. The loss reason from stage 3 is attached.
5. `lost_context`: the value is exposed, but a mandatory condition never appears in exposed or read evidence. The stage 4 loss reason is attached.
6. `lost_generation`: all gold evidence was available to the agent, but the pre-gate draft is not correct. The sub-reason is one of `wrong_value`, `wrong_unit`, `missing_condition`, `uncited`, `distractor_cited`, `wrong_behaviour`.
7. `lost_gate`: the pre-gate draft is correct, but the gate withheld it. The `reason_code` is attached.
8. `success`: correct, cited and published.

For `abstain` cases there is no gold evidence to lose. The outcome is one of `correct_abstention` (pre-gate abstained), `unsafe_draft_caught` (the draft stated a value and the gate withheld it), or `unsafe_published` (a value was published). For `hard_negative` answer cases, a distractor citation that is published is `unsafe_published` regardless of the stage at which the true evidence was lost.

A case where the harness could not complete is `infrastructure_failure` (indexing failed, provider error, timeout) or `replay_miss` (see below). Neither counts as a pipeline loss or as a success, and both are reported as counts next to every score.

The attribution histogram is the main diagnostic. A technique is proposed for the stage with the most losses. For example, many `lost_exposure` cases with reason `rank_9_to_12` point at a reranker or a different exposure cut, not at query rewriting.

## Record and replay

Retrieval is frozen per query so generation and gate changes can be evaluated without RAGFlow, and so CI can score without network access.

Recording, schema `mcdxkit.retrieval-recording/1`:

```json
{
  "schema": "mcdxkit.retrieval-recording/1",
  "corpus": "synthetic-public-v1",
  "gold_sha256": "…",
  "recorded": "2026-10-15T00:00:00Z",
  "sources": [{"sha256": "…", "name": "spec-rev-b.pdf", "document_id": "ragflow-doc-id-at-record-time"}],
  "retriever": {"page_size": 12, "top_k": 64, "similarity_threshold": 0.2, "keyword": true,
                "rerank_id": "", "embedding": "provider/model:free", "chunking": "RAGFlow parser and template name",
                "ragflow_version": "pinned version"},
  "agent_version": "knowledge/9",
  "grounding_version": "grounding/5",
  "queries": {
    "settlement limit new retaining walls": {
      "query": "Settlement limit for new retaining walls",
      "chunks": [{"rank": 1, "id": "…", "source_sha256": "…", "content": "…", "positions": [[6, 0, 0, 0, 0]],
                  "similarity": 0.81, "vector_similarity": 0.77, "term_similarity": 0.9}]
    }
  }
}
```

Rules:

- The query key uses the same normalization as the product's per-turn cache: whitespace collapsed, then casefolded.
- Chunks are stored as RAGFlow returned them, all 12, before `collect_evidence`, keyed to sources by SHA-256. The replay client maps `source_sha256` to the current workspace's document IDs, so `collect_evidence` and its foreign-document rejection run unchanged.
- The replay client implements the same `retrieve(dataset, ids, question)` method as `RAGFlow`. A query whose key is not in the recording raises a dedicated replay-miss error, and the harness marks the case `replay_miss`. It never falls through to a live call, because a changed prompt produces new queries and silently mixing live and frozen retrieval would make the comparison invalid.
- The replay-miss rate is reported for every replay run. Arms compared for acceptance must have a replay-miss rate of zero, or be re-recorded live together.
- A recording is invalid for a run whose corpus SHA-256 values, gold SHA-256 or retriever settings differ.

Replay levels:

| Level | Frozen | Live | Use |
| --- | --- | --- | --- |
| R1 | RAGFlow retrieval | Agent, `read_context` (local PDF reads), gate | Prompt, agent or context-recovery changes that keep the same queries |
| R2 | Retrieval plus captured pre-gate drafts and verifier inputs | Gate | Gate changes, scored against the same drafts |
| R3 | Everything, including captured verifier replies | Nothing | Scorer and attribution changes; the CI path |

R1 and R2 still make model calls. Only R3 is fully offline. `read_context` reads the original PDFs locally in every level, so the corpus files must be present.

`ask` searches only when sources are `ready`, have a `document_id`, and the project has a `dataset_id`. Without RAGFlow nothing sets those, so R1 and R2 reuse the pattern in `tests/document_browser_fixture.py`: a local fake RAGFlow HTTP provider that accepts dataset and document creation, reports documents as `DONE`, and answers `/retrieval` from the recording. R3 is pure scoring over saved files and needs no project.

## Ablation matrix

Each arm changes one factor from the baseline. A full factorial is not needed for 30 questions.

| Arm | Fan-out | `read_context` | Reranker | How it is produced | Status |
| --- | --- | --- | --- | --- | --- |
| A0 baseline | on | on | as configured | Production `ask` | Seam exists |
| A1 raw question only | none | n/a | as configured | Harness raw-question retrieval; retrieval stages only, no agent | Seam exists |
| A2 single query | one query per turn | on | as configured | Needs an evaluation-only agent setting that caps distinct queries at one | **Gap**: no switch exists |
| A3 no page context | on | off | as configured | `ask` only passes `read` when the agent's `answer` accepts it; the harness wraps the agent with a signature that omits `read` | Seam exists |
| A4 reranker off / on | on | on | toggled | Two separate live recordings of the same index with `rerank_id` set and unset; cannot be produced by replay | Needs a configured reranker |

Stage metrics compared per arm: A1 versus A0 for stage 1 and 2; A2 versus A0 for stages 1 to 3 and end to end; A3 versus A0 for stage 4 and end to end; A4 for stages 2 and 3 and end to end.

## Acceptance rule

A change (new technique, prompt, parameter or gate version) is adopted only when all of these hold, written down before the run in the evaluation record:

1. **Pre-registered target.** The record names the stage metric the change is meant to improve and the expected direction.
2. **Repeated runs.** The baseline and the candidate each run three times on the same recording or index. Retrieval-only arms are deterministic in principle and run twice to confirm it; any difference between those two runs is recorded as a finding.
3. **Noise floor, per split.** For each case, compare outcomes across the baseline's own repeated runs. The noise floor of a split is the number of its cases whose outcome differs between any two baseline runs. Dev and held-out each have their own floor.
4. **Per-case outcome.** A case's outcome in an arm is the outcome shared by at least two of its three runs. A case with no such majority is `unstable`, and an unstable candidate case counts as broken.
5. **Dev improvement.** On the dev split, cases fixed minus cases broken on the target metric is at least the larger of 2 and the dev noise floor.
6. **No held-out regression.** On the held-out split, cases broken end to end are no more than the held-out noise floor.
7. **No new unsafe publication.** Any case that becomes `unsafe_published` in any run, on either split, blocks adoption regardless of other gains.
8. **Reported cost.** Latency p50/p95 and call counts are reported for both arms. There is no fixed latency budget in v1; an increase is stated in the decision.
9. **No replay or infrastructure gaps.** Replay misses are zero and infrastructure failures are listed per arm; a case that failed for infrastructure in either arm is excluded from both and the exclusion is reported.

This differs from the grounding benchmark's all-pass rule on purpose. That benchmark has 24 atomic claims with evidence supplied and a fixed expected decision each, so every case is expected to pass. Here, retrieval over PDFs and live agent behaviour vary between runs, and an all-pass rule on 30 questions would either block every change on noise or reward tuning to the held-out set. Paired per-case flips against a measured noise floor are the honest test at this size. With about 20 dev and 10 held-out cases, only large effects are detectable; results are directional, and the record says so.

## Corpora

### Public synthetic corpus (CI)

- Fictional project specifications generated by a committed script, extending the minimal PDF writer in `tests/document_browser_fixture.py` to multiple pages, multiple sources and simple tables. No client text, no real projects.
- Designed to exercise each category: a value whose condition sits on the next page, a table row, two revisions of the same specification with a changed value, near-identical elements (for example a new wall and an existing wall) as hard negatives, a question with no answer in the library, and a mixed library with unrelated sources.
- Scanned pages need an image-only page. If a license-clean image page cannot be committed, `scanned_page` is private-only in v1 and the public report lists it as not covered.
- Committed: generator, generated PDFs (or their SHA-256 with deterministic regeneration), gold labels, RAGFlow recordings made locally, and R3 captures. Recordings contain only synthetic text and are safe to commit.
- CI runs scoring and attribution over the committed recordings and captures (R3). CI does not reach RAGFlow or any model provider.

### Private reviewer-labelled set (local only)

- Real project PDFs and questions, labelled by an engineering reviewer with the same schema. Labels, PDFs, recordings, traces and per-case results stay outside Git, in a location passed to the harness by flag. Coding agents do not read them.
- Committed: only aggregates in a run record, schema `mcdxkit.retrieval-evaluation/1`, under `docs/evaluations/`: metric values, counts per category and split, the attribution histogram, the ablation comparison, versions (`knowledge/*`, `grounding/*`, retriever settings, model IDs), and a SHA-256 of the gold file as a fingerprint. No questions, quotes, page text, file names, source hashes or per-case identifiers.
- The aggregate exporter has a redaction test: given a private-shaped fixture with sentinel strings in every text field, no sentinel appears in the export.
- Private results are reported alongside public ones and never merged into one number.

## Harness pieces for the test and evaluation owner

Ordered so each piece is useful on its own. All live under `tests/` (or `scripts/` for opt-in live runners), following the existing `tests/evaluate_grounding.py` pattern: explicit `--live`, new output path required, no overwrite, no retry, no provider fallback.

1. **Gold loader and label lint.** Validates `mcdxkit.retrieval-gold/1`, source hashes, page bounds and quote presence through the product's page reader.
2. **Public corpus generator** and about 30 labelled cases.
3. **Retrieval-only runner (first slice).** Indexes the public corpus in a local RAGFlow, calls `RAGFlow.retrieve` with production parameters for each raw question, writes a recording, and scores stages 0 to 3.
4. **Recording and replay retriever.** Same `retrieve` signature; replay-miss error; source mapping by SHA-256.
5. **Ask trace capture.** Wraps the retriever (`client=`), `agent.answer` (pre-gate payload), `verify` (statements, passages, reply) and `activity` (timestamps). Writes an evaluation-only trace beside the saved record.
6. **Scorer and attribution.** Pure functions from gold, recording, trace and saved record to per-stage metrics and first-loss labels. Unit-tested with hand-computed expected values, including every loss reason and each abstain outcome.
7. **Ablation runner and paired comparison.** Runs arms, computes the noise floor and per-case flips, and applies the acceptance rule.
8. **Private runner and aggregate exporter** with the redaction test.

Product changes this depends on, owned by product code and not part of the first slice:

- An evaluation-only cap on distinct queries per turn, for arm A2.
- Optionally, ordered evidence IDs per search in `searches[*]`, so rank metrics also work on ordinary saved answers.
- Provider token usage surfaced by the answer and verifier adapters, if cost in tokens is wanted.

## Smallest first slice

About 30 labelled questions on the public synthetic corpus, plus the retrieval-only runner (harness pieces 1 to 3).

| Category | Cases |
| --- | ---: |
| `direct_value` | 6 |
| `table` | 4 |
| `cross_page_condition` | 4 |
| `follow_up` | 3 |
| `hard_negative` | 4 |
| `conflicting_revision` | 3 |
| `unanswerable` | 4 |
| `scanned_page` | 2, or 0 with the gap stated if no public image page |

Scope and outputs:

- Raw-question retrieval only (arm A1). No agent, no answer or verifier model calls, no product change. Indexing does call the configured embedding model on synthetic text.
- Metrics: stage 0 coverage, page recall@{1, 3, 5, 8, 12}, quote recall@12, first gold rank and MRR, exposed page recall at the 8-chunk cut, irrelevant-context rate, per category and split. Follow-up questions are scored on their raw text, which measures how much the agent's rewriting has to recover.
- Artifacts: the recording, a run record in `docs/evaluations/`, and a CI test that re-scores the committed recording deterministically.
- This is also the first real parse, index and retrieve test through RAGFlow, which [model evaluation](model-evaluation.md#ragflow-boundary-still-awaiting-validation) lists as awaiting validation. If indexing fails, the run reports an infrastructure failure and claims no recall score.

Done for the first slice means the gold file passes lint, the recording and run record are committed, the CI scoring test passes on the recording, and the run record states what was not covered. Agent trace capture, replay levels R1 to R2, ablations and the private set follow as later slices.
