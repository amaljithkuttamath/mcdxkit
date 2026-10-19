# Ask answer checks and audit receipts

Ordinary Ask checks citation identity and keeps the requested answer format. It does not run a second model over every statement. These checks do not establish engineering correctness.

## Current chat checks (`citations/1`, `knowledge/10`)

- Each citation must point to evidence retrieved in the current project and turn. Unknown citations, including in headings, assessments, open questions and conflicts, reject the answer.
- Tables, conditions, open questions and conflicts are preserved. There is no statement-count cutoff. Existing answer-size and research-tool budgets still apply.
- Literal number mismatches and sections missing inline citations are recorded in the audit. Number mismatches remain visible warnings; no sentence is silently deleted.
- Details shows **Citations checked**. The model's own support assessment is labelled as such; it is not a verification result.
- No-evidence answers identify missing information. Navigation-only guidance comes from recorded workflow state.
- Saving requirements uses the separate original-page, exact-quote and literal-value checks in extraction. Requirements remain unreviewed until explicitly reviewed and bound to compatible inputs. Chat cannot approve or change calculation inputs.

The receipt retains the `grounding` field for saved-record compatibility, with status `citations_checked` and no statement verdicts for new answers. Historical records remain unchanged. Sources, page links, retrieval history and model identity remain available for audit.

## Historical statement check (`grounding/8`)

The following describes older saved answers and the explicit legacy benchmark, **not the current Ask path**. It over-rejected valid reports (including tables exceeding 40 heuristic statements), so ordinary chat no longer uses it.

After the agent drafts an answer, MCDXKit splits everything it would display (section headings, body sentences and bullets, conflict statements) into numbered statements. Short layout labels (≤ 6 words, no digits, no citation, no assertive verb or negation) are recognised by a fixed server rule and are not sent for checking.

One additional request to the **same selected provider and model** labels each statement against the cited passages and supplies verbatim quotes. There is no fallback provider and no automatic retry. Each supported statement may carry up to 8 distinct quotes from up to 8 evidence IDs, enough for compound statements and several inline citations. The verifier must quote the whole source sentence that states a value, including its subject, qualifiers and every condition, so quote limits fit long engineering sentences:

| limit | value |
| --- | --- |
| one quote | 2,000 characters |
| all quotes of one statement | 6,000 characters |
| all quotes in one verifier report | 20,000 characters |
| verifier reply (separate provider cap) | 8,192 output tokens on OpenRouter; ChatGPT uses its own per-request output budget |

The character limits are size guards on the parsed report. They do not guarantee that a reply fits the token cap. As a rough English-text estimate, 20,000 characters is about 5,000 tokens, but dense text such as numbers, symbols or non-English wording can use many more tokens per character. The output-token cap is applied independently by the provider. A reply cut off by it is incomplete even if it is within every character limit, and the draft is withheld. A sentence longer than one quote may be quoted as consecutive verbatim parts from the same passage. Each part is still checked against the full stored source sentence. If support cannot fit within the limits, the verifier is told to choose `uncertain`. A reply that exceeds any limit, repeats a quote within a statement or is otherwise malformed is not trimmed, repaired or retried. The draft is withheld with `invalid_verifier_response`, including when a reply truncated by the token cap no longer parses, or `verifier_output_too_long` when the provider reports that the reply ran out of output budget. Evidence passages and the 90,000-character cited-evidence input limit are unchanged. MCDXKit then checks that output mechanically:

- evidence IDs are allowed for the statement and were retrieved in that turn;
- quotes are verbatim in the stored passage (whitespace, quote marks, dashes and case normalised);
- every inline citation on the statement is quoted;
- every number in the statement, with its sign, appears in the quotes; recognised number–unit pairs (mm, cm, m, in, ft, kN, kip, lb, psi, ksi, psf, ksf, kPa, MPa, %, degrees, years, months, days) match as pairs; tolerance, multiplication, exponent and mixed-fraction formats are refused;
- each clause is bound to the full source sentence that states its value. That sentence must share the clause's content and negation, keep every listed qualifier and number–unit condition stated with the value, not conflict with it, and state any minimum/maximum direction the clause uses. Listed qualifier pairs: new/existing, temporary/permanent, short/long-term, total/differential, vertical/horizontal, static/seismic, service/strength, upstream/downstream, interior/exterior; "shall/must" vs "should/may" may not conflict. Clauses that only report that sources disagree or that a point is not established are exempt.

Parenthetical predicates such as “not applicable,” “not governing” and “not approved” qualify the enclosing assertion even when those words are absent from the draft. Only explicit subject-first exclusions such as “skin friction excluded” can use the existing content-overlap rule to distinguish table subjects. This is a word-based check, not a general parser of table structure or engineering applicability.

Only confirmed statements are published. If some statements fail, the answer is marked partial and the unconfirmed text is omitted. If no body statement passes, the draft is withheld. Retrieved passages remain available with their original pages. Unconfirmed text is kept separately on the server for an explicit **Check again** follow-up, never in the displayed answer or exported audit. The model's free-text assessment and gaps are never shown; explanations and gaps are written by MCDXKit. Workflow guidance without document evidence is written by the server from recorded project state. Clarification questions are shown only if they contain no digits and no approval, compliance, certification, safety or pass/fail wording.

A withheld record carries `reason_code`, a fixed server-owned code: `statements_unconfirmed`, `no_checkable_statements`, `too_many_statements`, `evidence_too_large`, `verifier_unavailable`, `provider_unavailable`, `provider_rate_limited`, `provider_error`, `verifier_output_too_long`, `invalid_verifier_response` or `verifier_failed`. It never contains provider messages, raw responses, credentials or draft text.

Answer badges: **Model-checked** (all published statements passed), **Draft withheld**, **Not found** (no supported answer), **Workflow guidance**. Answers saved before these checks are labelled as not checked.

### What the check cannot do

- The checker is the same model that drafted the answer; a repeated mistake can pass.
- Meaning, applicability, completeness of conditions and conflict detection still depend on the model. Unlisted qualifiers, numbers written in words, unrecognised units, conversions and ranges are not checked mechanically.
- Splitting into statements and clauses, stemming and the structural rule are word-based heuristics.
- Strict rules withhold some correct answers.
- Retrieval covers top-ranked passages of searchable documents, not every page; absence of evidence is not evidence of absence. Revision precedence between documents is not verified.

## Audit receipt

`GET /api/documents/{project}/answers/{answer}/audit` downloads `mcdxkit-answer-{answer}-audit.json`. It uses the same session, access token and Origin protections as the rest of the workspace API and returns 404 for an unknown or foreign project or answer. The same receipt is available to the SDK/CLI through `DocumentWorkflow.answer_audit(project, answer)`.

Producing a receipt never changes the project or the saved answer and never calls a model. It reads the saved record and computes SHA-256 hashes of the PDF files that the answer's evidence references.

### Fields (`mcdxkit.answer-receipt/1`)

| field | content |
| --- | --- |
| `schema`, `generated_at` | receipt schema; UTC time of this export |
| `project` | `id`, `title`, `revision_now` |
| `answer` | `id, created, conversation_id, parent_id, request, kind, revision, revision_matches_project, model, provider (chatgpt / openrouter / unrecorded), agent_version, grounding_version, status, explanation, conflicts, sections[{heading, body, evidence_ids}], limitations, actions, clarification{question, choices}, extraction_ids, extraction_offer{source_id, source_name, pages, question}` |
| `grounding` | `version, status, method, scope{retrieval, precedence, approval}, reason_code, coverage, draft_withheld, checked_statements, inline_anchored, statements[{id, kind, section, verdict, reasons, checks_applied, anchored, text, evidence_ids, quotes[{evidence_id, text}]}]`. Older answers: `version` and `status` are `unrecorded`, with a note. Withheld statements carry no text. |
| `citation_audit` | saved citation audit (`status, cited, unsupported_numbers[{heading, values}], sections_without_inline_citations, meaning`) or `null` |
| `retrieval` | `coverage{ready, total, excluded}` or `unrecorded`; `searches[{query, passages}]`; `original_pages_read[{source_id, source_name, anchor_pages, pages, truncated_pages, omitted_pages}]` |
| `evidence[]` | every saved evidence record under its original ID: `role` (retrieved_passage / original_page_read / extraction_quote), `cited`, `used_in_statements`, `source{id, name, saved_sha256}`, `locator{project_id, source_id, source_in_project, pages, page_available}`, `text`, `text_sha256`, `truncated`, `page_characters` |
| `evidence_summary` | counts of retrieved, original pages read, extraction quotes and cited records; `cited_pages[{source_id, pages}]`; `identical_text_groups` (records with identical text, listed, never merged) |
| `source_integrity[]` | per referenced source: `name`, `saved_sha256` (project register), `answer_sha256[]` (hashes recorded with this answer's evidence), `evidence_records`, `records_without_hash`, `current_sha256`, `status`, `answer_hash_status` |
| `digest` | SHA-256 of the receipt without `digest` (JSON, sorted keys, separators `,` and `:`, UTF-8) |

Details:

- **Cited** means listed in a published section's `evidence_ids`, referenced inline in a section body by a known evidence ID, or matched by a checked statement. Citations in open questions, headings, assessments and conflicts also count. Other records were retrieved or read but not used. Several statements often cite the same page because one clause states a value and its conditions; the receipt shows searched, read and cited evidence separately rather than adding sources.
- **Pages** are exported only as positive whole page numbers from the stored record. Other positions are omitted, and `page_available` is false when none remain.
- **`status`** compares the current stored file with the project register: `match`, `changed`, `missing` or `not_in_project`.
- **`answer_hash_status`** compares what the answer recorded with the register: `match`, `differs` (built from a different file revision; takes precedence), `partially_recorded` (all recorded hashes match but some evidence records have none), `unrecorded` or `not_in_project`.
- **The digest** lets you detect later edits to the exported file. Neither the digest nor the source hashes are digital signatures, and none of them prove an answer is true.
- **Never exported:** credentials, tokens, environment values, absolute paths, service URLs, search-index document/chunk IDs, workflow state or calculation files.
