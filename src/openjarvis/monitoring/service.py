"""FASE 4P.2 -- MonitorService: the deterministic monitor cycle.

STEP 12: this module contains NO LLM call anywhere. Evidence collection
calls the exact same already-governed tool objects a user's own request
would reach (ProactiveAnalyzeTool's own ``_call_ops`` helper,
SecondBrainFindRelatedExperiencesTool, DocumentSearchTool); detection
reuses the frozen FASE 4P.1 ProactiveReasoningService unmodified;
deduplication/state-transition/notification logic is plain Python. Claude
is never involved in deciding whether, or how, a cycle runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.agents.operational_evidence import build_evidence
from openjarvis.agents.proactive_insight import (
    ProactiveInsight,
    ProactiveReasoningService,
    _DEFAULT_DETECTORS,
)
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.store import MonitorStore
from openjarvis.monitoring.types import (
    _CADENCE_SECONDS,
    _NOTIFYING_TRANSITIONS,
    CADENCE_MANUAL,
    CADENCE_ONCE,
    ISSUE_STATE_ACTIVE,
    ISSUE_STATE_RESOLVED,
    MONITOR_STATUS_ACTIVE,
    MONITOR_STATUS_DISABLED,
    MonitorDefinition,
    MonitorRun,
    Notification,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_SKIPPED,
    RUN_STATUS_SUCCESS,
    TRANSITION_CHANGED,
    TRANSITION_NEW,
    TRANSITION_REOPENED,
    TRANSITION_RESOLVED,
    TRANSITION_UNCHANGED,
    VALID_CADENCES,
)

# The literal, parseable prefix a monitor's scheduler prompt carries, so
# MonitorCheckAgent can extract the monitor_id without any NLP -- pure
# string prefix matching, no free-form instruction ever gets to the
# scheduler (STEP 2's "no free-form executable code").
MONITOR_PROMPT_PREFIX = "__monitor_check__:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    from openjarvis.core.paths import get_config_dir

    return str(Path(get_config_dir()) / "monitoring.db")


def get_default_task_scheduler() -> Optional[Any]:
    """FASE 4Q.2 -- the real, canonical scheduler wiring that was
    entirely missing before this phase: every MonitorService(...) call
    site anywhere in the codebase constructed with scheduler=None
    (audited exhaustively -- see docs/MAIA_MONITORING_RUNTIME_V1.md).

    Returns a CRUD-only TaskScheduler (scheduler/scheduler.py) bound to
    the same persistent SchedulerStore the standalone `jarvis scheduler
    start` daemon (scheduler_cmd.py) polls -- never starts a poll loop
    or requires a JarvisSystem here, so this is safe and cheap to call
    from any process (a `jarvis chat`/`jarvis ask` invocation creating or
    enabling a monitor just needs to persist a ScheduledTask row; only
    the separate, explicitly-started scheduler daemon process actually
    executes due tasks -- see STEP 16 of the phase this was built in).

    Respects config.scheduler.enabled (default False) -- deliberately
    does not override the user's own choice to leave scheduling off;
    when disabled this returns None, preserving the exact pre-4Q.2
    behavior (monitors save with scheduler_task_id=None, MANUAL-only in
    effect) rather than silently forcing scheduling on.

    Never raises -- any failure to construct falls back to None, exactly
    as the previous unconditional None default did, so a broken/missing
    scheduler.db never blocks ordinary monitor creation.
    """
    try:
        from openjarvis.core.config import DEFAULT_CONFIG_DIR, load_config
        from openjarvis.scheduler.scheduler import TaskScheduler
        from openjarvis.scheduler.store import SchedulerStore

        config = load_config()
        if not config.scheduler.enabled:
            return None
        db_path = config.scheduler.db_path or str(DEFAULT_CONFIG_DIR / "scheduler.db")
        return TaskScheduler(SchedulerStore(db_path))
    except Exception:
        return None


class MonitorService:
    """Create/list/enable/disable monitors and run a deterministic cycle."""

    def __init__(self, store: Optional[MonitorStore] = None, scheduler: Optional[Any] = None) -> None:
        self._store = store or MonitorStore(_default_db_path())
        # Optional -- only needed to register/unregister the underlying
        # ScheduledTask for HOURLY/DAILY cadence. A MANUAL-only monitor,
        # or a test using an isolated store, works fine without one.
        self._scheduler = scheduler

    # -- Definition CRUD (STEP 2) ----------------------------------------

    def create_monitor(
        self,
        name: str,
        source_requirements: Dict[str, Any],
        *,
        cadence: str = CADENCE_MANUAL,
        detector_scope: Optional[List[str]] = None,
        principal: str = "monitor:default",
        bounds: Optional[Dict[str, Any]] = None,
        monitor_id: Optional[str] = None,
        enabled: bool = True,
        run_at: Optional[str] = None,
    ) -> MonitorDefinition:
        if cadence not in VALID_CADENCES:
            raise ValueError(f"Invalid cadence {cadence!r}; must be one of {sorted(VALID_CADENCES)}")
        if not isinstance(source_requirements, dict) or not source_requirements:
            raise ValueError("source_requirements must be a non-empty dict of governed source keys")
        if detector_scope is not None:
            valid_names = {d.name for d in _DEFAULT_DETECTORS}
            unknown = set(detector_scope) - valid_names
            if unknown:
                raise ValueError(f"Unknown detector(s) in detector_scope: {sorted(unknown)}")
        if cadence == CADENCE_ONCE:
            if not run_at:
                raise ValueError(
                    "cadence='ONCE' requires run_at (an ISO 8601 datetime -- "
                    "the specific future moment this one-time check should run)"
                )
            try:
                _parsed = datetime.fromisoformat(run_at)
            except ValueError as exc:
                raise ValueError(f"run_at {run_at!r} is not a valid ISO 8601 datetime: {exc}") from exc
            if _parsed.tzinfo is None:
                raise ValueError(f"run_at {run_at!r} must include a UTC offset (e.g. '+00:00' or 'Z')")
            # FASE 4Q.4A Fix B -- authoritative runtime clock, compared as
            # timezone-aware datetime objects (never as raw ISO strings,
            # which do not sort correctly across mixed UTC offsets). A
            # live certification persisted run_at over a year in the past
            # with no rejection anywhere in this path -- this is the
            # single, deepest boundary every caller (MonitorCreateTool,
            # maia_manage, any future caller) already funnels through.
            _now = datetime.now(timezone.utc)
            if _parsed <= _now:
                raise ValueError(
                    f"run_at {run_at!r} must be in the future (current time is "
                    f"{_now.isoformat()}) -- a one-time check cannot be scheduled "
                    "for a moment that has already passed"
                )
        elif run_at:
            raise ValueError("run_at is only valid together with cadence='ONCE'")

        mon = MonitorDefinition(
            id=monitor_id or uuid.uuid4().hex[:16],
            name=name,
            source_requirements=source_requirements,
            enabled=enabled,
            cadence=cadence,
            detector_scope=detector_scope,
            principal=principal,
            created_at=_now_iso(),
            bounds=bounds or {"timeout_seconds": 30, "max_consecutive_failures": 5},
            run_at=run_at,
        )

        if cadence != CADENCE_MANUAL and self._scheduler is not None and enabled:
            mon.scheduler_task_id = self._ensure_scheduler_task(mon)

        self._store.save_monitor(mon.to_dict())
        return mon

    def _ensure_scheduler_task(self, mon: MonitorDefinition) -> Optional[str]:
        """FASE 4Q.2: idempotent -- if a task with this monitor's
        deterministic id already exists (whatever its status), reuse it
        rather than creating a second one; only creates when genuinely
        missing. Shared by create_monitor, enable_monitor, and
        reconcile_scheduler_bindings so there is exactly one place that
        decides how a monitor's ScheduledTask looks.

        FASE 4Q.4A: CADENCE_ONCE binds to the scheduler's own already-
        working schedule_type="once" (see scheduler/scheduler.py) using
        the monitor's run_at as schedule_value, instead of the
        interval-seconds mapping the two recurring cadences use."""
        if self._scheduler is None:
            return mon.scheduler_task_id
        task_id = mon.scheduler_task_id or f"monitor:{mon.id}"
        existing = self._scheduler.get_task(task_id)
        if existing is None:
            if mon.cadence == CADENCE_ONCE:
                schedule_type, schedule_value = "once", mon.run_at
            else:
                schedule_type = "interval"
                schedule_value = str(_CADENCE_SECONDS[mon.cadence])
            task = self._scheduler.create_task(
                prompt=f"{MONITOR_PROMPT_PREFIX}{mon.id}",
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                agent="monitor_check",
                task_id=task_id,
            )
            return task.id
        return existing.id

    def get_monitor(self, monitor_id: str) -> Optional[MonitorDefinition]:
        d = self._store.get_monitor(monitor_id)
        return MonitorDefinition.from_dict(d) if d else None

    def list_monitors(self, *, enabled: Optional[bool] = None) -> List[MonitorDefinition]:
        return [MonitorDefinition.from_dict(d) for d in self._store.list_monitors(enabled=enabled)]

    def enable_monitor(self, monitor_id: str) -> MonitorDefinition:
        mon = self.get_monitor(monitor_id)
        if mon is None:
            raise KeyError(f"Monitor not found: {monitor_id}")
        mon.enabled = True
        mon.status = MONITOR_STATUS_ACTIVE
        mon.consecutive_failures = 0
        if mon.cadence != CADENCE_MANUAL and self._scheduler is not None:
            # FASE 4Q.2 fix: a monitor created while disabled never got a
            # task in the first place (create_monitor only creates one
            # when enabled=True at creation time) -- resume_task() alone
            # would silently no-op (KeyError swallowed) forever in that
            # case, leaving scheduler_task_id permanently None despite
            # enabled=True. _ensure_scheduler_task creates if missing,
            # reuses if present -- covers both the "never had a task" and
            # the "had one, just paused" cases with one call.
            mon.scheduler_task_id = self._ensure_scheduler_task(mon)
            try:
                self._scheduler.resume_task(mon.scheduler_task_id)
            except KeyError:
                pass
        self._store.save_monitor(mon.to_dict())
        return mon

    def disable_monitor(self, monitor_id: str) -> MonitorDefinition:
        mon = self.get_monitor(monitor_id)
        if mon is None:
            raise KeyError(f"Monitor not found: {monitor_id}")
        mon.enabled = False
        mon.status = MONITOR_STATUS_DISABLED
        # scheduler_task_id is deliberately RETAINED, not cleared -- the
        # underlying ScheduledTask row is paused, not deleted, so a later
        # enable_monitor() correctly resumes the same task instead of
        # creating a second one (STEP 4: "preserve monitor history").
        if mon.cadence != CADENCE_MANUAL and self._scheduler is not None and mon.scheduler_task_id:
            try:
                self._scheduler.pause_task(mon.scheduler_task_id)
            except KeyError:
                pass
        self._store.save_monitor(mon.to_dict())
        return mon

    def reconcile_scheduler_bindings(self) -> Dict[str, List[str]]:
        """FASE 4Q.2 STEP 6 -- startup reconciliation. Call once when the
        scheduler runtime starts (``jarvis scheduler start``), never
        during ordinary monitor CRUD. Idempotent by construction: every
        branch either finds an already-correct task and does nothing, or
        performs the exact same deterministic-task-id create/resume/pause
        _ensure_scheduler_task/resume_task/pause_task calls that
        create_monitor/enable_monitor/disable_monitor already use --
        calling this twice in a row produces the same end state and no
        duplicate ScheduledTask rows (task_id is a SQLite PRIMARY KEY).

        Returns which monitor ids were created, repaired (resumed or had
        their scheduler_task_id field corrected), or paused (an orphaned
        active task for a disabled/manual monitor) -- for logging/
        diagnostics, not required by callers.
        """
        result: Dict[str, List[str]] = {"created": [], "repaired": [], "paused": []}
        if self._scheduler is None:
            return result

        for mon in self.list_monitors():
            should_be_active = mon.enabled and mon.cadence != CADENCE_MANUAL
            task_id = mon.scheduler_task_id or f"monitor:{mon.id}"
            existing = self._scheduler.get_task(task_id)

            if should_be_active:
                if existing is None:
                    new_id = self._ensure_scheduler_task(mon)
                    if new_id != mon.scheduler_task_id:
                        mon.scheduler_task_id = new_id
                        self._store.save_monitor(mon.to_dict())
                    result["created"].append(mon.id)
                elif existing.status != "active":
                    try:
                        self._scheduler.resume_task(task_id)
                    except KeyError:
                        pass
                    if mon.scheduler_task_id != task_id:
                        mon.scheduler_task_id = task_id
                        self._store.save_monitor(mon.to_dict())
                    result["repaired"].append(mon.id)
                elif mon.scheduler_task_id != task_id:
                    # Task exists and is active, but the monitor row's own
                    # scheduler_task_id field is stale/missing -- repair
                    # the field only, no scheduler call needed.
                    mon.scheduler_task_id = task_id
                    self._store.save_monitor(mon.to_dict())
                    result["repaired"].append(mon.id)
            else:
                # Disabled or MANUAL: any lingering ACTIVE task is an
                # orphan (e.g. cadence was DAILY when created, monitor was
                # later disabled some other way) -- pause it, never leave
                # an automatic trigger running for a monitor that should
                # not be automatic right now.
                if existing is not None and existing.status == "active":
                    try:
                        self._scheduler.pause_task(task_id)
                    except KeyError:
                        pass
                    result["paused"].append(mon.id)

        return result

    # -- Notifications -------------------------------------------------------

    def list_notifications(
        self,
        *,
        principal: str,
        monitor_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        unread_only: bool = False,
        severity: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Notification]:
        """FASE 4Q.3 STEP 4: *principal* is required, not optional -- there
        is no "all principals" mode. This method filters by whatever
        principal it's given; it does not authenticate it -- the
        model-facing tools that call this always source it from
        resolve_runtime_principal(), never from a tool argument, which is
        the actual isolation guarantee."""
        return [
            Notification.from_dict(d)
            for d in self._store.list_notifications(
                principal=principal,
                monitor_id=monitor_id,
                acknowledged=acknowledged,
                unread_only=unread_only,
                severity=severity,
                limit=limit,
            )
        ]

    def get_notification(self, notification_id: str, *, principal: str) -> Optional[Notification]:
        d = self._store.get_notification(notification_id)
        if d is None:
            return None
        n = Notification.from_dict(d)
        if n.principal != principal:
            # Fail closed: a cross-principal lookup looks IDENTICAL to a
            # genuinely missing id -- never distinguishes "exists but
            # isn't yours" from "doesn't exist" (that distinction would
            # itself be an information leak).
            return None
        return n

    def mark_notification_read(self, notification_id: str, *, principal: str) -> Notification:
        """Idempotent -- a second call is a no-op (store-level guard
        already refuses to overwrite an existing read_at)."""
        n = self.get_notification(notification_id, principal=principal)
        if n is None:
            raise KeyError(f"Notification not found: {notification_id}")
        if n.read_at is None:
            self._store.mark_notification_read(notification_id, _now_iso())
            n = self.get_notification(notification_id, principal=principal)
        return n

    def acknowledge_notification(self, notification_id: str, *, principal: str) -> Notification:
        """Idempotent -- a second call returns the same already-
        acknowledged state rather than overwriting acknowledged_at.
        Setting read_at alongside acknowledged_at (if not already read) is
        deliberate: acknowledging implies having seen it, and without
        this an acknowledged-but-technically-unread row would incorrectly
        still count toward the unread total."""
        n = self.get_notification(notification_id, principal=principal)
        if n is None:
            raise KeyError(f"Notification not found: {notification_id}")
        if n.acknowledged_at is None:
            n.acknowledged = True
            n.acknowledged_at = _now_iso()
            if n.read_at is None:
                n.read_at = n.acknowledged_at
            self._store.save_notification(n.to_dict())
        return n

    def count_unread_notifications(self, *, principal: str) -> int:
        return self._store.count_unread_notifications(principal)

    # -- The cycle (STEP 7) ---------------------------------------------

    def run_cycle(self, monitor_id: str, *, force: bool = False) -> "tuple[MonitorRun, List[Notification]]":
        monitor = self.get_monitor(monitor_id)
        if monitor is None:
            raise KeyError(f"Monitor not found: {monitor_id}")

        run = MonitorRun(id=uuid.uuid4().hex[:16], monitor_id=monitor_id, started_at=_now_iso())

        # STEP 14-B: a disabled monitor does not run, even via run_now,
        # unless explicitly forced (used only by tests/diagnostics).
        if not monitor.enabled and not force:
            run.completed_at = _now_iso()
            run.status = RUN_STATUS_FAILED
            run.errors = ["monitor is disabled"]
            self._store.save_run(run.to_dict())
            return run, []

        # FASE 4Q.2 STEP 11: prevent two concurrent cycles for the same
        # monitor (a scheduler trigger racing a manual run_now, a slow
        # prior run still in flight, or -- in principle -- two scheduler
        # daemons). Claim is atomic (SQLite PRIMARY KEY insert) and
        # self-expiring (stale_after_seconds, from the monitor's own
        # existing timeout_seconds bound) so a crash mid-run does not
        # permanently wedge the monitor -- the next attempt after the
        # bound simply steals the stale lock. A skipped run is still
        # persisted (auditable) but never collects evidence, runs
        # detection, or touches issue state/notifications -- STEP 9's "a
        # scheduler failure must not create a fake business alert"
        # applies here too: skipping is not a business outcome.
        timeout_seconds = float((monitor.bounds or {}).get("timeout_seconds", 30))
        if not self._store.try_claim_run(monitor_id, run.id, stale_after_seconds=timeout_seconds):
            run.completed_at = _now_iso()
            run.status = RUN_STATUS_SKIPPED
            run.errors = ["monitor is already running (concurrent execution guard)"]
            self._store.save_run(run.to_dict())
            return run, []

        try:
            return self._run_cycle_locked(monitor, run)
        finally:
            self._store.release_run(monitor_id, run.id)

    def _run_cycle_locked(
        self, monitor: MonitorDefinition, run: MonitorRun
    ) -> "tuple[MonitorRun, List[Notification]]":
        monitor_id = monitor.id
        tool_results, collection_errors = self._collect_evidence(monitor)
        run.evidence_collected = len(tool_results)

        # STEP 9: a source failure must never fabricate a business
        # conclusion -- if NOTHING was collected, there is no evidence to
        # analyze at all; skip detection entirely rather than let
        # ProactiveReasoningService run against an empty/stale set and
        # imply something was checked when it wasn't.
        insights: List[ProactiveInsight] = []
        if tool_results:
            evidence = build_evidence(tool_results)
            detectors = None
            if monitor.detector_scope:
                detectors = [d for d in _DEFAULT_DETECTORS if d.name in monitor.detector_scope]
            insights = ProactiveReasoningService(detectors=detectors).analyze(tool_results, evidence)
        run.insights_generated = len(insights)

        notifications = self._diff_and_notify(monitor, insights)

        run.errors = collection_errors
        if collection_errors and tool_results:
            run.status = RUN_STATUS_PARTIAL
        elif collection_errors and not tool_results:
            run.status = RUN_STATUS_FAILED
        else:
            run.status = RUN_STATUS_SUCCESS
        run.completed_at = _now_iso()
        self._store.save_run(run.to_dict())

        monitor.last_run_at = run.completed_at
        if run.status != RUN_STATUS_FAILED:
            monitor.last_success_at = run.completed_at
            monitor.consecutive_failures = 0
        else:
            monitor.consecutive_failures += 1
            max_failures = int((monitor.bounds or {}).get("max_consecutive_failures", 5))
            if monitor.consecutive_failures >= max_failures:
                monitor.enabled = False
                monitor.status = MONITOR_STATUS_DISABLED
                # FASE 4Q.2: auto-disabling must behave exactly like a
                # manual disable_monitor() -- otherwise the scheduler
                # keeps firing an automatic trigger for a monitor the
                # system itself just decided to stop, an orphan-task case
                # STEP 6/9 both call out.
                if (
                    monitor.cadence != CADENCE_MANUAL
                    and self._scheduler is not None
                    and monitor.scheduler_task_id
                ):
                    try:
                        self._scheduler.pause_task(monitor.scheduler_task_id)
                    except KeyError:
                        pass
        self._store.save_monitor(monitor.to_dict())

        return run, notifications

    # -- Internals -----------------------------------------------------------

    def _collect_evidence(self, monitor: MonitorDefinition) -> "tuple[List[ToolResult], List[str]]":
        """STEP 7.2 / STEP 8: read-only, governed, per-source independent --
        one source failing never blocks the others (STEP 9's 'one source
        failing while others work')."""
        req = monitor.source_requirements or {}
        results: List[ToolResult] = []
        errors: List[str] = []

        ops_capability = req.get("ops_capability")
        if isinstance(ops_capability, str) and ops_capability.strip():
            try:
                from openjarvis.tools.proactive_insight_tools import ProactiveAnalyzeTool

                # Reuses ProactiveAnalyzeTool's own _call_ops -- the exact
                # NOT_AVAILABLE/data_not_available/ok handling certified in
                # FASE 4P.1B, never duplicated here.
                results.append(ProactiveAnalyzeTool()._call_ops(ops_capability.strip(), req.get("ops_params") or {}))
            except Exception as exc:
                errors.append(f"ops_capability={ops_capability}: {exc}")

        sb_query = req.get("second_brain_query")
        sb_domains = req.get("second_brain_domains")
        if sb_query or sb_domains:
            try:
                from openjarvis.tools.second_brain_tools import SecondBrainFindRelatedExperiencesTool

                # STEP 8: the monitor's OWN configured principal, never
                # the ambient/ MANUAL-session one -- an unattended
                # scheduled check must not silently inherit whoever
                # happened to be logged in when it was created.
                sb_tool = SecondBrainFindRelatedExperiencesTool(principal=monitor.principal)
                results.append(sb_tool.execute(query=sb_query, domains=sb_domains))
            except Exception as exc:
                errors.append(f"second_brain: {exc}")

        doc_query = req.get("document_query")
        if isinstance(doc_query, str) and doc_query.strip():
            try:
                from openjarvis.tools.document_knowledge_tools import DocumentSearchTool

                results.append(DocumentSearchTool().execute(query=doc_query.strip()))
            except Exception as exc:
                errors.append(f"document: {exc}")

        return results, errors

    def _diff_and_notify(self, monitor: MonitorDefinition, insights: List[ProactiveInsight]) -> List[Notification]:
        """STEP 4/5: deterministic fingerprint = insight.id (already a
        SHA256 of detector name + governed fields -- see
        proactive_insight.py::_stable_id -- never generated prose)."""
        monitor_id = monitor.id
        prior_state = self._store.get_issue_state(monitor_id)
        current_fingerprints = {i.id for i in insights}
        now = _now_iso()
        notifications: List[Notification] = []

        for insight in insights:
            fp = insight.id
            prior = prior_state.get(fp)
            if prior is None:
                transition = TRANSITION_NEW
                first_seen = now
            elif prior["status"] == ISSUE_STATE_RESOLVED:
                transition = TRANSITION_REOPENED
                first_seen = prior["first_seen"]
            elif prior.get("severity") != insight.severity or prior.get("confidence") != insight.confidence:
                transition = TRANSITION_CHANGED
                first_seen = prior["first_seen"]
            else:
                transition = TRANSITION_UNCHANGED
                first_seen = prior["first_seen"]

            self._store.upsert_issue_state(
                monitor_id,
                fp,
                status=ISSUE_STATE_ACTIVE,
                severity=insight.severity,
                confidence=insight.confidence,
                first_seen=first_seen,
                last_seen=now,
                resolved_at=None,
            )
            if transition in _NOTIFYING_TRANSITIONS:
                notifications.append(
                    self._create_notification(monitor, fp, transition, _insight_snapshot(insight), now)
                )

        for fp, prior in prior_state.items():
            if prior["status"] == ISSUE_STATE_ACTIVE and fp not in current_fingerprints:
                self._store.upsert_issue_state(
                    monitor_id,
                    fp,
                    status=ISSUE_STATE_RESOLVED,
                    severity=prior.get("severity"),
                    confidence=prior.get("confidence"),
                    first_seen=prior["first_seen"],
                    last_seen=prior["last_seen"],
                    resolved_at=now,
                )
                notifications.append(
                    self._create_notification(
                        monitor, fp, TRANSITION_RESOLVED, {"id": fp, "resolved": True}, now
                    )
                )

        return notifications

    def _create_notification(
        self, monitor: MonitorDefinition, fingerprint: str, transition: str, snapshot: Dict[str, Any], now: str
    ) -> Notification:
        # FASE 4Q.3: severity/title/summary are PROMOTED from the raw
        # insight snapshot into real, queryable fields -- the snapshot
        # itself (raw evidence) is kept verbatim, untouched, as
        # insight_snapshot. A RESOLVED transition's synthetic snapshot
        # ({"id": fp, "resolved": True}) has no title/summary/severity of
        # its own; that's honest -- there is no fabricated presentation
        # text invented here for it.
        n = Notification(
            id=uuid.uuid4().hex[:16],
            monitor_id=monitor.id,
            fingerprint=fingerprint,
            transition=transition,
            insight_snapshot=snapshot,
            created_at=now,
            principal=monitor.principal,
            source_type="monitor",
            source_id=monitor.id,
            severity=snapshot.get("severity"),
            title=snapshot.get("title"),
            summary=snapshot.get("summary"),
        )
        self._store.save_notification(n.to_dict())
        return n


def _insight_snapshot(insight: ProactiveInsight) -> Dict[str, Any]:
    return {
        "id": insight.id,
        "title": insight.title,
        "summary": insight.summary,
        "severity": insight.severity,
        "confidence": insight.confidence,
        "status": insight.status,
        "reasoning_basis": list(insight.reasoning_basis),
        "limitations": list(insight.limitations),
        "proposed_action_ids": [a.id for a in insight.proposed_actions],
    }


__all__ = ["MonitorService", "MONITOR_PROMPT_PREFIX"]
