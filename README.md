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
  the exported corpus into PR/ChangedFile/CodeRegion/Commit/Review/Comment
  nodes and relationships, including commit-scoped regions for every
  unified-diff hunk, with fail-fast endpoint integrity and dry-run semantics.
- **Component 3 — structural graph builder.** Selected changed files are
  fetched **at their own commit** and parsed with tree-sitter into File,
  Symbol, CodeRegion, ConfigKey, ExternalRef and Doc nodes. Selection reads
  only Component 1's exports; the parser never walks the repository.
- **Component 3 — intent extraction & evidence linking.** Every merged,
  high-signal PR is turned into evidence-linked `Decision` / `Constraint` /
  `Reversal` / `Incident` intents. This is a deterministic pipeline, not an
  agent: the model only *proposes* a claim and its citations, and code
  validates every citation against the ingestion store and graph index,
  resolves targets, computes confidence, de-duplicates and orders
  supersession before anything is written.
- **Phase 3 — extraction schemas.** One strict `CandidateExtraction` envelope
  (`candidates` + `missing_evidence`) covers design decisions, constraints,
  reversals, incident responses, API contracts and operational policies.
  Enums, length limits and `additionalProperties: false` are pinned by tests,
  and the hard evidence/refactor/weak-comment/reversal rules are enforced by
  code rather than trusted to the provider (D-0041).
- **Phase 4 — evidence validation.** Every candidate passes a local,
  claim-level firewall: atomic claims must share content with the cited text,
  each citation is scored for relevance, targets must resolve, temporal dates
  must exist in evidence, reversals need a commit/PR, participants must be
  real, and disagreement marks a decision `contested` instead of silently
  resolving it. The result is a small auditable JSON object with
  `final_status` `accepted_for_graph` / `observation` / `needs_review` /
  `rejected` / `discarded` (D-0042).
- **Phase 5 — extraction output models.** `ExtractedSymbol`,
  `ExtractedImport`, `ExtractedConfigKey` and `ExtractedCodeRegion` define
  the flat, resolved-ID contract that per-language parsers produce. The
  models reject impossible imports at construction time instead of letting
  the graph discover them later.
- **Phase 6 — extraction rules.** Definitions are limited to top-level
  functions, classes, methods, module/class constants and simple type
  aliases. Imports split per bound name, unresolved relative imports never
  become packages, and config keys carry literal values, types and exact
  YAML lines. `USED_BY` exists only for literal key access on a declared key.
- **Phase 7 — code regions.** `CodeRegion` nodes come from symbol
  definitions, changed hunks parsed out of unified diffs, and review-comment
  anchors. Hunk regions carry their commit-scoped file ID, source PR number
  and content hash, and link `CHANGED_IN → Commit`. Review regions keep the
  pinned D-0010 ID and gain the same bridge fields. Symbol definitions keep
  `Symbol → HAS_REGION → CodeRegion` from the structural pass.
- **Phase 8 — graph records.** Extraction and mapping emit one shared,
  database-free record contract (`GraphNode` / `GraphRelationship` in
  `app/graph/records.py`). No extractor builds Cypher; a single batched
  writer groups records by label/relationship type and sends them with
  `UNWIND`, so a load is a handful of round trips instead of one per node.
  Duplicate keys and dangling endpoints are refused before the first write.
- **Phase 9 — Neo4j writer.** `app/graph/neo4j_driver.py` owns the only
  connection: it calls `Driver.execute_query` (managed transactions with the
  driver's transient-failure retry) and always names the target database
  explicitly. Schema setup is an idempotent `CREATE CONSTRAINT … IF NOT
  EXISTS` / `CREATE INDEX … IF NOT EXISTS` sweep over the label vocabulary,
  and all writes go through the Phase 8 batched `MERGE` writer.
- **Phase 10 — validation.** `validate_graph` is the pure acceptance gate
  over the built records: duplicate IDs, every endpoint resolved, symbol and
  region line bounds checked against the `File.line_count`, orphan files,
  `USED_BY`/`DECLARES` config sanity, `IMPORTS` targets, high-signal region
  coverage and line-anchored review-comment coverage. It emits the plan's
  flat report (`symbols_created`, `relationships_created`, `orphans`, …)
  and a `status` of `pass` / `pass_with_warnings` / `fail`; checks whose
  input is absent are listed in `not_checked` rather than counted as a pass.
  `Neo4jStructuralValidator.full_report` re-runs the same sweep against a
  live database, and `python -m app.structural.cli validate` exits non-zero
  on `fail` so the pipeline cannot advance to LLM extraction on a broken
  graph.

Every rule in the structural pass obeys one constraint: *no invented
relationships.* Import edges resolve only against the known selected path
set, config references come only from literal access patterns, and docs link
only when they name a target explicitly. The decisions behind these choices
are recorded in `docs/DECISIONS.md` (D-0013 … D-0041).

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

### Extraction output contract

Parsers emit four flat models (`app/structural/extraction/models.py`), each
scoped by a resolved `file_id`:

| model | carries |
| --- | --- |
| `ExtractedSymbol` | name, qualified name, kind, span, signature, docstring, parent symbol |
| `ExtractedImport` | module/name/alias, relative flag, resolved file ID, resolution status |
| `ExtractedConfigKey` | dotted key, literal value, value type, line |
| `ExtractedCodeRegion` | span, content hash, optional excerpt/symbol, provenance |

The constructors enforce honesty: `resolved_internal` requires a
`resolved_file_id`, `resolved_external`/`unresolved` forbid one, and a
relative import cannot be labeled external. Regions name their provenance
(`definition`, `changed_hunk`, `review_comment`, `manual`) so a span from a
review comment is never confused with a parsed definition.

### Extraction rules

Symbols are restricted to definitions that carry structural meaning:

| extracted | not extracted |
| --- | --- |
| top-level functions, classes, methods | local variables and local aliases |
| module and class constants (`SCREAMING_CASE`) | lambdas, comprehensions, decorators |
| simple type aliases (`type X = ...`, `UserId = int`) | imported names, `TypeVar` factories |

`qualified_name` is module-qualified (`httpx._client.Client.send`), while
`qualname` stays file-local for ownership and reference matching. Nested
functions keep a conservative local name (`outer.inner`) rather than an
ambiguous ID.

Imports produce one output record per bound name. A resolved internal import
creates `File → File`; using the imported symbol creates
`Symbol → REFERENCES → Symbol`. Third-party imports create
`ExternalRef {kind: "package", name: "anyio"}` and are never resolved to
source. A relative import that cannot be found in the selected set is
`unresolved`, never external.

YAML keys keep the line of the exact mapping entry, plus a literal `value`
and `value_type` (`retry.max_attempts`, `"3"`, `int`). `USED_BY` is emitted
only for literal access such as `settings["retry"]["max_attempts"]` or
`config.retry.max_attempts`, and only when that dotted key is declared.
Uncertain chains such as `config.retry.get(...)` create nothing.

### Code regions

A `CodeRegion` is a bounded span with provenance, never a guessed
symbol-to-hunk mapping:

| source | span | owner |
| --- | --- | --- |
| `definition` | full symbol declaration | `Symbol → HAS_REGION` and `File → HAS_REGION` |
| `changed_hunk` | new-file range from one `@@` header | `ChangedFile → HAS_REGION` |
| `review_comment` | anchor (`start_line`/`line`) | `ChangedFile → HAS_REGION`, `ReviewComment → COMMENTS_ON` |
| `manual` | an explicitly supplied span | caller-provided; no automatic source yet |

Unified-diff hunks use the commit-scoped ID
`{short}:region:{path}@{sha}:{start}:{end}`, the same shape as structural
definition regions, so Component 3 can attach the symbols containing those
lines. Review-comment regions keep their pinned
`{short}:region:{path}:{start}:{line}` ID (D-0010) and now also carry
`commit_sha`, `file_id`, `source_pr_number`, `content_hash` and a
`CHANGED_IN` edge. A pure deletion anchors at the deletion line; a hunk with
no new-side line (a deleted file) creates no region. The commit is the PR's
latest known commit, which is the same version anchor the structural
selector uses.

Hunk creation is opt-in filterable: pass `high_signal_pr_numbers` to
`ops_from_records`/`GraphBuilder` to restrict changed-hunk regions to
high-signal PRs. Without a filter, every changed file with a patch is
considered. A pinned path alone does not create a manual region; manual pins
must name an explicit line span.

### Graph records and batched writes

Both graph halves produce the same transport-neutral records before any
Cypher exists (`app/graph/records.py`):

```python
GraphNode(label="Symbol", key="httpx:symbol:...@abc:Client.send", properties={...})
GraphRelationship(rel_type="DEFINES", start_key="httpx:file:...", end_key="...")
```

`key`, `start_key` and `end_key` use the project's existing commit-scoped
IDs (D-0008, D-0029). Labels and relationship types must be plain Cypher
identifiers; every other value travels as a parameter. `GraphPlan.to_records()`
and `StructuralGraph.to_records()` adapt the two producers, and
`app/graph/batch_writer.py` groups records by label/relationship type,
chunks them at `batch_size`, and emits `UNWIND $nodes … MERGE …` /
`UNWIND $relationships … MATCH … MERGE …`. Nodes are always written before
relationships, because relationship endpoints are matched, not created.
Integrity (unique keys, no dangling endpoint) is asserted before the first
statement, so a bad batch still costs zero writes.

### Neo4j writer

`app/graph/neo4j_driver.py` is the only object that opens a connection.
It uses `Driver.execute_query`, so every statement runs in a managed
transaction (with the driver's built-in retry of transient failures) and
every call passes `database_` explicitly rather than relying on the
server-side default. The batched writers accept its `run(query, **params)`
because `Neo4jClient.run` maps those keywords onto `execute_query`
parameters; no record value is interpolated into Cypher.

Schema is created once and is idempotent: `ensure_schema(labels)` issues
`CREATE CONSTRAINT … IF NOT EXISTS FOR (n:Label) REQUIRE n.record_id IS
UNIQUE` followed by `CREATE INDEX … IF NOT EXISTS FOR (n:Label) ON
(n.repo)` for each label. `build --write` runs it before the first batch, so
a load can never race its own constraints.

The graph keeps one canonical identity property, `record_id` (D-0030), whose
value is the label-specific ID the plan names (`file_id` for `File`,
`symbol_id` for `Symbol`, `config_id`, `region_id`, `ref_id`, `doc_id`).
Identity is therefore unique per label without duplicating the same string
into a second property. Writes batch at `DEFAULT_BATCH_SIZE = 250`, inside
the recommended 200–500 range.

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

## Intent extraction & evidence linking

Where the structural pass answers *what is here*, this component answers *why
is it like this*. It is a **pipeline, not an agent** (D-0033):

```text
PR bundles → CandidateDetector → ContextPacker → one strict candidate call
per PR → schema gate → evidence gate → target resolution → rule gate →
confidence → de-duplication → supersession → IntentGraph (+ review queue)
```

The model is a proposer only. It returns one strict `candidates` envelope
(`claim`, `decision_type`, `status`, `targets`, `evidence_ids`, `participants`,
`rationale`, `temporal_scope`) plus `missing_evidence`. Every object has
`additionalProperties: false`, so unknown fields are rejected. `status` and
`decision_type` are classification labels; the schema still has **no**
`confidence`, `supersedes` or graph-key field. Everything the model cannot
supply is computed by code:

- **Candidate detection** is a fixed, additive score (D-0039): merged `+2`,
  multiple review participants `+3`, line-anchored review comment `+4`,
  linked issue `+3`, issue discussion `+3`, motivating PR body `+2`,
  rationale commit `+2`, high-value path `+3`, docs/ADR `+4`, reversal title
  `+4`. A PR that clears the threshold receives one unified extraction call
  whose candidates are validated independently, so a reversal cannot smuggle
  in an unsupported constraint.
- **Context packing** is region-first and category-budgeted (D-0040). Each
  selected hunk is rendered with its current and previous sides, then anchored
  review comments, commits, PR body, linked issues and docs in priority order.
  Every artifact carries an `[E#]` alias and its exact `record_id`; the alias
  resolves back to the record ID before the evidence gate. Whole files, CI
  logs, unrelated test files and unlinked READMEs are excluded, and anything
  that does not fit is listed in `omitted_ids` and never shown (D-0034). A
  final guard keeps the prompt under the hard category-budget ceiling.
- **The evidence gate is non-negotiable.** A proposal is rejected wholesale —
  not partially — if *any* cited ID is absent from the packed context, the
  SQLite store, or the graph index. No evidence IDs means no intent.
- **The rule gate is local and semantic** (D-0041). A refactor without a
  stated rationale is not a decision; a review comment alone is downgraded to
  `uncertain`; an unmerged PR cannot produce an `accepted` candidate; a
  reversal must cite revert language; participant evidence must be cited by
  the candidate; and unresolved targets are rejected. The model answers
  `missing_evidence` instead of guessing.
- **Phase 4 is a claim-level firewall** (D-0042). A candidate is decomposed
  into atomic claims and each must share content terms with its cited text.
  Every citation is scored for relevance, every target must resolve, every
  temporal date must appear in evidence, a reversal must cite a commit or PR,
  and unsupported participants are dropped. A claim that is not supported goes
  to human review; an invented date is queued as `invalid_temporal`; partial
  targets become an `observation`; explicit disagreement marks the intent
  `contested` and is never silently resolved. Every candidate result is
  recorded in `evidence_validations` with `final_status`.
- **Identity is deterministic** (D-0035): the node ID hashes the claim and
  the accepted evidence IDs, so a replay reproduces the same IDs.
- **Confidence is deterministic and categorical** (D-0043, superseding
  D-0037): fixed points per evidence kind (merged PR 0.20, diff 0.20, review
  comment 0.20, commit 0.15, doc 0.15, issue comment 0.10), a small bonus for
  independent authors, a 0.6 discount for `uncertain`, and a 0.98 cap. The
  level is `high` at ≥ 0.75, `medium` at ≥ 0.45, `low` above zero, and
  `insufficient` when no usable evidence remains. Review-comment + commit +
  diff is medium; the UI shows only the category, never a percentage.
- **Targets** must already exist in the graph index (exact key or a unique
  path/commit-filtered alias). Ambiguity is an error, never a coin flip. A
  decision with no structural target is rejected.
- **Supersession and contest** are explicit (D-0038, Intent Phase 6 in D-0044):
  deterministic code finds later/earlier decisions sharing a
  `Symbol`/`ConfigKey`/`CodeRegion` and records reversal, removal, doc and
  review signals. One bounded LLM call returns `reverses`, `revises`,
  `supersedes`, `unrelated` or `insufficient_evidence`; only the first three
  create `REVERSES`/`REVISES`/`SUPERSEDES` edges (later → earlier).
  `unrelated` and `insufficient_evidence` create no edge, and insufficient
  evidence goes to review. A conflict without a relationship still marks the
  earlier decision `contested` and never overwrites it.
- **Duplicate decisions** are merged only after deterministic candidate
  generation and one LLM adjudication (D-0045): exact normalisation, shared
  specific targets, embedding similarity (`>= 0.70` by default) and compatible
  time periods. A `merge` verdict folds the later node into the earlier one,
  unions evidence and targets and records `merged_from`; conflicts keep both
  nodes with a `CONFLICTS_WITH` edge. High-impact merges are not automatic and
  go to human review. Rejected, low-signal, ambiguous, duplicate, conflicted
  and high-impact outputs go to a JSONL **review queue** with the reason and
  the raw model text.

Artifacts land under `artifacts/intent/`: `intent_batch.json` (the full run),
`intent_graph.jsonl` (the validated node/relationship batch),
`review_queue.jsonl`, `extraction_report.json` and, from `detect`, a
`candidates.json` report. Extraction is dry-run by default; Neo4j is touched
only with `--write`, and the standalone writer requires `--uri` or
`--dry-run`.

```bash
cd backend
# Inspect which PRs would reach the model (no LLM, no DB)
PYTHONPATH=. ./.venv/bin/python -m app.intent.cli detect \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl

# Extract (dry run), then optionally write the validated batch to Neo4j
PYTHONPATH=. ./.venv/bin/python -m app.intent.cli extract \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --db ../artifacts/evidence.db \
  --structural-graph ../artifacts/structural/structural_graph.jsonl
```

## Layout

```
backend/app/
  core/         settings, errors, logging
  domain/       evidence records and evidence IDs
  graph/        Component 2 graph builder (ops, mapping, cypher, writer)
  ingestion/    Component 1 (normalizer, store, scorer, client, CLI)
  structural/   Component 3 (selection, content, languages, extraction,
                graph, validation, CLI)
  intent/       Component 3 (selection, context, llm, extraction,
                validation, graph, review, cli)
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
