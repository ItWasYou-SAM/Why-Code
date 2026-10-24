# Component 4 — Decision extractor & evidence linking

Converts high-signal pull requests into evidence-linked engineering decisions, constraints, reversals and incidents.

This is a **pipeline, not an agent**. The model proposes one strict candidate envelope per PR; deterministic code validates every claim, citation, target and participant before anything is written.

**Status:** implemented through Phase 10 (supersession, de-duplication, the Neo4j intent writer, the human review queue and a hand-curated golden-decision evaluation set). Live LLM acceptance (≥10 valid decisions on 20 high-signal PRs) has not been demonstrated in this repository, and the public golden corpus has not been run because its ingestion artifacts are absent.

## Pipeline

```text
PR bundles
  → CandidateDetector        # deterministic signal score
  → ContextPacker            # region-first, category-budgeted evidence
  → one strict LLM call/PR   # claim + decision type + targets + evidence IDs
  → schema gate              # Pydantic + additionalProperties: false
  → evidence gate            # every cited ID must exist
  → target resolution        # exact key or unique alias, never a guess
  → rule gate                # refactor/weak-comment/reversal rules
  → claim-level firewall     # atomic claims + relevance scoring
  → confidence               # fixed points, categorical output
  → de-duplication           # deterministic pairs + one LLM adjudication
  → supersession             # pairwise adjudication, later → earlier
  → IntentGraph + write      # Phase 8: batched, validated records → Neo4j
  → review queue
```

## Candidate detection

A PR reaches the model only if it clears a fixed, additive score (D-0039):

| Signal | Weight |
| --- | ---: |
| merged PR | +2 |
| multiple review participants | +3 |
| line-anchored review comment | +4 |
| linked issue | +3 |
| issue discussion | +3 |
| motivating PR body | +2 |
| rationale commit | +2 |
| high-value path | +3 |
| docs/ADR changed | +4 |
| reversal title | +4 |

Threshold defaults to `4`. A PR that clears it gets **one** unified extraction call; candidates are validated independently, so a reversal cannot smuggle in an unsupported constraint.

## Context packing

Region-first and category-budgeted (D-0040):

1. selected hunk with current and previous sides,
2. anchored review comments,
3. commits,
4. PR body,
5. linked issues and discussion,
6. docs.

Every artifact carries an `[E#]` alias and its exact `record_id`; aliases resolve back before the evidence gate. Whole files, CI logs, unrelated tests and unlinked READMEs are excluded. Anything that does not fit is listed in `omitted_ids` and never shown (D-0034). A final guard keeps the prompt under the hard budget ceiling.

## Validation gates

**Schema.** One `CandidateExtraction` envelope (`candidates` + `missing_evidence`) covers design decisions, constraints, reversals, incident responses, API contracts and operational policies. Enums, length limits and `additionalProperties: false` are pinned by tests. The schema has **no** `confidence`, `supersedes` or graph-key field.

**Evidence.** If any cited ID is absent from the packed context, the SQLite store or the graph index, the proposal is rejected wholesale — not partially. No evidence IDs means no intent.

**Rules (D-0041).**

- a refactor without a stated rationale is not a decision,
- a review comment alone is downgraded to `uncertain`,
- an unmerged PR cannot produce an `accepted` candidate,
- a reversal must cite revert language,
- participant evidence must be cited,
- unresolved targets are rejected.

**Claim firewall (D-0042).** Each candidate is decomposed into atomic claims; each must share content terms with its cited text. Every citation is scored for relevance, every target must resolve, every temporal date must appear in evidence, and reversals need a commit or PR. Disagreement marks the intent `contested` instead of silently resolving it.

Each candidate receives a `final_status`:

```text
accepted_for_graph · observation · needs_review · rejected · discarded
```

## Confidence

Deterministic and categorical (D-0043, superseding D-0037):

| Evidence kind | Points |
| --- | ---: |
| merged PR | 0.20 |
| diff | 0.20 |
| review comment | 0.20 |
| commit | 0.15 |
| doc | 0.15 |
| issue comment | 0.10 |

Small bonus for independent authors; `uncertain` is discounted by 0.6; capped at 0.98. Levels: `high` ≥ 0.75, `medium` ≥ 0.45, `low` > 0, `insufficient` when no usable evidence remains. Review comment + commit + diff is medium. The UI shows the category, never a percentage.

## Identity, supersession and duplicates

**Identity (D-0035).** The node ID hashes the claim and accepted evidence IDs, so a replay reproduces the same IDs.

**Supersession (D-0038, D-0044).** Deterministic code finds later/earlier decisions sharing a `Symbol`, `ConfigKey` or `CodeRegion` and records reversal, removal, doc and review signals. One bounded LLM call returns `reverses`, `revises`, `supersedes`, `unrelated` or `insufficient_evidence`. Only the first three create `REVERSES` / `REVISES` / `SUPERSEDES` edges (later → earlier). Insufficient evidence goes to review. A conflict without a relationship still marks the earlier decision `contested`.

**Duplicates (D-0045).** Exact normalization, shared specific targets, embedding similarity (`>= 0.70`) and compatible time periods generate candidate pairs; one LLM call adjudicates. A `merge` folds the later node into the earlier one, unions evidence/targets and records `merged_from`. Conflicts keep both nodes with `CONFLICTS_WITH`. High-impact merges go to human review.

## Artifacts and review queue

```text
artifacts/intent/
├── intent_batch.json        # the full run
├── intent_graph.jsonl       # validated nodes/relationships
├── review_queue.jsonl       # rejected / ambiguous / conflicted outputs
├── extraction_report.json   # counts and reasons
├── evaluation_report.json   # Phase 10 metrics against the golden set
└── candidates.json          # detect report (detect command)
```

Nothing is silently dropped: rejected, low-signal, ambiguous, duplicate, conflicted and high-impact outputs enter the review queue with the reason and raw model text.

## Golden evaluation (Phase 10)

The extractor is measured against a small, manually curated golden set, not
against extraction volume (D-0048). The bundled suite has **20 cases**:

- **5 fixture cases** over the checked-in retry/timeout evidence, scored
  end-to-end in `tests/intent/test_intent_pipeline.py`;
- **15 public `encode/httpx` cases** (12 decisions, 3 refusals) waiting for a
  real ingestion/structural run.

Every case records `case_id`, `question`, `expected_decision`,
`expected_targets`, `expected_evidence`, `expected_status` and
`expected_supersession`; a case cannot enter without a human-located artifact
(D-0002). The evaluator reports candidate recall, decision precision/recall,
evidence precision/recall, target precision/recall, supersession
precision/recall, refusal correctness and status accuracy. Matching is
deterministic and one-to-one (PR, evidence, targets, claim vocabulary), with no
model in the scoring loop.

Precision counts only predictions from PRs annotated in the golden set.
Predictions from unannotated PRs are out of scope, not false positives. A
metric with no applicable cases is `null`, never `0`: "not checked" is not
"checked and clean". The fixture baseline currently has target recall `0.5`:
proposals name the `retries` config key but not the enclosing symbol or file.
That gap is recorded, not hidden by weakening the golden expectations.

The public corpus has **not** been executed in this repository because
`evidence.db`, `normalized.jsonl`, `high_signal_prs.jsonl` and a live LLM run
are absent. Do not quote public-suite numbers until such a run exists. Metric
formulas, report fields and case provenance are documented in
[`app/intent/evaluation/README.md`](evaluation/README.md) and
[`docs/GOLDEN_SET.md`](../../../docs/GOLDEN_SET.md).

The evaluator scores an existing batch offline; it is not a runtime pipeline
stage.

## CLI

```bash
cd backend

# Which PRs would reach the model? (no LLM, no DB)
PYTHONPATH=. python -m app.intent.cli detect \
  --repo encode/httpx \
  --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts

# Extract (dry run)
PYTHONPATH=. python -m app.intent.cli extract \
  --repo encode/httpx \
  --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --structural-graph ../artifacts/structural/structural_graph.jsonl \
  --db ../artifacts/evidence.db \
  --artifacts-dir ../artifacts

# Add --write to load Neo4j, or load an existing batch later
PYTHONPATH=. python -m app.intent.cli write \
  --input ../artifacts/intent/intent_graph.jsonl \
  --repo encode/httpx --dry-run

# Human decisions over the queue (Phase 9)
PYTHONPATH=. python -m app.intent.cli review \
  --queue ../artifacts/intent/review_queue.jsonl \
  --repo encode/httpx \
  --action approve \
  --candidate rq_httpx_...

# Score a run against the golden set (Phase 10, no LLM)
PYTHONPATH=. python -m app.intent.cli evaluate \
  --predictions ../artifacts/intent/intent_batch.json \
  --corpus all \
  --repo encode/httpx \
  --out ../artifacts/intent/evaluation_report.json
```

## Layout

```text
app/intent/
├── selection/    # candidate detection + evidence selection
├── context/      # budgeting, filters, diff and region rendering
├── llm/          # provider boundary, strict client, schemas, retry
├── extraction/   # unified + per-kind extractors
├── validation/   # schema, evidence, rules, claims, confidence, supersession, dedupe
├── review/       # JSONL review queue
├── graph/        # intent records + Cypher writer
├── evaluation/   # golden cases, deterministic matching and metrics
├── pipeline.py   # orchestration
└── cli.py        # detect / extract / write / review / evaluate
```

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests/intent -q
```

`tests/intent/test_evaluation.py` pins the golden-set shape, metric formulas,
null-vs-zero honesty and the `evaluate` CLI. `tests/intent/test_intent_pipeline.py`
scores a real fixture run and records the target-recall gap described above.
