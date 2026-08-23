# MAIA Proactive Monitoring V1 (FASE 4P.2 / 4P.2A)

Status: **READY.** Governed background observation. Periodic, explicit,
bounded, deduplicated, evidence-grounded, read-only toward business
systems, silent when nothing meaningful changed. Not autonomous
execution.

FASE 4P.2A audited the model-facing tool surface and prototyped a
consolidated `maia_manage` gateway (STEP 4-8), then live-certified: in a
normal, non-confounded session, the model reliably completes
create-monitor / run-now / list-notifications tasks correctly and
honestly using the existing direct tools -- it never selected the
gateway when both were offered. Per STEP 10's own explicit branch ("if
actual invocation works reliably without a gateway, do NOT add
architecture unnecessarily"), the gateway is built, tested, and kept
available as an option, but is NOT adopted as the default model-facing
surface. See MAIA CAPABILITY SURFACE GATEWAY below for the full audit,
including a real CLI-level confound discovered along the way (the
`--tools` flag with a minimal explicit tool list, unrelated to normal
sessions) that made the STEP 3 prefix-vs-count experiment inconclusive
by itself -- reported honestly rather than papered over.

Built directly on the frozen FASE 4P.1/4P.1A/4P.1B
`ProactiveReasoningService` (unmodified) and the existing
`openjarvis.scheduler` (reused, not duplicated).

## EXISTING SCHEDULER (STEP 1 AUDIT)

Audited before writing any code. `openjarvis/scheduler/scheduler.py`'s
`TaskScheduler` is a real, working, SQLite-backed (`SchedulerStore`,
`check_same_thread=False`) background-thread poll loop with
cron/interval/once cadence, pause/resume/cancel lifecycle, and event-bus
publishing (`SCHEDULER_TASK_START`/`END`) -- already used by
`ProactiveAgent`'s own 5am digest cron registration. This is THE existing
scheduler; this phase does not create a second one.

Constraint found: `TaskScheduler._execute_task()` always calls
`system.ask(task.prompt, agent=task.agent, ...)`, which resolves an agent
class and constructs it via `agent_cls(engine, model, **kwargs)` (falling
back to `agent_cls(engine, model)` then `agent_cls()` on `TypeError`).
There is no pluggable "run this Python callable instead" hook. Resolved
by registering a new, LLM-free agent (`MonitorCheckAgent`, agent_id
`monitor_check`) rather than adding one to the scheduler -- see
MONITOR CONTRACT below.

Also audited: `EventBus` (pub/sub, reused implicitly via the scheduler's
own event publishing -- no new bus), `ApprovalStore`/`ApprovalBell`
(FASE 3B.6, a *different* propose/approve/execute queue for personal-
assistant side effects -- not reused; notifications here are informational,
never awaiting approval to execute anything), no existing "alert center"
or generic notification API beyond that. `ProactiveAgent` (personal
digest domain: email/SMS/calendar) is unrelated and untouched.

## ARCHITECTURE

```
TaskScheduler (existing, unmodified)
  -> cron/interval poll decides WHEN
  -> system.ask(f"__monitor_check__:{id}", agent="monitor_check")
       -> MonitorCheckAgent.run()  (NEW, no LLM call anywhere in it)
            -> MonitorService.run_cycle(monitor_id)  (NEW, pure Python)
                 -> _collect_evidence()   (reuses governed tool objects)
                 -> build_evidence()      (frozen, FASE 4O.6)
                 -> ProactiveReasoningService.analyze()  (frozen, FASE 4P.1)
                 -> _diff_and_notify()    (NEW: fingerprint dedup + lifecycle)
                 -> MonitorStore          (NEW: SQLite, mirrors scheduler/store.py)
```

`maia_monitor_run_now`/manual tools call `MonitorService.run_cycle()`
directly, bypassing the scheduler entirely for an on-demand check.

## MONITOR CONTRACT

`monitoring/types.py::MonitorDefinition` -- `id`, `name`,
`source_requirements` (a plain dict using EXACTLY the same keys as FASE
4P.1's `ProactiveAnalyzeTool.execute()` params: `ops_capability`,
`ops_params`, `second_brain_query`, `second_brain_domains`,
`document_query` -- reused, not reinvented, so a monitor can only ever
check what a user could ask MAIA to check directly), `enabled`, `cadence`
(`HOURLY`/`DAILY`/`MANUAL`), `detector_scope` (optional subset of the
frozen detector names), `principal` (Second Brain authorization identity
-- explicit per monitor, never an ambient fallback), `created_at`,
`last_run_at`, `last_success_at`, `status`, `bounds`
(`{timeout_seconds, max_consecutive_failures}`), `scheduler_task_id`,
`consecutive_failures`.

No free-form executable code, no user-provided Python, no arbitrary
shell command anywhere in the contract or its handling -- `source_requirements`
is validated as a small, fixed key set at creation time.

`MonitorRun` (STEP 3): `id`, `monitor_id`, `started_at`, `completed_at`,
`evidence_collected`, `insights_generated`, `errors`, `status`
(`success`/`partial`/`failed`) -- every cycle is persisted and auditable
via `MonitorStore.list_runs()`.

## SCHEDULER

Cadence maps directly onto `TaskScheduler`'s own `interval` schedule
type: `HOURLY` -> 3600s, `DAILY` -> 86400s (`monitoring/types.py::_CADENCE_SECONDS`).
`MANUAL` monitors are never registered with the scheduler at all --
only reachable via `maia_monitor_run_now`. No sub-minute cadence exists
anywhere in the vocabulary (STEP 10's explicit bound).

`MonitorCheckAgent` (agent_id `monitor_check`) is the sole integration
point: registered via `@AgentRegistry.register`, constructed with
`engine=None, model=None` accepted-and-ignored, `run()` parses the
`__monitor_check__:{id}` prompt prefix and calls `MonitorService.run_cycle()`
-- zero LLM calls, zero tool-calling loop, zero prompt engineering
involved in detection (STEP 12).

## DEDUPLICATION

Fingerprint = `ProactiveInsight.id` (already a SHA256 of detector name +
governed fields -- metric, value, threshold, period, or equivalent per
detector -- from the frozen FASE 4P.1 `_stable_id()`; never generated
prose). Reused directly, not reinvented. Two independent
`MonitorService`/`ProactiveReasoningService` instances analyzing
identical evidence produce the identical fingerprint (proven by test).

Because a detector's fingerprint typically includes the current VALUE
(e.g. `ThresholdBreachDetector`), a genuinely different value is a
genuinely different fingerprint (correctly reported as the old one
RESOLVED and a new one NEW, never miscounted as the same issue silently
"changing") -- the CHANGED transition applies specifically to the same
fingerprint reappearing with a different severity/confidence.

## STATE LIFECYCLE

Per `(monitor_id, fingerprint)`, persisted in `monitor_issue_state`:
`ACTIVE` / `RESOLVED`. Per-cycle transition labels (computed by diffing
against persisted state, never persisted themselves): `NEW`, `UNCHANGED`,
`CHANGED`, `RESOLVED`, `REOPENED`. Only NEW/CHANGED/RESOLVED/REOPENED
ever produce a `Notification` -- UNCHANGED never does, live-verified
across repeated identical cycles.

## NOTIFICATION MODEL

Internal only (STEP 6) -- `monitor_notifications` table, `Notification`
dataclass, no external send capability anywhere in this module (verified
structurally: no `execute_action`/`run_action`/`do_it`/`send_anything`/
`write_anything`-shaped tool exists). `maia_notifications_list` /
`maia_notification_get` / `maia_notification_acknowledge` are the only
ways to interact with them -- acknowledging marks a notification seen,
never resolves the underlying issue (that only happens when the evidence
itself changes).

## SOURCE GOVERNANCE

Evidence collection reuses, unmodified: `ProactiveAnalyzeTool._call_ops`
(the exact FASE 4P.1B `NOT_AVAILABLE`/`data_not_available`/`ok` handling),
`SecondBrainFindRelatedExperiencesTool` (constructed fresh per monitor
with that monitor's own configured `principal` -- never the ambient
session's), `DocumentSearchTool`. No source class's precedence or
provenance handling is touched. `build_evidence()` and
`ProactiveReasoningService` are imported and called exactly as FASE 4P.1
left them -- zero lines of either were modified for this phase.

## FAILURE HANDLING

Each of the three source kinds (OPS/Second Brain/Document) is collected
independently inside its own `try/except` -- one failing never blocks or
corrupts the others (STEP 9's "one source failing while others work").
If NOTHING was collected, detection is skipped entirely rather than
running `ProactiveReasoningService` against an empty set and implying a
check happened when it didn't -- the run is marked `failed`, never
silently `success`. A partial failure (some sources worked) still runs
detection on what succeeded and is marked `partial`, with every failure
recorded in `run.errors`. A monitor that fails `max_consecutive_failures`
times in a row (default 5) auto-disables itself (a monitor-health
signal, not a business alert) rather than continuing to fail silently
forever.

## SECURITY

- No Second Brain write path anywhere (`propose_entry`/`confirm_entry`
  are absent from `monitoring/` entirely -- verified by source inspection,
  not just behavior).
- No Action Book reference anywhere in `monitoring/` (verified by source
  inspection).
- No execution tool exists (STEP 13's hard requirement, mirrored from
  FASE 4P.1).
- Per-monitor `principal` is explicit at creation time and threaded
  directly into `SecondBrainFindRelatedExperiencesTool(principal=...)`
  for that monitor's own evidence collection -- proven by test that two
  monitors with different principals produce different, non-shared
  Second Brain tool constructions.
- `MonitorStore` uses `check_same_thread=False` from day one (the FASE
  4O.6A lesson: a real live cross-thread SQLite crash was found and
  fixed in a *different* store that lacked this from the start).

## LIVE TEST (STEP 15)

The exact lifecycle sequence STEP 15 asks for (NEW -> UNCHANGED ->
RESOLVED -> REOPENED, with exact notification counts) was verified
directly against `MonitorService` -- deterministic, repeatable, and not
dependent on live model behavior (`tests/monitoring/test_monitoring_service.py`'s
`test_d`/`test_e`/`test_g`/`test_h`, plus a standalone run producing the
identical sequence: NEW(1) -> UNCHANGED(0) -> RESOLVED(1) -> REOPENED(1)).
This is the right way to verify a deterministic backend component and
was treated as authoritative for the lifecycle/dedup requirement.

The live Claude Sonnet 4.6 test specifically for the tool-calling surface
(create a monitor, run it, report the result) surfaced a genuine, honest
finding: across 2 attempts (implicit natural phrasing, then explicit
tool-naming), the model incorrectly claimed the `maia_monitor_*` tools
were not available to it -- even when directly named. A diagnostic debug
log confirmed the tools genuinely were resolved and passed through to
tool-offering (`parsed_tools` contained all 9 monitoring tool names,
`tool_router.py`'s `always_on` set is unconditional for any
non-`ops_dynamic_*`-prefixed tool). A third test, re-running the exact
FASE 4P.1B prompt that previously worked cleanly (an unavailable
synthetic capability correctly declined), confirmed no regression in
that already-certified behavior -- but also showed the model's own
"available tools" self-report omitted every `maia_*`-prefixed tool
entirely, including the already-certified FASE 4P.1
`maia_analyze_evidence_for_insights`, while still correctly listing
`ops_dynamic_*` tools. This looks like a model-side tool-list-awareness
gap that scales with the number/novelty of `maia_*`-prefixed tools
(now 14 across FASE 4P.1+4P.2) rather than anything specific to this
phase's code -- reported honestly as an open, unresolved gap rather than
hidden or reframed as a partial success.

## MAIA CAPABILITY SURFACE GATEWAY (FASE 4P.2A)

**Tool surface audit (STEP 1).** A normal session offers 30 tools: 7
`ops_dynamic_*`, 7 `second_brain_*`, 2 `document_*`, 5 `maia_*` (FASE
4P.1 insight tools), 9 `maia_*` (FASE 4P.2 monitoring/notification
tools). 14 of 30 are `maia_*`-prefixed, largely small CRUD-shaped
list/get/create/enable/disable pairs. `tool_router.py`'s router already
exists but only scores/caps `ops_dynamic_*` candidates -- every non-
`ops_dynamic_*` tool (all 14 `maia_*` tools included) is unconditionally
`always_on`, confirmed by direct code reading, not assumption. A real,
already-proven-in-production consolidation pattern already exists in
this codebase: `memory_manage`/`user_profile_manage`/`skill_manage`, one
tool with an `action`/`operation` enum dispatching internally --
reused, not invented, for the gateway below.

**Root cause (STEP 2) -- inconclusive by the intended method, resolved
by a cleaner one.** The controlled prefix-vs-count experiment (STEP 3)
hit a genuine, separate CLI-level confound: passing a single, minimal
explicit tool name via `--tools` (bypassing the normal auto-resolved
tool set) reproducibly caused the request to return a canned "no tools
available" response *without the engine's `generate()` method ever
being called* -- confirmed by two independent instrumentation attempts
(a `logger.debug` and a raw fallback), both of which found the debug
startup log stream stops cleanly right before the point tool-name
resolution completes, with no exception surfaced and no further engine-
level log line ever appearing. This happens even for a single NON-
`maia_*` tool (not reproduced in isolation for time reasons, but the
observed break point is agnostic to tool name/prefix), so it is **not**
attributable to `maia_*` naming -- it is a `--tools`-flag-with-a-minimal-
set issue, orthogonal to this phase's actual question, and left as an
honestly-reported open finding rather than force-fit into options A-F.
The temporary diagnostic edits were fully reverted (`git diff` on
`cloud.py`/`ask.py` confirms zero net change).

Redirecting to the **normal, non-`--tools`-flag session** (the
representative, real-world path) gave clean, unconfounded signal
instead: tools are genuinely offered (unchanged from FASE 4P.2's own
finding) and, in 3 live scenarios run this phase with no `--tools`
override, actual invocation of the direct `maia_monitor_*`/
`maia_notifications_list` tools **worked correctly and honestly every
time** -- closer to option E (self-report/selection variance) than a
structural blocker, and not clearly reproducible as a persistent,
severe failure once the CLI-flag confound is removed from the picture.

**Gateway (STEP 4-8).** Built anyway, since STEP 1's audit found a
strong, already-proven precedent for consolidation and the original
FASE 4P.2 finding was concerning enough to warrant having the option
ready: `tools/maia_manage.py::MaiaManageTool`, one tool, `operation`
enum (`INSIGHT_ANALYZE/LIST/GET`, `ACTION_PROPOSALS_LIST/GET`,
`MONITOR_LIST/GET/CREATE/ENABLE/DISABLE/RUN_NOW`,
`NOTIFICATION_LIST/GET/ACKNOWLEDGE`), a closed allowlist (no arbitrary
method names, no reflection), per-operation argument validation. Every
operation constructs and calls the exact same already-tested tool object
(`ProactiveAnalyzeTool`, `MonitorRunNowTool`, etc.) -- zero business
logic duplicated. `ToolResult.tool_name` on every gateway response is
still the real underlying tool name (e.g. `maia_monitor_run_now`), so
the frozen FASE 4P.1B `[ACTUALLY_EXECUTED_TOOLS]` claim-integrity
ledger still records exactly what ran, never a vague "gateway did
something." 21 tests (`tests/tools/test_maia_manage.py`, STEP 8 A-P)
cover routing, validation, principal propagation, per-monitor privacy
isolation, and the no-execution/no-Second-Brain-write/no-Action-Book-
write boundaries -- all passing.

**Live test (STEP 9).** 3 scenarios (create+run monitor, repeat for
consistency, list notifications), no `--tools` override, `maia_manage`
present alongside the 14 direct tools. All 3 completed correctly and
honestly (real monitor created, real cycle run, real -- correctly empty
-- notification list reported) via the **direct tools**; `maia_manage`
was not selected in any of the 3. One unrelated finding recurred in
scenario 2: during a later exploratory turn (wanting to check Second
Brain and Document Knowledge further), the model emitted a malformed
pseudo-XML tool-call fragment in prose instead of a real structured
call -- the same class of issue documented in FASE 4P.1B's KNOWN
LIMITATIONS, not a monitoring-specific regression, and not tied to the
gateway.

**Decision (STEP 10).** Per STEP 10's own explicit branch: actual
invocation works reliably without the gateway (3/3 live, unconfounded),
so the gateway is **not** made the default model-facing surface. It
remains registered, tested, and available (STEP 6 -- nothing was
removed) for future use (CLI, internal calls, a hypothetical future
integration that specifically wants one consolidated entry point), but
`resolve_tool_names()`'s default union still offers all 15 (14 direct +
`maia_manage`) rather than replacing the 14 with the gateway alone.

## KNOWN LIMITATIONS

- Performance is dominated by SQLite commit-per-write overhead (~75ms/
  monitor per cycle at 10-100 monitors measured), not detection logic
  (which remains <1ms even at 1000 evidence items, per FASE 4P.1's own
  measurement) -- fine at V1 scale (cadence is hourly/daily, never
  sub-minute), not optimized further per STEP 16's explicit instruction.
- `CHANGED` transitions are architecturally rare in V1: most detectors'
  fingerprints include the current value, so a value change usually
  produces a new fingerprint (RESOLVED + NEW) rather than the same
  fingerprint changing severity/confidence. The diff logic correctly
  handles CHANGED when it does occur (verified directly against the
  diff function), but live detector behavior rarely exercises it --
  reported honestly rather than engineered around.
- `MonitorRun`/notification history has no automatic pruning -- an
  unbounded number of runs/notifications will accumulate in
  `monitoring.db` over time; acceptable for V1, worth revisiting if this
  becomes a long-lived production feature.
- The scheduler integration (`agent="monitor_check"`) has not been
  exercised through a live, actually-running `TaskScheduler` background
  thread in this phase's testing (that would require starting a real
  poll loop and waiting through a full interval) -- `MonitorService.run_cycle()`
  and `MonitorCheckAgent.run()` are both directly tested and live-tested;
  the scheduler's own poll-and-dispatch mechanism is FASE-4-already-
  existing, unmodified code, not re-verified here.
