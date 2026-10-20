# WhyCode backend

FastAPI + SQLite + Neo4j backend for **WhyCode**. It ingests a repository's GitHub history, builds an evidence graph, enriches it with structural data and extracts evidence-linked decisions.

The retrieval/answer service (Component 5) is not built yet; the graph and extraction layers it will consume are.

For the project overview, screenshots and built-vs-planned table, see the [root README](../README.md).

## Component guides

| Component | Guide |
| --- | --- |
| Foundation (settings, errors, logging, domain) | this file |
| 1 · GitHub ingestion | [`app/ingestion/README.md`](app/ingestion/README.md) |
| 2 · Neo4j graph builder | [`app/graph/README.md`](app/graph/README.md) |
| 3 · Structural enricher | [`app/structural/README.md`](app/structural/README.md) |
| 4 · Decision extractor | [`app/intent/README.md`](app/intent/README.md) |
| 5 · Retrieval (not built) | [`app/retrieval/README.md`](app/retrieval/README.md) |

## Layout

```text
backend/
├── app/
│   ├── api/          # package reserved for API routers
│   ├── core/         # settings, error taxonomy, structured logging
│   ├── domain/       # evidence records, record types, stable evidence IDs
│   ├── graph/        # Component 2: records, mapping, regions, writer
│   ├── ingestion/    # Component 1: client, normalizer, store, scorer, exporter
│   ├── structural/   # Component 3: selection, content, tree-sitter, graph, validation
│   ├── intent/       # Component 4: selection, context, LLM, validation, graph, review
│   ├── retrieval/    # Component 5: not built
│   └── main.py       # FastAPI factory; /health only
├── tests/            # contract tests grouped by component
├── pyproject.toml
└── README.md
```

## Foundation

- **Settings** — `app/core/config.py`. One immutable `Settings` object loaded from environment variables; secrets are `repr=False`.
- **Errors** — `app/core/errors.py`. `TransientUpstreamError`, `RateLimitError`, `InvalidRecordError`, `GraphWriteError`, `InsufficientEvidenceError`.
- **Logging** — `app/core/logging.py`. Structured JSON records with request/component/record context.
- **Domain records** — `app/domain/records.py`, `record_types.py`, `evidence_ids.py`. Content-addressed evidence records and the stable ID strategy shared by every component.
- **API** — `app/main.py` exposes `GET /health` only. It deliberately does not touch Neo4j, so CI never depends on a paid database being awake.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
set -a; source .env; set +a
```

`.env` is not loaded automatically; the `source` line exports the variables for the current shell.

## Run

```bash
# API
PYTHONPATH=. uvicorn app.main:create_app --factory --reload

# Health check
curl http://localhost:8000/health
# {"service":"why-code","status":"ok","version":"0.0.1"}
```

Only `/health` exists. `POST /api/v1/ask` belongs to the unbuilt retrieval component.

## Pipeline

```bash
# 1. Ingest
PYTHONPATH=. python -m app.ingestion.cli ingest \
  --repo encode/httpx --since 2024-01-01 --limit 100 \
  --artifacts-dir ../artifacts

# 2. Structural enrichment + graph
PYTHONPATH=. python -m app.structural.cli build \
  --repo encode/httpx \
  --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts --remote --write

# 3. Decision extraction (dry run)
PYTHONPATH=. python -m app.intent.cli extract \
  --repo encode/httpx \
  --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --structural-graph ../artifacts/structural/structural_graph.jsonl \
  --db ../artifacts/evidence.db \
  --artifacts-dir ../artifacts
```

Every command is safe to rerun: ingestion is idempotent, structural extraction replays from the content manifest, and intent extraction is dry-run unless `--write` is passed.

## Tests and quality gates

```bash
PYTHONPATH=. python -m pytest --ignore=tests/structure
ruff check app tests
mypy app
```

Current state: **641 passed, 8 skipped**, ruff clean, mypy clean over 113 source files.

- `tests/structure/` is a legacy parallel contract suite that conflicts with `tests/structural/` at collection. The canonical suite excludes it.
- The 8 skipped tests are live-Neo4j integrations. Enable them with `WHYCODE_TEST_NEO4J_URI` plus `_USER`, `_PASSWORD` and `_DATABASE`.

## Environment

| Variable | Required | Default |
| --- | :---: | --- |
| `WHYCODE_GITHUB_TOKEN` | ✅ | — |
| `WHYCODE_NEO4J_URI` | ✅ | — |
| `WHYCODE_NEO4J_USER` | ✅ | — |
| `WHYCODE_NEO4J_PASSWORD` | ✅ | — |
| `WHYCODE_NEO4J_DATABASE` | — | `neo4j` |
| `WHYCODE_LLM_API_KEY` | ✅ | — |
| `WHYCODE_LLM_MODEL` | — | `gpt-4o-mini` |
| `WHYCODE_LLM_BASE_URL` | — | `https://api.openai.com/v1` |
| `WHYCODE_LLM_TIMEOUT_SECONDS` | — | `60` |
| `WHYCODE_ARTIFACTS_DIR` | — | `artifacts` |
| `WHYCODE_TEST_NEO4J_URI` | — | — |

## Product decisions

Every non-obvious choice is dated and contradictable in [`docs/DECISIONS.md`](../docs/DECISIONS.md). Validation evidence lives in [`docs/INGESTION_REPORT.md`](../docs/INGESTION_REPORT.md) and [`docs/VALIDATION_REPORT_PHASE10.md`](../docs/VALIDATION_REPORT_PHASE10.md).
