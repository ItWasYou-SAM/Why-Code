# Golden decision set (Intent Phase 10)

Status: **20 cases curated; fixture corpus executable; public corpus not yet
run.** This file is the human-readable index for
`backend/app/intent/evaluation/golden/decisions.jsonl`.

The set exists to measure correctness, not volume: candidate recall, decision
precision/recall, evidence precision/recall, target precision/recall,
supersession precision/recall and refusal correctness. Metric definitions and
caveats live in
[`backend/app/intent/evaluation/README.md`](../backend/app/intent/evaluation/README.md).

## Corpus split

| Corpus | Cases | Purpose |
| --- | ---: | --- |
| `fixture` | 5 | Runs end-to-end against the checked-in retry fixtures; proves the harness and the extractor agree on a small, exact corpus. |
| `httpx-public` | 15 | Manually curated from merged `encode/httpx` PRs and linked issues; waits for the real ingestion/structural artifacts. |

## Fixture cases

These cases are backed by `tests/intent/fixtures/retry_pr_evidence.json` and
`timeout_pr_evidence.json`. Every evidence ID in the cases exists in those
files.

| Case | PR | Decision | Expected status |
| --- | ---: | --- | --- |
| `retry_limited_to_connection_errors` | 1419 | Retry only connection errors; retrying reads/writes can duplicate side effects. | reversed by 1525 |
| `retry_connection_only_constraint` | 1419 | Never retry a non-connection request. | accepted |
| `retry_default_disabled` | 1525 | Disable retries by default; callers opt in. | accepted |
| `retry_opt_in_constraint` | 1525 | Retries must be explicitly opted into. | accepted |
| `revert_default_retry_behaviour` | 1525 | Revert the default retry behaviour. | reversal edge to 1419 |

## Public HTTPX cases

All public decisions are anchored to merged PRs in `encode/httpx`. Linked
issue evidence is included only when the PR body or commit closes/references
that issue. Targets use the graph's `type:identifier` form.

| Case | PR | Decision | Evidence |
| --- | ---: | --- | --- |
| `zstd_optional_decoding` | 3139 | zstd decoding is an optional `httpx[zstd]` extra. | PR 3139 |
| `deprecate_app_shortcut` | 3050 | `app=` is deprecated in favour of explicit WSGI/ASGI transports. | PR 3050 |
| `sslcontext_api` | 3319 | TLS configuration moves to `httpx.SSLContext`. | PR 3319 |
| `compact_json_request_bodies` | 3367 | JSON bodies use compact separators, UTF-8, and reject NaN. | PR 3367, issue 3363 |
| `socks5h_proxy_dns` | 3178 | `socks5h://` delegates DNS to the proxy. | PR 3178 |
| `asgi_transport_starlette_types` | 3109 | ASGI types accept `MutableMapping` / `Awaitable` to match Starlette. | PR 3109, issue 3111 |
| `query_param_percent_encoding` | 3187 | Query params are always percent-encoded; URLs preserve escapes. | PR 3187, issue 3140 |
| `lazy_certifi_httpcore_imports` | 3377 | `certifi` and `httpcore` load lazily. | PR 3377, issue 3330 |
| `drop_sniffio_dependency` | 3323 | Remove `sniffio`; use lazy ASGI detection and `perf_counter`. | PR 3323, PR 2858 |
| `reject_verify_string` | 3418 | Reject `verify=<str>` with an explicit migration error. | PR 3418 |
| `extensions_mapping_contract` | 3380 | Extensions are read-only `Mapping` values copied into a dict. | PR 3380 |
| `whatwg_percent_escape_sets` | 3371 | Use WHATWG percent-encode sets for URL components. | PR 3371 |

### Refusal cases

These PRs are real merged changes but contain no engineering decision with a
rationale. A correct run emits no decision for them.

| Case | PR | Why it is a refusal |
| --- | ---: | --- |
| `utility_functions_moved` | 3387 | Pure move of utility functions. |
| `docs_warning_indent` | 3056 | Documentation rendering fix. |
| `sponsorship_placement` | 3305 | Sponsor placement in the docs. |

## What is deliberately not claimed

- The public suite has not been executed. There are no public-suite metric
  values in this repository.
- The fixture baseline is not a perfect extraction claim. Fixture target recall
  is `0.5` because the fixture proposals only name the `retries` config key,
  while the cases expect the symbol and file too.
- The suite is not exhaustive. Precision ignores predictions from PRs outside
  the annotated set; `counts.predictions_out_of_scope` makes that explicit.
- The cases are not tuned after seeing a run. If a case is wrong, fix the case
  with a located artifact and record the correction; do not edit the expected
  answer to match the extractor.

## Running the harness

```bash
cd backend
PYTHONPATH=. python -m app.intent.cli evaluate \
  --predictions ../artifacts/intent/intent_batch.json \
  --corpus all \
  --repo encode/httpx \
  --out ../artifacts/intent/evaluation_report.json
```
