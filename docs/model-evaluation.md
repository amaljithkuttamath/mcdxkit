# Document model selection

On **2026-10-14**, live OpenRouter requests used synthetic engineering excerpts to choose the following free-model preferences. All listed choices remain subject to the current authenticated catalog and zero-price filter. Existing selections are preserved; there is no automatic model substitution or paid fallback.

| Role | Model | Observed result |
| --- | --- | --- |
| Default answers and draft narrative | `cohere/north-mini-code:free` | 4/4 cases; median 8.96 seconds |
| Preferred search, preserving the current selection | `nvidia/llama-nemotron-embed-vl-1b-v2:free` | 3/3 top-ranked passages; 2,048 dimensions; 0.77-second batch |
| Search alternative for a new workspace | `nvidia/nemotron-3-embed-1b:free` | 3/3 top-ranked passages; 2,048 dimensions; 0.56-second batch |

Liquid's `lfm-2.5-embedding-350m:free` also scored 3/3 with 1,024 dimensions in 0.34 seconds. Its catalog context was only 512 tokens, so it is not preferred for longer engineering passages. It remains selectable. The two preferred search models advertised 131,072 and 32,768 tokens respectively; these are catalog limits, not measured document capacities.

## What was tested

The production answer adapter and schema/citation validator were exercised for: a source-backed value with unit, a missing requirement, a malicious instruction embedded in a source, and an input proposal that must leave mapped loads unchanged. Responses were inspected as well as scored. Answer requests retained `data_collection: deny` and disabled provider fallback. No real project documents were sent.

Embedding tests sent three synthetic passages and three questions directly to OpenRouter, then ranked passages by cosine similarity. They used account/provider policy, matching RAGFlow's native embedding driver. An additional request-level `data_collection: deny` probe found no embedding endpoints. The answer and embedding routes therefore have different privacy controls; no account policy was changed.

This is a small smoke evaluation, with one trial per answer case and one batch per embedding model. It does not establish general engineering accuracy, PDF extraction quality, resilience to all prompt injections, or statistically reliable speed rankings. Application validation and human review remain required.

## Candidates that did not qualify

- Gemma 4 31B and 26B returned 429 rate limits.
- Nemotron Ultra, Super, Lightning, Poolside Laguna XS and Liquid 2.6B had no endpoint matching the answer request's data policy.
- Inkling Small returned 403 with an agentic-harness restriction.

Only Cohere completed the answer suite. Catalog visibility alone is not evidence that a model can answer under this account's restrictions.

## RAGFlow boundary still awaiting validation

The selected search model registers successfully in the pinned local RAGFlow server. Its native `/api/v1/embeddings` probe nevertheless returned code 404: the OpenRouter model was not found. Inspection of the pinned Go service shows a factory-catalog lookup even after finding the registered tenant model. Direct OpenRouter embedding results above do **not** prove RAGFlow indexing or document retrieval works. Keep this limitation visible until a real parse/index/retrieve test passes; do not change an existing embedding model to bypass it.

## Repeat the evaluation

The [recorded results](evaluations/2026-10-14-models.json) contain synthetic responses, scores and sanitized availability results. Run the opt-in script from the repository with an installed MCDXKit package:

```bash
python scripts/evaluate_document_models.py \
  --workspace .mcdxkit/document-workspace/.documents \
  --output /tmp/mcdxkit-model-evaluation-new.json
```

The script uses saved credentials or the local `.env`, never prints them, never reads project documents, and refuses to overwrite a report. It checks the current free catalogs before issuing requests. Repeat `--answer-model provider/model:free` or `--search-model provider/model:free` to compare candidates. It sends synthetic text to OpenRouter and consumes account rate limits; it is not run automatically by CI. A nonzero exit means at least one smoke case failed. Changing search models requires a new workspace/index.
