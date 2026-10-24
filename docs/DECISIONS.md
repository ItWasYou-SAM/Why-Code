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

**Status.** superseded by D-0029. The review-region ID is unchanged, but
regions now also come from changed hunks and carry a `CHANGED_IN` edge.

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

## D-0013 — 2026-10-19 — The structural parser is syntactic only

**Decision.** Component 3 extracts structure: classes, methods,
functions, module-level constants (ALL_CAPS), module-top-level imports,
with dotted qualnames (`Client._send_handling_redirects`). NO call
graph, NO cross-module type resolution, NO inferred semantics. If the
answer needs semantics, Component 4 must pay for it with evidence, not
Component 3 with guesswork.

**Status.** active

---

## D-0014 — 2026-10-19 — Config keys are toml/yaml/env-only; linking is mention-based

**Decision.** `ConfigKey` nodes come from `.toml` (dotted-section
names), `.yaml`/`.yml`, and env-style KEY=VALUE lines. Python
ALL_CAPS assignments stay `Symbol(kind=constant)` — they are not config
keys. `USED_BY` links a key to symbols in files whose content mentions
the key name — string mention, not type resolution, so cross-file links
are allowed and honest. Region IDs follow D-0010's
`{short}:region:{path}:{start}:{end}`; both `Symbol` and `ChangedFile`
get HAS_REGION edges to the symbol-defining span (retrieval can enter
from either).

**Status.** active

---

## D-0015 — 2026-10-19 — The enricher never fetches content

**Decision.** Enrichment consumes content via an injected
`content_provider(path, source_sha)` seam. Production wiring reads from
the store/in-blob flow; the enricher itself has no network, keeping it
replayable and testable without GitHub.

**Status.** active

---

## D-0016 — 2026-10-19 — Unparseable files are loud, zero-node, and non-fatal

**Decision.** A file Tree-sitter can't parse produces ZERO nodes, one
`unparseable_file` warning on the injected logger, and the run
continues. No placeholder symbol, no guessed span. Binary/unknown
extensions are skipped with a `skipped_binary` warning.

**Status.** active

---

## D-0017 — 2026-10-19 — Cross-plan node re-declaration is a merge no-op

**Decision.** The structure plan may re-declare nodes it references but
does not own (e.g., `ChangedFile`, `Doc` created by earlier components)
with minimal props (`record_id`/`repo`). Merging is idempotent (D-0012),
so re-declaration adds nothing and keeps referential integrity inside a
single plan without duplicating writes. Identity rests on record ids,
never on props.

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

---

## D-0027 — 2026-10-19 — Parser outputs are a resolved-ID contract

**Decision.** Parsers emit four flat models defined in
`app/structural/extraction/models.py`: `ExtractedSymbol`, `ExtractedImport`,
`ExtractedConfigKey`, and `ExtractedCodeRegion`. Each carries a file ID and a
deterministic record ID rather than a path/commit pair, so downstream code
never reconstructs identity. The contract is language-agnostic: symbol kinds
include `variable` and `type`, imports are per imported name (module, name,
alias), config values carry a literal `value_type`, and a region records its
provenance (`definition`, `changed_hunk`, `review_comment`, `manual`).

The models enforce D-0015 at construction time: `resolved_internal` is the
only import status permitted to carry `resolved_file_id`, the other statuses
forbid it, and a relative import can never be labeled `resolved_external`.
Line spans must be ordered, and IDs/names must be non-empty. The existing
`Symbol`/`ImportRef`/`ConfigKey`/`CodeRegion` models remain the serialization
contract for the Component 3 graph until the extractors migrate to this
output shape; they are not a second source of truth, just the current edge of
the migration.

**Status.** active

---

## D-0028 — 2026-10-19 — Extraction is limited to structural definitions

**Decision.** Symbols are extracted only for top-level functions, classes,
methods, constants and simple type aliases. Constants are accepted at module
and class scope; function-body assignments are local variables and are never
symbols. A lambda is an expression, and `TypeVar`/`ParamSpec`/`NewType`
factory calls are type machinery, not constants. Simple aliases are explicit
PEP 695 `type X = ...` statements or assignments whose name is type-shaped
and whose right-hand side is a type expression (identifier, attribute,
subscript, union, or a forward-reference string).

`qualified_name` is module-qualified (`httpx._client.Client.send`).
`qualname` remains file-local because ownership and reference resolution
compare within one file; the two exist side by side instead of forcing the
graph to resolve module paths during every lookup. Nested functions are kept
with a conservative local qualname (`outer.inner`), which is unambiguous and
therefore satisfies the "skip or be conservative" rule.

Imports are split per bound name in the parser-output model. A `File → File`
edge is created only when the target path is in the selected set; an imported
symbol used in code resolves to `Symbol → REFERENCES → Symbol`. Relative
imports that do not resolve stay `unresolved` and are never reclassified as
external packages. Third-party imports become
`ExternalRef {kind: "package", name: <root>}` and are never traced to source.

YAML keys are walked with `yaml.compose` so each key keeps the line of its
exact mapping entry, even when the same leaf name appears under several
parents. Each key carries the literal value and a stable `value_type`
(`int`, `float`, `bool`, `str`, `null`, `dict`, `list`). `USED_BY` edges are
created only for literal access (`settings["retry"]["max_attempts"]`,
`config.retry.max_attempts`, or an exact declared-key string) and only when
the dotted key is declared. A called attribute chain such as
`config.retry.get(...)` is uncertain and creates no edge.

**Status.** active

---

## D-0029 — 2026-10-19 — CodeRegion spans are explicit and commit-scoped

**Decision.** A `CodeRegion` is created only from a span the evidence
names: a symbol declaration, a unified-diff hunk, a review-comment anchor,
or an explicitly supplied manual pin. No hunk-to-symbol mapping is
inferred in Component 2; the structural pass owns that later.

Changed hunks use the commit-scoped ID
`{short}:region:{path}@{sha}:{start}:{end}`, matching Component 3's
definition regions. The span is the new-file range from the `@@` header.
A pure deletion anchors at the deletion line; a hunk with no new-side line
(a deleted file) produces no region. A changed file without a known commit
produces no hunk region, because there is no honest `file_id` or
`CHANGED_IN` endpoint.

Review-comment regions keep the pinned D-0010 ID
`{short}:region:{path}:{start}:{line}` so already-shipped `COMMENTS_ON`
edges survive. They gain `commit_sha`, `file_id`, `source`,
`source_pr_number`, `content_hash` and `CHANGED_IN`.

The commit for a PR is its latest known commit, the same anchor Component 3
uses to choose a file version. It is not a claim that this individual
commit introduced the hunk. `content_hash` comes from the normalized
changed-file evidence record in Component 2 and from the parsed file
content in Component 3.

High-signal filtering is explicit: callers pass
`high_signal_pr_numbers` to restrict changed-hunk regions. With no filter,
every changed file with a patch is considered. A pinned file alone is not
a manual region; a manual pin must name a line span.

**Status.** active

---

## D-0030 — 2026-10-19 — Extraction emits graph records; writes are batched

**Decision.** Extraction and mapping never build Cypher. They emit the shared
records `GraphNode(label, key, properties)` and
`GraphRelationship(rel_type, start_key, end_key, properties)` defined in
`app/graph/records.py`; `GraphPlan.to_records()` and
`StructuralGraph.to_records()` adapt the two producers. `key`/`start_key`/
`end_key` are the existing commit-scoped IDs (D-0008, D-0029), so the record
layer adds a transport boundary without renaming identity.

`app/graph/batch_writer.py` is the only module that turns records into Cypher.
It groups by label/relationship type, chunks at `batch_size` (default 250),
and sends `UNWIND $nodes … MERGE … SET n += node.properties` and
`UNWIND $relationships … MATCH … MATCH … MERGE … SET r += rel.properties`.
Nodes are written before relationships because endpoints are matched rather
than created. Label and relationship type are validated identifiers and are
the only interpolated strings; all values are parameters, extending the
injection ban already pinned at the adapter level.

`GraphRecords.assert_integrity()` refuses duplicate node keys and dangling
endpoints before the first statement, so D-0011's "failure means zero
database operations" holds for the batched path too. The old one-statement-
per-node/relationship adapter shapes are superseded; the schema statements
(constraints, indexes, scoped deletes) are unchanged.

**Status.** active

---

## D-0031 — 2026-10-19 — One driver, managed transactions, explicit database

**Decision.** `app/graph/neo4j_driver.py` owns the only `Driver` instance.
Every statement it sends goes through `Driver.execute_query`, which runs in
a managed transaction (the driver retries transient failures on its own) and
receives `database_` explicitly; the connection's default database is never
relied on. The batched writers depend only on `run(query, **params)`, so they
remain driver-agnostic and are tested against an in-memory fake.

Schema setup is a one-time, idempotent sweep: `CREATE CONSTRAINT <name> IF
NOT EXISTS FOR (n:<Label>) REQUIRE n.record_id IS UNIQUE`, then `CREATE INDEX
… IF NOT EXISTS FOR (n:<Label>) ON (n.repo)`, for every label in the plan
vocabulary. `build --write` calls it before the first batch so a load cannot
race its own constraints; a second run is a no-op.

The plan's per-label constraint properties (`file_id`, `symbol_id`,
`config_id`, `region_id`, `ref_id`, `doc_id`) are the label-specific values
the project already stores under the canonical `record_id` property (D-0030).
The graph keeps one identity property rather than duplicating each string
into a second property and a second unique index; a query that wants
`f.file_id` reads `f.record_id`. If per-label aliases are ever required for
external query vocabulary, this decision is the place to revisit.

Batches stay in the recommended 200–500 range (`DEFAULT_BATCH_SIZE = 250`).
A failure is re-raised as `GraphWriteError`, so the CLI's `AppError` handling
prints the taxonomy instead of a driver traceback.

Relationship provenance travels as properties (Component 2 writes
`origin_record_id`). The plan's illustrative `SET r.extracted_at =
datetime()` is deliberately not used: a server-side timestamp makes replay
output differ run to run, and this project treats identical re-runs as a
debugging contract (see the adapter statement-shape tests).

**Status.** active

---

## D-0032 — 2026-10-19 — Validation reports absent evidence as `not_checked`, never as a pass

**Decision.** `validate_graph` runs the Phase 10 acceptance sweep over the
built records with no database: duplicate IDs, resolved endpoints, symbol
and region bounds (against `File.line_count`), orphan files,
`DECLARES`/`USED_BY` config sanity, `IMPORTS` endpoints, high-signal region
coverage and line-anchored review-comment coverage. It returns a flat report
with the plan's field names and a `status` of `pass`,
`pass_with_warnings` or `fail`. The CLI exits non-zero on `fail`, which is
the hard gate before LLM extraction.

A check that has no input — review-anchor coverage in a structural-only
graph, region coverage when no high-signal list was supplied, unresolved
references when no batch was supplied — is recorded in `not_checked` and its
stat is `null`. It is never reported as `0` (which would read as "checked and
clean"). The alternative, inferring coverage from absence, is exactly the
false-comfort failure this phase exists to prevent.

An orphan is a file with no `DEFINES`/`HAS_REGION`/`DOCUMENTED_BY` edge.
The plan allows explicitly permitted orphans; a config-language file (yaml,
toml, json, env) that owns at least one `DECLARES` edge is on that
allowlist, because "declares config keys" is its structural meaning. Orphan
`Doc` nodes are reported separately as a warning: a doc that documents
nothing is visible but not fatal.

`unresolved_imports` counts every import that did not resolve to a selected
file. That deliberately includes third-party/stdlib packages (reported
separately as `external_imports`) so the headline number matches the plan's
meaning of "imports that are not internal edges"; `relative_unresolved_imports`
isolates the imports D-0015 says must never be guessed.

**Status.** active

---

## D-0033 — 2026-10-19 — Decision extraction is a pipeline, never an agent

**Decision.** Turning evidence into engineering decisions is an ordinary,
deterministic pipeline: candidate detection → context packing → one bounded
structured call per candidate class → schema validation → evidence
validation → target resolution → normalisation → de-duplication →
supersession. The language model is a *proposer* only. It has no tools, no
memory, and no path to SQLite or Neo4j; it cannot emit a node, an edge, a
confidence, a status, or a graph key. Every one of those is computed by code
from evidence that already exists in the ingestion store and the graph
index.

Extraction is dry-run by default. `python -m app.intent.cli extract` opens no
connection and writes JSON artifacts (`intent_batch.json`,
`intent_graph.jsonl`, `review_queue.jsonl`, `extraction_report.json`);
Neo4j is reached only with `--write`, and the standalone writer requires an
explicit `--uri` or `--dry-run`. This mirrors D-0011's "failure means zero
database operations" at the intent layer.

**Status.** active

---

## D-0034 — 2026-10-19 — The model is offered only retrieved evidence IDs

**Decision.** `ContextPacker` renders each artifact with its exact
`record_id` and packs the whole prompt under a hard token budget. Items that
do not fit are reported in `omitted_ids`; their IDs are never shown to the
model. A citation is therefore always traceable to something that was
actually retrieved, read, and scored — not merely something that exists
somewhere in the store.

The budget is enforced twice: an incremental guard while packing, and a
final whole-prompt guard that trims section *text* (never an evidence-ID
header) and pops whole sections from the end if accounting drifts. The
`Evidence` headers are what the model cites, so they are never cut.

**Status.** active

---

## D-0035 — 2026-10-19 — Intent identity is computed from the claim and its evidence

**Decision.** A node ID is
`{prefix}_{short_repo}_{slug(claim)}_{sha256(claim + sorted(evidence_ids))[:10]}`
(`dec_`, `con_`, `rev_`, `inc_`). A model-supplied `decision_id` is retained
only as the non-identifying `proposed_id` property. Replaying the same
proposal over the same accepted evidence yields the same node ID, which is
what makes dry-run diffs and re-runs reviewable and is the reason a run
never depends on request order or a server-side timestamp.

**Status.** active

---

## D-0036 — 2026-10-19 — Evidence wrappers, not re-ownership; integrity before the first write

**Decision.** Each cited source record becomes one `Evidence` wrapper node
(`evi_{short}_{sha256(evidence_id)[:16]}`), and — only when the source node
already exists in the index — a `REPRESENTS` edge to it. The intent batch
also carries minimal stubs for pre-existing endpoints so it is
self-integrity-checkable without pretending to re-own data another component
already wrote. If a cited source has no node in the graph, the fact is
recorded in `IntentGraph.skipped`; a wrapper is never silently upgraded into
a claim about provenance.

`IntentGraph.assert_integrity()` (via the shared
`GraphRecords.assert_integrity()`) refuses duplicate keys and dangling
endpoints before any statement is sent. The writer additionally probes
`MATCH (n {record_id: id})` for every cited source record when
`verify_evidence` is set, and aborts with `GraphWriteError` before the first
`MERGE` if any is missing.

**Status.** active

---

## D-0037 — 2026-10-19 — Confidence is categorical, code-computed, and never model-supplied

**Decision.** Confidence is a function of which evidence *types* support a
claim, so a reader can reproduce it:

- **high** — human discussion (review / review-comment / issue-comment) **and**
  a commit **and** a changed artifact (file or doc);
- **medium** — a review body alone, or at least two independent evidence
  types;
- **low** — one weak source: a review comment, PR body, commit or file.

A review comment alone can never be high, the plan's explicit ceiling.
**Amended by D-0042:** Phase 4 treats a single review comment as a *weak*
source and therefore scores it **low**, not medium. A review body remains
medium because it is a substantive human statement rather than an inline
suggestion.
**Superseded by D-0043 (Phase 5):** the level is now derived from an explicit
numeric score. A review comment alone is still low, but review-comment +
commit + diff is **medium** (0.55); *high* requires a merged PR, a commit, a
diff or doc, and a review comment (0.75 or more).

The scorer's API has no `confidence` parameter, so a model value cannot be
smuggled in; `confidence_reasons` is stored beside the level so every score
is explainable.

**Status.** superseded by D-0043.

---

## D-0038 — 2026-10-19 — Supersede only on a shared specific target; otherwise contest; never merge

**Decision.** Ordering is explicit: an earlier PR's decision is the candidate
for supersession by a later one. A later/earlier pair becomes a candidate only
when the two intents share a **specific** target — a `Symbol`, `ConfigKey` or
`CodeRegion`. A shared `File` is not enough, because "both touched this file"
is not a claim that one decision replaced another (D-0010's region discipline,
applied to intent).

**Amended by D-0044 (Intent Phase 6):** a candidate is adjudicated by one narrow LLM
call and can now emit `REVERSES`, `REVISES` or `SUPERSEDES` (later → earlier).
`unrelated` and `insufficient_evidence` emit no edge. A material conflict over
a specific target *without* an adjudicated relationship still marks the
earlier decision `CONTESTED` and records `contested_by`; it never silently
overwrites it. **Amended by D-0045 (Intent Phase 7):** de-duplication now merges an
adjudicated duplicate into its earlier canonical node, unioning evidence and
targets and recording `merged_from`. High-impact merges and conflicts keep
both nodes (conflicts gain a `CONFLICTS_WITH` edge). Existing decision nodes
are patched by `StatusUpdate`, never re-emitted. Rejected, low-signal,
ambiguous, duplicate and contested outputs are written to a JSONL review queue
with the reason and the truncated raw model text; nothing is dropped.

**Status.** active; amended by D-0044 and D-0045.

---

## D-0039 — 2026-10-19 — Additive signals gate what reaches the model; proposal schemas are strict

**Decision.** A PR is not sent to the model merely because it merged. A
fixed, explainable set of additive signals decides it:

| signal | weight |
| --- | --- |
| merged PR | +2 |
| multiple review participants | +3 |
| line-anchored review comment on a changed line | +4 |
| linked issue | +3 |
| issue discussion | +3 |
| PR body states motivation | +2 |
| commit states a rationale | +2 |
| high-value subsystem changed | +3 |
| docs / ADR changed with the code | +4 |
| reversal language in the title | +4 |

A score at or above the threshold yields a `DECISION_CANDIDATE`;
docs/config, reversal and incident vocabulary yield `CONSTRAINT`,
`REVERSAL` and `INCIDENT` candidates independently, so a single PR can
produce several candidates that are each evidence-linked on their own. A
reversal therefore cannot smuggle in an unsupported constraint: the
constraint is extracted and validated separately, or not at all. Detection
also returns `LOW_SIGNAL` for merged-but-quiet PRs, which are queued for
review rather than sent to the model.

The LLM-facing schemas (`app/intent/llm/schemas.py`) are strict: `extra`
is `forbid`, there is no `confidence`, `status`, `supersedes` or graph-key
field, evidence lists are non-empty and de-duplicated, and target lists are
capped. A provider that ignores the schema cannot smuggle an unknown field
through validation.

**Amended by D-0041.** The three per-class schemas are replaced by one
candidate envelope. `status` is now a classification label; the canonical
lifecycle and every graph link remain code-computed.

**Status.** active

---

## D-0040 — 2026-10-19 — The evidence pack is region-first, prioritised and category-budgeted

**Decision.** `ContextPacker` builds a small, auditable pack around the
changed code regions, not around the whole PR. Artifacts enter in this fixed
order, and a tight budget removes them from the bottom first:

1. the diff hunk for each selected changed region, with the **current** side
   (context plus additions, new line numbers) and the **previous** side
   (context plus deletions, old line numbers) reconstructed from the patch;
2. review comments anchored to that hunk (side-aware: `LEFT` uses the old
   span);
3. commit messages — the PR head plus rationale commits, capped, never every
   commit in a large PR;
4. the PR title and description;
5. linked issue titles and a bounded, ranked issue discussion;
6. docs changed in the same PR;
7. the copy-only target catalogue.

Hunks are selected because they contain an anchored comment or belong to a
changed file the candidate already points at. The packer never reads an
entire file and never includes CI configuration, repository READMEs (unless a
comment or issue text names the path), or unrelated test files. Only patch
context is used as "nearby unchanged code"; a missing previous side is stated
explicitly rather than inferred.

Each category has a weight, floor and ceiling derived from the Phase 2 budget
table (`app/intent/context/budget.py`). The allocator water-fills the
available budget, ignores floors when the total is deliberately tiny, and the
final guard keeps the rendered prompt under the hard ceiling by trimming
section text and dropping whole sections from the end. Section headers and
evidence aliases are never cut.

Every included artifact is printed as `[E#] <record_id> — …`. The `E#` label
is a reading convenience only: `EvidencePack.resolve_citation()` maps it back
to the exact source `record_id` before `EvidenceValidator` runs, so D-0034's
"retrieved evidence only" rule still holds. When an issue thread is too large
to include whole, lower-signal comments are summarised out, the retained
comments keep their original IDs, and the omitted count is shown.

**Status.** active

---

## D-0041 — 2026-10-20 — Extraction uses one strict candidate schema and code-enforced hard rules

**Decision.** Phase 3 replaces the three per-kind proposal shapes with one
provider-facing envelope, `CandidateExtraction`
(`app/intent/llm/schemas.py`):

```json
{
  "candidates": [ ... ],
  "missing_evidence": [ ... ]
}
```

Each candidate carries `claim` (≤ 240 characters), `decision_type`
(`design_decision`, `constraint`, `reversal`, `incident_response`,
`api_contract`, `operational_policy`), `status` (`accepted`, `rejected`,
`reversed`, `revised`, `uncertain`), `targets` (`file`, `symbol`,
`config_key`, `code_region`), at least one `evidence_ids`, `participants`
(login + role + optional evidence), `rationale` (≤ 800 characters) and a
`temporal_scope` with a required `valid_from`. Every object sets
`additionalProperties: false`; unknown fields are refused rather than
ignored. A normal run makes **one** structured call per selected PR, not one
per candidate class, and maps each returned `decision_type` to a canonical
`Decision`, `Constraint` or `Reversal` node.

`status` and `decision_type` are *classification labels*. They do not replace
the deterministic lifecycle: code still computes active/contested/
superseded/reversed state, confidence, identity, graph keys and every edge.
This refines D-0039's "no status" rule: the model may classify, but it still
may not assert a graph state or a supersession link.

The hard rules are enforced locally because provider schema guarantees are
not evidence guarantees:

| rule | enforcement |
| --- | --- |
| at least one evidence ID per candidate | Pydantic `minItems: 1` |
| every evidence ID exists in the pack, store and graph | `EvidenceValidator`; aliases resolve first (D-0040) |
| every target exists in the structural graph or the PR's changed-file set | `GraphLinkValidator`; changed-file records are a documented fallback key |
| a refactor without a stated rationale is not a decision | `ExtractionRuleValidator` rejects it |
| a review comment alone is weak evidence | the validator downgrades the candidate to `uncertain` unless another source corroborates it |
| a merged PR with explicit maintainer rationale can be accepted | accepted classification is preserved; the rationale is part of the evidence |
| a rejected (unmerged) PR cannot be an active decision | the validator classifies it `rejected`; canonical status becomes `rejected` |
| a reverted PR must create the reversal and its relationship | extraction must return the reversal candidate; Intent Phase 6 adjudicates the pair and creates `REVERSES`/`REVISES`/`SUPERSEDES` deterministically (D-0044) |
| the model must not guess | an unsupported response returns `missing_evidence`, which is attached to accepted intents and queued when nothing is accepted |

Rejected candidates, rule violations and unsupported responses go to the
review queue with the raw model text; nothing is silently dropped. Participant
*edges* remain deterministic (`ParticipantExtractor`); the model's
participant classification is preserved on the intent for audit but cannot
create a relationship on its own.

**Tests.** `tests/intent/test_extraction_rule_gate.py` pins the hard rules;
`tests/intent/test_schema_validator.py` pins the JSON Schema (required keys,
enums, limits, `additionalProperties: false`); `test_intent_pipeline.py` pins
the one-call-per-PR flow, alias resolution, evidence gate and reversal
relationship end to end.

**Status.** active

---

## D-0042 — 2026-10-20 — Evidence validation is a claim-level, local firewall

**Decision.** Phase 4 adds a second gate after the provider schema. No
candidate reaches the graph merely because the JSON was valid. Every candidate
passes through `EvidenceValidator.validate_candidate()` and receives the
stable result shape:

```json
{
  "decision_id": "dec_001",
  "schema_valid": true,
  "evidence_valid": true,
  "targets_valid": true,
  "unsupported_claims": [],
  "invalid_evidence_ids": [],
  "invalid_target_ids": [],
  "final_status": "accepted_for_graph"
}
```

`final_status` is one of `accepted_for_graph`, `observation`, `needs_review`,
`rejected` or `discarded`. `EvidenceValidationResult` is the public contract;
`CandidateValidation` carries the evidence, target, grounding, participant and
confidence detail used by the pipeline.

**Claim-level grounding.** `ClaimGrounder` (`validation/claim_grounding.py`)
decomposes a claim at sentence, semicolon and contrast boundaries — never
inside one proposition — and checks every atomic claim against the cited text.
Support is stemmed content-term coverage, computed locally and reproducibly.
The claim and its rationale must share content with the evidence, and the
claim itself must contribute at least one matched term. A term that appears in
every cited artifact (a module or product name, a shared preamble) is not
distinctive and cannot be the *only* term linking claim to evidence. Citation
precision is
scored per evidence item; an unrelated citation is reported and cannot raise
confidence. This is a lexical firewall, not an NLI model: it is deliberately
conservative about rejecting unsupported prose and sends borderline claims to
human review rather than inventing entailment.

| rule | Phase 4 enforcement |
| --- | --- |
| JSON / schema invalid | discarded before any evidence check |
| no evidence IDs | schema failure (`minItems: 1`) → discarded |
| evidence ID absent from pack, store or graph | candidate rejected; the whole citation set is discarded |
| no structural target | candidate rejected |
| target absent from the graph / changed-file set | all invalid → rejected; partial → `observation` with only resolved targets |
| claim not supported by cited text | `needs_review` with the atomic claims listed |
| invented `valid_from` / `valid_until` date | `needs_review`; dates must appear in evidence, and supporting artifacts may not postdate `valid_until` |
| reversal without a commit or PR | `needs_review`; cross-PR ordering is still computed by `SupersessionDetector` |
| unsupported participant | participant dropped (no graph edge); optional strict mode rejects the candidate |
| explicit disagreement in cited discussion | candidate kept, marked `contested`; never silently resolved |
| one weak evidence source | confidence `low`; review-comment + commit + diff is `medium`, and only a merged PR plus a commit plus a diff/review reaches `high` (D-0043) |

Temporal validity is checked against the packed PR header plus cited evidence,
so a merge-date `valid_from` is accepted even when the PR record itself was not
cited. Participant roles must match the cited artifact: author = PR author,
reviewer = review/review-comment author, commenter = comment author, and
maintainer/advocate/objector require a cited artifact by that login (advocates
and objectors also need stance language or a matching review state).

The pipeline records every result in `IntentRunResult.evidence_validations`
and serialises it under `evidence_validations` in `intent_batch.json`.
Observations are accepted with canonical status `uncertain` and queued for
review; conflicts are accepted with status `contested`; unsupported claims,
invented dates and reversal gaps go to the review queue with a specific
`ReviewReason` rather than being dropped.

**Tests.** `tests/intent/test_claim_grounding.py` pins atomic decomposition,
support/relevance, temporal anchoring, reversal and conflict detection;
`tests/intent/test_evidence_validation_phase4.py` pins the result shape and
each decision branch; `test_intent_pipeline.py` pins the firewall end to end,
including observation, conflict, serialization and review routing.

**Status.** active

---

## D-0043 — 2026-10-20 — Confidence is a deterministic score, displayed only as a category

**Decision.** Phase 5 replaces the rule table in `ConfidenceScorer` with an
explicit numeric score. The model never answers "how confident are you?"; the
number is computed from the evidence that survived validation:

| evidence kind | record types | points |
| --- | --- | --- |
| merged PR | `pull_request` with `merged: true` | 0.20 |
| commit | `commit` | 0.15 |
| diff | `changed_file` | 0.20 |
| review comment | `review_comment`, `review` | 0.20 |
| issue comment | `issue_comment`, `issue` | 0.10 |
| doc | `doc_file` | 0.15 |

Each kind contributes at most once. Every additional independent author adds
`0.03` (capped at three extra authors, so at most `+0.09`). Only evidence that
supports an atomic claim **and** passes Phase 4 relevance scoring contributes,
so an irrelevant citation cannot raise confidence. An `uncertain`
candidate — including a Phase 4 observation downgraded to canonical
`uncertain` — multiplies the score by `0.6`. The total is capped at `0.98`,
because lexical evidence cannot prove intent with certainty.

| final score | level | UI label |
| --- | --- | --- |
| ≥ 0.75 | `high` | High confidence |
| ≥ 0.45 | `medium` | Medium confidence |
| > 0 | `low` | Low confidence |
| no usable evidence kind | `insufficient` | Insufficient evidence |

The numeric score is stored on `ConfidenceResult` for audit and replay, but it
is **never displayed**: a percentage such as "87.3% confident" would imply
statistical certainty the system does not have. `ConfidenceScorer.score()` and
`display_label()` expose the categorical label; the frontend maps every
`Confidence` value to one of the four labels above.

A `pull_request` record that is explicitly unmerged earns no merged-PR points.
An empty or unrecognised evidence set is `insufficient`, not `low`: "no usable
evidence" is a different statement from "weak evidence".

**Consequences.** Review-comment + commit + diff scores `0.55` and is
**medium**; high now requires the merged PR as well (`0.75`). This corrects the
Phase 4 expectation recorded in D-0037/D-0042. The score remains a function of
evidence only, so a replay produces the same level, score and reasons.

**Tests.** `tests/intent/test_confidence_scorer.py` pins the weights,
thresholds, author bonus, uncertain discount, cap, insufficient case and
display labels; `test_evidence_validation_phase4.py` pins the validator's
medium/high branches; `test_intent_pipeline.py` pins the end-to-end level.

**Status.** active

---

## D-0044 — 2026-10-20 — Intent Phase 6: supersession candidates are deterministic; one LLM call adjudicates them

**Decision.** Phase 6 separates *finding* a possible supersession from
*deciding* it:

1. `SupersessionDetector.find_candidates()` implements the plan's candidate
   query in memory: two decisions that `GOVERNS` the same `Symbol`,
   `ConfigKey` or `CodeRegion`, from different PRs, with the later one ordered
   by merge date. The later node may be a `Decision` or a `Reversal`.
2. Deterministic signals are recorded on the candidate: reversal language,
   config-key removal, same code region, doc change, a review saying the
   approach failed, and a retained rationale (revision, not reversal).
3. `SupersessionJudge` asks the model one question over that pair and returns
   the strict `SupersessionVerdict` schema:

```json
{
  "earlier_decision_id": "dec_001",
  "later_decision_id": "dec_004",
  "relationship": "reverses",
  "evidence_ids": ["httpx:commit:..."],
  "explanation": "The later commit disables the default."
}
```

`relationship` is exactly `reverses`, `revises`, `supersedes`, `unrelated` or
`insufficient_evidence`. The verdict must name the same pair and may cite only
evidence from the two decisions or their PR bundles; a mismatched pair or an
invented evidence ID is downgraded to `insufficient_evidence`.

**Edges.** Only three relationship types may be written, and all point from
the later node to the earlier one:

```text
(:Decision)-[:REVERSES]->(:Decision)
(:Decision)-[:REVISES]->(:Decision)
(:Decision)-[:SUPERSEDES]->(:Decision)
```

`unrelated` and `insufficient_evidence` create no edge. `insufficient_evidence`
is queued for human review with `ReviewReason.INSUFFICIENT_EVIDENCE`; nothing
is guessed. `reverses` sets the earlier decision `reversed`; `revises` and
`supersedes` set it `superseded`; all three record `superseded_by`. The
`SUPERSEDED_BY` relationship is no longer written — the pointer survives as a
node property, while the graph relationship is one of the three above.

A conflicting pair with an `unrelated` (or fallback) verdict still marks the
earlier decision `contested` under D-0038, without creating an edge. An
explicit `ReversalIntent` is adjudicated before a later ordinary decision so
its `REVERSES` edge wins.

**Fallback.** `SupersessionDetector.detect()` accepts a classifier and the
pipeline always passes the LLM judge. Without a classifier (offline replay or
unit tests) a conservative deterministic fallback uses the recorded signals;
it never runs in a normal pipeline run. Provider/schema failure becomes
`insufficient_evidence`, not an edge.

`IntentRunResult.supersession_decisions` stores every verdict and
`as_dict()` serialises it beside `supersession_edges` in `intent_batch.json`.

**Tests.** `tests/intent/test_supersession.py` pins candidate discovery, all
three edges, no-edge verdicts, direction, existing-decision updates and the
fallback; `test_supersession_judge.py` pins the schema, prompt and evidence
validation; `test_intent_pipeline.py` pins the end-to-end reversal and verdict
serialization.

**Status.** active

---

## D-0045 — 2026-10-20 — Intent Phase 7: adjudicated duplicate merges, never silent merges

**Decision.** Duplicate handling is staged and deterministic:

1. **Normalisation.** Claims are lowercased, punctuation is stripped and
   whitespace is collapsed. An exact normalised match is always a candidate.
2. **Candidate generation.** Only intents of the same kind, from different
   PRs, with a shared **specific** target (`Symbol`, `ConfigKey` or
   `CodeRegion`) are compared. A deterministic hashed n-gram embedding
   supplies a cosine similarity; it is a candidate generator only. An
   injectable `EmbeddingModel` can replace it with a semantic model. The
   default threshold is `0.70` (`WHYCODE_INTENT_DUPLICATE_THRESHOLD`).
3. **Adjudication.** One bounded LLM call over each candidate returns
   `merge`, `keep_separate`, `conflicts` or `insufficient_evidence`
   (`DuplicateVerdict`). The schema requires the pair IDs, at least one
   evidence ID and an explanation, and forbids extra properties. A verdict
   naming the wrong pair or citing evidence outside the candidate bundle is
   downgraded to `insufficient_evidence`.
4. **Application.** Deterministic code owns the canonical ID, the evidence
   union and the graph edge.

A merge requires all of: semantically equivalent claims, overlapping
specific targets, compatible temporal scopes, and evidence that can be
unioned without contradiction. The earlier intent is canonical. Its
`evidence_ids`, `targets`, `produced_in` and `missing_evidence` are unioned,
its `merged_from` records the absorbed IDs, and the duplicate node is not
written. The audit trail remains in `duplicate_merges` in
`intent_batch.json`.

Temporal scopes are compatible when they do not end before the other begins;
an unknown scope is treated as open. An LLM `merge` verdict is downgraded to
`keep_separate` when the scopes are incompatible.

A conflict never merges. Both nodes survive, receive `CONTESTED` status and a
later → earlier `CONFLICTS_WITH` edge, and the pair goes to the review queue.
`insufficient_evidence` also creates no edge and goes to review.

**High-impact merges need a human.** Claims or rationales mentioning security,
authentication, privacy, data loss, breaking/public API or contract changes,
migrations, payments/billing, encryption or credentials, and decisions typed
`api_contract` or `operational_policy`, are not auto-merged. Both nodes stay,
the later gets `duplicate_of`, and the pair is queued with
`possible_duplicate`. This is the deliberate exception to "do not create
duplicate nodes": the human decides the merge.

**Consequences.** This supersedes the "mark `duplicate_of`, never merge" part
of D-0038. The old `mark_duplicates()` helper remains for callers outside the
pipeline, but the pipeline uses `Deduplicator.adjudicate()`. Embeddings are
never proof: without an affirmative `merge` verdict, the two decisions stay
separate.

**Tests.** `test_deduplication.py` pins normalisation, target overlap,
same-PR/terminal exclusions, temporal compatibility, evidence union,
high-impact review and conflict edges; `test_deduplicator_judge.py` pins the
strict schema, prompt and pair/evidence validation;
`test_deduplication_pipeline.py` pins the end-to-end merge to one Decision
node; `test_intent_graph_writer.py` pins the `CONFLICTS_WITH` edge.

**Status.** active

---

## D-0046 — 2026-10-20 — Intent Phase 8: writes go through the batched graph-record writer

**Decision.** Phase 8 writes the validated intent graph to Neo4j through the
same record pattern as Component 2: the intent producer builds
`IntentGraph` records first, integrity is asserted before the first statement,
and the shared batched `MERGE` writer in `app.graph.batch_writer` sends the
batch — grouped by label and relationship type, chunked at
`DEFAULT_BATCH_SIZE`, values always parameters (`$nodes` / `$relationships`),
never string-interpolated Cypher.

The decision-shaped write contract is part of the record payload:

- a `Decision` node MERGEs on its decision identity and carries `claim`,
  `decision_type`, `status`, `rationale`, `confidence_label`,
  `confidence_score`, `valid_from`, `valid_until`, `extracted_by` and
  `extraction_version`;
- a `SUPPORTED_BY` edge carries `validator_version` (the evidence-validation
  firewall that accepted the citation) and `support_score` (the audit score the
  acceptance produced);
- a `GOVERNS` edge carries `linked_by` (`intent_extractor`) and `linked_at`.

A dry run performs zero database operations; a real write verifies that every
cited source record exists before the first statement and refuses to write
otherwise. A dangling relationship aborts before any write.

**Consequences.** The intent layer owns no Cypher of its own: producers emit
transport-neutral records and the writer translates. Adding a phase-8 write
kind means adding record fields, not adding statements.

**Tests.** `tests/intent/test_intent_graph_writer.py` pins the phase-8 write
contract, the batched parameterized merges and the dry-run/integrity behaviour;
`tests/intent/test_intent_pipeline.py` pins that accepted intents and the
resulting graph keep the write contract fields.

**Status.** active

---

## D-0047 — 2026-10-20 — Intent Phase 9: uncertain extraction goes to a human review queue

**Decision.** Phase 9 builds the human review queue the pipeline already
pointed at: rejected, ambiguous, duplicate and contested outputs are queued, never
silently dropped and never auto-merged.

Each queued item carries the phase-9 shape: `candidate_id`, `pr_number`, `claim`,
`status` (`needs_review` while queued), `reason`, `evidence` (cited record IDs),
`targets` (resolved graph keys), `proposed_confidence` and `reviewer_decision`
(`null` until a human acts).

Reviewers resolve items through one of six actions: `approve`, `reject`,
`edit_claim`, `change_status`, `mark_contested`, `mark_duplicate`. An unknown
action or unknown candidate is refused; an approve/reject outcome moves the item
to `approved`/`rejected`, an edit replaces the claim, a contest marks it
contested, a duplicate marks it duplicate.

**Consequences.** The queue is the only human surface for the LLM: the model is
never trusted to self-certify, and a `needs_review` item never reaches the graph
without a reviewer decision.

**Tests.** `tests/intent/test_intent_review_queue.py` pins the item shape and
every review action; `tests/intent/test_intent_pipeline.py` pins that an
unsupported claim queues a phase-9 item with evidence, targets and confidence.

**Status.** active
