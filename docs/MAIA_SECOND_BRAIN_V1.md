# MAIA Second Brain V1 — Storage/Domain Foundation + Governed Tools

Status: FASE 4N.4 — storage foundation (4N.1), 6 governed model-callable
tools and the two-step propose/confirm capture workflow (4N.2), stable
runtime-resolved identity binding (4N.2A), a certified full operational
experience cycle (4N.3), and deterministic Retrieval Intelligence V1
(4N.4: progressive broadening + a 7th tool, `second_brain_find_related_experiences`),
implemented and tested (51/51). No Obsidian sync. No RAG/embeddings —
broadening is structured-filter progression, not a vector index. No
graph reasoning beyond one bounded bundle walk. No proactive/autonomous
memory writes — every write still requires an explicit, separate human
confirmation.

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

**FASE 4N.3**: multi-hop traversal is still not implemented in
`SecondBrainService`/`SecondBrainStore` (that constraint is unchanged),
but a model can now walk a chain of direct-neighbor hops itself: every
`second_brain_search`/`second_brain_get` result renders each
relationship's actual `related_entry_id` (not just a status count) into
the text the model reads, so it can call `second_brain_get` again on
that id, one hop at a time, for as many hops as the chain has. This is
still "no graph reasoning" in the sense the phrase was scoped —
nothing here computes a path, ranks routes, or reasons over the graph
structure itself; it only makes each already-existing direct edge
visible enough for a model to choose to follow it.

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

Seven tools (six from FASE 4N.2, plus one added in FASE 4N.4),
registered in `tools/second_brain_tools.py`, auto-discovered by
`jarvis ask`/`jarvis chat` exactly like OPS Bridge tools (unioned into
`resolve_tool_names()` unconditionally — Second Brain tools need no
live governance check the way OPS Bridge capabilities do, since every
rule is already enforced inside `SecondBrainService`):

| Tool id | Contract | Wraps |
|---|---|---|
| `second_brain_search` | `second_brain.search` | `search_entries()` / `list_entries()` |
| `second_brain_get` | `second_brain.get` | `get_entry()` |
| `second_brain_propose_entry` | `second_brain.propose_entry` | `propose_entry()` |
| `second_brain_confirm_entry` | `second_brain.confirm_entry` | `confirm_entry()` |
| `second_brain_link` | `second_brain.link` | `create_relationship()` |
| `second_brain_archive` | `second_brain.archive` | `archive_entry()` |
| `second_brain_find_related_experiences` (4N.4) | `second_brain.find_related_experiences` | `find_related_experiences()` + `get_experience_bundle()` |

There is no "generic write" tool and no tool that accepts raw SQL or an
arbitrary trust-status mutation. Every tool is a thin pass-through to
`SecondBrainService` — none of them contain governance logic of their
own, so there is exactly one place (the service) where the rules could
ever drift from what's enforced. The new tool is no exception: all of
its broadening logic lives in the service (`retrieval.py` + the
`find_related_experiences`/`get_experience_bundle` methods), not in the
tool itself, which only renders the result.

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

**FASE 4N.3 STEP 11, live-verified in isolated storage**
(`test_lesson_correction_preserves_history`): a previously stored
LESSON later proven incomplete is superseded exactly this way — the
original entry's title/summary remain byte-identical after correction,
`superseded_by` points at the replacement, the `SUPERSEDES` relationship
is `CONFIRMED`, the old version stays independently fetchable by its
own id, and `verify_audit_chain()` still reports valid afterward. A
caller wanting the *current* answer follows `superseded_by`; a caller
wanting the *history* can still fetch the original directly — both
paths stay open, nothing is hidden.

## EXPERIENCE CYCLE (FASE 4N.3)

The full operational learning loop:

```
PROBLEM
  │ (evidence references, RELATED_TO)
  ▼
HYPOTHESIS  ── user verifies/rejects, never automatic ──▶ (stays HYPOTHESIS until a human acts)
  │ DECIDED_IN
  ▼
DECISION  (requires timestamp + provenance — FASE 4N.1, unchanged)
  │ RESULTED_IN
  ▼
ACTION
  │ RESULTED_IN
  ▼
OUTCOME
  │ RESULTED_IN (CONFIRMED required)
  ▼
LESSON  (LEARNED only if outcome_backed — see LESSON GOVERNANCE)
```

**STEP 1's audit confirmed no schema change was needed anywhere in this
cycle.** `EntryType` already had all six stages (`PROBLEM`,
`HYPOTHESIS`, `DECISION`, `ACTION`, `OUTCOME`, `LESSON`) and
`RelationshipType` already had every relationship the cycle needs
(`CAUSES`, `RELATED_TO`, `DECIDED_IN`, `RESULTED_IN`, `RESOLVED_BY`,
`AFFECTS`, `PRECEDES`, `SIMILAR_TO`) since FASE 4N.1 — verified by a
direct set-membership check before writing any code. The entire cycle
is built from primitives the frozen foundation already had; FASE 4N.3
only had to prove they compose, and close one real gap in how the
*tool layer* (not the schema) surfaced relationships (see below).

Isolated, deterministic test coverage:
`test_full_experience_chain_persists_correctly` persists all six
stages through the governed tool layer, confirms every entry keeps its
own provenance and trust-lifecycle status (nothing silently changes
stage), confirms the relationship chain is walkable end-to-end, confirms
none of the six entries has `superseded_by` set (no history was
overwritten), and confirms `verify_audit_chain()` reports valid.

**Live-verified** (Claude Sonnet 4.6 + Orchestrator, real `jarvis ask`):
given a single search hit, the model made 5 further `second_brain_get`
calls — one per hop — correctly walking PROBLEM → HYPOTHESIS → DECISION
→ ACTION → OUTCOME using nothing but each entry's `related_entry_id`,
and correctly reconstructed the full chain in its answer with the
correct ids, without inventing any step.

## LESSON GOVERNANCE

"No outcome, no certified LEARNED lesson" is enforced in two layers
that answer two different questions:

1. **At write time** (FASE 4N.1, unchanged): `create_entry`/
   `propose_entry` require a LESSON/LEARNED entry to be grounded in
   something concrete (`domains`/`entities`/`evidence_references`) —
   enforced then because the entry doesn't have an id yet to link an
   OUTCOME relationship to (chicken-and-egg).
2. **At retrieval time** (FASE 4N.3, new): `SecondBrainService.is_outcome_backed(entry_id)`
   checks whether the entry now has at least one `CONFIRMED`
   relationship connecting it to an entry of type `OUTCOME`. This is
   computed on every read, never stored — it becomes `True` the moment
   a human confirms the right link, with no migration or mutation of
   the entry itself required.

The tool layer surfaces this as `outcome_backed` on every LESSON (or
`LEARNED`-status entry) `second_brain_search`/`second_brain_get`
returns, **in the rendered text the model actually reads** — not
buried in metadata the model never sees. This matters structurally:
the orchestrator's tool-calling loop only threads `ToolResult.content`
back into the conversation (`Message(role=Role.TOOL, content=...)`);
`ToolResult.metadata` never reaches the model at all. FASE 4N.3 found
`_entry_summary()` computed rich relationship/outcome-backing data but
put it only in `metadata` — structurally invisible to the model despite
being computed correctly. Fixed by rendering the same data into
`content` via `_render_entry_text()` (`tools/second_brain_tools.py`),
which is also what makes chain-walking possible: each relationship's
`related_entry_id` appears in the text a model reads, giving it
something to call `second_brain_get` on. An unbacked lesson is explicitly labeled
"not linked to any CONFIRMED OUTCOME -- treat as unverified," so a
model presenting it has no way to accidentally treat it as equally
certain as one that is backed.

Live-verified: in the isolated test, a LESSON linked to its OUTCOME via
a still-`PROPOSED` relationship correctly reports `outcome_backed=False`;
the same LESSON reports `outcome_backed=True` only after a human
explicitly confirms that relationship.

## HISTORICAL EVIDENCE / CURRENT VS. HISTORICAL FACTS

A new prompt section, `_HISTORICAL_EVIDENCE_RULE` (`prompt/builder.py`,
always included, as generic and business-agnostic as the existing
`_TOOL_GROUNDING_RULE` it sits beside — see FASE 4M.5A), makes the
distinction explicit: a Second Brain result is real evidence about a
PAST case, never proof of the CURRENT one. Correct: *"In a previous
case, X was associated with the problem, and action Y produced outcome
Z."* Incorrect: *"The current problem is definitely X."* A past case
may suggest what to investigate; only a tool result describing the
CURRENT situation can certify a current cause, status, or outcome.

**Deliberately not implemented as an extension of `OperationalEvidence`.**
`OperationalEvidence`/`build_evidence()` (`agents/operational_evidence.py`,
frozen since FASE 4M.5B) is scoped specifically to OPS Bridge capability
results (FACT/KNOWLEDGE per capability) and stays that way — mixing
Second Brain's fundamentally different kind of evidence (organizational
memory of past cases, not live operational state) into the same
classifier would conflate two distinct grounding models for no real
gain. The tool name alone already distinguishes them
(`ops_dynamic_*` vs `second_brain_*`), and the new prompt rule
generalizes the distinction — that combination was sufficient in every
test; extending `OperationalEvidence` was evaluated and rejected as
unnecessary scope growth into a system FASE 4M.5B already certified.

Live-verified (STEP 10, `jarvis ask`, one turn): asked for a current
OPS fact (production OEE) and a historical Second Brain search in the
same message, the model retrieved and reported both, correctly kept
them in separate sections of its answer, and drew no causal or
comparative link between them since the historical search happened to
return nothing that turn — no fabricated connection was invented to
fill the gap. (This run also surfaced a real, minor limitation: the
Second Brain free-text query was phrased in Italian while the seeded
test content was in English, and FTS5 found no match on wording alone —
see KNOWN LIMITATIONS.)

**Re-verified in FASE 4N.4** with the fixed retrieval path and a real
historical match available: asked in one message "è la stessa causa
dei casi precedenti?" alongside a current OPS KPI question, the model
called both `second_brain_find_related_experiences` and
`ops_dynamic_production_get_kpi` in the same turn, presented the OEE
figure and the historical Theta-6/Sigma-8 experience chain in clearly
separate sections, and stated plainly: *"Non posso affermare che la
causa per la Sigma-8 sia la stessa... può suggerire cosa investigare,
ma non certifica la causa attuale."* No fictional company conclusion
was written; the two evidence sources coexisted without merging.

## SIMILAR CASE RETRIEVAL (no embeddings)

V1 similarity is **structural overlap only** — the same `domain`/
`entity`/`type` filters `second_brain_search`/`list_entries` already
had (FASE 4N.1/4N.2), used as the similarity mechanism itself rather
than a new scoring layer. No vector index, no computed percentage: the
tool's description explicitly instructs "state which domains/entities/
terms two cases share -- never invent or imply a numeric similarity
score unless a tool result actually computed and returned one," and no
tool in this codebase does.

Live-verified: asked whether a new problem "resembled" a past one, the
model's answer named the exact shared basis — *"Condivide il dominio
`test-domain` e la tipologia di problema... nessun punteggio numerico è
stato calcolato"* — matching the required output shape (matched
domain, matched entry type, no invented score) without any dedicated
"similar case" tool or field existing at all; the existing filters
were sufficient.

**One live-observed limitation, addressed by tool-description wording,
not a code change**: an entity-only search for a *new* entity
correctly finds nothing (that exact entity was never recorded before) —
but a model that stops there without also trying a broader domain-level
search will report "no similar case" even when one exists under a
different entity name. The tool description was strengthened to
instruct trying a broader search before concluding nothing similar
exists. Live testing after that change showed mixed results: a direct,
simply-phrased request to search by domain worked reliably and
correctly retrieved and walked the full historical chain; a single
complex message bundling many sub-questions at once sometimes did not
trigger the broader search despite the instruction. This is reported
as an honest, live-observed model-behavior characteristic — not a
defect in the retrieval mechanism itself, which was independently
confirmed correct by direct, non-model tool invocation in every case.

**Superseded by FASE 4N.4's Retrieval Intelligence** (see below): that
finding was exactly the problem 4N.4 set out to remove. Prompt wording
can nudge a model toward broadening; it cannot guarantee it. FASE 4N.4
makes broadening the runtime's job, not the model's, so this
limitation no longer depends on how a question happens to be phrased.

## RETRIEVAL INTELLIGENCE (FASE 4N.4)

FASE 4N.3 found, live, that the model does not reliably choose to
broaden a narrow search on its own inside one complex message — see
above. FASE 4N.4's answer is architectural, not another prompt tweak:
`SecondBrainService.find_related_experiences()` runs a **fixed,
deterministic sequence** of the exact same structured queries the
frozen store already had (`list_entries`/`search_entries_fts`-family),
so a model no longer has to invent the right sequence of
`second_brain_search` calls — one call to the new
`second_brain_find_related_experiences` tool runs the whole sequence
itself.

`second_brain_search` is **unchanged** — a separate, additional tool
was added (STEP 6's choice) rather than silently changing a frozen
tool's behavior underneath existing callers.

## PROGRESSIVE BROADENING

Four fixed levels, run in this order, each only if its required input
was supplied (`src/openjarvis/second_brain/retrieval.py`):

| Level | Trigger | Underlying query |
|---|---|---|
| `EXACT` | `entities` given | `list_entries(entity=...)` per entity |
| `STRUCTURED` | `domains` given | `list_entries(domain=..., entry_type=...)` per domain × type |
| `TERM` | `query` given | OR-joined FTS (`search_entries_fts_broad` — new, see below) |
| `RELATIONSHIP` | (always, bounded) | `get_relationships()` on up to 5 top seed candidates, CONFIRMED only |

Bounded at every level (STEP 2's "must not return the entire Second
Brain"): each level's own store query is capped
(`_PER_LEVEL_LIMIT = 10`), `RELATIONSHIP` only expands from the
strongest `_MAX_RELATIONSHIP_SEEDS = 5` candidates found so far (not
every candidate), and the final merged result is capped at
`_DEFAULT_MAX_CANDIDATES = 15`. None of this depends on database size —
the bounds are fixed constants, not a fraction of the table.

**Bugfix found live, fixed in this phase**: a query combining a
brand-new identifier with genuinely matching descriptive terms (e.g.
"Sigma-8 performance degradate", where only "performance degradate"
exists in stored content) returned **zero** results under FTS5's
default implicit-AND semantics — backwards for a tool whose purpose is
broadening. `TERM` now uses a new OR-joined query
(`store.py::_fts5_safe_query_or` / `search_entries_fts_broad`) — FTS5's
own `rank` (bm25) still orders a row matching more terms above one
matching fewer, so this is not an unranked bag-of-words match, just no
longer one where a single unmatched word can zero out an otherwise-good
result. `second_brain_search`'s own AND-joined FTS
(`search_entries_fts`, used by `SecondBrainService.search_entries()`)
is untouched — this fix is scoped to the new broadening path only.
Regression-tested: `test_term_query_with_one_unmatched_word_still_matches`.

**A second bugfix found in the same live session**: an archived entry
(correctly excluded by `list_entries()`) still appeared in FTS results
— `search_entries_fts()` never checked `archived_at` at all. Fixed for
both the frozen FTS path and the new broad one (`include_archived: bool
= False` parameter added to both, matching `list_entries()`'s existing
convention); every existing caller keeps its previous call signature
and gets the corrected default automatically.
Regression-tested: `test_o_fts_search_excludes_archived_entries`.

## MATCH BASIS

Every `RetrievalCandidate` (`retrieval.py`) carries exactly why it
matched — `matched_domains`, `matched_entities`, `matched_terms`,
`relationship_basis` — populated only from the structured query that
actually found it. **No synthetic similarity percentage exists
anywhere in this codebase** (verified structurally in FASE 4N.3's
`test_similar_case_retrieval_no_automatic_causality` and re-verified
here); the tool renders each candidate's basis directly into
`content` (STEP 3's "do not let the model fabricate match reasons" —
there is nothing to fabricate, the reason is read off stored structure).

Live-verified: the model's rendered basis lines read, verbatim, things
like `matched via TERM: term=['performance degradate linea produzione']`
and `relationship=[RESULTED_IN (CONFIRMED) via <id>]` — exactly the
underlying match, not a paraphrase or an invented explanation.

## EXPERIENCE BUNDLES

`SecondBrainService.get_experience_bundle(anchor_entry_id, actor=...)`
— a bounded breadth-first walk over **CONFIRMED relationships only**
(STEP 9-L: a `PROPOSED` relationship is a model's unverified inference,
not certified structure; following it would let an unconfirmed guess
masquerade as part of the experience chain) starting from one anchor
entry, capped at `_DEFAULT_MAX_BUNDLE_HOPS = 8` hops and
`_DEFAULT_MAX_BUNDLE_ENTRIES = 12` entries. Each stage keeps its own
`id`/`type`/`summary`/`trust_status`/`provenance`/relationship-basis —
**never collapsed into one generated summary**, which would lose
exactly the distinctions FASE 4N.3 built LESSON governance and
trust-lifecycle tracking to preserve.

`second_brain_find_related_experiences` bundles automatically for the
single strongest candidate only (not every candidate — STEP 12's
bounds) after every search, so a model does not need to make one
`second_brain_get` call per chain stage the way it did before this
phase (FASE 4N.3's live test needed 5 separate `second_brain_get`
calls to walk one chain; FASE 4N.4 needed 0 — the bundle arrived with
the first search).

## ACTIVE VERSION POLICY

A `SUPERSEDED` candidate (`entry.superseded_by is not None`) is
resolved to its active replacement (`_resolve_active()`, following the
supersession chain forward, bounded by construction since no entry can
supersede itself) before being returned — the replacement's match
reasons absorb the superseded entry's, tagged
`relationship_basis=[..., "supersedes <old_id>"]`, so the connection to
the corrected version stays visible rather than silently disappearing.
`get_entry()` by id is untouched — a caller (or a human) can still
fetch the original superseded entry directly for historical inspection
at any time; only the *discovery* path (search/broadening) prefers the
active version, never the storage layer itself.

## RETRIEVAL BOUNDS (V1 limits, explicit)

- Candidates: capped at 15 total (`max_candidates`, overridable per call).
- Per-level store queries: capped at 10 each.
- Relationship expansion: from at most 5 seed candidates, not all of them.
- Experience bundle: at most 8 hops / 12 entries; `bundle.truncated`
  is set `True` when either bound was hit, so a caller can tell a
  complete chain from a cut-off one.
- Measured on the FASE 4N.4 test dataset: `find_related_experiences`
  ~3.6ms, `get_experience_bundle` ~1.1ms, rendered tool `content` for a
  10-candidate result with a 6-stage bundle ≈ 4.5KB — comfortably
  within normal tool-result sizes, no full-database dump.
- None of these bounds scale with database size — they are fixed
  constants (`retrieval.py`), so behavior stays predictable as the
  Second Brain grows.

## ANTI-CAUSALITY RULES

Folded into the same `_HISTORICAL_EVIDENCE_RULE` prompt section (one
rule, not a separate mechanism, since both address the same underlying
"don't let historical/correlational evidence pose as current/causal
evidence" failure mode):

- A `SIMILAR_TO`/`CORRELATES_WITH` relationship is never restated as
  `CAUSES`.
- A past decision/action that produced a good outcome is context for a
  recommendation, not a recommendation by itself — the current
  situation still needs its own current evidence first.
- Two records being related or co-occurring in the past does not mean
  one caused the other.

No domain-specific wording (no "OEE," no "production line," no
hardcoded example) — correct and inert in any session, including one
with no Second Brain or OPS tools connected at all.

Live-verified (STEP 8/9): asked "quindi la causa è la stessa?" after a
strong historical domain-and-type match was found and fully walked,
the model refused to certify the current cause, explained precisely
why (the historical hypothesis itself was never certified as a
verified cause; domain similarity across two different assets is not
proof of a shared cause; no current evidence had been gathered for the
new case at all), and reframed the historical lesson as *"un'ipotesi da
testare, non una certezza"* — a starting point for investigation, not
an automatic conclusion or an automatically-recommended repeat action.


## OBSIDIAN PROJECTION V1 (FASE 4O.2)

One-way, implemented: `second_brain/projections/obsidian.py` (pure
rendering: slugs, filenames, frontmatter, note/page bodies) +
`projections/obsidian_sync.py` (`ObsidianProjection`: manifest-tracked
rebuild/update orchestration). Deliberately kept outside
`service.py`/`store.py`/`types.py` — a projection is a *consumer* of
the governed API, never part of it, and every read in this module goes
through `SecondBrainService` exactly like a tool would.

`connectors/obsidian.py` (pre-existing, unchanged) reads a vault into
`knowledge.db` — the opposite direction, for a different purpose
(general document ingestion, not Second Brain). The two are never
combined into one component; mixing them would blur the exact one-way
boundary this phase exists to keep sharp.

```
SECOND BRAIN (SQLite, authoritative, unchanged by any export)
        ↓  ObsidianProjection.rebuild() / .update()
MARKDOWN VAULT (derived, read-only, per-principal)
```

## SOURCE OF TRUTH

The Second Brain remains authoritative in every case — **no code path
in this module ever reads a vault file and writes it back**. Every
generated note declares this in both frontmatter (`generated: true`,
`source_of_truth: false`) and a rendered body warning (STEP 10) — never
documentation-only. Editing a generated note has zero effect; the next
`rebuild()`/`update()` overwrites it.

## VAULT STRUCTURE

```
<vault>/
  .maia_projection_manifest.json      ← projection metadata, never business data
  MAIA/
    Dashboard/Dashboard.md
    Problems/  Hypotheses/  Decisions/  Actions/
    Outcomes/  Lessons/  Procedures/  Meeting Notes/
    Events/  Observations/
    _Entities/     ← one derived page per unique entities[] value
    _Domains/      ← one derived page per unique domains[] value
    _Superseded/   ← historical versions, excluded from active indexes
    _Archived/     ← archived entries, excluded from active indexes
```

Folders map 1:1 to the frozen `EntryType` vocabulary — no `Projects`/
`People`/`Assets` folders exist, since those aren't `EntryType`s;
they're free-text values inside `entities[]`/`domains[]`, surfaced
instead as derived `_Entities`/`_Domains` pages (STEP 3/7, confirmed
against FASE 4O.1's audit rather than assumed). An entry with
`archived_at` set always lands in `_Archived` (checked first); one with
only `superseded_by` set lands in `_Superseded`; otherwise its type
folder.

## NOTE MODEL

Filename: `<TYPE>_<date>_<slug-title>_<id-prefix>.md` — deterministic
given the entry's current state, Windows-safe (strips `<>:"/\|?*` and
control characters, guards reserved names like `CON`/`PRN`), Unicode-
preserving (Italian accented characters pass through unchanged — NTFS
handles Unicode natively, transliterating would only make notes harder
to recognize). The 8-char id prefix makes every filename globally
unique even with identical titles, and makes the bare filename usable
directly as a wikilink target with no folder-path ambiguity.

Body: title, read-only warning, Summary, Lifecycle/Trust, Relationships,
Evidence References — no LLM-generated text anywhere in this module;
every line is templated from real `SecondBrainEntry`/`Relationship`
fields.

## FRONTMATTER

1:1 with real fields, nothing invented: `second_brain_id`, `type`,
`trust_status`, `outcome_backed` (LESSON/LEARNED only, reusing FASE
4N.3's `is_outcome_backed()`), `visibility`, `domains`, `entities`,
`created_by`, `provenance`, `source`, `confidence`, `event_timestamp`,
`created_at`, `superseded_by`, `archived`, `evidence_references`
(`capability`/`domain`/`metric`/`period`/`filters`/
`trust_status_at_capture`/`fetched_at` — **never a numeric value**,
since `EvidenceReference` has no value field to copy, verified
structurally by `test_n_evidence_references_no_kpi_value`).

## RELATIONSHIPS

Rendered as real Obsidian wikilinks (`[[filename]]`) pointing at the
actual related note, with direction (`→`/`←`), relation type, and a
visually distinct status marker — `✅ CONFIRMED`, `🟡 PROPOSED
(unconfirmed)` — never the same styling for both (STEP 6/STEP 14-D).
`REJECTED` relationships are excluded from the rendered body by
default. If the related entry isn't resolvable for the current
principal (PRIVATE to someone else, or genuinely absent), the
relationship line is **silently omitted** — never rendered as a
placeholder, since even "a restricted relationship exists here" would
leak information about content the principal cannot read.

## ENTITY/DOMAIN PAGES

One derived page per unique `entities[]`/`domains[]` value across all
entries visible to the principal, tagged `derived: true` and explicitly
labeled as navigation only: *"co-occurrence here is navigation only,
never a causal or semantic relationship"* — the same anti-causality
discipline already certified for retrieval (FASE 4N.3/4N.4), now
carried into the projection layer too. Lists only active (non-archived,
non-superseded) entries by default.

## SECURITY

Enforced **before serialization**, not by hiding output: every read
goes through `SecondBrainService.list_entries()`/`get_entry()`, the
exact same fail-closed visibility clause certified in FASE 4N.2A/4N.4
(`visibility != 'PRIVATE' OR created_by = ?`, `actor` bound as a
parameter so a missing/wrong principal can never match). A PRIVATE
entry owned by a different principal is never fetched, so it can never
reach a rendered file — not filtered afterward, not present at all.

Live-verified with the real CLI, two separate vault runs, no scripting
shortcuts: the entry's owner (`local-os-user:Luigi`) exported it
correctly; a different principal (`test-stranger:nobody`) exporting
against the exact same Second Brain got **zero** entry notes, an
honest all-zero dashboard, and grep-verified zero occurrences of the
PRIVATE entry's id or title anywhere in that principal's vault.
Isolated-test-covered too (`test_ef_private_owner_exported_other_not`,
`test_f_unresolved_principal_fails_closed`).

## SUPERSEDED / ARCHIVED

Both preserved permanently, never deleted — `_Superseded` notes link
forward to their active replacement (`[[...]]`) and stay independently
fetchable; `_Archived` notes stay in the vault, just excluded from
Dashboard/entity/domain default listings. Live and isolated-verified
(`test_g_superseded_historical_folder_and_link`,
`test_h_archived_folder`; the real vault smoke test's 19 `_Archived`
notes are FASE 4N.3/4N.4's own prior test chains, still intact and
inspectable).

## REBUILD

`ObsidianProjection.rebuild()`: deletes and regenerates the entire
`MAIA/` tree from scratch, deterministically. Idempotent — no
wall-clock timestamp is embedded in any rendered file, so two
back-to-back rebuilds of an unchanged Second Brain produce byte-
identical output (`test_j_rebuild_twice_identical`). Always available
as the correctness ground truth every other operation can fall back to.

## INCREMENTAL SYNC

`ObsidianProjection.update()`: compares each visible entry's own
`updated_at` (and its currently-computed path, to catch title/type/
archival/supersession changes) against what the manifest recorded last
time, and only re-renders what changed — a thin filter over the exact
same per-entry renderer `rebuild()` uses, so its correctness for entry-
content changes is inherited, not independently re-verified. Falls
back to a full `rebuild()` whenever the manifest is missing, unreadable
(including deliberately-corrupted, `test_p_interrupted_projection_recoverable_via_rebuild`),
or belongs to a different principal — never guesses at a partial state.

**Honestly scoped as PARTIAL, not silently incomplete**: a relationship
created or confirmed between two *already-rendered* entries does not
change either entry's `updated_at` (relationships live in a separate
table), so `update()` alone will not notice a relationship-only change
and refresh the affected "Relationships" sections. The alternative
(diffing the audit log for relationship events) was evaluated and
rejected — audit records can carry a PRIVATE entry's title in `details`
even for a principal who cannot read that entry, and this module has no
reason to open that privacy surface just to make incremental sync
slightly more complete. Mitigation: run `update()` often, `rebuild()`
periodically — the exact hybrid strategy FASE 4O.1's audit already
recommended, not a new invention.

## MANIFEST

`.maia_projection_manifest.json` at the vault root (hidden dotfile,
sibling to Obsidian's own `.obsidian/`): `version`, `principal`,
`last_sync_at`, and per-entry `{relative_path, updated_at}`. Pure
projection metadata — never a second copy of business data, never
consulted by `rebuild()` (which regenerates from the Second Brain
alone), only by `update()` as its change-detection cursor.

## CLI

`jarvis second-brain export-obsidian` (`cli/second_brain_cmd.py`, a
new `second-brain` command group — deliberately not reusing `jarvis
vault`, which already means something unrelated: the encrypted
credential store). Options: `--vault` (default
`~/.openjarvis/obsidian_vault/`, inside OpenJarvis' own config
directory so a bare default run can never collide with a user's real
personal vault), `--principal` (defaults to
`resolve_runtime_principal()`, the same identity every Second Brain
tool already uses), `--rebuild` (force full rebuild), `--force`
(required to write into a non-empty directory that isn't already a
MAIA projection — refuses by default, so a mistaken `--vault` pointing
at an unrelated real vault is never silently overwritten).

## KNOWN LIMITATIONS (added by FASE 4O.2)

- Incremental `update()` does not detect relationship-only changes
  (see INCREMENTAL SYNC above) — mitigated by periodic `rebuild()`,
  not solved.
- No import/reconciliation path exists — this phase is one-way only,
  exactly as scoped; Obsidian edits are never read back.
- Multi-vault-per-principal is the only supported topology in V1 — a
  single vault mixing content from multiple principals was
  deliberately not designed, to keep the security boundary simple and
  obviously correct rather than cleverly correct.

## KNOWN LIMITATIONS (V1)

- **Cross-language free-text search is not guaranteed to match**
  (found live in FASE 4N.3 STEP 10): FTS5 matches on the tokenized
  words actually stored, not meaning — an Italian query against
  English-language stored content (or vice versa) can miss a real
  match that a domain/entity/type filter would still find. Not a bug —
  FTS5 has no translation layer, and none was ever claimed — but worth
  knowing when composing a search: prefer structured filters
  (`domain`/`entity`/`type`) over free text alone when language might
  differ between the query and the stored content.
- **RESOLVED in FASE 4N.4**: "broader-search fallback is a prompted
  behavior, not a guarantee" (FASE 4N.3's top limitation) — broadening
  is now `find_related_experiences()`'s own fixed algorithm, not
  something a model has to remember to attempt. Live-verified: the
  exact combination that failed in FASE 4N.3 (one long message bundling
  several sub-questions) succeeded reliably in FASE 4N.4 once the model
  called the new tool with any reasonable domain/entity/term hint —
  including composed with a current OPS fact in the same turn.
  **What still depends on the model**: which `query`/`domains`/
  `entities`/`entry_types` values to pass in the first place — the
  tool cannot broaden using information it was never given. A model
  that passes nothing at all still gets an honest empty result, not a
  fabricated one (verified: STEP 9-K, live query 8).
- **Cross-language free-text matching is improved, not solved**: the
  new OR-joined `TERM` level (see PROGRESSIVE BROADENING) means a query
  with *any* overlapping word now matches, which meaningfully softens
  the FASE 4N.3 cross-language gap in practice (a query sharing even
  one cognate/borrowed term with the stored content can now match).
  It is still not translation — a query and stored content with zero
  shared tokens in any language will not match on `TERM` alone.
  Structured filters (`domains`/`entities`) remain unaffected by
  language entirely and are the more reliable broadening axis when
  available.
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
