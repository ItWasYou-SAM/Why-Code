# Component 2 — Neo4j graph builder

Turns the normalized evidence corpus into a traceable Neo4j graph of repositories, pull requests, commits, files, code regions, reviews, comments, issues and people.

**Status:** implemented. Graph construction and validation run offline in tests; a live AuraDB load is opt-in and was not executed in this repository. See [`docs/VALIDATION_REPORT_PHASE10.md`](../../../docs/VALIDATION_REPORT_PHASE10.md).

## Pipeline

```text
normalized.jsonl
      ↓
ops_from_records()      # domain operations, no database
      ↓
mapping                 # nodes + relationships with locked IDs
      ↓
cypher templates        # parameterized statements
      ↓
builder / batch_writer  # integrity checks, then batched MERGE
```

No extractor writes Cypher. Producers emit one transport-neutral record contract, and a single writer sends it.

## Graph shape

```text
(:Repo)-[:HAS_PULL_REQUEST]->(:PullRequest)
(:PullRequest)-[:HAS_COMMIT]->(:Commit)
(:PullRequest)-[:CHANGED_FILE]->(:ChangedFile)
(:ChangedFile)-[:HAS_REGION]->(:CodeRegion)
(:Commit)-[:CHANGED_REGION]->(:CodeRegion)
(:PullRequest)-[:HAS_REVIEW]->(:Review)
(:Review)-[:HAS_COMMENT]->(:ReviewComment)
(:ReviewComment)-[:COMMENTS_ON]->(:CodeRegion)
(:PullRequest)-[:DISCUSSES]->(:Issue)
(:Issue)-[:HAS_COMMENT]->(:IssueComment)
(:Person)-[:AUTHORED]->(:Commit | :PullRequest)
(:Person)-[:REVIEWED]->(:PullRequest)
(:Person)-[:COMMENTED]->(:ReviewComment | :IssueComment)
```

Component 3 adds `File`, `Symbol`, `ConfigKey`, `Doc` and `ExternalRef` nodes plus `DEFINES`, `DECLARES`, `HAS_REGION`, `IMPORTS`, `REFERENCES`, `USED_BY` and `DOCUMENTED_BY` edges.

## Code regions

A `CodeRegion` is always a bounded span with provenance — never a guessed symbol-to-hunk mapping.

| Source | Span | Owner |
| --- | --- | --- |
| `definition` | full symbol declaration | `Symbol → HAS_REGION`, `File → HAS_REGION` |
| `changed_hunk` | new-file range from one `@@` header | `ChangedFile → HAS_REGION`, `CHANGED_IN → Commit` |
| `review_comment` | anchor (`start_line`/`line`) | `ChangedFile → HAS_REGION`, `ReviewComment → COMMENTS_ON` |

Hunk IDs are commit-scoped (`{short}:region:{path}@{sha}:{start}:{end}`), so the structural pass can attach the symbols containing those lines. Review regions keep the pinned D-0010 ID and gain `commit_sha`, `file_id`, `source_pr_number`, `content_hash` and a `CHANGED_IN` edge.

A pure deletion anchors at the deletion line; a deleted file creates no region. Hunk creation can be restricted to high-signal PRs.

## Records and batched writes

Both graph halves emit the same shape (`app/graph/records.py`):

```python
GraphNode(label="Symbol", key="httpx:symbol:...@abc:Client.send", properties={...})
GraphRelationship(rel_type="DEFINES", start_key="httpx:file:...", end_key="...")
```

`app/graph/batch_writer.py` groups records by label/relationship type, chunks at `batch_size`, and emits `UNWIND $nodes … MERGE` / `UNWIND $relationships … MATCH … MERGE`. Nodes are written before relationships because endpoints are matched, not created.

Integrity is asserted before the first statement: unique keys within a label and no dangling endpoint. A bad batch therefore costs zero writes.

## Neo4j driver and schema

`app/graph/neo4j_driver.py` is the only object that opens a connection. It:

- uses `Driver.execute_query` (managed transactions with driver retry),
- always names the target database explicitly,
- runs an idempotent `CREATE CONSTRAINT … IF NOT EXISTS` / `CREATE INDEX … IF NOT EXISTS` sweep over the label vocabulary before the first batch,
- keeps one canonical identity property, `record_id`, whose value is the label-specific ID (`file_id`, `symbol_id`, `config_id`, `region_id`, `ref_id`, `doc_id`).

Writes batch at `DEFAULT_BATCH_SIZE = 250`.

Full file content never enters Neo4j — only `content_hash`, `byte_size` and `line_count`. Content lives in the structural content store.

## CLI

```bash
cd backend

# Build + validate records offline (no database)
PYTHONPATH=. python -m app.structural.cli build \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts --remote

# Write the graph (requires WHYCODE_NEO4J_*)
PYTHONPATH=. python -m app.structural.cli build \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts --remote --write
```

The pass is dry-run by default; it connects only with `--write` and only after validation passes.

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests/graph -q
```

Live Neo4j tests are skipped unless `WHYCODE_TEST_NEO4J_URI` (plus `_USER`/`_PASSWORD`/`_DATABASE`) is set.
