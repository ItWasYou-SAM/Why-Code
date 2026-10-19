# Why-Code

Answers *why is this code like this?* for a repository using its own
development history as evidence. GraphRAG over Neo4j.

## Status

Components 0–3 are implemented and green:

- **Component 0 — foundation.** Settings, error taxonomy, evidence-record
  models, evidence IDs, `/health`.
- **Component 1 — ingestion.** Merged PRs and their artifacts (commits,
  changed files, reviews, comments, issues, docs) normalized into a SQLite
  evidence store, scored, and exported as `normalized.jsonl` /
  `high_signal_prs.jsonl`.
- **Component 2 — graph builder.** `ops → mapping → cypher → builder` turns
  the exported corpus into File/Symbol/CodeRegion/Commit/Review/Comment
  nodes and relationships, with fail-fast endpoint integrity and dry-run
  semantics.
- **Component 3 — structural graph builder.** Selected changed files are
  parsed with tree-sitter into File, Symbol, CodeRegion, ConfigKey,
  ExternalRef and Doc nodes. Selection reads only Component 1's exports;
  the parser never walks the repository.

Every rule in the structural pass obeys one constraint: *no invented
relationships.* Import edges resolve only against the known selected path
set, config references come only from literal access patterns, and docs link
only when they name a target explicitly. The decisions behind these choices
are recorded in `docs/DECISIONS.md` (D-0013 … D-0019).

## Layout

```
backend/app/
  core/         settings, errors, logging
  domain/       evidence records and evidence IDs
  graph/        Component 2 graph builder (ops, mapping, cypher, writer)
  ingestion/    Component 1 (normalizer, store, scorer, client, CLI)
  structural/   Component 3 (selection, content, languages, extraction,
                graph, validation, CLI)
  main.py       FastAPI app; /health is liveness only
backend/tests/  contract tests, grouped by component
artifacts/      generated outputs (not committed)
```

## Run the tests

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest
```

Integration tests that need a real Neo4j/AuraDB instance are skipped unless
`WHYCODE_TEST_NEO4J_URI` (plus `_USER`/`_PASSWORD`/`_DATABASE`) is set.

## Lint and types

```bash
cd backend
./.venv/bin/ruff check .
./.venv/bin/mypy app
```

## Run the pipeline

Ingestion needs a GitHub token and network; it writes the artifacts that the
structural pass consumes:

```bash
cd backend
PYTHONPATH=. ./.venv/bin/python -m app.ingestion.cli ingest \
  --repo encode/httpx --since 2024-01-01 --limit 200
```

The structural pass is dry-run by default — it prints the node/edge plan and
writes JSON manifests without touching Neo4j. It only connects when `--write`
is passed and validation passes:

```bash
cd backend
PYTHONPATH=. ./.venv/bin/python -m app.structural.cli build \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl --write
```

For an offline replay, pass `--content-root DIR` (a checkout of the target
commit) so content is read from disk instead of `raw.githubusercontent.com`.
