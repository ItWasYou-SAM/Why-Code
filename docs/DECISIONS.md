# DECISIONS

Dated, contradictable decisions. When a decision is overturned, we append a new
entry linking to the old one — *that is the exact behavior our product must
implement on other people's repositories, so we practice it ourselves.*

---

## D-0001 — 2026-10-19 — Build order inverted against risk

**Decision.** Component 5 (retrieval/Q&A) is the riskiest claim in the plan.
A manual retrieval spike (hand-loaded mini-graph, hand-written Cypher, hand
written answer with its evidence) runs BEFORE Component 1 (ingestion).
Order: 0 → evidence/ground-truth + spike → 1 → 2 → merged 3+4 → 5 → 6(cut).

**Status.** active

---

## D-0002 — 2026-10-19 — Golden questions require human-written ground truth

**Decision.** A golden question cannot enter the suite until a human has
located the actual artifacts (PR / comment / commit) containing the real
answer and recorded them. Question without ground truth is deleted, not
deferred. "7 of 10" is meaningless otherwise.

**Status.** active

---

## D-0003 — 2026-10-19 — Supersession is pairwise LLM adjudication, not a graph heuristic

**Decision.** A "later PR touched the same region" heuristic produces
false supersession (extension, revert+reapply, pure refactor). Supersession
is adjudicated claim-vs-claim on the same target with the two evidence packs
in the prompt, capped to pairwise relationships only.

**Status.** active

---

## D-0004 — 2026-10-19 — Decisions must reground before storage

**Decision.** Schema validation (Pydantic) catches malformed JSON, not
invention. Before a decision is persisted, an evidence-regrounding check
verifies the cited excerpt actually substantiates the claim. No reground →
decision rejected, logged with reason.

**Status.** active

---

## D-0005 — 2026-10-19 — Scope cuts for the hackathon window

**Status.** active

Cuts: Docker (demo frozen before), OpenTelemetry (structlog only), Aura
Agent / MCP (bolt-on at end or never), Next.js (single static page calling
the API; React Flow only if the answer service is done).

---

## D-0006 — 2026-10-19 — httpx target is provisional until ground-truth pass

**Decision.** The flagship questions must be traceable in httpx history
before it stays the target. If ground truth fails, swap repos (candidates:
pytest, requests, zappa). Architecture is repo-agnostic.

**Status.** active

---

## D-0007 — 2026-10-19 — Per-PR ingestion is atomic: any artifact failure rolls back the PR

**Decision.** If any artifact of a PR fails normalization (malformed
payload, missing required field), the entire PR is rolled back — zero
partial writes — and marked `error` in `ingestion_state`. Salvaging
half-ingested PRs produces half-graphs that then "prove" nonexistent
relationships. Replay (force or resume) is the only recovery path.
Runs never stop because one PR is poisoned.

**Status.** active

---

## D-0008 — 2026-10-19 — Evidence ID repo token is the short name

**Decision.** Evidence IDs use the short repo name (`httpx`), not the org
slug (`encode/httpx`). Locked by test in Component 0 and made explicit here
because the graph, CLI, and citations all embed these tokens. One target
repo per dataset.

**Status.** active

---

## D-0009 — 2026-10-19 — The graph builder never talks to GitHub

**Decision.** Component 2 consumes ONLY the exported `normalized.jsonl`
plus optional store lookups for replay. No direct GitHub API calls, so
the graph is a pure function of the evidence corpus and stays replayable
offline. Any builder fetching from the network is a bug.

**Status.** active

---

## D-0010 — 2026-10-19 — CodeRegion in Component 2 comes from review-comment anchors only

**Decision.** A `CodeRegion` node exists iff a review comment carries
`path` plus an anchor (`line`/`start_line`). Its ID is
`{short}:region:{path}:{start_line}:{line}`. The `Commit`→
`CHANGED_REGION` edge is DEFERRED to Component 3, whose structural pass
can tell which hunks map to which regions; Component 2 does not guess.
Honest graph beats complete-looking graph.

**Status.** active

---

## D-0011 — 2026-10-19 — Referential integrity is fail-fast, pre-write

**Decision.** Any relationship whose endpoint record_id is not in the
same load corpus raises `InvalidRecordError` BEFORE any database
operation. The graph must never contain dangling edges; repairing them
after the fact is how carelessness hides.

**Status.** active

---

## D-0012 — 2026-10-19 — Rebuild and dry-run semantics

**Decision.** `rebuild` = wipe this repo's nodes (DETACH DELETE scoped
by the `repo` node property) then reload everything. `dry_run` =
produce the full plan and report with zero database operations.
Incremental upsert merges every node on its source `record_id`; rerun
of the same corpus yields identical node and relationship counts.

**Status.** active
