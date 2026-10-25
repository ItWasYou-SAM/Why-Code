# Component 3 — Structural enricher

Adds a shallow but correct structural layer to the evidence graph with Tree-sitter. Given the files already touched by high-signal PRs, it extracts symbols, imports, references, config keys, docs and code regions.

**Status:** implemented. Python is the only parsed language; every other suffix is routed out deliberately.

## Selection

The enricher never walks the repository. It reads Component 1's exports and selects only:

- files changed by high-signal PRs,
- explicitly pinned paths,
- resolvable direct imports when `include_imports` is on.

Lock files, generated files, CI configuration and tests are excluded by default. Each selected file is fetched **at the commit that changed it**, never at the default branch.

## Language routing

One suffix table (`app/structural/languages/router.py`) is the only place that decides treatment:

| Suffix | Treatment |
| --- | --- |
| `.py`, `.pyi` | Tree-sitter Python extraction |
| `.yaml`, `.yml` | config-key extraction |
| `.toml` | config-key extraction |
| `.md` | `Doc` node only (no `File` node) |
| anything else | skipped, unless manually pinned |

A pinned path with no registered suffix is kept as lightweight metadata (`content_hash`, `byte_size`, `line_count`) and never sent to a parser. JSON, notebooks, JavaScript, Rust and lock files are not parsed.

## Query-driven extraction

Definitions, imports and references come from named Tree-sitter queries in `app/structural/languages/queries/*.scm`, using captures such as `@definition.function`, `@definition.class`, `@name` and `@reference.call`. The extractor consumes query matches, not a hand-written node walk, so the grammar is auditable data.

References are deliberately shallow. A `REFERENCES` edge exists only when the target is:

- a symbol in the same file,
- an imported symbol whose defining file is in the batch,
- an attribute of a known imported module or class,
- a simple local rule such as `self.method`.

Everything else is discarded. There is no type inference and no dynamic dispatch.

## Output contract

Parsers emit four flat models (`app/structural/extraction/models.py`), each scoped by a resolved `file_id`:

| Model | Carries |
| --- | --- |
| `ExtractedSymbol` | name, qualified name, kind, span, signature, docstring, parent symbol |
| `ExtractedImport` | module/name/alias, relative flag, resolved file ID, resolution status |
| `ExtractedConfigKey` | dotted key, literal value, value type, line |
| `ExtractedCodeRegion` | span, content hash, optional excerpt/symbol, provenance |

The constructors enforce honesty: `resolved_internal` requires a `resolved_file_id`; `resolved_external` and `unresolved` forbid one; a relative import cannot be labeled external. Regions name their provenance (`definition`, `changed_hunk`, `review_comment`, `manual`).

## Extraction rules

| Extracted | Not extracted |
| --- | --- |
| top-level functions, classes, methods | local variables and local aliases |
| module and class constants (`SCREAMING_CASE`) | lambdas, comprehensions, decorators |
| simple type aliases (`type X = ...`, `UserId = int`) | imported names, `TypeVar` factories |

`qualified_name` is module-qualified (`httpx._client.Client.send`), while `qualname` stays file-local for ownership and reference matching. Nested functions keep a conservative local name (`outer.inner`).

Imports produce one record per bound name:

- resolved internal import → `File → File`,
- using the imported symbol → `Symbol → REFERENCES → Symbol`,
- third-party import → `ExternalRef {kind: "package"}`, never resolved to source,
- unresolved relative import → `unresolved`, never external.

YAML keys keep the exact mapping line plus a literal `value` and `value_type`. `USED_BY` is emitted only for literal access such as `settings["retry"]["max_attempts"]` or `config.retry.max_attempts`, and only when that dotted key is declared. Uncertain chains such as `config.retry.get(...)` create nothing.

## Code regions

| Source | Span | Owner |
| --- | --- | --- |
| `definition` | full symbol declaration | `Symbol → HAS_REGION`, `File → HAS_REGION` |
| `changed_hunk` | new-file range from one `@@` header | `ChangedFile → HAS_REGION`, `CHANGED_IN → Commit` |
| `review_comment` | anchor (`start_line`/`line`) | `ChangedFile → HAS_REGION`, `ReviewComment → COMMENTS_ON` |

Regions are emitted from symbol definitions, unified-diff hunks and review-comment anchors. A span is always bounded and carries its provenance.

## Content storage

Full file content never enters Neo4j. `fetch` (and `extract`/`build`, which fetch as they go) writes:

```text
artifacts/structural/content/<repo>/<sha256>.txt   # content-addressed blob
artifacts/structural/content_manifest.jsonl        # metadata only
```

Each manifest row is `file_id, repo, path, commit_sha, language, content_hash, byte_size, line_count, blob_path`. The manifest doubles as a replay cache: a second run reads the blob and makes no network call. For offline replay, pass `--content-root DIR` (a checkout of the target commit).

## Retrieval metadata

The graph keeps only retrieval-facing text, not file content. Structural writes
populate two fields that the vector indexes need:

- `Symbol.docstring` — the first string literal in a function/class body;
- `CodeRegion.excerpt` — a bounded excerpt of the already-fetched source span.

`StructuralGraphWriter.prepare_schema()` also creates the retrieval full-text
and vector indexes (`app/retrieval/indexes/index_manager.py`). At write time
the writer computes an `embedding` for `Symbol`, `ConfigKey` and `CodeRegion`
nodes; Component 4 does the same for `Decision` and `Evidence`. Embeddings are
produced before the first merge, and an empty or malformed vector aborts the
write.

## Validation

`app/structural/validation/graph_validator.py` is the pure acceptance gate over built records:

- duplicate IDs,
- every relationship endpoint resolved,
- symbol/region line bounds against `File.line_count`,
- orphan files,
- `USED_BY` / `DECLARES` config sanity,
- `IMPORTS` targets,
- high-signal region coverage,
- line-anchored review-comment coverage.

It emits a flat report and `pass` / `pass_with_warnings` / `fail`. Checks whose input is absent are listed in `not_checked`, never counted as a pass. `python -m app.structural.cli validate` exits non-zero on `fail`.

## CLI

```bash
cd backend

# Select files
PYTHONPATH=. python -m app.structural.cli select \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts --remote

# Extract, build records, validate (offline)
PYTHONPATH=. python -m app.structural.cli extract \
  --repo encode/httpx --normalized ../artifacts/normalized.jsonl \
  --high-signal ../artifacts/high_signal_prs.jsonl \
  --artifacts-dir ../artifacts --remote

PYTHONPATH=. python -m app.structural.cli graph \
  --batch ../artifacts/structural/batch.json \
  --out ../artifacts/structural/structural_graph.jsonl

PYTHONPATH=. python -m app.structural.cli validate \
  --batch ../artifacts/structural/batch.json \
  --graph ../artifacts/structural/structural_graph.jsonl
```

Add `--write` to `build` to load Neo4j; use `write-graph` to load an existing records file.

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests/structural -q
```
