# MAIA Second Brain V1 — Storage/Domain Foundation

Status: FASE 4N.1 — storage foundation implemented and tested. No
model-callable tools registered yet. No conversational capture. No
Obsidian sync. No RAG/embeddings.

## PURPOSE

MAIA's business memory must not belong to the reasoning model. Claude
Sonnet 4.6 is today's reasoning provider baseline (certified READY in
FASE 4M.5E), but MAIA's decisions, lessons, and evolving understanding
of the business must survive a future model swap intact. The Second
Brain is that model-independent memory.

It is deliberately **not** a replacement for any existing OpenJarvis
memory subsystem — see BOUNDARIES.

## BOUNDARIES

The FASE 4N audit found 8 pre-existing, uncoordinated memory
subsystems in OpenJarvis. The Second Brain does not merge with or
replace any of them:

| System | Role | Relationship to Second Brain |
|---|---|---|
| `memory.db` (SQLiteMemory) | free-text `memory_store`/`memory_retrieve` | Untouched. No trust governance — this is exactly the surface that let ORION/AX-417 contaminate answers in FASE 4M.5D. |
| `knowledge.db` (KnowledgeStore) | ingested external documents (email, Slack, Obsidian) | Untouched. Different lifecycle (raw ingested documents, not MAIA-authored knowledge). |
| `sessions.db` | per-channel conversation history | Untouched. Raw chat log, not extracted knowledge. |
| `traces.db` | append-only interaction audit | Untouched. Rich raw material a future capture phase could mine from — never read live. |
| `agents.db` (`agent_learning_log`) | scaffolded, unused per-agent learning events | Untouched, unrelated table in a different database. |
| `KnowledgeGraphMemory` (`knowledge_graph.db`) | generic entity/relation graph, never instantiated | Not reused directly — structurally close, but lacks `status`/`confidence` columns the governance model requires. |
| Vector backends (dense/faiss/colbert/hybrid) | registered, never wired | Not used. No embeddings in V1. |
| `connectors/obsidian.py` | read-only vault → `knowledge.db` ingestion | Not used yet — future sync target, see OBSIDIAN BOUNDARY. |

The Second Brain also must never become a second source of truth for
OPS ONE facts — see EVIDENCE REFERENCES.

## OBJECT MODEL

`SecondBrainEntry` (`src/openjarvis/second_brain/types.py`):

```
id, type, title, summary, domains[], entities[], timestamp,
source, created_by, provenance, trust_status, confidence,
evidence_references[], visibility, superseded_by,
created_at, updated_at, archived_at
```

`EntryType`: `EVENT, PROBLEM, OBSERVATION, HYPOTHESIS, DECISION, ACTION,
OUTCOME, LESSON, PROCEDURE, MEETING_NOTE`.

The originally-proposed design had 5 parallel `related_*[]` arrays
(facts/memories/decisions/actions/outcomes). These are unified into a
single typed `relationships[]` concept (see RELATIONSHIPS) — one array
with a `relation_type`, not five redundant ones.

## TRUST MODEL

`EntryTrustStatus`: `OBSERVED, HYPOTHESIS, VERIFIED, DECISION, OUTCOME,
LEARNED`.

**This is a completely separate namespace from OPS ONE's Knowledge V1
trust statuses** (`TRUSTED`, `BUSINESS_LOGIC_IN_REVISION`,
`DATA_NOT_AVAILABLE`, ...). A Second Brain `DECISION` never certifies
OPS ONE business logic; a `TRUSTED` OPS metric never auto-promotes a
Second Brain entry. The two enums are never compared or converted into
one another anywhere in this codebase.

States are **reachable, not mandatory waypoints**: a `MEETING_NOTE` or
`PROCEDURE` dictated directly by a user can start life at `VERIFIED`/
`DECISION` without ever passing through `OBSERVED`/`HYPOTHESIS`.

**V1 exposes no operation that mutates `trust_status` on an existing
entry.** `SecondBrainService` has no `update_entry()`/`promote_entry()`
method (verified by `test_f_hypothesis_does_not_auto_promote`, which
asserts neither method exists on the service). "Promotion" always
means: create a new entry, link it to the original via a relationship.
A `HYPOTHESIS` can never silently become `VERIFIED` in place — there is
structurally no code path that could do that.

Type-specific rules enforced in `service.create_entry()`:
- `DECISION` requires `timestamp` (in addition to the always-required
  `created_by`/`provenance`).
- `LESSON` type or `trust_status=LEARNED` requires the entry to be
  grounded in something concrete — at least one of `domains`,
  `entities`, or `evidence_references` — since the actual OUTCOME
  relationship can't exist yet at entry-creation time (it needs this
  entry's own id).

## STORAGE

Dedicated `~/.openjarvis/second_brain.db` (`store.py`,
`DEFAULT_SECOND_BRAIN_DB_PATH`), created via the same `secure_create`
permission helper every other OpenJarvis SQLite store uses. Three
tables: `entries`, `relationships`, `audit_log`, plus `entries_fts`
(FTS5).

`entries_fts` mirrors the auto-syncing, trigger-maintained,
content-linked pattern already used by `connectors/store.py`'s
`KnowledgeStore` (`content='entries', content_rowid='rowid'` +
AFTER INSERT/UPDATE/DELETE triggers) — not `tools/storage/sqlite.py`'s
manually-resynced pattern. This means FTS can never drift out of sync
with `entries` by construction.

`memory.db` and `knowledge.db` schemas are untouched — verified by
running the existing memory/knowledge test suites unmodified after
this phase (see REGRESSION in the final report).

## RELATIONSHIPS

`Relationship`: `id, source_entry_id, target_entry_id, relation_type,
source, created_by, confidence, status, created_at, updated_at`.

`RelationshipType`: `CAUSES, CORRELATES_WITH, PRECEDES, RESULTED_IN,
RESOLVED_BY, DECIDED_IN, RELATED_TO, SIMILAR_TO, AFFECTS, SUPERSEDES,
DUPLICATES`.

`RelationshipStatus`: `PROPOSED, CONFIRMED, REJECTED`.

`service.create_relationship()` has **no `status` parameter at all** —
every relationship is born `PROPOSED`, unconditionally, regardless of
who calls it. Only `update_relationship_status()` (a distinct,
explicit call) can move it to `CONFIRMED`/`REJECTED`. This makes "a
relationship proposed by MAIA never becomes automatically certified" a
structural guarantee, not a convention someone could forget.

One deliberate exception: `supersede_entry()`'s internally-created
`SUPERSEDES` relationship is `CONFIRMED` immediately. Unlike
`create_relationship()` (which records a model's *inference* that two
entries relate — something a human should get to confirm or reject),
`supersede_entry()` is an explicit, deliberate structural action the
caller invoked on purpose. There is nothing left for a human to
confirm; the act of calling it *is* the confirmation.

`get_relationships()` returns **direct neighbors only** — no multi-hop
traversal, no graph reasoning, in V1.

## EVIDENCE REFERENCES

`EvidenceReference`: `capability, domain, metric, period, filters,
trust_status_at_capture, fetched_at`.

**Deliberately has no numeric value field.** The dataclass has no
`value`/`kpi_value` attribute at all (enforced structurally, not just
by convention — see `test_q_evidence_reference_no_kpi_value`, which
inspects the dataclass fields directly). The Second Brain stores *how
to ask OPS ONE again*, never a copy of what it said once. This
prevents the exact failure mode already seen twice in this
engagement — stale/disagreeing values (FASE 4M.3's Bilance bug,
FASE 4M.4A's formula drift) — from ever being reproduced inside MAIA's
own memory. `trust_status_at_capture` is a label snapshot ("what
Knowledge V1 called this metric's trust when we looked"), not a value
snapshot.

No OPS ONE capability is called automatically by this phase — the
field exists and is validated to persist correctly; populating it from
a live capability call is future work (see INTEGRATION BOUNDARY).

## AUDIT

`audit_log` table + `SecondBrainStore.append_audit_event()` /
`verify_audit_chain()` directly mirror `security/audit.py`'s
`AuditLogger` (SHA-256 chain: `row_hash = sha256(prev_hash | fields)`,
`tail_hash()`, `verify_chain()`) rather than inventing a new mechanism.
Every mutation is audited: `ENTRY_CREATED, ENTRY_ARCHIVED,
ENTRY_SUPERSEDED, RELATIONSHIP_CREATED, RELATIONSHIP_STATUS_CHANGED`.
Chain integrity (including tamper detection) is covered by
`test_r_audit_hash_chain_integrity`.

## RETRIEVAL

V1 is exact retrieval only, three axes:
- **By id** — `get_entry()`.
- **Free-text / FTS** — `search_entries()`, FTS5 over
  title/summary/domains/entities.
- **Filters** — `list_entries()` by type, trust_status, domain, entity,
  archived state, and time range.

`get_relationships()` gives direct-neighbor relationship traversal
only. No semantic similarity, no embeddings, no multi-hop graph
reasoning — all explicitly deferred, per this phase's scope.

## MODEL INDEPENDENCE

`SecondBrainService` is the only supported entry point; `store.py` has
zero governance logic itself, so nothing can bypass validation by
reaching for SQL directly. No model — Claude or otherwise — ever sees
raw storage. This mirrors the exact contract-boundary discipline
already proven for OPS ONE (`ops_dynamic_*` tools → Capability
Registry → Bridge, never direct DB access from the model). Swapping
the reasoning provider changes nothing about what's stored; it only
changes whether the new model can call the future tool contract (see
INTEGRATION BOUNDARY) — the same lesson already learned qualifying
Qwen vs. Claude in FASE 4M.5D/E.

## INTEGRATION BOUNDARY (future phase, not implemented here)

No tool is registered against `SecondBrainService` in this phase. The
documented future contract:

- `second_brain.search` — read, FTS/filters only.
- `second_brain.get` — read single entry by id.
- `second_brain.propose_entry` — write, always creates with whatever
  `trust_status` the caller declares (service-layer validation still
  applies) — this is the model-facing surface for "MAIA wants to
  remember something," not a bypass of governance.
- `second_brain.confirm_entry` — the human-in-the-loop counterpart;
  without a UI/CLI path to call this, `propose_entry` output should
  never be treated as confirmed.
- `second_brain.link` — wraps `create_relationship()` (always
  `PROPOSED`, per RELATIONSHIPS above).

Registering these tools, wiring conversational capture ("Vuoi che
salvi questa conclusione?"), and any user-confirmation UX belong to
the next phase, after this storage foundation is certified.

## FUTURE OBSIDIAN BOUNDARY (not implemented here)

`connectors/obsidian.py` already reads a vault (`.md`/`.markdown`/
`.txt`, YAML frontmatter) into `knowledge.db` — one-way, read-only, and
unrelated to the Second Brain today. The proposed future model (FASE
4N Step 1 design, unchanged):

```
Second Brain (SQLite, source of truth for governance/trust_status)
        ↕ (future — not implemented)
Markdown Vault (Obsidian, Human Knowledge Workspace)
```

Export: each `SecondBrainEntry` → one `.md` file, YAML frontmatter
(`id`, `type`, `trust_status`, `domains`, relationships as
`[[wikilinks]]`), `summary` as body. Import: reuse the existing
`obsidian.py` parser to detect human edits and reconcile them back —
always landing as `PROPOSED`, never auto-certified. Obsidian is never
the primary database, never the Capability Registry, never a
governance authority — it is a mirror for humans to read and propose
changes through.

## KNOWN LIMITATIONS (V1)

- No model-callable tools yet — the storage foundation is usable only
  from Python, by design (see STEP 10 of the phase spec).
- No conversational capture — nothing captures a MAIA answer into an
  entry automatically or semi-automatically yet.
- Similarity retrieval, if added later, is structural (domain/entity
  overlap) per the original FASE 4N design — not semantic/embeddings.
- `get_relationships()` has no multi-hop traversal; a caller wanting a
  path (`A →(CAUSES) B →(RESULTED_IN) C`) must walk it themselves,
  one direct-neighbor call at a time.
- `KnowledgeGraphMemory` (dormant, generic entity/relation store) was
  evaluated and *not* reused — it lacks `status`/`confidence` as
  first-class columns, which the "AI-proposed relationships are never
  auto-certified" governance rule requires as queryable, not
  JSON-buried, fields.
- The 4 test fixtures flagged (not deleted) at the end of FASE 4N
  Step 1 (`test-codeword=ZEBRA-7`, `operator:researcher:state`,
  2× `NEBULA KB TEST AGENT`, `SCHEDULER PATCH TEST AGENT`) were
  re-verified and removed/archived in this phase's STEP 1 — see the
  FASE 4N.1 final report's TEST FIXTURE CLEANUP section for exact
  before/after identifiers.
