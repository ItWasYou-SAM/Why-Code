# Component 1 — Ingestion Report (brutal version)

Date: 2026-10-19
Scope: `backend/app/ingestion/*` (store, normalizer, client, scorer,
service, exporter, CLI).
Verdict: **the fixture-level pipeline is real and green. The live
100-PR httpx claim is still unproven — there was no GitHub token in this
environment, so no live run was possible.**

## What was actually executed

```bash
cd backend
./.venv/bin/python -m pytest tests/ingestion -q
# 95 passed

./.venv/bin/ruff check app tests
# All checks passed

./.venv/bin/mypy app/ingestion app/core/errors.py
# Success: no issues found in 9 source files
```

The full suite was **318 passed, 7 skipped** at the moment the ingestion
work finished. A separate, concurrent edit to the structural package
(not part of this task and not made here) later left the full suite at
**282 passed, 37 failed**; all 37 failures are in
`tests/structural/*`, and the ingestion suite stayed at **95 passed**.
The same concurrent edit also broke `mypy app` (8 errors in
`app/structural/graph/graph_records.py`), while
`mypy app/ingestion app/core/errors.py` still passes. Do not read the
full-suite/full-app mypy numbers as an ingestion regression.

A fixture-driven end-to-end run was executed into
`/tmp/opencode/whycode-ingestion-demo/artifacts` (not committed). It
produced `evidence.db`, `normalized.jsonl`, `high_signal_prs.jsonl`, and
`ingestion_report.json`.

## The 12-test plan: results

All tests are in `backend/tests/ingestion/test_acceptance_plan.py`.
They use canned payloads and a mocked wire (`respx`); none touch live
GitHub.

| # | Test | Result | What it actually proves |
|---|------|--------|--------------------------|
| 1 | `test_pr_record_id_stability` | PASS | Same PR ⇒ same ID and hash; different PR ⇒ different ID. |
| 2 | `test_merged_filter` | PASS | Closed/unmerged PR is skipped; zero records written. |
| 3 | `test_date_window` | PASS | Merge before `since` excluded; exact boundary included. |
| 4 | `test_review_comment_anchor` | PASS | `path`, `line`, `start_line`, `side` survive to SQLite. |
| 5 | `test_renamed_file` | PASS | `previous_filename` preserved; absent value is explicit `null`. |
| 6 | `test_pagination_next_link` | PASS | Client follows `Link: rel="next"` and does not guess page 2. |
| 7 | `test_rate_limit_backoff` | PASS | 429 honors `Retry-After`; remaining quota tracked; retries counted. |
| 8 | `test_idempotent_upsert` | PASS | Three runs, zero duplicate rows, zero duplicate raw keys. |
| 9 | `test_content_change_updates_record` | PASS | Edited PR body replaces old payload; one row, new content hash. |
| 10 | `test_malformed_comment` | PASS | Bad comment errors that PR only; sibling PR still completes. |
| 11 | `test_export_record_types` | PASS | JSONL contains all nine record types with valid payloads. |
| 12 | `test_report_counts` | PASS | Report counts equal DB counts; run status/failures are accurate. |

Five additional service tests cover acceptance items the plan left out:

- raw responses are persisted (`test_raw_responses_are_saved_locally`)
- one-command evidence inspection (`test_show_command_prints_one_prs_evidence_trail`)
- failures name the endpoint that broke (`test_report_failure_names_the_endpoint`)
- retry only touches errored PRs (`test_retry_errored_only_refetches_failed_prs`)
- run IDs are monotonic (`test_run_id_is_monotonic`)

A sixth, CLI-level test (`TestRetryCommand` in `test_cli.py`) proves the
`retry` subcommand repairs an errored PR and rewrites the report.

## Gaps found (the build was not actually complete)

These were missing despite the "build is complete" claim, and were fixed:

1. **Report schema was not the promised schema.** It had no `run_id`,
   `status`, `selected_prs`, `processed_prs`, `failed_prs`,
   `high_signal_prs`, `rate_limit_remaining`, or structured `failures`.
   It now emits every field, and legacy keys are retained for older
   callers.
2. **Raw responses were never saved.** `EvidenceStore.upsert_raw` existed
   and was tested, but the service never called it. Every artifact call
   (PR, files, commits, reviews, comments, linked issues, doc blobs) is
   now archived under `{repo}:{endpoint}` before normalization.
3. **Pagination ignored the `Link` header.** It guessed page numbers.
   It now follows `rel="next"` and keeps page-increment as a fallback.
4. **Renames lost `previous_filename`.** It is now preserved on every
   changed-file record.
5. **Reported API usage was always zero on real runs.** The real client
   never counted requests. It now counts every wire attempt, including
   retries, and records `X-RateLimit-Remaining`.
6. **Failures had no endpoint.** Reports could only say "normalize".
   Errors now carry the GitHub endpoint (or the artifact endpoint when
   normalization rejects a payload).
7. **No way to inspect a PR.** Added
   `python -m app.ingestion.cli show --repo … --pr …` (offline; `--json`
   for full records + raw payloads).
8. **No retry command.** Added
   `python -m app.ingestion.cli retry --repo …`, which re-fetches only
   PRs marked `error` and never touches completed PRs.

## Report shape (real fixture run)

```json
{
  "run_id": "run_2026_10_19_001",
  "repo": "encode/httpx",
  "started_at": "2026-10-19T14:56:15.994690+00:00",
  "finished_at": "2026-10-19T14:56:15.998143+00:00",
  "status": "completed_with_failures",
  "selected_prs": 3,
  "processed_prs": 2,
  "failed_prs": 1,
  "records": {
    "pull_request": 2,
    "commit": 4,
    "changed_file": 4,
    "review": 2,
    "review_comment": 3,
    "issue": 1,
    "issue_comment": 2,
    "person": 4,
    "doc_file": 2
  },
  "records_total": 24,
  "high_signal_prs": 1,
  "api_requests": 24,
  "rate_limit_remaining": null,
  "failures": [
    {
      "pr_number": 1132,
      "endpoint": "pulls/1132/comments",
      "error_type": "InvalidRecordError",
      "message": "InvalidRecordError: review comment is missing a numeric id"
    }
  ]
}
```

`rate_limit_remaining` is `null` here because the fake client does not
simulate GitHub headers. The real client populates it from
`X-RateLimit-Remaining`.

## Acceptance checklist — honest status

| Requirement | Status |
|---|---|
| Select 100 merged httpx PRs | **Unverified live.** Selection logic is tested on fixtures; no token/network run was done. |
| Fetch evidence for each selected PR | Verified on fixtures. |
| Raw responses saved locally | Verified; fixed in this pass. |
| Normalized records stored in SQLite | Verified. |
| Reruns do not duplicate records | Verified (3 consecutive runs). |
| Changed PR data updates, not duplicates | Verified. |
| Review comments retain file/line anchors | Verified. |
| High-signal PRs exported separately | Verified. |
| Failures do not stop the pipeline | Verified (poisoned PR + sibling completes). |
| Report shows counts, failures, API usage | Verified; fixed in this pass. |
| Inspect any PR's evidence with one command | Verified (`show`). |

## Remaining risks / things I would not claim

1. **No live 100-PR proof.** This is the one acceptance item that matters
   most and cannot be closed without `WHYCODE_GITHUB_TOKEN`. Run:
   ```bash
   cd backend
   PYTHONPATH=. ./.venv/bin/python -m app.ingestion.cli ingest \
     --repo encode/httpx --since 2024-01-01 --limit 100
   ```
2. **404 is reported as `TransientUpstreamError`.** The taxonomy has no
   not-found class, so a deleted PR/file would be mislabelled and would
   consume four retry attempts before failing.
3. **A non-rate-limit 403 is reported as `RateLimitError`.** Bad token
   or missing permission will look like quota exhaustion in the report.
4. **Raw archive stores combined pages, not individual page bodies.**
   Replay is possible, but byte-level debugging of a specific page is
   already lost after pagination.
5. **Raw payloads are committed immediately, while normalized records
   are per-PR atomic.** This is deliberate (failures must remain
   debuggable), but it means a failed PR leaves raw data and no
   normalized records. The report should be read with that asymmetry in
   mind.
6. **Doc discovery is narrow.** Only changed files under `docs/` or a
   changed README are ingested. Unchanged design docs are never
   discovered.
7. **Linked issues are regex-only** (`close/fix/resolve #N` in the PR
   body). Timeline cross-references and “part of #N” links are ignored.
8. **`rate_limit_remaining` is only the last observed value**, not the
   quota at the start or end of the run.
9. **The legacy report keys duplicate the new ones** (`records` vs
   `by_type`, `api_requests` vs `api_calls`). Intentional compatibility,
   but the report is not minimal.
10. **The 7 skipped tests are Neo4j integration tests**, not ingestion
    tests. They require external credentials and were not run.

## Commands added in this pass

```bash
# Full run (writes all four artifacts; report is generated every run)
python -m app.ingestion.cli ingest --repo encode/httpx --since 2024-01-01 --limit 100

# Recover only PRs marked error
python -m app.ingestion.cli retry --repo encode/httpx

# Inspect one PR's normalized + raw evidence trail, offline
python -m app.ingestion.cli show --repo encode/httpx --pr 1131
python -m app.ingestion.cli show --repo encode/httpx --pr 1131 --json
```
