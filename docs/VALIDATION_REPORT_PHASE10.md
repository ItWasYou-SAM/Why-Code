# Phase 10 — Validation Report (brutal version)

Date: 2026-10-19
Scope: the built structural graph artifact (`artifacts/structural/batch.json`),
the new Phase 10 validator, and the review-anchor path in Component 2.
Verdict: **the validator is real and green; the "build" it was pointed at is a
6-file smoke corpus, not the 84-file high-signal run the plan describes. The
demo numbers in the plan are not reproducible from anything in this repo.**

## What was executed

```bash
cd backend
./.venv/bin/python -m pytest tests --ignore=tests/structure -q   # 457 passed, 8 skipped
./.venv/bin/python -m ruff check app tests                       # All checks passed
./.venv/bin/python -m mypy app                                   # Success, 64 source files

./.venv/bin/python -m app.structural.cli graph \
  --batch ../artifacts/structural/batch.json \
  --out ../artifacts/structural/structural_graph.jsonl           # nodes=169 edges=300

./.venv/bin/python -m app.structural.cli validate \
  --batch ../artifacts/structural/batch.json \
  --graph ../artifacts/structural/structural_graph.jsonl \
  --out ../artifacts/structural/validation_report.json           # exit 0
```

`8 skipped` are the opt-in Neo4j integration tests: no `WHYCODE_TEST_NEO4J_URI`,
no `.env`, no Docker, no reachable server. **Nothing was validated against a
live Neo4j instance.**

## The validator that now exists

`app/structural/validation/graph_validator.py` implements every check in the
Phase 10 table as a pure function over the built records (no DB), plus the
same sweep as Cypher for post-write runs.

| Check | Rule | Status |
|---|---|---|
| Duplicate IDs | node keys unique per label (`symbol_id`/`region_id`/`config_id` are the keys) | implemented |
| Broken references | every relationship endpoint exists | implemented |
| Invalid symbol ranges | `1 <= start <= end <= File.line_count` | implemented |
| Invalid regions | same, counts as `invalid_regions` | implemented |
| Orphan files | no `DEFINES`/`HAS_REGION`/`DOCUMENTED_BY`; config files with `DECLARES` are the allowlist | implemented |
| Config edge sanity | `USED_BY` must start at a declared `ConfigKey` with a literal value | implemented |
| Import sanity | `IMPORTS` targets only `File` or `ExternalRef`; internal edges counted | implemented |
| Region coverage | high-signal PRs must own a changed `CodeRegion` | implemented, `not_checked` without `--high-signal` |
| Review anchor coverage | line-anchored `ReviewComment` must have a containing `CodeRegion` via `COMMENTS_ON` | implemented, `not_checked` in a structural-only graph |

Honesty rule (D-0032): a check with no input is reported in `not_checked`
with a `null` stat. It is never printed as `0` — a zero reads as "checked and
clean", which is the false comfort this phase exists to kill. `status` is
`pass`, `pass_with_warnings` or `fail`, and the CLI exits non-zero on `fail`.

New commands:

```bash
python -m app.structural.cli graph       --batch batch.json --out structural_graph.jsonl
python -m app.structural.cli validate    --batch batch.json [--graph graph.jsonl] [--high-signal prs.jsonl]
python -m app.structural.cli validate    --uri $NEO4J_URI --database neo4j --repo encode/httpx
python -m app.structural.cli write-graph --input structural_graph.jsonl --uri $NEO4J_URI --database neo4j
```

## The actual run (structural corpus)

`artifacts/structural/validation_report.json`:

```json
{
  "files_parsed": 6,
  "symbols_created": 60,
  "imports_created": 18,
  "config_keys_created": 22,
  "code_regions_created": 74,
  "relationships_created": 300,
  "unresolved_imports": 31,
  "external_imports": 31,
  "relative_unresolved_imports": 0,
  "internal_import_edges": 0,
  "unresolved_references": 0,
  "orphans": 0,
  "orphan_docs": 1,
  "invalid_regions": 0,
  "invalid_symbols": 0,
  "duplicate_ids": 0,
  "missing_review_anchors": null,
  "high_signal_prs_missing_region": 0,
  "status": "pass_with_warnings",
  "not_checked": ["region_coverage", "review_anchor_coverage"]
}
```

Read that against the plan's sample (`84 files`, `612 symbols`, `1420
relationships`, `41 unresolved imports`, `97 unresolved references`, `3
orphans`, `2 missing_review_anchors`): **the shapes are the same and the
numbers are nothing alike.** The artifact is 8 manually pinned paths at one
commit (`818e29f`), filtered to 6 parseable files. It is a smoke test.

Three honest headlines inside even this small run:

1. `internal_import_edges: 0`. None of the 6 selected files import another
   selected file, so the "internal imports resolve to File@commit" check is
   **vacuous here**. All 31 imports are third-party/stdlib.
2. `missing_review_anchors: null`, not `0`. This artifact contains no
   `ReviewComment` nodes, so anchor coverage cannot be judged from it. The
   plan says the demo depends on comment→code links; **this artifact does not
   contain a single one.**
3. `orphan_docs: 1`. The only `Doc` node is `README.md`, unreachable because
   its linked target `docs/DECISIONS.md` is markdown (no `File` node). The one
   doc relation the corpus had was dropped. A `Doc → Doc` mention is currently
   unrepresentable.

## The review-anchor path is proven, but only on fixtures

Against the Component 2 test corpus (PR 1131, hand-built records):

```
relationships_created: 24   code_regions_created: 2
missing_review_anchors: 0   high_signal_prs_missing_region: 0
```

The checker correctly flags a comment whose anchor falls outside every region
(unit test `test_review_anchor_coverage_reports_missing`). So the mechanism
works. What does **not** exist is a real PR corpus to point it at.

## Blockers — do not proceed to LLM extraction yet

1. **No ingestion database.** `artifacts/httpx_ingestion.db`, `evidence.db`,
   `normalized.jsonl`, `high_signal_prs.jsonl` and `ingestion_report.json` do
   not exist anywhere in the repo. Component 1's output — the only input the
   structural selector accepts — was never persisted here. Everything in the
   plan that says "high-signal PRs" cannot run.
2. **No Component 2 graph artifact.** There is no PR/ChangedFile/Review/
   ReviewComment graph JSONL. The review-anchor check has no real input.
3. **The checked-in `build_report.json` was stale.** It recorded 234 edges
   and zero `REFERENCES`; the current code builds 300 edges including 66
   `REFERENCES` from the same batch. It was regenerated in this pass and now
   matches (300 edges). Any number read from the old artifact was wrong by 66
   relationships (22%).
4. **Bare `pytest` cannot collect the repo.** `tests/structure/` (the
   abandoned "C3 red (alt contract)" suite, untracked) collides on module
   basenames with `tests/ingestion/test_service.py` and `tests/graph/*`, and
   `tests/structure/test_service.py` fails to import its own `fakes`. It is
   dead code. Until it is deleted (or made a package), only
   `pytest tests --ignore=tests/structure` is meaningful. Similarly
   `backend/app/structure/` is a second, unused implementation of the same
   component (`app/structural/` is the one under test).
5. **No live Neo4j run.** Post-write validation, schema, and batched writes
   are exercised only against in-memory fakes.

## What must happen before Component 4

1. Run Component 1 against the real repo and persist `evidence.db` +
   `high_signal_prs.jsonl`.
2. Build the Component 2 PR graph, write it, and materialize the structural
   graph for the **same** high-signal PR set.
3. Run `validate --batch … --high-signal …` and require
   `missing_review_anchors == 0` (or an explicitly accepted small number) and
   `high_signal_prs_missing_region == 0`.
4. Load into Neo4j and run `validate --uri … --repo …`; require the same
   result before the LLM ever sees the graph.
5. Delete `tests/structure/` and `app/structure/`, or state in one line which
   one is authoritative. Two implementations of one component is the biggest
   process risk in the repo today.
