# MAIA Monitoring Runtime & Scheduler V1 (FASE 4Q.2)

Status: **FROZEN.** Deterministically certified + live-certified against
the real scheduler runtime in an isolated environment.

**Explicit production-activation decision (final, this phase's own
closeout):** the real certification monitor (`170e4a4f6e2d4e50`) is NOT
activated, `config.scheduler.enabled` is NOT turned on globally, and no
persistent `jarvis scheduler start` process is being started on the
user's PC as part of this phase. The engine is certified as working;
turning it on in production is a deliberately separate, later decision
-- see PRODUCTION ACTIVATION below.

**MAIA and OPS ONE are not separate final products.** This
OpenJarvis-Vanilla-Claude working copy is temporary infrastructure for
MAIA engine development. Before deployment, a dedicated consolidation
phase will merge OPS ONE + OpenJarvis + MAIA into one canonical
integrated product/repository/runtime. Everything built this phase was
kept backend-owned and CLI-agnostic specifically so that consolidation
stays clean -- see OPS ONE FUTURE INTEGRATION below, unchanged from the
phase's own design intent.

Closes the gap FASE 4Q.1's live certification surfaced: a persisted,
enabled, DAILY monitor had `scheduler_task_id=null` because no code
anywhere in the codebase ever constructed `MonitorService` with a real
scheduler. This phase does not build a new scheduler -- it wires the
existing, already-correct `TaskScheduler` (`scheduler/scheduler.py`)
into `MonitorService`, and fixes the two real gaps found in
`MonitorService`'s own binding logic along the way.

## SCHEDULER ARCHITECTURE

**Exactly one canonical scheduler class already existed**:
`scheduler/scheduler.py::TaskScheduler`, backed by a persistent SQLite
`SchedulerStore`. This is distinct from `agents/scheduler.py::AgentScheduler`
(an in-memory-only, unrelated tick scheduler for the separate managed-agent/
channel runtime -- audited and confirmed it has no `create_task`/
`resume_task`/`pause_task` API and was never a candidate). No second
scheduler was created this phase.

`TaskScheduler` was already fully capable of everything `MonitorService`
needed (`create_task`, `pause_task`, `resume_task`, `cancel_task`,
deterministic pinned `task_id`, a background poll thread bounded by
`poll_interval`, SQLite persistence surviving restart) -- **the entire
gap was that nothing ever constructed one and passed it to
`MonitorService`.** Confirmed by auditing every `MonitorService(` call
site in the codebase (10 total, across every monitoring tool and
`MonitorCheckAgent`'s own default): all ten used the bare
`scheduler=None` default.

One small, generic addition was made to `TaskScheduler` itself:
`get_task(task_id) -> Optional[ScheduledTask]` -- every other CRUD verb
already existed (create/list/pause/resume/cancel) except a single-task
lookup, which reconciliation genuinely needs and which is clearly
generic (not monitor-specific).

## SCHEDULER OWNERSHIP

`agents/monitoring/service.py::get_default_task_scheduler()` (new) is
the one place that decides how a CRUD-only `TaskScheduler` gets
constructed for model-facing use: respects `config.scheduler.enabled`
(default `False` -- unchanged, not touched by this phase), points at
the same `SchedulerStore` db path (`config.scheduler.db_path` or
`~/.openjarvis/scheduler.db`) every other scheduler consumer already
uses, and never raises (falls back to `None`, preserving the exact
pre-4Q.2 behavior when anything goes wrong). Wired into exactly the
three tools that actually create/enable/disable monitors
(`MonitorCreateTool`, `MonitorEnableTool`, `MonitorDisableTool`,
`tools/monitoring_tools.py`) via a small `_default_scheduled_monitor_service()`
helper -- every other `MonitorService()` call site (list/get/run_now/
notifications, and every test) deliberately keeps the bare
`scheduler=None` default, so no test or read-only tool gets a surprise
side effect against a real store.

## MONITOR BINDING

`MonitorService._ensure_scheduler_task(mon)` (new, shared) is the one
place that decides how a monitor's `ScheduledTask` looks: deterministic
`task_id=f"monitor:{mon.id}"`, idempotent (checks `get_task()` first --
reuses if present, creates only if genuinely missing). Used by
`create_monitor()`, `enable_monitor()`, and `reconcile_scheduler_bindings()`
so there is exactly one code path deciding this, not three
divergent ones.

**Real gap found and fixed**: `enable_monitor()` previously only called
`resume_task()`, silently swallowing `KeyError` -- correct for a monitor
that already had a task (e.g. disable → enable), but wrong for a
monitor *created while disabled* (which never got a task at creation
time at all, since `create_monitor()` only creates one when
`enabled=True` at creation). Such a monitor could be "enabled" forever
while never actually getting `scheduler_task_id` set. Fixed:
`enable_monitor()` now calls `_ensure_scheduler_task()` first (create-if-
missing), then resumes.

`MANUAL` monitors and monitors created `enabled=False` never receive a
task. Disabling an automatic monitor **retains** `scheduler_task_id`
(the underlying `ScheduledTask` is paused, not deleted) so a later
re-enable resumes the same task instead of creating a second one.

## STARTUP RECONCILIATION

`MonitorService.reconcile_scheduler_bindings()` (new). Called exactly
once, at the one place that legitimately represents "the monitoring
runtime is starting" -- `jarvis scheduler start`
(`cli/scheduler_cmd.py::scheduler_start()`), right before
`sched.start()`. Best-effort (a failure here never prevents the
scheduler daemon itself from starting).

For every monitor: if it should be active (enabled, non-MANUAL) and has
no valid active task, create or resume one; if it should NOT be active
(disabled or MANUAL) but has a lingering active task (an orphan), pause
it. **Idempotent by construction** -- every branch either finds an
already-correct state and does nothing, or performs the exact same
deterministic-task-id operation `create_monitor`/`enable_monitor` already
use. Calling it 1, 2, or 100 times in a row produces identical end state
and zero duplicate `ScheduledTask` rows (`task_id` is a SQLite PRIMARY
KEY -- a duplicate is structurally impossible, not just avoided by
convention). Live-verified: two consecutive `jarvis scheduler start`-
equivalent startups against the same isolated db produced a no-op
second reconciliation (`{"created": [], "repaired": [], "paused": []}`).

## CADENCE

Unchanged, reused exactly: `HOURLY` = 3600s, `DAILY` = 86400s
(`monitoring/types.py::_CADENCE_SECONDS`), mapped to `TaskScheduler`'s
own `schedule_type="interval"`. No sub-minute production cadence exists
or was added.

## ENABLE/DISABLE

See MONITOR BINDING above. Live/deterministically verified: enabling
twice, disabling then re-enabling, and enabling a monitor that was
created disabled all converge to exactly one valid `ScheduledTask` --
never zero (the old bug), never two.

## MISSED RUN POLICY

Audited `TaskScheduler`'s existing behavior rather than inventing new
backfill logic (per instruction). `_compute_next_run()` for an interval
task always computes `now + interval` at the moment a run completes (or
a task is resumed) -- never from the task's own last *scheduled* time.
Combined with `next_run` being a single field per task row (not a
queue), a task that was overdue for a long time (e.g. the process was
offline overnight) fires **exactly once** on the next poll cycle after
restart, then reschedules fresh from that moment -- there is no storm of
catch-up executions for the same missed occurrences, by construction of
the existing schema. This is the correct, already-existing, "simple,
bounded" behavior the phase asked for; nothing needed to change here.

## CONCURRENCY

New: `MonitorStore.try_claim_run(monitor_id, run_id, stale_after_seconds)`
/ `release_run(monitor_id, run_id)`, backed by a new, separate
`monitor_run_locks` table (not a column on `monitors` -- avoids any
schema migration on an existing real `monitoring.db`). Claiming is
atomic (a SQLite `PRIMARY KEY` insert either succeeds or raises
`IntegrityError` -- no read-then-write race). A lock older than
`monitor.bounds.timeout_seconds` is deleted before each claim attempt,
so a crash mid-run does not permanently wedge the monitor -- the next
attempt after the bound simply steals the stale lock (STEP 9's "restart
during run", STEP 11's "slow concurrent run").

`MonitorService.run_cycle()` now claims before doing any work and always
releases in a `finally` block. A failed claim returns immediately with a
new `RUN_STATUS_SKIPPED` result -- persisted (auditable) but never
collects evidence, never runs detection, never touches issue state or
notifications. Live/deterministically verified: a manually pre-claimed
lock causes a concurrent `run_cycle()` call to skip cleanly with zero
tool calls made; a stale lock (older than the bound) is recoverable.

## FAILURE HANDLING

Unchanged, reused: a source reporting `status != "ok"` (e.g. OPS
unreachable) legitimately produces an honest `MissingDataDetector`
insight (severity `INFO`) -- reporting a gap is not fabrication; a
*business value* is never invented for a source that reported it has
none. Repeated consecutive failures still auto-disable a monitor after
its own configured bound (unchanged) -- **newly fixed this phase**: when
that auto-disable fires, the monitor's scheduler task is now also
paused (previously it stayed active, an orphan-task case identical to a
manual disable that forgot to pause).

## PRINCIPAL BINDING

Unchanged, already correct: `_collect_evidence()` explicitly passes
`monitor.principal` (never ambient/whoever's logged in) to
`SecondBrainFindRelatedExperiencesTool`. Verified this phase with two
monitors under different principals executed back-to-back -- each
reached Second Brain under its own, correctly isolated principal, never
the other's. OPS and Document Knowledge calls carry no principal concept
in this codebase (capability-level governance, not per-principal), so
there was nothing further to bind there.

## OPERATING REQUIREMENTS

**Exact answer to "what must be running":**

- **`jarvis chat` / `jarvis ask` open**: monitors can be *created,
  enabled, disabled* (persisted correctly, real `scheduler_task_id`
  assigned when `config.scheduler.enabled=True`) -- but **no automatic
  execution happens from these processes**. They never poll, never fire
  a task.
- **`jarvis serve` (the backend server)**: also does **not** run the
  monitoring scheduler -- audited `cli/serve.py` directly: it starts
  `AgentScheduler` (the unrelated managed-agent tick scheduler) but
  never touches `system.scheduler` (the real `TaskScheduler`).
- **The only process that actually executes scheduled monitor
  checks is `jarvis scheduler start`** (`cli/scheduler_cmd.py`) -- a
  separate, standalone, foreground daemon command that must be run
  explicitly. It builds a real `JarvisSystem`, a real `TaskScheduler`
  bound to it, runs startup reconciliation once, then polls forever
  (Ctrl+C/SIGTERM to stop; already Windows-safe -- falls back to a
  sleep loop where `signal.pause()` is unavailable).

So: **(B) "while backend/server is running" is FALSE as currently
wired** -- this was worth stating precisely rather than assuming, since
it's the opposite of what might be expected. The correct answer is a
distinct **(C) separate service** (`jarvis scheduler start`), not (A)
chat-session-scoped and not (D) deployment-only -- it works today, it
just needs its own running process, exactly like `jarvis scheduler
start` already existed to do for any other scheduled prompt (this
phase gave monitors a real hook into that same existing daemon, not a
new one).

## OPS ONE FUTURE INTEGRATION

Everything added this phase lives in backend-owned modules
(`monitoring/service.py`, `monitoring/store.py`, `scheduler/scheduler.py`)
with no CLI-only logic buried in `cli/scheduler_cmd.py` beyond the one
reconciliation *call* (the reconciliation *logic* itself is a
`MonitorService` method, callable from anywhere -- a future OPS ONE
integration point, an HTTP endpoint, or a different daemon entry point
could all call `reconcile_scheduler_bindings()` identically). Monitor
definitions, run status, and notification state are all plain,
already-serializable dataclasses (`to_dict()`/`from_dict()`) exactly as
before -- nothing about this phase's wiring is OPS ONE-integration-
hostile; no new coupling to the CLI, no new format OPS ONE would need to
learn beyond what `MonitorDefinition`/`MonitorRun`/`Notification`
already exposed.

## LIVE CERTIFICATION

Fully isolated (tempfile `MonitorStore` + tempfile `SchedulerStore`,
never the real dbs). A safe, read-only real OPS capability
(`ops.production.get_kpi`) against the live OPS Bridge already confirmed
reachable. `system.ask()` was a minimal, faithful shim reproducing
exactly the documented contract `MonitorCheckAgent`'s own docstring
specifies (`agent_cls(engine, model, **kwargs).run(prompt)`) rather than
a full `JarvisSystem` -- stated explicitly, not left implicit; `MonitorCheckAgent`
is LLM-free regardless, so engine/model are never actually used either
way.

1. Created an enabled `HOURLY` monitor -> real `scheduler_task_id`
   assigned immediately.
2. Started the real `TaskScheduler` background poll thread
   (`poll_interval=1s`, a demo-only override of the CLI's own already-
   configurable `--poll-interval`, not a production cadence change).
3. **The task fired automatically within the poll window -- no manual
   `run_now` call was ever made.**
4. A real `MonitorRun` was persisted (`status=success`).
5. A second automatic fire produced zero additional notifications
   (unchanged-state dedup holding under real automatic execution, not
   just a unit test).
6. Stopped the scheduler, constructed entirely fresh `SchedulerStore`/
   `TaskScheduler`/`MonitorService` objects against the same db files
   (simulating a process restart), and confirmed the task still existed,
   was still `active`, and reconciliation against it was a clean no-op.

All isolated temp db files were deleted after the run.

## PERFORMANCE

| Monitors | Create (total) | Reconcile (no-op) | Due-task lookup |
|---|---|---|---|
| 10 | 95ms (9.5ms/monitor) | 4.5ms | 0.6ms |
| 100 | 1067ms (10.7ms/monitor) | 21ms | 1.2ms |

Scales linearly, no optimization needed at this scale (human-paced
monitoring, not high-throughput) -- none attempted, per instruction.

## CERTIFICATION MONITOR DECISION

The real certification monitor (`170e4a4f6e2d4e50`, `Monitoraggio OEE
giornaliero`, `enabled=true`, `cadence=DAILY`) was **deliberately not
touched**. Inspected what reconciliation would do if actually run
against it right now: `config.scheduler.enabled` is `False` in this
environment (the existing, untouched default), so
`get_default_task_scheduler()` returns `None` and any reconciliation
attempt against the real store would be a genuine no-op today --
`scheduler_task_id` would stay `null` unless BOTH (a)
`config.scheduler.enabled` is explicitly turned on, AND (b)
reconciliation is actually invoked against the real `monitoring.db` +
real `scheduler.db` (i.e. `jarvis scheduler start` is actually run for
real). Neither was done. **Explicit authorization is requested before
either of those two things happens** -- turning this specific real OEE
monitor into an actively, automatically scheduled DAILY check is a
distinct decision from certifying that the engine can do so.

**Final decision (this phase's own closeout, superseding the request
above -- no longer pending):** the answer is no, not yet, and not as
part of this phase. `170e4a4f6e2d4e50` stays exactly as it was --
`enabled=true`, `cadence=DAILY`, `scheduler_task_id=null` -- verified
unchanged at closeout. `config.scheduler.enabled` stays `False`. No
`jarvis scheduler start` process was started on the user's PC.

## PRODUCTION ACTIVATION

Stated explicitly, not left implicit:

- **Automatic monitoring requires the `TaskScheduler` runtime to
  actually be running** -- specifically the standalone `jarvis
  scheduler start` daemon process (see OPERATING REQUIREMENTS above).
  Nothing else in this codebase runs it, by design.
- **The current local configuration keeps scheduling disabled**
  (`config.scheduler.enabled=False`, the existing default, untouched by
  this phase). Monitors can still be created/enabled/disabled and are
  correctly persisted; they simply do not receive a live
  `scheduler_task_id` until this flag is turned on.
- **No persistent scheduler service is being activated on the user's
  PC** as part of this phase or its closeout. The engine is certified
  end-to-end (deterministically and live, in isolation) -- turning it on
  for real, ongoing use is a separate, later, explicit decision.
- **Production scheduler activation belongs to the future integrated
  OPS ONE + MAIA backend runtime**, not to this temporary
  OpenJarvis-Vanilla-Claude working copy. When OPS ONE + OpenJarvis +
  MAIA are consolidated into one canonical product, that is the
  intended place to decide how and where the scheduler daemon actually
  runs in production (a managed service, a container, etc.) --
  everything built this phase (backend-owned `MonitorService`/
  `TaskScheduler` logic, no CLI-only entanglement) was deliberately kept
  ready for that move without requiring rework.
- **Notifications remain internal/pull-only** (`maia_notifications_list`/
  `maia_notification_get`) until a dedicated future notification-delivery
  phase -- unchanged by this phase, stated here again for completeness
  since it's directly adjacent to "will I be told when something
  happens."

## KNOWN LIMITATIONS

- `config.scheduler.enabled` defaults to `False` -- monitors created via
  `jarvis chat`/`jarvis ask` today will still get `scheduler_task_id=null`
  unless the user explicitly turns this on (unchanged default,
  deliberately not touched by this phase).
- Running the scheduler requires a separate, manually-started foreground
  process (`jarvis scheduler start`) -- there is currently no supervised/
  auto-restart wrapper (e.g. a Windows service or systemd unit) around
  it; if that process isn't running, no monitor executes automatically
  regardless of how correctly it's bound.
- No update/delete API exists at the `MonitorService` level (only a
  low-level, never-exposed `MonitorStore.delete_monitor()`) -- per
  instruction, none was added this phase since none currently exists to
  keep in sync; if one is added later, it must call the same
  `_ensure_scheduler_task`/pause-on-disable pattern this phase
  established.
- The concurrency guard's stale-lock expiry uses the monitor's own
  `timeout_seconds` bound (default 30s) -- a genuinely slow but still-
  running cycle longer than that bound could have its lock stolen by a
  concurrent trigger; this is a deliberate, simple, documented trade-off
  (favoring eventual recovery from a crash over a perfectly tight guard
  against every conceivable overlap), not an oversight.
