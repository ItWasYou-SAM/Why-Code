# Component 1 — GitHub ingestion & evidence store

Fetches merged pull requests and everything around them, normalizes each artifact into a typed, content-addressed record, and persists it in SQLite so the rest of the pipeline can replay it without touching GitHub.

**Status:** implemented and fixture-tested. The live 50–100 PR acceptance run is not reproduced in this repository because it needs a GitHub token and network access. See [`docs/INGESTION_REPORT.md`](../../../docs/INGESTION_REPORT.md).

## Record types

| Record | Meaning |
| --- | --- |
| `pull_request` | merged PR metadata, body and merge commit |
| `commit` | commits belonging to the PR |
| `changed_file` | path, status, additions/deletions and unified-diff patch |
| `review` | review state and author |
| `review_comment` | line-anchored review comment (`path`, `line`, `start_line`, `side`) |
| `issue` | linked issue or PR discussion |
| `issue_comment` | comments on the linked issue |
| `person` | authors, reviewers and commenters |
| `doc_file` | documentation touched by the PR |

Each row carries `record_id`, `record_type`, `repo`, `pr_number`, `payload`, `content_hash` and timestamps.

## Evidence IDs

IDs are stable and repo-scoped, using the short repository name:

```text
pull_request     httpx:pr:1131
commit           httpx:commit:5f3a1c9
changed_file     httpx:pr:1131:file:httpx/_transports/default.py
review_comment   httpx:review_comment:812
issue_comment    httpx:issue_comment:451
```

Deterministic IDs make reruns idempotent and let the graph, citations and review queue refer to the same artifact.

## Ingestion flow

1. List merged PRs in the requested date window.
2. Skip PRs already marked complete unless `--force` is passed.
3. Fetch PR metadata, files, commits, reviews, review comments and linked issue comments.
4. Normalize each artifact and upsert it transactionally.
5. Roll the **whole PR** back if any artifact fails (D-0007); mark it `error` and continue.
6. Score PRs with fixed, explainable signals.
7. Export `normalized.jsonl`, `high_signal_prs.jsonl` and `ingestion_report.json`.

Pagination, rate-limit backoff and resume are handled by the client and the `ingestion_state` table. Raw GitHub payloads are kept separately for replay and debugging.

## SQLite tables

```sql
evidence_records(record_id PRIMARY KEY, record_type, repo, pr_number,
                 payload, content_hash, ingested_at, updated_at)
raw_payloads(source_id PRIMARY KEY, endpoint, payload, fetched_at)
ingestion_state(repo, pr_number, status, completed_at, error,
                PRIMARY KEY (repo, pr_number))
```

Indexes cover `(repo, pr_number)` and `record_type`.

## CLI

```bash
cd backend

# Ingest
PYTHONPATH=. python -m app.ingestion.cli ingest \
  --repo encode/httpx --since 2024-01-01 --limit 100 \
  --artifacts-dir ../artifacts

# Inspect one PR's complete evidence trail, offline
PYTHONPATH=. python -m app.ingestion.cli show \
  --repo encode/httpx --pr 1131 --artifacts-dir ../artifacts

# Re-fetch only PRs marked error
PYTHONPATH=. python -m app.ingestion.cli retry \
  --repo encode/httpx --artifacts-dir ../artifacts
```

`--force` re-ingests completed PRs.

## Outputs

```text
artifacts/
├── evidence.db            # SQLite evidence store
├── normalized.jsonl       # every record, one JSON object per line
├── high_signal_prs.jsonl  # PRs that clear the relevance score
└── ingestion_report.json  # run id, counts, failures, API usage
```

The report records selected/processed/failed PRs, per-type counts, high-signal count, API requests, remaining quota and per-PR failure details.

## Layout

```text
app/ingestion/
├── cli.py          # ingest / retry / show
├── github_client.py# pagination, auth, rate-limit handling
├── normalizer.py   # GitHub payload → typed records
├── service.py      # per-PR orchestration and atomic rollback
├── store.py        # SQLite schema, upserts, state
├── scorer.py       # deterministic relevance scoring
└── exporter.py     # JSONL exports and report
```

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests/ingestion -q
```

Coverage includes normalization, idempotent upserts, pagination, rate limits, malformed payloads, scoring, exports, CLI wiring and the plan's acceptance assertions.
