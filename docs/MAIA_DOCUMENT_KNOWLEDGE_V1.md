# MAIA Document Knowledge Ingestion V1 (FASE 4O.5)

Status: **FROZEN baseline established this phase.** Governs the path:

```
AUTHORIZED LOCAL WORKSPACE -> document -> ingestion ->
source/evidence identity -> parsed knowledge -> governed retrieval -> MAIA
```

## CRITICAL SEMANTIC SEPARATION

This feature is one of three, deliberately non-overlapping systems:

- **DATA** — structured operational facts/numbers. The certified OPS
  Calculator/Semantic Layer remains the sole authority. Nothing in this
  package can override or recompute a certified value (verified
  structurally: zero imports of any OPS/capability-registry module
  anywhere in `document_knowledge/`).
- **KNOWLEDGE** (this package) — procedures, manuals, notes, reports,
  specifications, meeting material. Textual material with a traceable
  source file, never a certified number.
- **SECOND BRAIN** — governed experiential/organizational memory
  (PROBLEM/DECISION/OUTCOME/... entries via its own propose/confirm
  workflow). This package cannot create a Second Brain entry (verified
  structurally: zero imports of `second_brain.service`/`SecondBrainEntry`
  anywhere in `document_knowledge/`).

A PDF containing a number never silently becomes a certified KPI.
Document retrieval never overrides an OPS Calculator result. Ingestion
alone never creates a Second Brain entry.

## KNOWLEDGE WORKSPACE V1 / AUTHORIZED ROOT

`src/openjarvis/document_knowledge/workspace.py`. The default authorized
root is `~/.openjarvis/maia_documents/` (via `get_config_dir()`, the same
convention every other OpenJarvis config subdirectory uses) —
auto-created on first use, never auto-populated. A user must explicitly
place files there; MAIA never crawls the rest of the filesystem.
Overridable via `MAIA_DOCUMENT_WORKSPACE` for tests only.

Deliberately a NEW, dedicated root — not a reuse of the pre-existing
`OPENJARVIS_WORKSPACE` env var (`server/api_routes.py`'s
`/v1/memory/index`). That endpoint accepts an admin-supplied path with
weak per-chunk provenance (a bare path string, no content hash, no
mtime, no dedup); reusing it here would have imported those weaker
guarantees into MAIA's governed document workspace.

**`safe_resolve(root, candidate)`** is the single choke point every path
in this package goes through. `Path.resolve()` normalizes `..` segments
AND follows symlinks on both POSIX and Windows, so comparing the
*resolved* target against the *resolved* root via containment closes
both the traversal vector and the symlink/junction-escape vector in one
check — the same pattern already established in
`tools/file_read.py::FileReadTool`. Live-verified this phase against a
real Windows junction point (`mklink /J`), not just reasoned about.

`security.file_policy.is_sensitive_file()` (the same guard used
elsewhere in the codebase) blocks `.env`/`*.pem`/`id_rsa`/etc.
unconditionally before any file of that name is ever read.

## SUPPORTED FORMATS

V1: **PDF, TXT, Markdown** (`.pdf`, `.txt`, `.md`, `.markdown`).

DOCX is deliberately **not** included. This phase's audit found
`python-docx` referenced in `server/upload_router.py` but never declared
as a project dependency — adding real DOCX support means first adding a
proper `pyproject.toml` extra and validating it, out of scope for "do
not add broad format support merely for completeness."

PDF extraction uses `pdfplumber` — the established convention already
used by three other modules in this codebase, lazy-imported so a
missing/broken PDF dependency never breaks TXT/Markdown ingestion.

## DOCUMENT MODEL / PROVENANCE MODEL

`document_knowledge/types.py`:

- **`DocumentEvidenceReference`** — `doc_id`, `chunk_id`, `workspace_id`,
  `relative_path`, `filename`, `content_hash`, `chunk_index`, `page`
  (PDF only), `section` (heading-based chunks only). A deliberately
  SEPARATE dataclass from `second_brain.types.EvidenceReference` — that
  type points at a certified OPS ONE capability query; this one points
  at a file. Both types' docstrings warn against conflating trust/
  evidence namespaces across systems; reusing `EvidenceReference` here
  would violate that warning. `citation_label()` renders e.g.
  `"procedure.pdf, page 4"` or `"notes.md, section \"Setup\""` — never
  invents a page number that wasn't actually tracked.
- **`DocumentRecord`** — file-level provenance: `doc_id`, `relative_path`,
  `filename`, `file_type`, `content_hash` (SHA-256 of the whole file),
  `mtime`, `ingested_at`, `parser_version`, `chunk_count`.
- **`IngestOutcome`** — full accounting of one ingestion sweep: `added`,
  `updated`, `removed`, `unchanged`, `skipped_unsupported`,
  `skipped_sensitive`, `errors`, `chunks_written`.

`document_knowledge/file_state.py::FileStateStore` — one dedicated
SQLite table (`files`: `relative_path` PK, `doc_id`, `sha256`, `mtime`,
`file_type`, `ingested_at`, `parser_version`) tracking file-level
identity, deliberately separate from `KnowledgeStore`'s own schema
(which tracks chunk-level `content_hash`, not file-level `path`/`mtime`/
`sha256`).

`doc_id` is deterministic: `maia_documents:{workspace_id}:{relative_path}`
— the same file always maps to the same id, so an unmodified file is
idempotently a no-op rather than accumulating duplicates, and a modified
file's old chunks can be found and replaced cleanly under the same id.

## INGESTION FLOW

`document_knowledge/connector.py::LocalDocumentsConnector` (a real
`BaseConnector`, modeled on `connectors/obsidian.py::ObsidianConnector`)
walks the authorized root and yields `Document` objects, extended
relative to Obsidian's connector for: PDF support (page-aware, via
`parsers.py`), hard workspace-root re-verification per file (defends
against a directory becoming a symlink between the walk starting and a
file being opened), and sensitive-file blocking.

`document_knowledge/ingest.py::sync_workspace()` orchestrates the sweep.
**Deliberately does NOT reuse `connectors/pipeline.py::IngestionPipeline`**
— that pipeline dedupes by `doc_id` *permanently* (loaded once from the
store at construction, never re-checked against content), so a file
modified after its first ingest would silently keep serving its stale
original chunks forever. This violates the phase's explicit requirement
that modifying and re-ingesting a document have deterministic,
documented behavior. Instead, `sync_workspace()` reuses `KnowledgeStore`
and `SemanticChunker` directly (both stateless/general-purpose) and adds
its own SHA-256-based file diff:

| File state | Behavior |
|---|---|
| New file | Chunk + store, record file state |
| Unchanged (same SHA-256) | No-op — not re-chunked, not re-hashed into the store |
| Modified (different SHA-256) | `store.delete(doc_id)` first, then chunk + store fresh — clean replace, old and new chunks never coexist |
| Missing from disk since last sweep | `store.delete(doc_id)` + remove file-state record — deterministically gone from the index, not silently stale |

PDF content is chunked **per page** (not joined into one blob) so every
chunk carries a real page number — `SemanticChunker.chunk()` is called
once per page, tagged with `page=N` in the chunk's metadata. TXT/
Markdown are chunked whole, with `## heading`-based section detection
(`SemanticChunker`'s existing "document" strategy) producing a `section`
tag where applicable.

## RETRIEVAL FLOW

`document_knowledge/service.py::DocumentKnowledgeService` — the ONLY
module other code should import from; `ingest.py`/`connector.py`/
`file_state.py` are implementation details of index-building.

- **`search_documents(query, *, top_k=10, filename=None)`** — lexical
  (FTS5/BM25) search via `KnowledgeStore.retrieve()`, scoped to
  `source="maia_documents"`. Every result is a `DocumentChunkResult`
  carrying its full `DocumentEvidenceReference` — never bare text.
- **`get_document(doc_id)`** — file-level `DocumentRecord`.
- **`get_document_chunk(chunk_id)`** — a single chunk with provenance.
- **`list_documents()`** — inventory of everything ingested.

Query sanitization: every query is passed through the same
`_fts5_safe_query()` technique already used in `second_brain/store.py`
(wrap each whitespace token in double quotes so FTS5 treats it as a
literal phrase, not query syntax) — `KnowledgeStore.retrieve()` itself
lacks this sanitizer (a gap this phase's own audit found); applying it
at this one governed entry point closes it without touching the shared
store's code.

**Lexical-only, no embeddings.** V1 works without downloading any ML
model — established after auditing what OpenJarvis already has
(`connectors/embeddings.py::OllamaEmbedder`, opt-in, degrades gracefully
if unavailable) and confirming lexical FTS5/BM25 is sufficient for V1's
scope. See KNOWN LIMITATIONS / FUTURE EMBEDDINGS below.

## MODEL-CALLABLE TOOLS

`tools/document_knowledge_tools.py`, auto-discovered via
`tools/__init__.py` (same pattern as `second_brain_tools.py`):

- **`document_search`** — the tool the requirement `"According to
  Procedure X, page 4..."` refers to. Every result's `content` is
  emitted alongside its citation; the tool's own description explicitly
  tells the model this is document knowledge, not a certified OPS value,
  and not Second Brain memory.
- **`document_list_sources`** — inventory, used before claiming "no
  document covers this."

Enable with `jarvis ask --tools document_search,document_list_sources`
(not in any default toolset in V1 — an explicit opt-in, matching how
`second_brain_*` tools are also enabled per-invocation).

## CLI

`jarvis document ingest` / `jarvis document search "<query>"` (JSON) /
`jarvis document list` (JSON) — see `cli/document_cmd.py`.

## SECOND BRAIN BOUNDARY

Nothing in this package writes a Second Brain entry, directly or
implicitly. Verified structurally (a dedicated test scans every source
file's import statements for `second_brain.service`/`SecondBrainEntry`/
`propose_entry`/`confirm_entry` and asserts none exist) and behaviorally
(`test_l_no_second_brain_write` ingests+searches against a real,
separate `SecondBrainStore` and asserts its entry count is unchanged).
A future phase may add an explicit, user-confirmed workflow linking a
`SecondBrainEntry` to a `DocumentEvidenceReference` as evidence — but
that is out of scope for V1 and does not exist today.

## OPS / DATA BOUNDARY

Nothing in this package imports any OPS ONE / OPS Bridge / capability
registry module (verified structurally, same import-statement-scan
technique). `document_search`'s own tool description explicitly warns
the model: a number found in a retrieved document is not a certified
value, and the appropriate `ops_dynamic_*` tool should be used instead
for certified KPIs.

## SECURITY MODEL

Fail-closed throughout. Live-verified this phase (not just reasoned
about):

1. Allowed file inside workspace — reads correctly.
2. `../` traversal — `DocumentAccessError`, verified.
3. Absolute path outside the workspace — `DocumentAccessError`, verified.
4. Symlink/junction escaping the workspace — `DocumentAccessError`,
   verified against a real Windows junction point (symlink privilege
   unavailable on the dev machine used; a junction is a functionally
   equivalent escape vector caught by the same `resolve()`+containment
   check).
5. Unsupported file type — skipped, reported in `IngestOutcome`, never
   an error.
6. Malformed document (corrupt PDF) — reported as an error string, never
   a crash.
7/8. Modified source — old chunks deleted, replaced cleanly; old content
   verified unreachable via search after the change.
9. Deleted source — chunks removed from the index on the next sweep;
   verified unreachable via search afterward.
10. Workspace root that isn't a directory (or an unauthorized/
   unconfigured root) — fails closed, `DocumentAccessError`, never
   silently creates or guesses.

Sensitive files (`.env`, `*.pem`, `id_rsa`, etc.) are never ingested,
verified live.

## KNOWN LIMITATIONS (V1)

- **Lexical-only retrieval.** No stemming beyond FTS5's built-in
  `porter unicode61` tokenizer, no semantic matching. A query whose
  wording doesn't share vocabulary with the document (e.g. asking about
  "frequency" when the document says "every 21 days") returns zero
  results rather than a near-miss. Live-verified during this phase's own
  MAIA test: the model's first query attempt genuinely returned no
  results for this reason, and MAIA correctly reported "not found"
  rather than fabricating an answer — the safe behavior, but a real
  discoverability gap worth addressing with embeddings in a later phase.
- **DOCX not supported.** See SUPPORTED FORMATS above.
- **`LocalDocumentsConnector` is registered under `ConnectorRegistry`
  for interface consistency but is deliberately NOT wired into
  `connectors/__init__.py`'s auto-import sweep** (the mechanism the
  opt-in Deep Research CLI uses to discover connectors) — this feature
  has its own dedicated entry points (`jarvis document ingest`,
  `DocumentKnowledgeService`) rather than going through that generic
  flow. A deliberate scope choice, not an oversight.
- **No incremental section/heading traceability beyond `##`.** Deeper
  heading levels (`###`+) aren't tracked separately in `section`
  metadata — inherited from `SemanticChunker`'s existing behavior,
  unchanged in this phase.
- **Single global FTS5 relevance ranking**, no per-document boosting or
  freshness weighting.

## FUTURE EMBEDDINGS/RAG PATH

Not implemented in V1, deliberately. If a later phase adds semantic
search: `connectors/embeddings.py::OllamaEmbedder` already exists,
already degrades gracefully when unavailable, and `KnowledgeStore`
already has `embedding`/`embedding_model_version` columns ready to use
— `IngestionPipeline` (not reused by this phase, see INGESTION FLOW)
already demonstrates the wiring pattern. Any future embeddings adoption
here should preserve V1's core guarantee: retrieval must always degrade
to lexical-only if the embedding backend is unavailable, never fail
closed on a missing local model.
