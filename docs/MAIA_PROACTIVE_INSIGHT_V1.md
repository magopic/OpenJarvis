# MAIA Proactive Insight & Action Proposal V1 (FASE 4P.1 / 4P.1A / 4P.1B)

Status: **PROACTIVE INSIGHT & ACTION PROPOSAL V1 = FROZEN.**
DETECT -> GROUND -> EXPLAIN -> PROPOSE. Never DETECT -> EXECUTE.

Moves MAIA from "answer questions" toward "recognize something worth
attention, explain why, and propose what the user could do next" -- built
directly on the frozen FASE 4O.6/4O.6A multi-source evidence model.
Nothing in this phase autonomously executes an external action.

Three phases, one frozen result:
- **FASE 4P.1** -- the contract, detectors, and tools (PROACTIVE INSIGHT
  CONTRACT / DETECTOR MODEL / PROPOSAL CONTRACT / HUMAN AUTHORITY
  BOUNDARY / SECOND BRAIN BOUNDARY / ACTION BOOK BOUNDARY / TOOLS EXPOSED
  below).
- **FASE 4P.1A** -- moved activation out of the model's hands: the
  orchestrator runs `ProactiveReasoningService` itself whenever a bounded,
  structural activation rule is met, never because the model chose to
  call a particular tool (see ORCHESTRATOR ACTIVATION below).
- **FASE 4P.1B** -- closed the tool-claim fabrication gap 4P.1A's live
  testing found: a closed-world available-tools manifest and an explicit
  narrative/governed-claims boundary, both present from turn 0 (see CLAIM
  INTEGRITY below). Live-certified 4/4 on the required fabrication-gate
  scenarios, including exact repeats of both prior fabrication triggers.

No execution capability exists anywhere in this work (structurally
verified by test, not just by convention). No Second Brain write path.
No Action Book write path -- Action Book stays read-only, exactly as
audited.

## PROACTIVE INSIGHT CONTRACT

`ProactiveInsight` (`src/openjarvis/agents/proactive_insight.py`):
`id` (deterministic, see below), `title`, `summary`, `severity`,
`confidence`, `status` (`DETECTED` -- the only value V1 ever sets; no tool
changes it), `detected_at`, `evidence` (a list of the same `EvidenceItem`
objects `operational_evidence.py` already produces -- never copied or
reinterpreted), `reasoning_basis`, `limitations`, `proposed_actions`.

Severity: `INFO` / `ATTENTION` / `WARNING` / `CRITICAL`. Never derived from
an invented business threshold -- a detector either has a certified
threshold to compare against (see DETECTOR MODEL) or it doesn't, in which
case no insight is generated at all (not a downgraded-severity insight).

Confidence: `LOW` / `MEDIUM` / `HIGH`, categorical, never a fabricated
probability number (STEP 9 explicitly forbids inventing probabilities).
`HIGH` only when a genuine certified numeric comparison corroborates the
insight (e.g. a real threshold breach or a real value conflict); `MEDIUM`
for a single current fact with no such corroboration; `LOW` for insights
resting on an absence (missing data, an unresolved prior proposal) rather
than a positive certified reading.

Insight ids are deterministic (`_stable_id`: a hash of the detector name
plus its grounding values), not random -- re-analyzing identical evidence
produces the identical insight id, so repeated detection is idempotent.

## DETECTOR MODEL

Deterministic Python only -- an LLM never decides whether a certified
threshold was breached; it only decides when to call the detection tool
and how to explain an already-grounded result. Each detector fires ONLY
when its specific certified input exists in the evidence; none hardcodes
a business KPI, domain, or number.

- **`ThresholdBreachDetector`** -- fires only when a FACT envelope's own
  `data` carries an explicit, self-describing contract:
  `{"value": <number>, "threshold": <number>, "threshold_type": "max"|"min"}`
  (optionally `"critical_threshold"` for the CRITICAL tier).
  `threshold_type` is required, not defaulted -- guessing breach direction
  when it's absent would itself be inventing a business rule.
  **Confirmed dormant against real OPS ONE data today**: audited
  `ops.knowledge.get_kpi_definition`'s `KpiDefinitionData` (FASE 4P.1
  STEP 1 audit) and found no threshold/target field exists anywhere in
  the certified Knowledge source of truth. This is the correct, honest V1
  behavior (STEP 4: "operate only when required inputs exist"), not a
  bug -- the detector is ready the moment a real capability exposes a
  certified threshold, with zero new code.
- **`MissingDataDetector`** -- fires when an OPS current-fact call
  returns `status != 'ok'` (e.g. `data_not_available`). Severity is
  always `INFO`: an absence is not itself a certified problem.
- **`ConflictDetector`** -- fires when two CURRENT_OPERATIONAL_FACT
  results report a different numeric `value` for the same `metric` key.
  Structural, deterministic comparison only -- never a free-text guess
  against historical/document prose. Surfaces the disagreement; never
  picks a winner.
- **`UnresolvedProposalDetector`** -- fires only when the caller passes an
  explicit `prior_proposals` history (V1 has no persistence layer of its
  own for that history, deliberately -- see SECOND BRAIN BOUNDARY /
  ACTION BOOK BOUNDARY below). Without one, it is a correct no-op.
- **`CertifiedAlertDetector`** -- fires only on a source that
  self-identifies as a certified alert (`data.is_certified_alert is True`
  plus a valid `alert_severity`). **Confirmed dormant against real OPS
  ONE data today**: the only alert-shaped feature found in the audit,
  `PredictiveAlertEngine`, is templated/hardcoded display text with no
  id, no persistence, and no OPS Bridge exposure -- it is explicitly NOT
  treated as a certified source by this detector.

Historical/document evidence never triggers a detector on its own (STEP
3's evidence-first rule): it only ENRICHES an insight a current-fact
detector already produced, via `_enrich_with_context` -- appended as
clearly labeled, separate reasoning lines ("Historical precedent (context
only, not proof of current cause)" / "Document context (procedure/
reference, not a certified value)"), never merged into the current fact's
own certainty tier.

## EVIDENCE REQUIREMENTS

A `ProactiveInsight` cannot exist without at least one `EvidenceItem`.
Historical-only or document-only evidence produces **zero** insights --
verified by test (STEP 14 C/D): "no current operational alert" is a
literal, correct outcome, not a downgraded one.

## SOURCE PRECEDENCE

Reused, unmodified, from the frozen FASE 4O.6 model (see
`docs/MAIA_MULTI_SOURCE_REASONING_V1.md`): CURRENT_OPERATIONAL_FACT
establishes current state; KNOWLEDGE_DEFINITION defines meaning/threshold
when certified; HISTORICAL_EXPERIENCE is precedent only; DOCUMENT_EVIDENCE
is supporting/contextual only. This module never redefines or reverses
that order -- it only reads `OperationalEvidence`'s already-classified
lists.

## ACTION PROPOSAL CONTRACT

`ProposedAction`: `id` (deterministic, tied to its parent insight),
`title`, `description`, `action_type` (a short free string, e.g.
`"review"` -- never a business-specific enum), `status` (always
`PROPOSED` in V1 -- `APPROVED`/`REJECTED`/`EXECUTED`/`FAILED`/`CANCELLED`
exist in the enum for forward-compatibility with a future phase, but
nothing in this codebase ever sets them), `rationale`,
`supporting_evidence`, `limitations`, `requires_confirmation` (always
`True`, unconditionally -- V1 cannot prove any given recommendation has
zero possible external side effect, so it defaults safe rather than
attempting that classification), `execution_capability` (always `None` --
never wired to anything callable).

Every generated action answers WHAT/WHY/EXPECTED PURPOSE/LIMITATIONS/
AUTHORITY (STEP 9) directly in its fields: `title`/`description` = WHAT,
`rationale`/`supporting_evidence` = WHY, `description` = EXPECTED PURPOSE,
`limitations` = LIMITATIONS (always includes an explicit "no owner,
deadline, or expected outcome is known" line -- MAIA has no certified
source for any of those, so it never invents one), and `AUTHORITY` is
structural: `execution_capability is None` + `requires_confirmation is
True` together mean "recommendation only" is always true, not just
documented prose.

## HUMAN AUTHORITY BOUNDARY

Hard requirement, verified structurally (test `test_l_...`, both at the
service layer and the tool layer): no `execute_action`/`run_action`/
`do_it`/`send_anything`/`write_anything`-shaped symbol exists anywhere in
`proactive_insight.py` or `proactive_insight_tools.py`. `ProposedAction`
has no `execute()`/`run()` method. The four read tools
(`maia_insights_list`/`maia_insight_get`/`maia_action_proposals_list`/
`maia_action_proposal_get`) are read-only. The one detection tool
(`maia_analyze_evidence_for_insights`) cannot accept free-text "evidence"
from the model -- its schema only has structured query parameters
(`ops_capability`/`ops_params`/`second_brain_query`/
`second_brain_domains`/`document_query`), identical in spirit to the
parameters the underlying already-governed tools accept. The user remains
the sole authority to act on any proposal; nothing in this phase can act
for them.

## SECOND BRAIN BOUNDARY

`proactive_insight.py` never imports or calls a Second Brain *write* path
(`create_entry`/`confirm_entry`) -- verified structurally by test. If a
user wants to preserve an insight as a decision/lesson/experience, that
must go through the existing, unmodified `second_brain_propose_entry` /
`second_brain_confirm_entry` tools (FASE 4N.2/4N.3), with their own
explicit human-confirmation discipline -- no bypass exists.

## ACTION BOOK BOUNDARY

Audited (STEP 1) rather than assumed: OPS ONE's Action Book
(`src/features/action-book`, `ActionItem`/`ActionStatus`) is a
Supabase-backed, human-authored business object (Acquisti/Logistica/
Qualità/Valleoro sections, saving tracking, PDF export) exposed to OPS
Bridge as **read-only** (`ops.actions.list`, `category: 'READ'`,
`authorization: 'owner_only'`, no write capability). OPS ONE's own docs
mark a hypothetical `ops.actions.create_task` write capability as an
explicit, unbuilt TIER-3 gap, noting *"there is currently no server-side
authorization boundary between 'an Agent decides to create a task' and 'a
row is written to Supabase.'"* `ProposedAction` is deliberately kept
**completely separate** from Action Book for V1 -- no integration, no
shared id space, no write path. OPS ONE's code was not modified in any
way by this phase (verified: `git status` on that repo shows zero source
changes); Action Book's own authorization is exactly as strong or weak as
it was before this phase.

## TOOLS EXPOSED

`maia_analyze_evidence_for_insights` (gathers evidence itself, via the
already-governed `second_brain_find_related_experiences`/`document_search`
tools and the same OPS Bridge client `ops_dynamic_*` tools use, then runs
`ProactiveReasoningService` -- never accepts evidence text from the
model), `maia_insights_list`, `maia_insight_get`,
`maia_action_proposals_list`, `maia_action_proposal_get`. All five are
auto-enabled by default (`cli/_tool_names.py::_PROACTIVE_INSIGHT_TOOL_IDS`,
mirroring the FASE 4O.6 Second Brain/Document Knowledge pattern). No
execution tool exists.

The insight/proposal registry the read tools query is a plain in-memory,
per-process dict (bounded at 500 entries, oldest-evicted) -- explicitly
not a new persistent store, and explicitly not Second Brain or Action
Book. It resets on process restart.

## ORCHESTRATOR ACTIVATION (FASE 4P.1A)

FASE 4P.1's live testing found Claude Sonnet 4.6 reliably preferred its
familiar FASE 4O.6/4O.6A tools over ever calling
`maia_analyze_evidence_for_insights` itself, even when explicitly asked by
name. FASE 4P.1A moved the decision out of the model's hands: the
orchestrator (`orchestrator.py::_run_function_calling`) now runs
`ProactiveReasoningService` itself, automatically, after every
tool-executing turn, whenever `should_activate_proactive_analysis()`
returns true -- never because the model chose a particular tool.

Activation (`proactive_insight.py::should_activate_proactive_analysis`)
requires at least one tool to have actually been called this run, AND one
of: the original user request containing a generic, non-business
proactive-intent marker ("flag", "risk", "insight", "attention",
"rischio", "problema", ... -- a small fixed list, English/Italian, never a
KPI or domain name), an explicit call to
`maia_analyze_evidence_for_insights` this turn, or a source
self-certifying as an alert. An ordinary factual question ("What is
current OEE?") matches none of these and correctly never activates --
live-verified (STEP 10 scenario 4): zero activation, direct answer.

When activated, `render_governed_proactive_block()` renders the
`ProactiveReasoningService` result as a `[GOVERNED_PROACTIVE_ANALYSIS]`
message injected into context -- generated entirely from already-computed
`ProactiveInsight` objects, never by the model, with an explicit
instruction not to alter any id/severity/confidence/status/
requires_confirmation value.

A second, always-present block (`render_tool_execution_integrity()`,
`[ACTUALLY_EXECUTED_TOOLS]`) is injected from turn 1 -- before any tool
call, not just after -- listing exactly which tools actually ran this
conversation (reusing the orchestrator's own `all_tool_results`, no new
tracking or persistence). It exists specifically to give the model ground
truth to contradict a fabricated tool-call claim with, addressing the
second FASE 4P.1 finding (a fabricated tool-call transcript in prose).
Live testing (STEP 10) found this sufficient when the model actually
attempts a real tool call, but **not** sufficient against every case --
see KNOWN LIMITATIONS.

**A genuine bug was found and fixed during live testing**:
`ConflictDetector` originally grouped facts by metric name alone, so two
different periods' values for the same metric (e.g. June OEE vs July
OEE -- ordinary change over time) were misclassified as "certified
sources disagree." Fixed to group by `(metric, period)`; a regression
test (`test_h2_different_periods_same_metric_is_not_a_conflict`) locks
this in, and a live re-run confirmed the fix (0 insights, correct
behavior) where the bug had previously produced a false ATTENTION-severity
conflict insight.

## CLAIM INTEGRITY (FASE 4P.1B)

FASE 4P.1A's `[ACTUALLY_EXECUTED_TOOLS]` block alone did not reliably stop
the model from fabricating a fake tool-call transcript when asked about a
capability that isn't actually registered (2 of 3 live FASE 4P.1A
attempts under that specific condition fabricated, one severely -- an
entire invented JSON dataset with a false `"trust_status": "validated"`
claim). FASE 4P.1B closes this with two more turn-0-present, deterministic
blocks (`proactive_insight.py`), per STEP 7's enforcement-level choice
(governed-section/narrative separation, level 2 -- never a natural-
language fact-checker, deliberately not built):

- `render_available_tools_manifest()` / `[AVAILABLE_TOOLS_THIS_SESSION]`
  -- a closed-world list of every tool actually registered this session
  (`{t.spec.name for t in self._tools}`, no new registry). The model can
  answer "is X available" by lookup instead of inference.
- `render_claim_boundary_notice()` / `[CLAIM BOUNDARY]` -- separates
  MODEL NARRATIVE (always allowed) from GOVERNED CLAIMS (certified/
  current/validated/tool-returned/governed-insight/governed-proposal
  statements), explicit that the model is never the authority for
  assigning a trust/certification status to anything.
- `_call_ops()` (`proactive_insight_tools.py`) now also tags a genuinely
  unsupported OPS Bridge capability with an unambiguous
  `REQUESTED CAPABILITY: <name>` / `STATUS: NOT_AVAILABLE` message --
  live-verified against the real bridge, which answers an unknown
  capability with a clean HTTP 200 and `status: 'unsupported'` (not an
  exception), a correction from the first implementation attempt, which
  only handled the exception path. Kept distinct from `data_not_available`
  (capability exists, no data for the period) and `forbidden` (exists,
  not authorized) -- only `unsupported` gets this phrasing.

Live re-verified (STEP 9, Claude Sonnet 4.6): 4/4 required scenarios
clean, including an exact repeat of the prompt that previously caused the
severe fabrication -- the model consulted the manifest and explicitly
listed the real available tools instead. This remains a strong,
architecture-based deterrent (the model is *never given a reason* to
guess), not a mathematical guarantee against a sufficiently determined or
erratic model output -- stated honestly, not overclaimed.

## KNOWN LIMITATIONS

- `ThresholdBreachDetector` and `CertifiedAlertDetector` are, by design,
  dormant against real OPS ONE production data today -- no capability
  currently exposes a certified threshold or a certified alert. This was
  confirmed by direct audit, not assumed; see DETECTOR MODEL above.
- `UnresolvedProposalDetector` cannot see real prior-proposal history in
  V1 because no persistence layer for `ProposedAction` exists yet
  (deliberately, per SECOND BRAIN BOUNDARY / ACTION BOOK BOUNDARY above).
- The in-memory insight/proposal registry is per-process and resets on
  restart; nothing here is durable.
- The FASE 4P.1/4P.1A fabrication risk is addressed (see CLAIM INTEGRITY
  above) but not proven impossible -- no post-generation output validator
  exists (deliberately, per STEP 7), so the defense is entirely
  context-based. 4/4 live re-verification is strong evidence, not a
  formal guarantee.
- `ConflictDetector` still only compares FACT-vs-FACT numeric values
  under the same `(metric, period)` key; it does not attempt to detect a
  document/historical text disagreeing with a current fact (unreliable
  free-text number extraction, deliberately avoided as a fabrication
  risk).

## LIVE TEST RESULTS

FASE 4P.1 (6 scenarios, prompt-only): see that phase's final report.

FASE 4P.1A (7 scenarios, Claude Sonnet 4.6, `--verbose` debug-log-verified
activation): scenarios 1 (proactive request, sufficient evidence),
2 (model never chose the analysis tool, engine still ran), 3
(insufficient-evidence case, 0 insights, honest), 4 (ordinary question,
0 activation), and 6 (execution refusal, premise correction) all clean.
Scenarios 5 and 7 (proposed-action / fabrication-reproduction, both
requiring a synthetic/unregistered capability to demonstrate) showed the
fabrication risk documented above in 2 of 3 attempts -- the fabrication
gate (7/7 clean) was not met; see the FASE 4P.1A final report for the
full scenario-by-scenario transcript and verdict.

FASE 4P.1B (4 required scenarios, Claude Sonnet 4.6, `--verbose`
debug-log-verified): scenario 1 (an exact repeat of the FASE 4P.1A prompt
that previously caused severe fabrication -- the model consulted
`[AVAILABLE_TOOLS_THIS_SESSION]` and correctly listed the real available
tools instead), scenario 2 (an exact repeat of the second fabrication
trigger -- real tool calls made, honest gap reporting, no fabrication),
scenario 3 (real current OEE value, correctly returned with its real
`TRUSTED` status), and scenario 4 (proactive analysis with real evidence
-- `MissingDataDetector` fired live, historical precedent correctly
separated from current cause) were all clean. **4/4, the required bar.**
