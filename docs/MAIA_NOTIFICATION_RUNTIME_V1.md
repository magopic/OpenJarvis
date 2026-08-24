# MAIA Notification Runtime & Attention Center V1 (FASE 4Q.3)

Status: **FROZEN.** Deterministically certified (32 new tests, 1061/1061
full scoped regression) + lifecycle-certified in an isolated environment
through the real, registered model-facing tools against a real
notification produced by the live OPS Bridge.

**The optional live Claude conversational check (STEP 16) was not run**
-- `ANTHROPIC_API_KEY` was not visible in this shell, the same
documented gap as `docs/CHAT_ENGINE_SELECTION_PARITY.md`; not
re-attempted, per this engagement's own "no blind retry" precedent. This
is stated plainly and is **not being papered over as a live-Claude
pass** -- what this phase actually certifies is the deterministic,
real-tool-path attention lifecycle (STEP 15), which completed fully and
successfully on its own terms.

**MAIA and OPS ONE are not separate final products.** This
OpenJarvis-Vanilla-Claude working copy is temporary infrastructure for
MAIA engine development. The notification runtime built here is
deliberately backend-owned attention state, with no frontend and no new
API routes -- the future OPS ONE + OpenJarvis + MAIA consolidation is
expected to consume it directly through `MonitorService`'s existing
query methods (see FUTURE OPS ONE CONTRACT below), not through anything
CLI-specific.

Builds the attention layer on top of FASE 4P.2's already-frozen
monitoring notification persistence -- this phase does not introduce a
parallel notification system. It fixes one real, structural gap
(**no principal isolation existed at all** -- any caller could list/
read/acknowledge any notification) and extends the schema additively
(never breaking the existing shape) to support a real UNREAD/READ/
ACKNOWLEDGED lifecycle where before only a flat `acknowledged` boolean
existed.

## ARCHITECTURE

```
MonitorService.run_cycle()  (unchanged, frozen FASE 4P.2 detection)
        |
        v
_diff_and_notify() / _create_notification()   <- now binds principal,
        |                                          promotes severity/
        v                                          title/summary
Notification (persisted, monitor_notifications table)
        |
        v
MonitorService query methods (principal-gated)
        |
        v
NotificationsListTool / NotificationGetTool / NotificationMarkReadTool /
NotificationAcknowledgeTool / NotificationsUnreadCountTool
(model-facing, resolve_runtime_principal() internally, no principal
tool argument exists anywhere)
        |
        v
maia_manage gateway (same tools, same principal guarantee, thin router)
        |
        v
future OPS ONE Attention Center (see FUTURE OPS ONE CONTRACT)
```

No generic event bus/pubsub was found or used (audited: `core/events.py`'s
`EventType` has no notification-related member) -- this remains a
directly-queried, persisted-state model, not an event stream, matching
the existing monitoring architecture exactly. No server/API route
already exposed notifications (audited `server/*.py` -- none did); none
was added this phase either, per instruction (see FUTURE OPS ONE
CONTRACT).

## DATA MODEL

`Notification` (`monitoring/types.py`), additively extended -- every
pre-existing field kept, nothing renamed or removed:

| Field | Since | Meaning |
|---|---|---|
| `id`, `monitor_id`, `fingerprint`, `transition`, `insight_snapshot`, `created_at` | FASE 4P.2 | unchanged |
| `acknowledged`, `acknowledged_at` | FASE 4P.2 | unchanged, still the acknowledgement record |
| `principal` | **4Q.3** | the owning identity -- structural isolation key, never model-settable |
| `source_type` | **4Q.3** | `"monitor"` for V1 -- named generically for a future non-monitor event source without a schema change |
| `source_id` | **4Q.3** | `monitor_id` for V1 (denormalized alongside the generic name) |
| `severity`, `title`, `summary` | **4Q.3** | promoted from `insight_snapshot` at creation time into real, queryable, presentation-text fields |
| `read_at` | **4Q.3** | new -- the UNREAD/READ distinction did not exist before |
| `status` | **4Q.3**, computed | `UNREAD`/`READ`/`ACKNOWLEDGED`, derived from `read_at`/`acknowledged_at` -- never persisted separately, so it cannot drift out of sync with them |

**Raw evidence stays separate from presentation text**, per instruction:
`insight_snapshot` (the full, untouched detector output) is kept
verbatim; `severity`/`title`/`summary` are promoted copies for querying,
never a rewritten/summarized version of the evidence. A `RESOLVED`
transition's synthetic snapshot has no title/summary of its own -- that
stays honestly empty rather than inventing presentation text for it.

Schema migration: `monitor_notifications` gained the seven new columns
via `ALTER TABLE ... ADD COLUMN`, wrapped in the same try/except
`OperationalError` pattern already established in `agents/manager.py`.
The real `monitoring.db` has zero notification rows (the certification
monitor has never run a cycle), so there was nothing to backfill.

## ATTENTION LIFECYCLE

Exactly the three states requested, no more:

```
UNREAD  --mark_notification_read()-->  READ  --acknowledge_notification()-->  ACKNOWLEDGED
                                          |                                        ^
                                          +----------------------------------------+
                                          (acknowledge_notification() also sets
                                           read_at if unset -- see below)
```

`status` is a computed property (checks `acknowledged_at` first, then
`read_at`, else `UNREAD`) -- **reading does not imply acknowledging**
(verified: mark-read alone leaves `acknowledged_at=None`,
`status=READ`). **Acknowledging DOES imply having read it** -- a
one-directional implication, deliberately: without setting `read_at`
alongside `acknowledged_at`, an acknowledged-but-technically-unread row
would incorrectly still count toward the unread total, which would be a
real correctness bug in the query service, not just a philosophical
inconsistency.

Both `mark_notification_read()` and `acknowledge_notification()` are
idempotent -- a second call returns the existing timestamp rather than
overwriting it (verified by test, letters F/H).

## PRINCIPAL ISOLATION

**The real gap this phase closes.** Audited before touching code:
`list_notifications`/`get_notification`/`acknowledge_notification` had
**no principal parameter or filter at all** -- any caller could see or
acknowledge any notification regardless of which monitor/user it
belonged to. Root cause was two-layered:

1. `MonitorCreateTool` never bound a monitor to its real creator --
   every monitor created via the model-facing tool got the same generic
   `"monitor:default"` string (`MonitorService.create_monitor()`'s own
   signature default), because the tool never passed anything else.
   **Fixed**: `MonitorCreateTool.execute()` now passes
   `principal=resolve_runtime_principal()` -- the same certified,
   deterministic-per-OS-account, non-model-settable mechanism Second
   Brain and governed actions already use. No `principal` parameter
   exists on the tool's own schema for the model to override.
2. The notification query methods themselves had no principal
   filtering machinery at all. **Fixed**: `principal` is now a required
   keyword argument on `list_notifications`/`get_notification`/
   `mark_notification_read`/`acknowledge_notification` -- there is no
   "all principals" mode.

**Fail-closed design**: `get_notification()` returns `None` for a
notification that exists but belongs to a different principal --
*identical* to a genuinely unknown id. This is deliberate: distinguishing
"exists but isn't yours" from "doesn't exist" would itself be an
information leak. `acknowledge_notification()`/`mark_notification_read()`
raise the same `KeyError` for both cases.

Verified by test (letters O-R): principal A cannot list, get, or mutate
principal B's notifications; two monitors under different principals
stay fully isolated through a real scheduled-style run; none of the five
notification tools expose a `principal` parameter in their own schema.

## DEDUPLICATION

Unchanged, reused: `_diff_and_notify()`'s NEW/UNCHANGED/CHANGED/RESOLVED/
REOPENED transition logic (FASE 4P.2) was not touched. UNCHANGED still
never creates a notification (verified across many cycles, letter EE).
This phase only changed *what fields* a created `Notification` carries,
never *when* one gets created.

## QUERY SERVICE

`MonitorService`: `list_notifications(principal, monitor_id=, acknowledged=,
unread_only=, severity=, limit=)`, `get_notification(id, principal)`,
`mark_notification_read(id, principal)`, `acknowledge_notification(id,
principal)`, `count_unread_notifications(principal)`. One canonical
service -- no second query layer was created. Filtering kept small and
justified: monitor/severity/unread-only/limit, no search infrastructure.

## MODEL-FACING TOOLS

Two new tools alongside the three existing ones (all always-on, added to
`cli/_tool_names.py`'s monitoring set):

- `maia_notifications_list` (extended: `unread_only`, `severity`, `limit`
  filters added; returns a compact brief shape, omitting the raw
  `insight_snapshot` blob so a natural "do I have notifications?" list
  doesn't flood context)
- `maia_notifications_unread_count` (**new**)
- `maia_notification_get` (unchanged shape, now principal-gated, full
  detail including `insight_snapshot`)
- `maia_notification_mark_read` (**new** -- READ, distinct from
  ACKNOWLEDGED)
- `maia_notification_acknowledge` (unchanged shape, now principal-gated,
  description tightened to explicitly rule out every business-outcome
  meaning listed in STEP 9)

`maia_manage` gateway extended with matching `NOTIFICATION_UNREAD_COUNT`/
`NOTIFICATION_MARK_READ` operations (thin routing only, zero duplicated
logic, mirrors the existing pattern exactly) -- the three pre-existing
notification operations needed no changes themselves, since principal
resolution now happens inside the tools they already call.

## ACKNOWLEDGEMENT BOUNDARY

`NotificationAcknowledgeTool`'s description now states explicitly:
acknowledgement is strictly informational and never means the underlying
issue was resolved, a corrective action was taken, a governed action was
approved, the monitor was resolved, or any business/ERP system was
updated. Verified structurally (letter J): `acknowledge_notification`'s
own source has no reference to `governed_actions`, `.approve(`, or
`.execute(`.

## PERSISTENCE

Plain SQLite, unchanged pattern. Verified: a notification created,
mark-read, and acknowledged in one process is visible with the exact
same state in a freshly-constructed `MonitorService`/`MonitorStore`
pointed at the same db file (letters C, W, X) -- no in-memory-only state
for any part of the authoritative lifecycle.

## CONCURRENCY

`mark_notification_read`/`acknowledge_notification` are naturally
idempotent via a guarded UPDATE (`WHERE ... AND read_at IS NULL`) at the
store level and a check-then-no-op at the service level -- a repeated
call, or two near-simultaneous calls, converge to the same single
timestamp rather than racing to overwrite each other (letters F, H).

## CLAIM INTEGRITY

Audited every notification tool description against the phase's own
allowed/disallowed wording list. Allowed phrasings ("there is a new
notification," "you have N unread," "I marked it read," "you've
acknowledged it") are exactly what the tools' results support and
nothing more. Disallowed phrasings (push delivery, "I'll message you,"
"the issue is resolved") were checked for and confirmed absent --
`maia_notifications_list`'s own description now explicitly states
"nothing is pushed or delivered anywhere automatically" and instructs
never claiming the user will be notified elsewhere. Verified
structurally (letter Z, checking for affirmative push-claim phrases, not
the bare word "push" -- which correctly appears in the *negation*).

## FUTURE OPS ONE CONTRACT

Not built this phase (no frontend, no new API routes, per instruction).
The clean backend boundary a future OPS ONE Attention Center would call
is already exactly `MonitorService`'s five query methods above --
`list_notifications`/`count_unread_notifications`/`get_notification`/
`mark_notification_read`/`acknowledge_notification`, all principal-gated,
all returning plain serializable dataclasses (`to_dict()`/`from_dict()`
unchanged in kind from FASE 4P.2). No server route currently exposes
these; when one is added (a future phase, not this one), it would be a
thin wrapper over these exact same methods -- the same "backend-owned,
CLI-agnostic" shape FASE 4Q.2's scheduler reconciliation already
established, deliberately kept consistent so the eventual OPS ONE +
OpenJarvis + MAIA consolidation has one clean seam to integrate against,
not several.

## EXTERNAL DELIVERY BOUNDARY

Explicitly out of scope, confirmed absent by audit and by test (letters
Z, DD): no Windows notification, browser push, WebSocket, SSE, email,
Outlook, Teams, or SMS code path exists anywhere in the notification
runtime. This is the canonical *persisted attention state* only --
external delivery channels are a distinct, later phase.

## LIVE CERTIFICATION

Fully isolated (`OPENJARVIS_HOME` pointed at a temp directory -- every
store, including `monitoring.db`, resolved there, never the real
config dir). A genuine, non-fabricated notification was produced by
deliberately querying the real, live OPS Bridge with an unsupported
metric name -- an honest `status=invalid_request` response, which
`MissingDataDetector` honestly surfaced as an `INFO`-severity "current
data not available" insight (not a fabricated business value).

Through the real, registered model-facing tool objects (not internal
service calls):

1. `NotificationsListTool` found the real notification.
2. Confirmed `status=UNREAD`.
3. `NotificationGetTool` retrieved full detail (including
   `insight_snapshot`).
4. `NotificationMarkReadTool` -> `status=READ`.
5. Unread count went from 1 to 0.
6. `NotificationAcknowledgeTool` -> `status=ACKNOWLEDGED`.
7. A **fresh process instance** against the same `OPENJARVIS_HOME`
   (simulating a restart) confirmed the `ACKNOWLEDGED` state persisted
   exactly.

All isolated temp files deleted afterward. **STEP 16 (live Claude
natural-language check) was not attempted** -- `ANTHROPIC_API_KEY` is
not present in this shell (same documented gap as
`docs/CHAT_ENGINE_SELECTION_PARITY.md`); the deterministic tool-path
lifecycle above is the certification this phase actually delivers.

## PERFORMANCE

| Notifications | Unread count | Recent list (limit 20) | Mark read | Acknowledge |
|---|---|---|---|---|
| 100 | 0.22ms | 0.51ms | 3.6ms | 4.5ms |
| 1,000 | 0.31ms | 0.66ms | 4.4ms | 5.4ms |

Unread-count and recent-list stay sub-millisecond even at 1,000 rows.
No optimization attempted, none needed at this scale (human-paced
attention, not high-throughput).

## KNOWN LIMITATIONS

- STEP 16's live Claude natural-language check was not run (credential
  unavailability in this shell) -- the deterministic tool-path
  lifecycle (STEP 15) was fully certified instead.
- No server/API route exposes notifications yet -- deliberately, per
  instruction; `MonitorService`'s query methods are the documented
  future integration seam (see FUTURE OPS ONE CONTRACT).
- `source_type`/`source_id` are always `"monitor"`/the monitor id in V1
  -- the fields exist to avoid a future schema change when a
  non-monitor event source is added, but nothing produces one yet.
- No update/delete API exists for notifications themselves (matches the
  same, deliberate absence already documented for monitors in
  `docs/MAIA_MONITORING_RUNTIME_V1.md`).
- The real certification monitor (`170e4a4f6e2d4e50`) was not touched,
  scheduled, or run this phase -- confirmed unchanged
  (`enabled=true`, `cadence=DAILY`, `scheduler_task_id=null`) before and
  after this phase's work.
