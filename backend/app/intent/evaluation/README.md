# Phase 10 — golden decision set and evaluation

A small, hand-curated suite of real decisions, scored without an LLM in the
loop. It exists to answer one question before a repository-wide run:

> Are the extracted decisions correct, evidence-backed and linked to the right
> code — or did we only produce a large number of plausible paragraphs?

The suite is deliberately not a benchmark for "decisions extracted per PR".
Adding cases is a human act: a case links to the artifacts that actually
contain the decision (D-0002, D-0048).

## Files

```text
app/intent/evaluation/
├── golden/decisions.jsonl   # 20 hand-curated cases
├── models.py                # case + prediction schemas
├── golden.py                # load and cross-check the suite
├── predictions.py           # intent_batch.json / JSONL / JSON input
├── matching.py              # deterministic one-to-one case matching
├── metrics.py               # the Phase 10 metrics
├── runner.py                # per-corpus orchestration
└── README.md
```

`golden/decisions.jsonl` has **20 cases**: five executable fixture cases
(`corpus: "fixture"`) and fifteen public `encode/httpx` cases
(`corpus: "httpx-public"`). Seventeen are decisions; three are refusal cases
where the PR is a pure refactor or documentation change.

The fixture corpus is scored end-to-end in
`tests/intent/test_intent_pipeline.py`. The public corpus is manual ground
truth for the first real run; it has **not** been executed here because this
repository does not contain `evidence.db`, `normalized.jsonl` or
`high_signal_prs.jsonl`. Do not report public-suite numbers until there is a
run to score.

## Run it

```bash
cd backend

# Score the bundled fixture corpus against an intent batch.
PYTHONPATH=. python -m app.intent.cli evaluate \
  --predictions ../artifacts/intent/intent_batch.json \
  --corpus fixture \
  --out ../artifacts/intent/evaluation_report.json

# Score both corpora after the full HTTPX run.
PYTHONPATH=. python -m app.intent.cli evaluate \
  --predictions ../artifacts/intent/intent_batch.json \
  --corpus all \
  --repo encode/httpx \
  --out ../artifacts/intent/evaluation_report.json
```

The input can be `intent_batch.json`, a JSONL file of intent records, or a
small JSON object with `decisions` / `edges` / `refusals`. An intent batch's
`supersession_edges` and `supersession_decisions` are both accepted.

## Metrics

Every metric reports `numerator`, `denominator` and `value`. A metric with no
applicable cases is `null`, never `0`: "not checked" is not "checked and
clean" (D-0032). Values are ratios in `[0, 1]`; higher is better.

| Metric | Numerator | Denominator |
| --- | --- | --- |
| `candidate_recall` | Expected-decision PRs with at least one in-scope prediction | Unique expected-decision PRs |
| `decision_precision` | Golden decisions matched to a prediction | Predictions from golden PRs |
| `decision_recall` | Golden decisions matched to a prediction | Golden decisions (refusals excluded) |
| `evidence_precision` | Cited evidence IDs that are in the matched case's expected key evidence | All evidence IDs cited by matched decisions |
| `evidence_recall` | Expected key evidence IDs cited by the matched prediction | All expected key evidence for matched cases |
| `target_precision` | Predicted targets that are in the matched case's expected targets | All targets predicted by matched decisions |
| `target_recall` | Expected targets found in the matched prediction | All expected targets for matched cases |
| `supersession_precision` | Predicted edges matching the expected later case, relationship and earlier case | Predicted edges whose two endpoints matched golden cases |
| `supersession_recall` | Correct reversal edges | Expected reversal edges |
| `refusal_correctness` | Refusal cases with no prediction from their PR | Refusal cases |
| `false_refusal_rate` | Explicit refusals at expected-decision PRs | Expected decisions |
| `status_accuracy` | Matched decisions whose lifecycle status matches the case | Matched decisions |

Precision is limited to predictions whose PR belongs to the golden set. The
golden set is not an exhaustive annotation of the repository, so a correct
decision from an unannotated PR must not be called a false positive. Ignored
predictions are counted in `counts.predictions_out_of_scope`.

`evidence_precision` and `evidence_recall` use exact evidence IDs. If a run
cites extra evidence that is genuinely supportive but not listed in the case,
it counts as imprecise; that is intentional, because the case's evidence list
is the human statement of what supports the decision. Add it to the case when
it is genuinely key.

`target_precision` / `target_recall` compare normalised `type:identifier`
pairs. The normaliser accepts the graph's `ConfigKey` spelling as well as
`config_key`, drops a leading `httpx.` from config keys, and normalises file
path separators.

A supersession edge is scored only when both endpoint predictions matched
golden cases. Edges to unmatched nodes are listed in
`unscored_supersession_edges` rather than silently ignored.

## Reading a report

`report.cases` is the review surface, not just the headline ratio. Each case
records:

- the matched prediction ID (or `null`),
- missing and extra evidence,
- missing and extra targets,
- whether the supersession edge was expected and whether it was correct.

A false positive is a prediction from a golden PR that matched no case. A
missing decision is a golden case with no matched prediction. The report keeps
them separate so recall and precision can move independently.

## Adding a case

1. Locate the real artifact: merged PR, review comment, commit, issue, doc.
2. Record the exact evidence IDs that a correct decision should cite.
3. Record the code/config target(s) the decision governs, as
   `symbol:...`, `config_key:...`, `file:...` or `code_region:...`.
4. Record the expected lifecycle status and, only when the artifacts prove it,
   the expected reversal edge with `relationship` and `target_case_id`.
5. If there is no decision in the artifact, add a `refused` case with no
   decision, targets or evidence.
6. Run the suite tests. A case without located evidence does not enter the
   file.

## Known fixture baseline

The fixture corpus currently scores `1.0` for candidate recall, decision
precision/recall, evidence precision/recall and supersession
precision/recall. Target precision is `1.0` but target recall is `0.5`: the
fixture proposals name the `retries` config key but not the enclosing symbol
or file. The golden expectations were not lowered to hide that gap.
