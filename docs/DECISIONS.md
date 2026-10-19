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
