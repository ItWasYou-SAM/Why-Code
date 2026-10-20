# Component 5 — GraphRAG retrieval & answer service

**Status: not built.** This package is intentionally empty.

The plan defines the component; no implementation exists yet. The FastAPI app currently exposes only `GET /health`.

## What it will own

- Turn a natural-language “why” question into a retrieval plan.
- Detect the target type: config key, symbol, file, concept or unknown.
- Run hybrid retrieval (vector + full-text) over Neo4j.
- Expand candidates through graph relationships (PR, commit, review, decision, evidence).
- Pack evidence under a token budget.
- Synthesize an answer with citations, confidence and a graph path.
- Refuse explicitly when the evidence is insufficient.

## Planned API

```http
POST /api/v1/ask
```

```json
{
  "question": "Why are retries disabled by default in httpx?",
  "repo": "encode/httpx",
  "max_evidence_items": 8,
  "include_graph_path": true
}
```

The response will carry `answer`, `confidence`, `status`, `citations`, `graph_path`, `superseded_by` and `retrieval_trace_id`.

Planned supporting endpoints:

```http
GET /api/v1/evidence/{evidence_id}
GET /api/v1/graph/path/{decision_id}
GET /api/v1/health
```

## Planned pieces

| Piece | Responsibility |
| --- | --- |
| `QueryPlanner` | question → `RetrievalPlan` |
| `HybridGraphRetriever` | vector + full-text candidates, then graph expansion |
| `ContextPacker` | rank and pack evidence under a token budget |
| `AnswerSynthesizer` | cited answer from the evidence pack |
| `RefusalPolicy` | decide answered / refused / insufficient |
| API layer | REST (and optional SSE) transport |

## Exit criteria (from the plan)

- 10 golden questions with human-verified ground truth.
- At least 7 answered with valid citations.
- At least 2 unsupported questions refused.
- No cited evidence ID absent from Neo4j.
- A graph path for every answered question.
- A query completes in under 10 seconds on the demo dataset.

## Current prerequisites

The graph already contains the evidence, structural and decision layers needed by retrieval. What is missing is the retrieval/orchestration layer, an embedding/vector index, the answer synthesizer, the refusal policy and the API endpoints.
