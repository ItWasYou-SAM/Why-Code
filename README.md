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
  fetched **at their own commit** and parsed with tree-sitter into File,
  Symbol, CodeRegion, ConfigKey, ExternalRef and Doc nodes. Selection reads
  only Component 1's exports; the parser never walks the repository.

Every rule in the structural pass obeys one constraint: *no invented
relationships.* Import edges resolve only against the known selected path
set, config references come only from literal access patterns, and docs link
only when they name a target explicitly. The decisions behind these choices
are recorded in `docs/DECISIONS.md` (D-0013 … D-0026).

### Language routing

Phase 3 routes every selected path through one suffix table
(`app/structural/languages/router.py`):

| suffix | treatment |
| --- | --- |
| `.py`, `.pyi` | Tree-sitter Python extraction |
| `.yaml`, `.yml` | config-key extraction |
| `.toml` | config-key extraction |
| `.md` | `Doc` node only (no `File` node) |
| anything else | skipped, unless manually pinned |

A pinned path with no registered suffix is kept as lightweight metadata
(`content_hash`, `byte_size`, `line_count`) and is never sent to a parser.
JSON, notebooks, JavaScript, Rust, and lock files are therefore not parsed;
lock files are skipped even when pinned.

### Query-driven extraction

Definitions, imports, and references come from named Tree-sitter queries
(`app/structural/languages/queries/*.scm`) with code-navigation captures
(`@definition.function`, `@definition.class`, `@name`, `@reference.call`,
…). The extractor consumes query matches, not a hand-written walk of node
types, so the grammar is auditable data.

References are deliberately shallow. A `REFERENCES` edge is created only
when the target is a symbol in the same file, an imported symbol whose
defining file is in the batch, an attribute of a known imported module or
class, or a simple local rule such as `self.method`. Anything else is
discarded; there is no type inference and no dynamic dispatch.

### Content storage

Full file content never enters Neo4j. `app.structural.cli fetch` (or the
`extract`/`build` commands, which fetch as they go) writes:

```
artifacts/structural/content/<repo>/<sha256>.txt     # content-addressed blob
artifacts/structural/content_manifest.jsonl          # metadata only
```

Each manifest row is `file_id, repo, path, commit_sha, language,
content_hash, byte_size, line_count, blob_path`. The `File` node carries
only `content_hash`, `byte_size` and `line_count`, so a citation can be
verified without storing the codebase in the graph. The manifest doubles as
a replay cache: a second run reads the blob and makes no network call.

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

Every ingest run writes `artifacts/ingestion_report.json` (run id, status,
selected/processed/failed PRs, per-type record counts, high-signal count,
API requests, remaining quota, and per-PR failures with the failing
endpoint). The full acceptance report and its honest limits are in
[`docs/INGESTION_REPORT.md`](docs/INGESTION_REPORT.md).

```bash
# Inspect one PR's complete evidence trail, offline
PYTHONPATH=. ./.venv/bin/python -m app.ingestion.cli show \
  --repo encode/httpx --pr 1131

# Re-fetch only PRs marked error (completed PRs are untouched)
PYTHONPATH=. ./.venv/bin/python -m app.ingestion.cli retry \
  --repo encode/httpx
```

Phase 2 — fetch content at each file's own commit:

```bash
cd backend
PYTHONPATH=. ./.venv/bin/python -m app.structural.cli fetch \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl --remote
```

The structural pass is dry-run by default — it prints the node/edge plan and
writes JSON manifests without touching Neo4j. It only connects when `--write`
is passed and validation passes:

```bash
cd backend
PYTHONPATH=. ./.venv/bin/python -m app.structural.cli build \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl --remote --write
```

For an offline replay, pass `--content-root DIR` (a checkout of the target
commit) so content is read from disk instead of `raw.githubusercontent.com`.
