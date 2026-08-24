# MAIA Operational Copilot V1 (FASE 4Q.1 / 4Q.1A)

Status: **FROZEN.** A real, persistent, six-turn Italian
conversation ran against genuine Claude Sonnet 4.6 (`--engine cloud
--model claude-sonnet-4-6`) after FASE 4Q.1A closed a real engine-
selection parity gap that had silently routed an earlier attempt to a
local model instead. This phase does not introduce a new orchestration
architecture -- it audits, wires, and tests what FASE 4O.x/4P.x already
built (`OrchestratorAgent`'s function-calling loop, `operational_evidence.py`'s
multi-source composition, `proactive_insight.py`'s deterministic
detectors, `monitoring_tools.py`, `governed_actions/`), and fixes exactly
two real, narrow gaps found along the way (see KNOWN LIMITATIONS /
CLAIM-INTEGRITY FIX below).

## TURN LIFECYCLE

Already implemented, not rebuilt. One coherent lifecycle inside
`OrchestratorAgent._run_function_calling` (`agents/orchestrator.py`):

```
UNDERSTAND        -- the model reads the user's turn + prior conversation
PLAN EVIDENCE      -- the model's own tool selection, bounded by
                      tool_router.select_relevant_tools() (top-N, bounded
                      expansion) and a one-shot, non-forcing coverage
                      nudge (FASE 4O.6A) if an always-on family (Second
                      Brain/Document Knowledge) was never attempted
COLLECT            -- tool execution (parallel when >1 call per turn)
COMPOSE            -- agents/operational_evidence.py::build_evidence() +
                      render_note(), injected as [OPERATIONAL EVIDENCE
                      COLLECTED THIS TURN]
VALIDATE CLAIMS    -- [ACTUALLY_EXECUTED_TOOLS] ledger (generic, every
                      tool call, not just governed ones) +
                      [GOVERNED_ACTION_EVENT] (governed-action outcomes
                      only) + [GOVERNED_PROACTIVE_ANALYSIS] (when
                      activated)
ANSWER             -- final natural-language generation
PROPOSE NEXT ACTION -- optional: a ProposedAction surfaced by proactive
                      insight, or a governed action the model prepares
```

None of this machinery is exposed to the user -- the user only ever sees
natural language and, when relevant, a governed approval prompt.

## EVIDENCE PLANNING

Reused verbatim from FASE 4O.6/4O.6A -- no new evidence-source logic
was written this phase. `operational_evidence.py::build_evidence()`
classifies every tool result into one of five source classes
(`CURRENT_OPERATIONAL_FACT`, `KNOWLEDGE_DEFINITION`,
`HISTORICAL_EXPERIENCE`, `DOCUMENT_EVIDENCE`, plus a `LIMITATIONS` list
for reported gaps) purely from *which tool* returned it and *its own
result shape* -- never from content inference. There is no separate
"evidence planner" component: the model's own tool selection *is* the
plan, bounded by the router and nudged (never forced) toward unattempted
always-on families. Verified this phase (`tests/agents/
test_maia_operational_copilot.py`, letters A-F): a simple OPS-only
question calls exactly one tool; a three-source question composes all
three when the model attempts them; a nudge does not force a call the
model has already declined.

## SOURCE SELECTION

Simple questions do not over-call. Complex questions compose correctly.
Verified live: turn 1 ("Come sta andando l'OEE?") called OPS only and
got both the current value AND the built-in period-over-period
comparison in one capability response (`ops.production.get_kpi` returns
`value`/`previous_value`/`absolute_delta` together) -- independently
re-verified against the real OPS Bridge post-session: July 2026 OEE =
88.61%, previous (June) = 89.64%, delta = -1.03pp, exactly matching what
the live session reported. Turn 3 correctly queried Second Brain alone
(historical question); turn 4 correctly queried Document Knowledge
(procedure question) without re-querying OPS unnecessarily.

## CONTEXT CONTINUITY

Reused, not rebuilt: `jarvis chat`'s REPL (`cli/chat_cmd.py`) already
maintained an in-process `history: List[Message]` and threaded prior
turns into each new call via `AgentContext.conversation` -- this was
already correct code before this phase; `jarvis ask` (single-shot,
stateless by design) was never in scope for multi-turn continuity.
Verified structurally (`test_maia_operational_copilot.py`, letters I/J/K:
prior turns are present and correctly ordered in the messages sent to
the engine) and live: turn 2 ("E rispetto al mese scorso?") correctly
reused turn 1's already-collected OEE context rather than re-asking or
losing it; turn 5 ("Quindi cosa mi consigli?") correctly synthesized
across all four prior turns.

## PROACTIVE HANDOFF

Reused unmodified from FASE 4P.1 (`agents/proactive_insight.py`) --
deterministic detectors only (`ThresholdBreachDetector`,
`MissingDataDetector`, `ConflictDetector`, `CertifiedAlertDetector`),
never an LLM judgment call, activated only on explicit proactive intent,
an explicit `maia_analyze_evidence_for_insights` call, or a
self-certified alert source -- never on every answer. Not separately
re-tested this phase (frozen, unmodified); its own test suite is part of
the STEP 15/STEP 10 regression scope.

## MONITORING HANDOFF

`"Controllalo nei prossimi giorni"` correctly mapped onto the existing
`maia_monitor_create` tool (`monitoring_tools.py`) -- no duplicate
monitoring logic was written. Live-verified: a real monitor was created
and independently confirmed in the real `monitoring.db`:

```json
{
  "id": "170e4a4f6e2d4e50",
  "name": "Monitoraggio OEE giornaliero",
  "enabled": true,
  "cadence": "DAILY",
  "status": "active",
  "source_requirements": {
    "ops_capability": "ops.production.get_kpi",
    "ops_params": {"metric": "OEE"},
    "second_brain_query": "calo OEE efficienza produzione"
  },
  "principal": "monitor:default",
  "created_at": "2026-08-24T17:36:31.362749+00:00",
  "last_run_at": null,
  "last_success_at": null,
  "scheduler_task_id": null
}
```

**Runtime guarantee gap found and disclosed (not fixed structurally, per
instruction: "do not redesign monitoring")**: `scheduler_task_id` is
`null` -- `MonitorService.create_monitor()` only registers a scheduler
task when a real `TaskScheduler` instance was passed into
`MonitorService.__init__(scheduler=...)`. Audited every construction
site in the codebase (`grep -rn "MonitorService(" src/`): all ten of
them, in every tool and in `MonitorCheckAgent`'s own default, construct
`MonitorService()` with no scheduler argument. **No code path anywhere
in this codebase currently wires a real scheduler into any
model-facing-tool-created monitor.** So *"the system will check OEE
every day"* was **not currently a runtime-guaranteed claim** -- the
monitor is genuinely saved and enabled, but nothing will invoke it
automatically until a real scheduler is wired to it (a future phase's
work, deliberately not attempted here). This is exactly the kind of
DORMANT-BUT-PLANNED gap the codebase already documents elsewhere
(`agents/monitor_check_agent.py`'s own docstring: *"the scheduler's
cron/interval polling is the only thing that decides *when* this
runs"*) -- the wiring code exists and is exercised by tests
(`monitoring/service.py`'s scheduler-integration branches), it is simply
never instantiated with a real scheduler in the current default tool
registration.

**Fix applied (claim-boundary only, per instruction)**: `_monitor_full()`
now exposes `scheduler_task_id` in every `maia_monitor_create` /
`maia_monitor_get` result, and `MonitorCreateTool`'s spec description and
result both now carry an explicit, structural note when
`scheduler_task_id is None`, instructing the model to tell the user the
monitor was *saved*, not that it is *actively running*, and that any
future notification is never pushed into chat -- it must be checked
explicitly via `maia_notifications_list`. No scheduler wiring, no new
execution logic, no monitoring redesign.

## ACTION HANDOFF

Reused from FASE 4P.3, unmodified in mechanism. One real, narrow gap
found and fixed: the runtime-only affirmative-phrase list
(`governed_actions/runtime_hook.py::_AFFIRMATIVE_PHRASES`) had "procedi"
(singular imperative) but not "procediamo" (the natural first-person-
plural "let's proceed" this very phase's own spec uses as its own
action-handoff example). Added `"procediamo"` to the fixed phrase list.
Safe by construction: the runtime hook only ever acts when exactly one
action is genuinely `PENDING_APPROVAL` for the real principal; otherwise
it is inert regardless of the phrase list's contents. Verified by test
(`test_n2_unambiguous_procediamo_resolves_the_one_pending_action`) and
the pre-existing ambiguity test (`test_n_ambiguous_action_two_pending_no_auto_approval`)
still holds -- two pending actions still correctly refuse to
auto-resolve.

## CLAIM INTEGRITY

Reused unmodified: the generic `[ACTUALLY_EXECUTED_TOOLS]` ledger
(built by `proactive_insight.py::render_tool_execution_integrity()`,
listing every tool call in the turn, not only governed ones) and the
narrower `[GOVERNED_ACTION_EVENT]` block are both independent,
already-frozen mechanisms; nothing here was changed.

**Full six-turn transcript review**, cross-checked against the real
sources (not narrative-trusted):

| Turn | Claim | Verification | Classification |
|---|---|---|---|
| 1 | OEE July 2026 = 88.61%, June = 89.64%, delta = -1.03pp | Independently re-queried the real OPS Bridge post-session -- exact match | **SUPPORTED** |
| 2 | Repeated the certified comparison for "mese scorso" | Same certified data, correctly reused via context continuity | **SUPPORTED** |
| 3 | "No similar problems found in the past" (Second Brain) | Real Second Brain store independently confirmed to have 0 total entries | **SUPPORTED** (trivially true) |
| 4 | "No procedure found" (Document Knowledge) | Real document workspace independently confirmed to have 0 documents | **SUPPORTED** (trivially true) |
| 5 | Recommended deeper OEE-component analysis before corrective action | Framed as advice/synthesis, not a certified fact -- appropriate | **SUPPORTED** |
| 6a | "I created a daily OEE monitor" (id, cadence, status) | Independently confirmed in the real `monitoring.db`, exact id/name/cadence match | **SUPPORTED** |
| 6b | "The system will check OEE every day" (implied automatic execution) | `scheduler_task_id=null`; no code path wires a scheduler into any model-facing monitor | **OVERSTATED** |
| 6c | "You'll receive an alert directly here on Jarvis" | No push/delivery mechanism exists anywhere in the monitoring subsystem into a chat session -- notifications are pull-only, and even that requires a cycle to have run first | **UNSUPPORTED** |

Items 6b/6c are the claim-boundary fix applied this phase (see MONITORING
HANDOFF above) -- future monitor creations will carry the structural
`scheduler_task_id`/`execution_note` fields so the model can no longer
make this specific overstatement without contradicting its own tool
result.

Item 4 (Second Brain procedure-write suggestion): reviewed the
underlying capability (`second_brain_propose_entry` +
`second_brain_confirm_entry`, `tools/second_brain_tools.py`) -- it is a
genuine, real, model-callable two-step capability (propose never
persists; confirm is the only call that persists), gated by *prompt-level*
discipline (the tool descriptions explicitly instruct the model to only
call `confirm_entry` after a real user reply, never as a same-turn
follow-up to its own proposal) rather than a *structural* runtime gate
like governed actions' approve/execute split. Nothing in the reported
transcript indicates the model actually called either tool -- if it
merely suggested creating a procedure entry in natural language, that
was correctly framed as a suggestion requiring the capability's own
confirm step, not a claim of having already written anything.

## FAILURE BEHAVIOR

Reused, not rebuilt: a failed/unavailable source (`success=False`, or an
OPS `status != "ok"` envelope) is never fabricated or silently
substituted -- `build_evidence()` skips a failed `ToolResult` entirely
(no `FACTS` entry invented) and a `status != "ok"` envelope adds an
explicit `LIMITATIONS` line. Verified this phase
(`test_o_source_tool_failure_not_fabricated_not_silently_substituted`,
`test_h_missing_ops_fact_not_fabricated`).

## LIVE CERTIFICATION

**FASE 4Q.1's first attempt did not actually reach Claude** -- FASE
4Q.1A found and fixed a real parity gap: `cli/chat_cmd.py` never passed
`model` into `get_engine()`, so FASE 4P.3A's strict engine+model pairing
guard (already frozen and working for `jarvis ask`) could never activate
for `jarvis chat`. An explicit `--engine cloud --model claude-sonnet-4-6`
request silently ran against a local `llamacpp` engine instead for
~1h40 with no error. Fixed by mirroring `ask.py`'s exact
`effective_engine_key`/`selection_model` computation and
`EngineConnectionError` handling in `chat_cmd.py` (see
`docs/CHAT_ENGINE_SELECTION_PARITY.md` for full detail), plus adding a
configurable `--turn-timeout` (default 300s) so no single turn can again
consume unbounded wall-clock time.

**The real live certification then completed successfully**:

```
Engine: cloud   Model: claude-sonnet-4-6   Agent: orchestrator
```

Six-turn Italian conversation, one persistent `jarvis chat` session:

1. *"Come sta andando l'OEE?"* -> OEE July 2026 = 88.61%, previous month
   = 89.64%, delta = -1.03pp (CURRENT_OPERATIONAL_FACT, independently
   re-verified against the real OPS Bridge post-session).
2. *"E rispetto al mese scorso?"* -> correctly retained and restated the
   OEE context from turn 1 (context continuity, no re-fabrication).
3. *"Abbiamo avuto problemi simili in passato?"* -> correctly queried
   Second Brain; no historical precedent found (independently confirmed:
   the real store has zero entries).
4. *"C'è qualche procedura che può aiutarmi?"* -> correctly checked
   Document Knowledge (and, per the transcript, Second Brain again); no
   procedure found (independently confirmed: the real workspace has zero
   documents).
5. *"Quindi cosa mi consigli?"* -> preserved full prior context;
   recommended deeper OEE-component analysis before jumping to
   corrective action, rather than inventing a root cause from a single
   domain's data.
6. *"Controllalo nei prossimi giorni."* -> correctly invoked
   `maia_monitor_create`; a real, persistent DAILY monitor was created
   (id `170e4a4f6e2d4e50`) -- see MONITORING HANDOFF above for the
   claim-boundary caveat this turn's narration was found to overstate
   (automatic execution / in-chat push notification), now fixed at the
   tool-result/description level.

No fabricated tool execution occurred at any point (verified both via
the generic `[ACTUALLY_EXECUTED_TOOLS]` mechanism's own design and by
independently cross-checking every retrievable claim against the real
sources above). No autonomous business execution occurred -- the only
side effect of the entire six-turn session was the one, expected,
user-requested monitor creation.

## PERFORMANCE

Not separately re-measured this phase beyond what the live session
itself demonstrated: source selection stayed proportionate to each
question (single-source for simple questions, composed only where the
question genuinely spanned sources), consistent with the deterministic
test matrix's own selectivity checks (letters F, Q in
`test_maia_operational_copilot.py`).

## KNOWN LIMITATIONS

- **Monitoring execution is not yet automatically scheduled.** Creating
  a monitor via `maia_monitor_create` persists it correctly but does not
  wire a scheduler task -- `scheduler_task_id` is `null` for every
  monitor created through any currently-registered tool path in this
  codebase. A future phase would need to pass a real `TaskScheduler`
  instance into whichever `MonitorService` construction backs the live
  tool registration. Deliberately not attempted here (out of scope:
  "do not redesign monitoring").
- **Notifications are pull-only.** Even once a monitor cycle does run
  and produces a notification, nothing pushes it into an active or
  future `jarvis chat` session -- it must be explicitly retrieved via
  `maia_notifications_list`/`maia_notification_get`. No push/websocket/
  SSE delivery path exists in the monitoring subsystem.
- **Second Brain procedure-writes are prompt-gated, not structurally
  gated.** Unlike governed actions' approve/execute split (a runtime-only,
  model-uncallable boundary), `second_brain_confirm_entry` IS technically
  callable by the model itself -- the "only after a real user reply"
  rule lives in the tool description, not in code. This is an existing,
  frozen design (FASE 4P.1), not something this phase changed; noted
  here because it surfaced during the claim-integrity review.
- `docs/MAIA_MULTI_SOURCE_REASONING_V1.md`'s own known limitation
  (occasional wrong-capability selection / malformed tool-call parsing
  under long repetitive-failure conversations) was not observed in this
  six-turn session but remains an open, separately-scoped issue.
- Outlook 4P.4 remains parked, untouched, uncommitted throughout this
  entire phase (verified via `git status` at every checkpoint).
