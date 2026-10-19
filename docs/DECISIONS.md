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

---

## D-0020 — 2026-10-19 — Content is fetched at the commit and stored off-graph

**Decision.** File content is always read at the file's own commit
(`raw.githubusercontent.com/<repo>/<sha>/<path>`), never from a branch tip.
Parsing `master` for a historical PR links the graph to code that never
existed at that PR — false evidence. The full text is written to a
content-addressed blob under artifact storage
(`<content_dir>/<repo>/<sha256>.txt`, written once per distinct content)
and described by a `ContentRecord` in `content_manifest.jsonl` carrying
`file_id, repo, path, commit_sha, language, content_hash, byte_size,
line_count, blob_path`. The manifest never contains the content.

**Status.** active

---

## D-0021 — 2026-10-19 — Neo4j stores metadata, never source code

**Decision.** The `File` node carries `content_hash`, `byte_size` and
`line_count` so a citation can be verified without the code, but no file
body is ever a node property. The codebase lives on disk; the graph holds
structure and small retrieval-facing metadata. Storing source in Neo4j
would duplicate the repo, bloat every query, and drift from the blob.

**Status.** active

---

## D-0022 — 2026-10-19 — The content manifest is the replay cache

**Decision.** If the manifest already has a record for `path@commit_sha`
and its blob still exists, the pipeline reads the blob instead of fetching.
Replay is therefore deterministic and offline. A missing blob invalidates
the entry (refetch); a different commit is never served from a path match,
because content identity is `path@commit_sha`, not path. This is what makes
the Phase 2 acceptance run reproducible after the first network pass.

**Status.** active

---

## D-0023 — 2026-10-19 — Property accessors are distinct symbols

**Decision.** `@property`, `@x.setter`, `@x.getter` and `@x.deleter` define
separate symbols that share a bare name. Their IDs append the accessor role
(`...:BaseClient.timeout:setter`) while the human-facing `qualname` stays
`BaseClient.timeout`. Without this, real files (httpx `_client.py` has seven
property/setter pairs) produce duplicate symbol IDs and fail validation.
Region and config ownership resolve by line containment within the
qualname, so the getter and setter never claim each other's spans.

**Status.** active

---

## D-0024 — 2026-10-19 — tree-sitter is pinned below 0.26

**Decision.** `tree-sitter>=0.25.2,<0.26` with `tree-sitter-python<0.26`.
`tree-sitter-python 0.25.0` declares `tree-sitter~=0.24`; under 0.26.0 the
Python 3.14 runtime segfaults during garbage collection while walking a
parsed tree. The failure is nondeterministic, so it cannot be caught by a
normal assertion — it is pinned instead. A dependency bump must re-run the
large-file smoke test, not just the unit suite.

**Status.** active

---

## D-0025 — 2026-10-19 — The language router is the only suffix authority

**Decision.** Phase 3 supports a deliberately small suffix table:
`.py`/`.pyi` → Tree-sitter Python, `.yaml`/`.yml`/`.toml` → config keys,
`.md` → a `Doc` node only. Everything else (JSON, notebooks, JavaScript,
Rust, lock files) is skipped. The table lives in
`app/structural/languages/router.py`; selection, extraction, and the CLI ask
the router rather than repeating suffix checks, so `language_for_path` cannot
drift from what the extractor does. A manually pinned path with no
registered suffix is included as `Language.OTHER` with metadata only and no
parser call; lock files remain excluded even when pinned because they carry
no structural facts worth a node.

**Status.** active

---

## D-0026 — 2026-10-19 — Extraction is query-driven, references are shallow

**Decision.** Definitions, imports, and references are read from named
Tree-sitter queries with explicit code-navigation captures
(`@definition.function`, `@definition.class`, `@name`, `@reference.call`,
`@reference.attribute`, …). The extractors consume query matches; they do not
re-walk node types by hand, so the grammar is data that can be audited and
changed without touching Python. A `REFERENCES` edge is created only when
the target is (a) a symbol defined in the same file, (b) an imported symbol
whose defining file is in the batch, (c) an attribute of a known imported
module or class, or (d) resolvable by a simple local rule such as
`self.method`. Imported targets are carried as `target_path` +
`target_qualname` during per-file extraction and resolved to a symbol ID by
`resolve_imported_references` once every selected file has been extracted.
Anything not provable is discarded; the plan allows an unresolved reference
to be dropped, and a guessed edge is worse than an absent one. This extends
D-0015 and D-0018 to the reference half of the graph.

**Status.** active
