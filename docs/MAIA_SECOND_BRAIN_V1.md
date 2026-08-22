# MAIA Second Brain V1 — Storage/Domain Foundation + Governed Tools

Status: FASE 4N.2A — storage foundation (4N.1) plus 6 governed
model-callable tools, the two-step propose/confirm capture workflow
(4N.2), and stable runtime-resolved identity binding (4N.2A),
implemented and tested (34/34). No Obsidian sync. No RAG/embeddings. No
graph reasoning. No proactive/autonomous memory writes — every write
still requires an explicit, separate human confirmation.

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
permission helper every other OpenJarvis SQLite store uses. Four
tables: `entries`, `relationships`, `proposals` (FASE 4N.2, additive —
see CAPTURE WORKFLOW), `audit_log`, plus `entries_fts` (FTS5).

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

**FASE 4N.2**: `second_brain_link` is a direct pass-through to
`create_relationship()`, so the model-facing tool inherits the exact
same guarantee described above — no parameter anywhere in the tool or
the service lets a caller request anything other than `PROPOSED` at
creation time.

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

## TOOL CONTRACTS

Six tools, registered in `tools/second_brain_tools.py`, auto-discovered
by `jarvis ask`/`jarvis chat` exactly like OPS Bridge tools (unioned
into `resolve_tool_names()` unconditionally — Second Brain tools need
no live governance check the way OPS Bridge capabilities do, since
every rule is already enforced inside `SecondBrainService`):

| Tool id | Contract | Wraps |
|---|---|---|
| `second_brain_search` | `second_brain.search` | `search_entries()` / `list_entries()` |
| `second_brain_get` | `second_brain.get` | `get_entry()` |
| `second_brain_propose_entry` | `second_brain.propose_entry` | `propose_entry()` |
| `second_brain_confirm_entry` | `second_brain.confirm_entry` | `confirm_entry()` |
| `second_brain_link` | `second_brain.link` | `create_relationship()` |
| `second_brain_archive` | `second_brain.archive` | `archive_entry()` |

There is no seventh "generic write" tool and no tool that accepts raw
SQL or an arbitrary trust-status mutation. Every tool is a thin
pass-through to `SecondBrainService` — none of them contain governance
logic of their own, so there is exactly one place (the service) where
the rules could ever drift from what's enforced.

## CAPTURE WORKFLOW

Two-step, matching the FASE 4N.2 example verbatim:

1. **Propose.** The model calls `second_brain_propose_entry` with
   everything it has (type, title, summary, trust_status, ...). This
   validates against the *exact same* rules as a direct
   `create_entry()` (shared via `_validate_entry_kwargs()`) but writes
   only to the `proposals` table — nothing is searchable yet. The tool
   returns a `proposal_id` plus a rendered prompt ("Vuoi che salvi
   questa conclusione nel Second Brain?") the model must relay to the
   user, verbatim or in its own words.
2. **Confirm.** Only a later, separate call to
   `second_brain_confirm_entry(proposal_id, actor=...)` turns that
   proposal into a real `SecondBrainEntry` (`ENTRY_CREATED` audited).
   A proposal can be confirmed exactly once — a second confirm on the
   same id is rejected (`status != PENDING`).

**Live-verified** (FASE 4N.2 STEP 10, Claude Sonnet 4.6 + Orchestrator,
real `jarvis ask` calls, no scripting): given a message that already
contains explicit confirmation ("Ricordati che... Sì, salvala nel
Second Brain."), the model correctly calls `second_brain_propose_entry`
then `second_brain_confirm_entry` in sequence, using the real
`proposal_id` the first call returned — and a follow-up search in the
same conversation finds the entry. Given an ambiguous message with no
explicit save instruction, the model asked for confirmation in prose
*without* calling any Second Brain tool at all — stricter than the
minimum bar (STEP 4 only requires "no silent save"), never a violation
of it, since nothing is created either way until an explicit
`confirm_entry` call exists.

## CONFIRMATION MODEL

- No tool exists that persists an entry without first going through
  `propose_entry`. This isn't a convention the model is asked to
  follow — there is no `create_entry`-equivalent tool exposed at all,
  so a direct write is not just discouraged, it's absent from the
  model's action space.
- Silence is never consent: nothing is created by the passage of time,
  by the user changing topics, or by a subsequent unrelated tool call.
  Only an explicit `confirm_entry` call persists anything.
- `confirm_entry` requires its own `actor` — the workflow does not
  assume the confirmer is the same identity as the proposer (though in
  practice, for a single-operator install, it usually is).

## AUTHORIZATION

Deliberately **not** a reuse of OPS ONE's Action Book authorization
(which is a server-verified "authenticated Action Book owner" check —
see `ops.actions.list`'s `forbidden` response to an anonymous caller).
Second Brain has no equivalent server-side identity system to check
against, so pretending otherwise would be dishonest. What V1 actually
does instead:

- Every entry carries `visibility` (`PRIVATE`/`TEAM`/`COMPANY`) and
  `created_by`.
- `PRIVATE` is visible only when the caller's declared `actor` string
  exactly equals the entry's `created_by`. `TEAM`/`COMPANY` are visible
  to any actor (V1 has no team/company membership model — see KNOWN
  LIMITATIONS).
- **Fails closed by construction, not by convention**: the SQL clause
  is `visibility != 'PRIVATE' OR created_by = ?` with `actor` bound as
  a parameter. SQLite NULL comparisons are always false, so a missing
  actor can never match any `created_by` and a `PRIVATE` row is
  excluded automatically — there is no separate "did we remember to
  check" step that could be skipped (`store.py::_visibility_clause`).
  `get_entry()`/`archive_entry()`/`supersede_entry()` additionally
  *raise* `SecondBrainAuthorizationError` rather than silently
  returning nothing, so a denial is never confused with "doesn't
  exist."
- Live-verified (FASE 4N.2 STEP 9/10, re-verified 4N.2A): the entry's
  owner can `get` it; a different actor, or a missing actor, is
  denied; it never appears in another actor's search results.
- **FASE 4N.2A: identity is runtime-resolved, never model-supplied.**
  None of the 6 tools accept `actor`/`created_by`/`principal` as a
  tool-call argument any more (verified structurally by
  `test_second_brain_tools_schema_has_no_identity_params`, which
  inspects the actual JSON schema the model sees). Each tool takes an
  optional `principal` constructor argument instead; `_build_tools()`
  (`cli/ask.py`) injects `identity.resolve_runtime_principal()` there
  exactly the way `_MEMORY_TOOLS` already receive a constructor-injected
  `backend`. STEP 1's audit found the only existing canonical identity
  concept in OpenJarvis — `sessions.session.SessionIdentity` — is wired
  exclusively into the `jarvis serve`/channel runtime and never reaches
  `jarvis ask`/`jarvis chat` (confirmed: no `[user]` config section, no
  session store touched by either CLI command). Since nothing existing
  reaches this path, `resolve_runtime_principal()` fills a real gap
  rather than inventing a competing identity system: for the current
  single-user MAIA development environment it derives a deterministic
  local principal from the OS login account (`getpass.getuser()`,
  prefixed `local-os-user:` so it's self-describing as a dev-environment
  stand-in, not a real authenticated id), with an
  `OPENJARVIS_PRINCIPAL_OVERRIDE` env-var escape hatch for tests/CI —
  process-controlled, never visible to or settable by the model.
  Live-verified end-to-end (`jarvis ask`, real Claude Sonnet 4.6, three
  separate process invocations): the same OS account across two
  separate `jarvis ask` invocations retrieves its own PRIVATE entry
  with zero actor/identity string in the conversation; a different
  runtime principal (via the override) gets an honest "not found," no
  fabrication. A model attempting to pass a spoofed identity in the
  tool-call payload has no effect at all — the field doesn't exist in
  the schema and the code never reads unknown kwargs for authorization.
- **Still explicitly a development-environment placeholder, not real
  authentication** — see KNOWN LIMITATIONS for the upgrade path.

## CORRECTIONS

STEP 6 ("Correggi: la causa verificata era il cambio formato.") reuses
`supersede_entry()` (FASE 4N.1) rather than introducing a new
mechanism: `confirm_entry()` takes an optional `supersedes_entry_id`.
When set, the newly confirmed entry is created via `supersede_entry`
instead of a bare `create_entry` — the old entry's content is never
touched, only its `superseded_by` pointer is set, and a `CONFIRMED`
`SUPERSEDES` relationship links new → old. There is deliberately no
separate "correction" tool; correcting is just confirming a new
proposal while naming what it replaces.


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

- **RESOLVED in FASE 4N.2A** (was the top limitation as of 4N.2): the
  cross-invocation identity gap is fixed at the runtime/tool boundary
  — see AUTHORIZATION above. What remains is a narrower, explicitly
  scoped limitation: `resolve_runtime_principal()`'s current
  implementation is a **single-user development-environment
  placeholder**, not real authentication. It identifies "this OS
  account on this machine," which is exactly right for the current
  MAIA development environment but does not extend to: multiple real
  people sharing one OS account (they'd collide onto the same
  principal), the same person across multiple machines (each machine
  gets its own principal, so a PRIVATE entry made on one machine isn't
  visible from another), or any server/multi-tenant deployment. The
  upgrade path is deliberately narrow: `resolve_runtime_principal()` is
  the only function that needs to change (to a real authenticated
  identity — SSO token, `SessionIdentity.user_id` threaded through a
  future CLI login, etc.) — every caller already treats its return
  value as an opaque string, so nothing above this function needs to
  change when that day comes.
- Proposals are not automatically expired or cleaned up — a `PENDING`
  proposal nobody ever confirms sits in `proposals` indefinitely. No
  tool exists to reject/withdraw one either (STEP 1's tool list has no
  "reject_entry"); it simply stays `PENDING` and inert.
- No conversational capture is automatic or semi-automatic in the
  "MAIA notices something worth remembering on its own" sense — the
  model must actively choose to call `propose_entry`; nothing scans
  conversation content proactively (explicitly out of scope: "no
  proactive autonomous memory writes").
- **FIXED in FASE 4N.2A, found live**: `search_entries_fts()` passed
  raw user/model text straight into FTS5's `MATCH` string. FTS5's query
  grammar overloads plain characters as operators — a bareword like
  `Zeta-9` parsed as a column filter on `9` and crashed with
  `"no such column: 9"` (hit live by Claude Sonnet 4.6 on a real
  question about a test line name). Fixed by wrapping every whitespace
  token in double quotes before it reaches `MATCH`
  (`store.py::_fts5_safe_query`), so punctuation is always treated as
  literal text, never syntax — covered by
  `test_o_fts_search_handles_fts5_special_characters`.
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
- All FASE 4N Step 1 test fixtures (`test-codeword=ZEBRA-7`,
  `operator:researcher:state`, 2× `NEBULA KB TEST AGENT`,
  `SCHEDULER PATCH TEST AGENT`) were re-verified and removed/archived
  in FASE 4N.1 STEP 1 — see that phase's final report for exact
  before/after identifiers. None remain flagged.
