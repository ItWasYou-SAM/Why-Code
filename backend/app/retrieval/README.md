# Component 5 — GraphRAG retrieval & answer service

**Status: Phases 1–10 are built, along with the downstream pipeline, the API,
the prepared judge demo and the contract tests.** Question understanding,
indexes, all four retrieval strategies, WRRF fusion, explainable reranking, the
compact evidence packer, constrained answer synthesis, claim-level citation
validation, deterministic refusal behavior, the Phase 9 answer API, the Phase
10 rehearsed demo and the read-only Neo4j adapter are implemented. The pipeline
is dependency-injected and runs end-to-end against an in-memory fake or a live
`Neo4jClient`.

The service answers natural-language “why” questions from the decision graph.
It is a fixed retrieval-and-synthesis pipeline, not an agent: exact match,
full-text search and vector search run as independent sources, graph
expansion adds evidence and supersession context, and the answer model only
ever sees the packed evidence.

## What is implemented

| Stage | Module | Contract |
| --- | --- | --- |
| Question classification | `question/question_classifier.py` | Deterministic six-class classifier; no model call |
| Entity extraction | `question/entity_extractor.py` | Exact graph index first; constrained LLM fallback may not invent identifiers |
| Query building | `question/query_builder.py` | Verbatim identifiers, deduplicated lexical terms, Lucene escaping |
| Index definitions | `indexes/index_manager.py` | Idempotent full-text and vector indexes for all embedded labels |
| Exact retrieval | `retrieval/exact_retriever.py` | Known identifiers first; rank recorded, never a raw score |
| Full-text retrieval | `retrieval/fulltext_retriever.py` | BM25 candidates as a ranked source |
| Vector retrieval | `retrieval/vector_retriever.py` | One embedding per question; malformed vectors fail closed |
| Graph expansion | `retrieval/graph_expander.py` | Evidence, targets, PRs, incidents, later decisions |
| Neo4j backend | `retrieval/neo4j_backend.py` | Static read-only Cypher; parameters only; repo-scoped |
| Fusion | `retrieval/fusion.py` | WRRF: `Σ weight / (k + rank)`, `k=60`; ranks only |
| Reranking | `retrieval/reranker.py` | Exact-path, corroboration, supersession, review anchor, age and target rules; cap 5 |
| Evidence packing | `context/evidence_packer.py` | Deterministic aliases, diff/review/commit/issue priority, names and dates, token budget |
| Answer synthesis | `answer/answer_synthesizer.py` | Fixed system/user prompts, documented required-field schema, corrective retry |
| Citation validation | `answer/citation_validator.py` | Claim-level grounding, selected-decision check, fabricated code/person/PR/date/SHA detection, quote verification |
| Refusal policy | `answer/refusal_handler.py` | `NO_ENTITY`, `NO_DECISION`, `CONTESTED` with both sides, `SUPERSEDED_ONLY` with the reversal trail |
| API | `api/routes.py` | Phase 9 answer API: `/ask` and supporting routes, `/feedback` |
| Prepared demo | `demo/`, `api/demo_routes.py` | Phase 10 deterministic corpus, `/demo/script`, `/demo/ask`, `/demo/rehearse` |

## Question understanding (Phase 1)

The first stage is a fixed boundary, not an agent:

1. **Classify deterministically.** `QuestionClassifier` scores the six classes
   from phrases, vocabulary and already-matched entity types. It never calls a
   model. See the exact weights and reasons with
   `classify_details()`.
2. **Match exact catalog entities first.** `EntityCatalog` is a read-only,
   repository-scoped allowlist loaded from Neo4j (or supplied statically in
   tests). Config keys, symbols, files, decision IDs and explicit person logins
   are matched before any model is considered.
3. **Match lexically, conservatively.** A single generic word cannot select a
   symbol. Decision claims require at least two matched distinctive terms, or
   an exact identifier. Each candidate carries its match source and score.
4. **Consult the constrained LLM only if needed.** When there is no confident
   entity, or same-type lexical candidates are too close, the model receives a
   shortlist and may return only `option_id` values plus display text. It never
   supplies a graph key, normalized identifier, question class or time
   constraint. Unknown options are discarded; without an LLM, extraction
   degrades deterministically with a warning.

The output keeps the small pipeline contract:

```json
{
  "question_type": "CONFIG_WHY",
  "entities": [
    {
      "type": "config_key",
      "text": "retry limit",
      "normalized": "retry.max_attempts"
    }
  ],
  "time_constraint": null,
  "requires_supersession_check": true
}
```

Diagnostics add `repo`, `classification`, `extraction_method` and `warnings`
without changing that contract. Only explicit years/month ranges are parsed;
relative phrases such as "last year" are unsupported. The original question is
always the vector text.

Inspect Phase 1 without a live database:

```bash
PYTHONPATH=. python -m app.retrieval.cli understand \
  --repo encode/httpx \
  --question "Why is the retry limit three?" \
  --catalog-json /tmp/catalog.json --no-llm
```

## Indexes and embeddings (Phase 2)

Retrieval indexes are created by Component 3 (structural) and Component 4
(intent) during `prepare_schema()`. Both writers run the same idempotent
`IndexManager` sweep, so loading either graph half first leaves the database in
the same state. Index DDL is never issued on the request path.

Full-text indexes:

| Index | Node | Fields |
| --- | --- | --- |
| `decision_fulltext` | `Decision` | `claim`, `rationale` |
| `evidence_fulltext` | `Evidence` | `excerpt` |
| `symbol_fulltext` | `Symbol` | `name`, `qualified_name` |
| `config_fulltext` | `ConfigKey` | `key` |

Vector indexes use `n.embedding`, the configured dimensions (default `1536`)
and cosine similarity:

| Index | Node | Embedded text |
| --- | --- | --- |
| `decision_embedding` | `Decision` | `claim` + `rationale` |
| `evidence_embedding` | `Evidence` | `excerpt` |
| `symbol_embedding` | `Symbol` | `qualified_name` + `docstring` |
| `config_embedding` | `ConfigKey` | `key` |
| `code_region_embedding` | `CodeRegion` | `excerpt` |

Structural extraction now carries the two source fields that only the graph
needs for retrieval: Python docstrings on `Symbol` and a bounded source
excerpt on `CodeRegion`. At write time `embed_graph_records()` projects the
configured text fields, calls the injected `EmbeddingProvider`, and attaches
the vector before the batch is merged. A provider that returns an empty,
non-finite or misframed vector aborts the load before any write. Nodes with no
target text are deliberately left without an embedding rather than represented
by a zero vector. `HashingEmbeddingProvider` remains the deterministic offline
provider; production can inject a semantic provider with the same protocol.

Full-text search and vector search remain independent ranked sources; Phase 3
fusion combines their ranks.

## Retrieval strategies (Phase 3)

The four sources are separate stages, then one rank-fusion step. No stage
compares a cosine score with a BM25 score.

1. **Exact.** `ExactRetriever` runs for every resolved identifier and never
   asks a model to guess one. `Neo4jGraphBackend.exact_search()` resolves a
   decision directly by `decision_id`/`record_id`, then through `GOVERNS`
   (config keys, symbols, files, regions), `SUPPORTED_BY` (evidence),
   `PRODUCED_IN` (pull requests) and `MOTIVATED_BY` (incidents). Direct
   identity ranks first; the result is capped by `exact_limit` (10).
2. **Full text.** `FullTextRetriever` sends the escaped Lucene expression
   built in Phase 1 to `decision_fulltext`. Exact names, error text and unusual
   vocabulary survive here even when embeddings miss them.
3. **Vector.** `VectorRetriever` embeds the original question once and queries
   `decision_embedding` for paraphrases. The backend checks the vector is
   non-empty, finite and the configured width before issuing any query; the
   index is queried with over-fetch, then the repo filter and `vector_limit`
   apply.
4. **Graph expansion.** After the first fusion, `GraphExpander` expands the
   top `max_expansion_candidates` decisions with the Cypher shape used by
   Neo4j's `HybridCypherRetriever`: `SUPPORTED_BY` evidence, `GOVERNS`
   targets, `PRODUCED_IN` pull requests, `MOTIVATED_BY` incidents, and the
   real supersession/conflict edge types (`SUPERSEDES`, `REVERSES`, `REVISES`,
   `CONFLICTS_WITH`). Expanded decisions join a second fusion as the `graph`
   source.

Exact, full-text and vector retrieval run concurrently (`parallel_retrieval`,
default on). Graph expansion necessarily waits for their fused candidates.
Fusion still uses only per-source ranks, so a decision surfaced by several
independent strategies outranks one with a single high raw score. Candidate
order is deterministic: ties are broken by decision ID.

Every query in `Neo4jGraphBackend` is static and parameterized. Identifiers,
Lucene text, embeddings and repo names never become Cypher source. The
adapter is read-only and returns transport-neutral models; it does not import
the Neo4j driver, so it can be driven by `Neo4jClient` or a fake runner.

## Fusion and reranking (Phase 4)

Fusion is weighted reciprocal rank fusion (WRRF). A raw cosine similarity and
a BM25 score are never compared, averaged or summed. Each source ranks its own
candidates, and only those ranks enter the formula:

```text
WRRF(c) = Σ_s  weight_s / (k + rank_s(c))
```

| Source | Weight | Signal |
| --- | ---: | --- |
| `exact` | 5.0 | Graph identifier or provenance edge resolved directly |
| `graph` | 2.5 | Reached through expansion around a fused candidate |
| `fulltext` | 2.0 | BM25 match on claim/rationale |
| `vector` | 1.5 | Cosine match on the question embedding |

`k = 60`. A decision that several independent sources agree on therefore beats
one that a single source scored highly. Missing from a source adds nothing;
duplicates within a source keep their first rank; ties break by decision ID.
`FusedCandidate.contributions` records each source's contribution so a rank can
be explained after the fact.

Reranking then applies deterministic, configurable rules to the fused score.
Every applied rule appends a reason string.

| Rule | Effect | Signal |
| --- | ---: | --- |
| Direct graph path to an **exact** entity identity | `+0.20` | `graph_path_to_exact_entity` |
| At least two independent evidence **kinds** | `+0.12` | `independent_evidence_sources >= 2` |
| No supersession/reversal edge | `+0.05` | `not superseded` |
| Review comment anchored to a governed file/region | `+0.10` | `review_comment_on_region` |
| Target matches the extracted entity | `+0.15` | `target_matches_entity` |
| Superseded or reversed | `-0.25` | `superseded` |
| Only one weak evidence source | `-0.08` | `single_weak_evidence` |
| Target unrelated to the question | `-0.20` | `not target_related` |
| Old evidence with no supersession check | `-0.05` | `stale_without_supersession_check` |

A partial identifier such as `retry` can be relevant to
`retry.max_attempts`, but it does not earn the exact-path bonus; exact identity
does. A lexical or LLM extraction whose normalized identifier equals the graph
target does earn it, because the target itself is exact. A file entity matches
a `CodeRegion:path:start-end` target for the same file.

A review comment counts as anchored only when its path is a target path (or
the file the question named) and its line range overlaps the governed region;
a comment on the right file but outside the region, or in another file, does
not. Evidence of one kind is one source even when several items exist. Evidence
is old when its newest dated item is older than `stale_evidence_days` (365 by
default, overridable with `WHYCODE_RETRIEVAL_STALE_EVIDENCE_DAYS`); undated
evidence is never called old, and a later decision replaces the stale penalty
with the supersession penalty.

The final set is capped at five decisions (`max_decisions`). Fewer are kept
when the graph has fewer defensible candidates; the cap is never filled with
unrelated decisions. The reranker sorts by final score and then decision ID, so
the same graph produces the same order across reruns.

## Evidence packing (Phase 5)

`EvidencePacker` builds the model's entire world. The pack is deterministic and
has six sections: question, candidate decision, evidence, graph path,
supersession and answer rules. Evidence is ordered by the product priority, and
the same order decides what survives a tight token budget:

| Priority | Evidence kind | Why |
| ---: | --- | --- |
| 1 | Decision claim and status | The question's answer boundary |
| 2 | Directly supporting evidence | What the decision cites |
| 3 | `changed_file` | The hunk is the strongest code-local support |
| 4 | `review_comment` | Human rationale anchored to the changed lines |
| 5 | `commit` | The implementation action and its message |
| 6 | `issue_comment`, `issue` | Linked incident and discussion context |
| 7 | `pull_request`, `review` | Broader provenance |
| 8 | Superseding decision | Currentness, rendered in its own section |
| 9 | Author/reviewer names and dates | Attribution on every evidence row |

A changed file is stored with its patch as the excerpt, so the pack shows a
real diff hunk rather than a filename. Review comments keep their PR, path and
line span; commits keep their SHA, author and date; issue evidence keeps its
issue number. The intent writer copies this metadata onto the `Evidence`
wrapper, and the retrieval adapter also infers a commit SHA or issue number
from a GitHub URL when older graphs do not carry the explicit property.

The rendered pack looks like:

```text
QUESTION:
Why are retries disabled by default in httpx?

CANDIDATE DECISION:
[D1] HTTPX does not retry requests by default.
Status: active
Confidence: high
Targets: retry.max_attempts, Client.send

EVIDENCE:
[E1] Changed diff hunk
PR #1234, httpx/_client.py
"@@ -100,6 +100,12 @@ def _send_handling_redirects(self, request): ..."

[E2] Review comment by alice on 2024-03-30
PR #1234, httpx/_client.py:312-318
"Unbounded retries can amplify failures during an outage."

[E3] Commit message by bob on 2024-03-31
SHA abc123d
"Disable automatic retries to avoid duplicating non-idempotent requests."

GRAPH PATH:
ConfigKey:retry.max_attempts
→ Symbol:Client.send
→ CodeRegion:httpx/_client.py:310-325
→ PullRequest:#1234
→ Decision:D1
→ Evidence:E1,E2,E3

SUPERSESSION:
None found in ingested window.

ANSWER RULES:
...
```

Aliases are the only IDs the model may cite. The graph path inside the pack is
rendered with those aliases (`Decision:D1`, `Evidence:E1,E2`), while
`EvidencePack.graph_path` and `AskResponse.graph_path` keep the real IDs for
inspection and API clients. The citation validator resolves every alias back to
exactly one evidence ID, and an omitted item never receives an alias.

`max_evidence_items` bounds the evidence count and `max_context_tokens` bounds
the rendered text. Evidence is added round-robin across the selected decisions
so one decision cannot crowd out another; when the budget is tight, the packer
drops the lowest-priority evidence first and records the omitted IDs.

## Answer synthesis (Phase 6)

`AnswerSynthesizer` gives the model exactly two prompts and one schema. The
system prompt fixes the role — synthesis, not research — and the eight rules
from the product contract: use only supplied evidence, cite every factual
statement, explain conflicts, state reversals, refuse when evidence is
insufficient, never invent file names, PR numbers, people, dates or quotes,
use no outside knowledge, and stay concise and technical.

The user prompt repeats the question, embeds the whole evidence pack, and names
every required output field. It is intentionally a *synthesis* prompt: the
model cannot retrieve, expand the graph or call a tool.

The provider receives the documented JSON schema in `ANSWER_SCHEMA`:

| Field | Type | Values |
| --- | --- | --- |
| `answer_status` | string | `answered`, `insufficient_evidence`, `contested` |
| `answer` | string | the cited answer |
| `citations` | array of string | evidence aliases used, e.g. `[E1]` |
| `caveats` | array of string | limitations the reader should know |
| `confidence` | string | `high`, `medium`, `low`, `none` |

Every property is required and `additionalProperties` is `false`, so
structured output constrains the *shape*. It does not constrain the *truth*:
the response is parsed by Pydantic and then passed to `CitationValidator`.

**Validation is a separate gate.** The validator resolves every declared and
inline citation against the pack, rejects unknown IDs and decision aliases,
checks each factual sentence carries at least one resolved evidence ID, and
verifies quoted text against the excerpts the model actually saw. A response
that fails once is regenerated with a strict corrective prompt; a second
failure returns `insufficient_evidence`. Confidence is the minimum of the
model's label and a computed ceiling based on the number of evidence items and
their kinds; `contested` answers are capped at `medium`.

Contested and historical cases are decided deterministically before synthesis.
`RefusalHandler` returns `contested` with both claims and their evidence,
`superseded_only` when every candidate is historical, and
`insufficient_evidence` when the graph has no defensible trail. Those paths
never call the model, so a conflict cannot be smoothed over by generation.

## Citation validation (Phase 7)

`CitationValidator` is the final anti-hallucination gate. For every generated
answer it:

1. extracts declared and inline evidence citations;
2. rejects IDs that are not in the pack, including decision aliases such as
   `[D1]`;
3. rejects a cited item whose `decision_id` is not one of the selected
   decisions, so evidence from an unrelated decision can never ground an
   answer;
4. splits the answer into atomic claims and requires every factual claim to
   carry at least one resolved evidence ID;
5. checks specific details inside a claim — file names, people, PR and issue
   numbers, dates, years and commit SHAs — against the evidence the *same
   claim* cites, so a citation cannot launder an invented detail;
6. verifies each quoted string against the cited excerpts, including
   ellipsis-separated fragments.

The result is explicit and auditable:

```json
{
  "answer_status": "answered",
  "citation_valid": true,
  "unsupported_claims": [],
  "invalid_citations": [],
  "quote_mismatches": [],
  "fabricated_details": [],
  "final_confidence": "high"
}
```

`unsupported_claims` names the exact atomic statements that failed grounding.
`fabricated_details` lists the code/person/PR/issue/date/SHA details that the
cited evidence does not support. A failed validation is regenerated once with
the strict corrective prompt; a second failure returns
`insufficient_evidence`. Invalid citations, quote mismatches and fabricated
details force `final_confidence` to `none`, while merely uncited claims lower
it to `low`.

## Refusal behavior (Phase 8)

Refusal is a feature, not an error path. The product claim is that an answer
comes from the repository's own decision history, so when that history cannot
support an answer the pipeline says so instead of falling back on general
knowledge. Every refusal is deterministic and never calls the model.

| Mode | Trigger | Response |
| --- | --- | --- |
| `NO_ENTITY` | No matching file, symbol, config key, or decision | “I could not identify a relevant code entity or decision.” |
| `NO_DECISION` | The entity exists but no decision is connected | “The graph contains the code entity but no defensible decision trail.” |
| `CONTESTED` | Multiple conflicting decisions | Shows both claims with their evidence aliases and states that the history conflicts. |
| `SUPERSEDED_ONLY` | Only superseded or reversed decisions matched | Names the historical decision, the superseding decision IDs and tells the reader to answer from the current decision. |

`NO_ENTITY` and `NO_DECISION` are pure graph outcomes and carry nearest
candidates for the UI. `CONTESTED` is detected from a `contested` decision
status or a conflict edge and renders both sides with `[E#]` citations, capped
at `low` confidence. `SUPERSEDED_ONLY` is detected when every candidate is
historical (status or later-decision edge); it explains the reversal trail
instead of presenting the old claim as current truth. None of the four modes
can invent a fact about the codebase.

## Pipeline

```text
question
  → classifier + entity extractor
  → exact / full-text / vector retrieval (parallel)
  → WRRF fusion (exact + full-text + vector)
  → graph expansion
  → re-fusion with the graph source + Phase 4 reranking
  → refusal check
  → priority-ordered evidence pack
  → constrained synthesis
  → citation validation (one corrective retry, then refuse)
  → answer + evidence + graph path + confidence
```

The final candidate set is capped at five decisions. Evidence aliases (`E1`,
`E2`, …) are assigned by the packer and resolved by the validator, so a model
cannot cite an evidence ID it was not given.

## API (Phase 9)

Phase 9 makes the fixed pipeline a product surface. The design is one primary
endpoint plus supporting reads and one feedback hook; the API layer is
transport only and never becomes a second retrieval path.

### `POST /ask`

The single primary endpoint. It runs the fixed pipeline and returns the answer
with the evidence that supports it, the graph path, the supersession trail and
the retrieval trace.

```json
{
  "question": "Why are retries disabled by default in httpx?",
  "include_graph_path": true,
  "include_evidence": true,
  "max_evidence_items": 8
}
```

Response:

```json
{
  "answer_status": "answered",
  "answer": "...",
  "confidence": "high",
  "citations": [],
  "graph_path": [],
  "supersession": [],
  "retrieval_trace": {
    "exact_matches": 1,
    "fulltext_candidates": 8,
    "vector_candidates": 10,
    "fused_candidates": 5,
    "selected_decisions": 2
  }
}
```

The response also carries `decision_ids`, `evidence`, `caveats` and
`nearest_candidates` for the UI. Refusals are `200` responses whose
`answer_status` is `insufficient_evidence` or `contested` and whose `answer`
is the deterministic refusal text; they are never error statuses and never call
the model.

| Field | Meaning |
| --- | --- |
| `answer_status` | `answered`, `insufficient_evidence` or `contested` |
| `answer` | The cited answer, or the refusal text |
| `confidence` | `high`, `medium`, `low` or `none`; contested answers are capped at `low`, refusals at `none` |
| `citations` | Evidence IDs resolved by the citation validator; inline answer aliases are `[E1]`, `[E2]`, … |
| `graph_path` | Rendered path entries as `Label:identifier` (for example `Decision:dec_…`, `Evidence:…`) |
| `supersession` | Supersession records for the selected decisions |
| `retrieval_trace` | Stage counts: `exact_matches`, `fulltext_candidates`, `vector_candidates`, `graph_candidates`, `fused_candidates`, `selected_decisions` |
| `citation_valid` | Whether the claim-level citation gate passed |
| `evidence_strength` | `strong`, `moderate`, `thin` or `none`; categorical, never a raw score |
| `refusal_mode` | `NO_ENTITY`, `NO_DECISION`, `CONTESTED` or `SUPERSEDED_ONLY` when refused |
| `decisions` | Selected decisions with status, targets, evidence IDs, `is_historical` and `superseded_by` |
| `people` | Contributors attached to the selected evidence, with roles and counts |

### Supporting endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/ask` | Primary answer endpoint (also `POST /api/v1/ask`) |
| `GET` | `/decisions/{decision_id}` | One decision with targets, evidence, path and supersession |
| `GET` | `/evidence/{evidence_id}` | One evidence record |
| `GET` | `/entities/{entity_id}/history` | Every decision that ever governed an entity |
| `POST` | `/feedback` | Record a judge/user verdict |

The versioned aliases (`/api/v1/ask`, `/api/v1/decisions/{decision_id}`,
`/api/v1/evidence/{evidence_id}`, `/api/v1/entities/{entity_id}/history`,
`/api/v1/feedback`) mirror the primary routes.

### Errors and behaviour

- Without an injected retrieval service the answer routes return `503`.
- An unknown repo returns `400`.
- Unknown decision or evidence IDs return `404`.
- Request validation failures return `422`.

### Feedback

`POST /feedback` lets a judge or user mark an answer as `helpful`, `wrong` or
`missing_evidence`, optionally with a comment and the decision IDs it relates
to. Verdicts are stored for the post-hackathon improvement story.

`create_app(retrieval_service=...)` mounts the routes. Without an injected
service the answer routes return `503`; `/health` remains independent of
Neo4j and of the retrieval pipeline.

## Prepared judge demo (Phase 10)

Phase 10 adds a deterministic, rehearsed surface for the judge demo without
creating a second retrieval path. `create_demo_service()` composes the normal
`RetrievalService` with an in-memory graph, a deterministic vocabulary
embedder and a curated synthesizer; classification, all four retrieval
sources, WRRF fusion, reranking, packing, synthesis and citation validation
still run.

```bash
cd backend
PYTHONPATH=. .venv/bin/uvicorn app.main:create_demo_app --factory --reload
```

`create_demo_app()` needs no Neo4j or model credentials. It serves the demo
routes and also mounts the normal answer routes against the same fixture
service.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/demo/script` | The five prepared questions, expectations and latency budget |
| `POST` | `/demo/ask` | Runs one script question and returns the answer plus rehearsal checks |
| `POST` | `/demo/rehearse` | Runs all five questions and returns pass/fail and measured latency |

The script is fixed; an unscripted question returns `400` rather than silently
becoming a different demo. The versioned aliases (`/api/v1/demo/*`) mirror the
three primary routes. CORS allows the local frontend at
`http://localhost:3000` and `http://127.0.0.1:3000`.

| # | Question | What it proves |
| --- | --- | --- |
| 1 | Why are retries disabled by default in httpx? | Evidence-backed config decision, people, citations and a code→decision→evidence path |
| 2 | Why are simple retries limited to connection failures? | Idempotency constraint and the code-level rationale |
| 3 | Was the retry approach ever revised or reversed? | Supersession: the old default-on decision is retained but labelled historical |
| 4 | Why does the client use separate connect, read, write, and pool timeouts? | A different subsystem and config-key path |
| 5 | Why does httpx use a purple logo? | `insufficient_evidence` / `NO_ENTITY`, with no evidence, citations or invented path |

Each `/demo/ask` response wraps the real `AskResponse` with:

- `latency_ms`, `budget_ms` and `within_budget` (8,000 ms budget; 5,000 ms
  display target);
- `expected_status` and `status_matches_expectation`;
- named checks for status, citation gate, evidence, expected decision, graph
  path, people, evidence strength, historical labelling, refusal mode and
  latency.

`/demo/rehearse` returns the same checks for every question plus an aggregate
pass count. The frontend `/demo` page calls these routes directly, renders the
answer with evidence cards, decision timeline, people, supersession records and
the interactive graph path, and exposes the checks as a trust panel.

**Latency caveat:** the measured number is local fixture-pipeline latency. It
does not include a live Neo4j round trip or a production model call, so the
prepared demo does not by itself satisfy the live end-to-end latency exit
criterion. Run `/demo/rehearse` before presenting; the checks are deterministic
but the measurement is honest about the mode.

## Wiring

`app/retrieval/ports.py` defines the four boundaries:

- `GraphBackend` — exact/full-text/vector search plus graph expansion.
  `Neo4jGraphBackend` is the read-only production adapter; the fakes in
  `tests/retrieval/fixtures/` are the dependency-free test form.
- `EmbeddingProvider` — one question embedding (`HashingEmbeddingProvider`
  is the deterministic offline implementation).
- `TextGenerator` — structured JSON completion. The existing
  `OpenAICompatibleProvider` satisfies this protocol.
- `LookupBackend` — supporting decision/evidence/entity-history endpoints.
  `Neo4jGraphBackend` implements these too.

The adapter takes any `QueryRunner` with `run(query, **params)` returning
`.records`, which is exactly `Neo4jClient`'s shape. A process composition
root therefore builds the client, backend, embedder and generator once:

```python
client = Neo4jClient.connect(uri=..., database=..., user=..., password=...)
backend = Neo4jGraphBackend(client, config=config)
service = RetrievalService(
    backend=backend,
    embedder=embedder,
    generator=generator,
    lookup=backend,
    config=config,
)
```

No live AuraDB acceptance run is part of this change. The adapter's Cypher,
record mapping, dimension checks and every lookup are covered by fakes.

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests/retrieval -q
# 321 passed at this checkpoint
```

The suite covers classification, entity exact-match precedence, index
definitions, all four retrieval signals, the Neo4j adapter's static Cypher and
record mapping, vector dimension failure, the WRRF formula, exact-vs-lexical
target matching, review-anchor path and line overlap, evidence age, weak-source
counting, the five-decision cap, evidence priority and budgeting, schema
retries, citation and quote rejection, all refusal modes, stable reruns, the
evidence pack's section order, diff/review/commit/issue priority, author/date
metadata, alias-only graph paths and budget behaviour, the documented
synthesis schema, prompt rules, corrective retries, selected-decision
enforcement, fabricated detail detection and quote verification, the exact
refusal messages and reversal trail, the Phase 9 API contract (the primary
`/ask` endpoint, its supporting reads and `/feedback`), the Phase 10 script
contract (all five expected outcomes, historical labelling, refusal without
synthesis, stable reruns, CORS and the latency budget) and the CLI. No test
calls a model or a database.

## Exit criteria

- [x] Known config key returns an evidence-backed answer.
- [x] Every factual sentence must resolve to a pack evidence ID.
- [x] Graph path is returned and inspectable.
- [x] Superseded/reversed decisions are labelled historical.
- [x] Conflicting decisions produce `contested`.
- [x] Unsupported questions return `insufficient_evidence`.
- [x] `NO_ENTITY` and `NO_DECISION` use the exact product messages.
- [x] `CONTESTED` shows both sides with evidence and never calls the model.
- [x] `SUPERSEDED_ONLY` names the reversal trail instead of the old claim.
- [x] No refusal path answers from general knowledge.
- [x] The same question returns the same structure across reruns.
- [x] Read-only Neo4j adapter for all four retrieval strategies and lookups.
- [x] WRRF never compares or averages raw BM25 and cosine scores.
- [x] Every reranking boost and penalty is configurable and reason-bearing.
- [x] The final decision set is capped at five.
- [x] Evidence is ordered diff → review → commit → issue, with names and dates.
- [x] The model sees only pack aliases and can cite no other ID.
- [x] The provider schema requires all five answer fields and forbids extras.
- [x] Every citation is resolved and every quoted excerpt is verified.
- [x] Every cited evidence item belongs to a selected decision.
- [x] Unsupported claims and fabricated file/person/PR/issue/date/SHA details are reported.
- [x] Contested and superseded-only cases are decided without the model.
- [x] The primary endpoint is `POST /ask` with the documented answer contract.
- [x] Refusals are returned as contract responses, not error statuses.
- [x] The decision, evidence, entity-history and feedback routes are live.
- [x] `/demo/rehearse` runs the five-question script with deterministic checks.
- [x] Prepared answers stay under the 8,000 ms demo budget on fixture data.
- [ ] End-to-end latency measurement on the demo data with a live database.
