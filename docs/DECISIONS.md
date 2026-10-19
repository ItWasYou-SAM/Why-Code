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

---

## D-0013 — 2026-10-19 — Structural parsing never walks the repository

**Decision.** Component 3 parses only what Component 1 emitted as changed
files in high-signal PRs, plus explicitly pinned paths. There is no
`os.walk` over the checkout; a file that was not selected does not exist
to the parser. Full-repo parsing would silently import current-HEAD facts
into a historical graph.

**Status.** active

---

## D-0014 — 2026-10-19 — Structural file identity is `path@commit_sha`

**Decision.** A File node is a file-at-a-commit, not a path. IDs:
`{short}:file:{path}@{sha}`, `{short}:symbol:{path}@{sha}:{qualname}`,
`{short}:region:{path}@{sha}:{start}:{end}`, `{short}:config:{source}:{key}`,
`{short}:external:{module}`, `{short}:doc:{path}`. When no merge commit is
known the selector uses the PR's latest commit timestamp; that choice is
recorded as the file's identity, never as a claim about the merge.

**Status.** active

---

## D-0015 — 2026-10-19 — Imports resolve only against the known selected set

**Decision.** An `IMPORTS` edge to another `File` is created only when the
imported path is provably one of the selected files (named submodule for
`from . import X`, then `__init__.py`). Anything else becomes an
`ExternalRef` node keyed by module name. An unresolvable relative import is
never guessed, and no cross-repo or dynamic import is resolved.

**Status.** active

---

## D-0016 — 2026-10-19 — Config references come from literal access patterns only

**Decision.** Declared keys come from parsing YAML/TOML/JSON/`.env`. A
key is *referenced* only by an explicit literal: `os.environ[...]`,
`os.environ.get`, `os.getenv`, an uppercase `settings.KEY` attribute, or a
string literal that exactly matches a declared key. No key is invented from
comments, prose, or name similarity.

**Status.** active

---

## D-0017 — 2026-10-19 — Docs link only on an explicit mention

**Decision.** A `DOCUMENTED_BY` edge is created only when the doc names the
target explicitly as a backticked path or an exact config key. "This
paragraph is about retries" links nothing. This is the doc-side statement
of the no-inference rule the whole component obeys.

**Status.** active

---

## D-0018 — 2026-10-19 — Unparseable or unfetchable files are skipped, never half-represented

**Decision.** If content cannot be fetched, exceeds `max_file_bytes`, or the
grammar produces an error node, the file yields zero nodes and is recorded in
`batch.skipped` with a reason. A partial graph for a broken file invites
false relationships later; honest absence beats a partial node.

**Status.** active

---

## D-0019 — 2026-10-19 — Structural writes are dry-run unless `--write` and validation passes

**Decision.** The structural CLI computes the full node/edge plan and a
validation report with zero database I/O. Neo4j is opened only when
`--write` is passed AND validation passes; a failing validation never writes.
Pre-write validation (endpoint integrity, identity uniqueness) is the same
fail-fast rule as D-0011, extended to the structural half.

**Status.** active
