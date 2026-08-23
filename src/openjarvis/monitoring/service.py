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
    ISSUE_STATE_ACTIVE,
    ISSUE_STATE_RESOLVED,
    MONITOR_STATUS_ACTIVE,
    MONITOR_STATUS_DISABLED,
    MonitorDefinition,
    MonitorRun,
    Notification,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
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
        )

        if cadence != CADENCE_MANUAL and self._scheduler is not None and enabled:
            task = self._scheduler.create_task(
                prompt=f"{MONITOR_PROMPT_PREFIX}{mon.id}",
                schedule_type="interval",
                schedule_value=str(_CADENCE_SECONDS[cadence]),
                agent="monitor_check",
                task_id=f"monitor:{mon.id}",
            )
            mon.scheduler_task_id = task.id

        self._store.save_monitor(mon.to_dict())
        return mon

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
            try:
                self._scheduler.resume_task(mon.scheduler_task_id or f"monitor:{mon.id}")
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
        if mon.cadence != CADENCE_MANUAL and self._scheduler is not None:
            try:
                self._scheduler.pause_task(mon.scheduler_task_id or f"monitor:{mon.id}")
            except KeyError:
                pass
        self._store.save_monitor(mon.to_dict())
        return mon

    # -- Notifications -------------------------------------------------------

    def list_notifications(
        self, *, monitor_id: Optional[str] = None, acknowledged: Optional[bool] = None
    ) -> List[Notification]:
        return [
            Notification.from_dict(d)
            for d in self._store.list_notifications(monitor_id=monitor_id, acknowledged=acknowledged)
        ]

    def get_notification(self, notification_id: str) -> Optional[Notification]:
        d = self._store.get_notification(notification_id)
        return Notification.from_dict(d) if d else None

    def acknowledge_notification(self, notification_id: str) -> Notification:
        n = self.get_notification(notification_id)
        if n is None:
            raise KeyError(f"Notification not found: {notification_id}")
        n.acknowledged = True
        n.acknowledged_at = _now_iso()
        self._store.save_notification(n.to_dict())
        return n

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

        notifications = self._diff_and_notify(monitor_id, insights)

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

    def _diff_and_notify(self, monitor_id: str, insights: List[ProactiveInsight]) -> List[Notification]:
        """STEP 4/5: deterministic fingerprint = insight.id (already a
        SHA256 of detector name + governed fields -- see
        proactive_insight.py::_stable_id -- never generated prose)."""
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
                    self._create_notification(monitor_id, fp, transition, _insight_snapshot(insight), now)
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
                    self._create_notification(monitor_id, fp, TRANSITION_RESOLVED, {"id": fp, "resolved": True}, now)
                )

        return notifications

    def _create_notification(
        self, monitor_id: str, fingerprint: str, transition: str, snapshot: Dict[str, Any], now: str
    ) -> Notification:
        n = Notification(
            id=uuid.uuid4().hex[:16],
            monitor_id=monitor_id,
            fingerprint=fingerprint,
            transition=transition,
            insight_snapshot=snapshot,
            created_at=now,
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
