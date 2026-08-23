# MAIA Multi-Source Reasoning / Evidence Composition V1 (FASE 4O.6)

Status: certified this phase, live-tested with real Claude Sonnet 4.6 against
a real OPS Bridge, an isolated Second Brain, and synthetic documents.

Extends `agents/operational_evidence.py` (previously OPS-only, FASE 4M.5B) so
MAIA can compose evidence from every governed source in one answer without
letting one source's semantics bleed into another. Does not redesign any
frozen subsystem -- Second Brain, Document Knowledge, and OPS Bridge
classification logic are read, never modified.

## MULTI-SOURCE EVIDENCE MODEL

One structural, cross-turn ledger (`OperationalEvidence`, built fresh each
turn by `build_evidence(tool_results)`, re-injected as a `role=user` message
via `render_note()`) now classifies results from **every** governed tool
family, not just OPS Bridge. Before this phase, Second Brain and Document
Knowledge tool results reached the model only as raw tool-result text with
static, per-tool-description framing -- real, but with no cross-turn
aggregation, no participation in the "insufficient evidence" recap, and no
structural tag distinguishing them from OPS facts. They now get the exact
same kind of persistent, re-asserted framing OPS evidence already had.

## SOURCE CLASSES

Five classes, one discriminator field (`EvidenceItem.source_class`):

| Class | Meaning | Derived from |
|---|---|---|
| `CURRENT_OPERATIONAL_FACT` | Certified OPS Bridge operational value | `ops_dynamic_*` tool results (Bridge envelope `status=ok`, not the Knowledge capability) |
| `KNOWLEDGE_DEFINITION` | Certified OPS Knowledge-capability definition (what a KPI *means*, never its value) | `ops_dynamic_knowledge_get_kpi_definition` results |
| `HISTORICAL_EXPERIENCE` | A Second Brain entry -- a PAST case | `second_brain_search` / `second_brain_get` / `second_brain_find_related_experiences` results |
| `DOCUMENT_EVIDENCE` | A Document Knowledge chunk -- file-sourced, contextual | `document_search` results |
| (limitation) | A source explicitly reported a gap | Any of the above, or a zero-result call |

No source can be silently reclassified as another -- classification is
purely structural (which tool returned it, what shape its own result took),
never inferred from content.

## SOURCE PRECEDENCE

Generic, never business-domain-specific (verified: no hardcoded metric/
domain name governs precedence, only source class):

1. A certified `CURRENT_OPERATIONAL_FACT` always outranks a number merely
   mentioned in `DOCUMENT_EVIDENCE` or recalled from `HISTORICAL_EXPERIENCE`.
2. A certified `KNOWLEDGE_DEFINITION` always outranks a definition implied
   by document text or general model knowledge.
3. `HISTORICAL_EXPERIENCE` is precedent/context, never proof of current
   cause -- a past case cannot become a current diagnosis without current
   evidence saying so.
4. `DOCUMENT_EVIDENCE` is supporting/contextual, never a substitute for a
   certified fact.

This is stated explicitly to the model as a `PRECEDENCE` line in
`render_note()`, appended only when more than one non-fact source is
present (nothing to compose against otherwise).

## CONFLICT HANDLING

`render_note()` never reconciles a disagreement itself -- it presents every
source under its own labeled heading and instructs the model to surface a
difference explicitly rather than silently pick one. Live-verified (STEP 12,
real Claude Sonnet 4.6): asked whether a document-stated changeover standard
(15 minutes) was the actual current value, MAIA correctly separated "document
standard: a rule, not a measurement" from "current actual time: not
available," never substituting the document's number for a missing
certified fact.

## PROVENANCE

Every `EvidenceItem` still traces back to exactly the tool result it came
from:

- OPS facts/knowledge: `domain`, `period`, `period_status`, `trust_status`
  (OPS ONE's own vocabulary, e.g. `TRUSTED`/`BUSINESS_LOGIC_IN_REVISION`),
  `provenance` -- unchanged from before this phase.
- `HISTORICAL_EXPERIENCE`: entry id, `EntryType` (PROBLEM/HYPOTHESIS/
  DECISION/ACTION/OUTCOME/LESSON), Second Brain's own `EntryTrustStatus`,
  provenance, archived/superseded flags, and (for
  `second_brain_find_related_experiences`) the exact match basis
  (domain/entity/term/relationship) -- never a similarity score.
- `DOCUMENT_EVIDENCE`: the real citation (`filename, page N` or
  `filename, section "X"` or `filename, chunk N`) as `provenance` -- exactly
  what lets MAIA say "According to X, page Y..." instead of presenting the
  text as its own knowledge.

## CURRENT VS HISTORICAL

`HISTORICAL_EXPERIENCE` items are rendered under a heading that explicitly
states "PAST cases -- precedent/context only, never the current situation."
Live-verified (STEP 7): asked "is this happening again," MAIA retrieved the
full historical PROBLEM→DECISION→ACTION→OUTCOME→LESSON chain, explicitly
noted current OPS data was unavailable for the specific query it tried, and
concluded "this only serves as historical precedent, not proof that the
same issue is occurring now" -- without being told to say that.

## DOCUMENT EVIDENCE

`DOCUMENT_EVIDENCE` items never carry a `trust_status` (documents aren't
OPS-trust-classified) and are labeled "NEVER a certified operational value
even if it contains a number." Live-verified (STEP 6, STEP 12): a document-
stated number (15-minute changeover standard) was consistently presented as
a document requirement, never as the current OEE or current changeover time.

## INSUFFICIENT EVIDENCE

A zero-result call from Second Brain or Document Knowledge is not silence --
`build_evidence()` adds an explicit `LIMITATIONS` line ("Second Brain
queried -- no historical precedent found." / "Document Knowledge queried --
no matching source found."), the same structural treatment OPS's
`data_not_available` status already got. Live-verified repeatedly (STEPs 7,
9, 12): every "not found" case was reported as a gap, never silently filled.

## ROUTING FIX (STEP 13)

Audited the actual tool-offering path (router `always_on`/`ops_dynamic_*`
scoring, `cli/_tool_names.py::resolve_tool_names`, `cli/ask.py`'s tool
sets, `system/builder.py::_resolve_tools`) rather than assuming a gap.
Found one real, proven registration gap (not a routing/scoring gap): unlike
Second Brain tools (always auto-enabled), `document_search`/
`document_list_sources` were registered but never unioned into the default
tool set anywhere -- a default MAIA session could never reach Document
Knowledge at all. Fixed in `cli/_tool_names.py::resolve_tool_names` only,
mirroring the exact Second Brain pattern. The bounded re-routing/expansion
mechanism (`orchestrator.py`'s `ops_dynamic_*`-only widening) was left
untouched -- it was never the actual gap.

## KNOWN LIMITATIONS (V1)

- **RESOLVED in FASE 4O.6A.** The three-source reliability gap described
  below (originally attributed to model tool-use behavior alone) had a
  second, larger contributing cause: `document_knowledge/file_state.py`'s
  `FileStateStore` opened its SQLite connection without
  `check_same_thread=False`, unlike every other SQLite-backed store in the
  codebase. Live-traced via `--json` tool-call tracing: `document_list_sources`
  crashed with "SQLite objects created in a thread can only be used in that
  same thread" when called alongside OPS/Second Brain tools in one turn, and
  the immediately-following `document_search` call also silently returned a
  false "No matching documents found" -- the same broken connection object
  carried the failure forward. Fixed with a one-line change (see
  `file_state.py`'s own comment). FASE 4O.6A also added a small, generic,
  bounded orchestrator mechanism (`orchestrator.py::_run_function_calling`,
  `_COVERAGE_FAMILIES`): before accepting a final answer, if an always-on
  evidence family (Second Brain / Document Knowledge) is available this
  session but was never attempted in any turn, the model gets exactly one
  non-forcing nudge to consider it -- structural (which tool names were
  attempted vs. available), never a keyword/phrase/language/business-domain
  match on the question text, and never repeated. After the SQLite fix,
  6 live three-source questions (English and Italian, independently
  phrased) against real Claude Sonnet 4.6 all attempted all three tool
  families; 5 of 6 did so cleanly with a coherent final answer, needing no
  nudge (the model batched all three calls natively). One run (see
  `docs/MAIA_MULTI_SOURCE_REASONING_V1.md` FASE 4O.6A report / final report
  in that phase's transcript) showed a separate, still-open issue: the
  model selected the wrong OPS capability (`ops_dynamic_balance_get_kpi`
  instead of `ops_dynamic_production_get_kpi`), retried Second Brain
  repeatedly until the existing loop-guard budget stopped it, and its
  final turn's content was a malformed, un-parsed tool-call fragment
  instead of a coherent answer -- non-fabrication still held (no invented
  fact), but the interaction quality degraded. This is a distinct,
  narrower residual limitation (OPS capability selection / tool-call
  response parsing under long repetitive-failure conversations), out of
  this phase's scope (no OPS routing/capability-selection logic or
  tool-call parsing was touched), and not something a prompt-wording
  change should paper over.
- Inherits Document Knowledge V1's own lexical-only retrieval limitation
  (see `docs/MAIA_DOCUMENT_KNOWLEDGE_V1.md`).
- `EvidenceItem.domain`/`period`/`period_status` remain `None` for
  `HISTORICAL_EXPERIENCE`/`DOCUMENT_EVIDENCE` items (these concepts don't
  apply to those sources) -- `trusted_domains_covered()`/cross-domain
  sufficiency checking stays scoped to OPS facts only, by design.
- No automatic reconciliation logic exists or is planned for V1 conflicts
  -- surfacing, not resolving, is the explicit design choice.
