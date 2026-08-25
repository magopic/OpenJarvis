"""FASE 4P.2 -- governed monitor/run/notification contracts.

No free-form executable code, no user-provided Python, no arbitrary shell
commands anywhere in this module (STEP 2). ``source_requirements`` is a
plain dict using exactly the same keys as the frozen FASE 4P.1
``ProactiveAnalyzeTool.execute()`` parameters (``ops_capability``,
``ops_params``, ``second_brain_query``, ``second_brain_domains``,
``document_query``) -- reused, not reinvented, so a monitor can only ever
check the same already-governed sources a user could ask MAIA to check
directly, never something novel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CADENCE_HOURLY = "HOURLY"
CADENCE_DAILY = "DAILY"
CADENCE_MANUAL = "MANUAL"
# FASE 4Q.4A: a genuine one-time future check ("controllalo domani") is not
# a recurring cadence -- the underlying TaskScheduler already supports
# exactly this via schedule_type="once" (see scheduler/scheduler.py), it
# was only ever missing from this higher-level vocabulary. A ONCE monitor
# requires an explicit run_at target timestamp (see MonitorDefinition.run_at)
# instead of a seconds-per-cycle interval.
CADENCE_ONCE = "ONCE"
VALID_CADENCES = frozenset({CADENCE_HOURLY, CADENCE_DAILY, CADENCE_MANUAL, CADENCE_ONCE})

# Seconds-per-cycle for the two recurring cadences -- bounded, no
# sub-minute polling anywhere (STEP 10). CADENCE_ONCE is deliberately not
# here: it schedules at an explicit run_at timestamp, not a fixed interval.
_CADENCE_SECONDS = {CADENCE_HOURLY: 3600, CADENCE_DAILY: 86400}

MONITOR_STATUS_ACTIVE = "active"
MONITOR_STATUS_DISABLED = "disabled"

RUN_STATUS_SUCCESS = "success"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_FAILED = "failed"
# FASE 4Q.2 STEP 11 -- a run that never actually collected evidence or
# ran detection because a concurrent run already held the guard. Never
# produces a notification and never touches issue state -- distinct from
# FAILED (a real attempt that broke) so it can be told apart in monitor
# history/audit.
RUN_STATUS_SKIPPED = "skipped"

# Per-issue lifecycle (STEP 5) -- persisted state, not the per-cycle
# transition label (below).
ISSUE_STATE_ACTIVE = "ACTIVE"
ISSUE_STATE_RESOLVED = "RESOLVED"

# Per-cycle transition labels (STEP 5/7) -- computed by diffing this
# cycle's insights against persisted issue state, never persisted
# themselves; only NEW/CHANGED/RESOLVED/REOPENED ever produce a
# Notification. UNCHANGED never does.
TRANSITION_NEW = "NEW"
TRANSITION_UNCHANGED = "UNCHANGED"
TRANSITION_CHANGED = "CHANGED"
TRANSITION_RESOLVED = "RESOLVED"
TRANSITION_REOPENED = "REOPENED"
_NOTIFYING_TRANSITIONS = frozenset(
    {TRANSITION_NEW, TRANSITION_CHANGED, TRANSITION_RESOLVED, TRANSITION_REOPENED}
)


@dataclass
class MonitorDefinition:
    """A governed, explicitly-configured periodic check. STEP 2."""

    id: str
    name: str
    source_requirements: Dict[str, Any]
    enabled: bool = True
    cadence: str = CADENCE_MANUAL
    detector_scope: Optional[List[str]] = None
    principal: str = "monitor:default"
    created_at: str = ""
    last_run_at: Optional[str] = None
    last_success_at: Optional[str] = None
    status: str = MONITOR_STATUS_ACTIVE
    bounds: Dict[str, Any] = field(
        default_factory=lambda: {"timeout_seconds": 30, "max_consecutive_failures": 5}
    )
    scheduler_task_id: Optional[str] = None
    consecutive_failures: int = 0
    # FASE 4Q.4A: the target ISO datetime for a CADENCE_ONCE monitor --
    # unused (None) for every other cadence.
    run_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_requirements": self.source_requirements,
            "enabled": self.enabled,
            "cadence": self.cadence,
            "detector_scope": self.detector_scope,
            "principal": self.principal,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "status": self.status,
            "bounds": self.bounds,
            "scheduler_task_id": self.scheduler_task_id,
            "consecutive_failures": self.consecutive_failures,
            "run_at": self.run_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MonitorDefinition":
        return cls(
            id=d["id"],
            name=d["name"],
            source_requirements=d.get("source_requirements") or {},
            enabled=bool(d.get("enabled", True)),
            cadence=d.get("cadence", CADENCE_MANUAL),
            detector_scope=d.get("detector_scope"),
            principal=d.get("principal", "monitor:default"),
            created_at=d.get("created_at", ""),
            last_run_at=d.get("last_run_at"),
            last_success_at=d.get("last_success_at"),
            status=d.get("status", MONITOR_STATUS_ACTIVE),
            bounds=d.get("bounds") or {"timeout_seconds": 30, "max_consecutive_failures": 5},
            scheduler_task_id=d.get("scheduler_task_id"),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            run_at=d.get("run_at"),
        )


@dataclass
class MonitorRun:
    """One auditable execution of a monitor cycle. STEP 3."""

    id: str
    monitor_id: str
    started_at: str
    completed_at: Optional[str] = None
    evidence_collected: int = 0
    insights_generated: int = 0
    errors: List[str] = field(default_factory=list)
    status: str = RUN_STATUS_SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "monitor_id": self.monitor_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "evidence_collected": self.evidence_collected,
            "insights_generated": self.insights_generated,
            "errors": self.errors,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MonitorRun":
        return cls(
            id=d["id"],
            monitor_id=d["monitor_id"],
            started_at=d["started_at"],
            completed_at=d.get("completed_at"),
            evidence_collected=int(d.get("evidence_collected", 0)),
            insights_generated=int(d.get("insights_generated", 0)),
            errors=d.get("errors") or [],
            status=d.get("status", RUN_STATUS_SUCCESS),
        )


STATUS_UNREAD = "UNREAD"
STATUS_READ = "READ"
STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"


@dataclass
class Notification:
    """An internal (never external) record surfaced only on a real
    transition (NEW/CHANGED/RESOLVED/REOPENED). STEP 6.

    FASE 4Q.3: extends the original FASE 4P.2 shape with the attention
    layer -- principal (structural isolation, never model-settable),
    source_type/source_id (generic -- V1 only ever has "monitor", but
    named for a future non-monitor event source without a schema
    change), and severity/title/summary promoted to real, queryable
    fields (extracted from insight_snapshot at creation time -- the raw
    evidence stays in insight_snapshot, untouched, kept separate from
    this presentation text per STEP 2's own instruction). read_at is new
    -- UNREAD/READ is now distinct from ACKNOWLEDGED, where it wasn't
    before."""

    id: str
    monitor_id: str
    fingerprint: str
    transition: str
    insight_snapshot: Dict[str, Any]
    created_at: str
    acknowledged: bool = False
    acknowledged_at: Optional[str] = None
    principal: Optional[str] = None
    source_type: str = "monitor"
    source_id: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    read_at: Optional[str] = None

    @property
    def status(self) -> str:
        """Computed, never persisted separately -- a single source of
        truth (read_at/acknowledged_at) can't drift out of sync with a
        redundant stored status column."""
        if self.acknowledged_at is not None:
            return STATUS_ACKNOWLEDGED
        if self.read_at is not None:
            return STATUS_READ
        return STATUS_UNREAD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "monitor_id": self.monitor_id,
            "fingerprint": self.fingerprint,
            "transition": self.transition,
            "insight_snapshot": self.insight_snapshot,
            "created_at": self.created_at,
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at,
            "principal": self.principal,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "read_at": self.read_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Notification":
        return cls(
            id=d["id"],
            monitor_id=d["monitor_id"],
            fingerprint=d["fingerprint"],
            transition=d["transition"],
            insight_snapshot=d.get("insight_snapshot") or {},
            created_at=d["created_at"],
            acknowledged=bool(d.get("acknowledged", False)),
            acknowledged_at=d.get("acknowledged_at"),
            principal=d.get("principal"),
            source_type=d.get("source_type") or "monitor",
            source_id=d.get("source_id") or d.get("monitor_id"),
            severity=d.get("severity"),
            title=d.get("title"),
            summary=d.get("summary"),
            read_at=d.get("read_at"),
        )


__all__ = [
    "CADENCE_HOURLY",
    "CADENCE_DAILY",
    "CADENCE_MANUAL",
    "CADENCE_ONCE",
    "VALID_CADENCES",
    "_CADENCE_SECONDS",
    "MONITOR_STATUS_ACTIVE",
    "MONITOR_STATUS_DISABLED",
    "RUN_STATUS_SUCCESS",
    "RUN_STATUS_PARTIAL",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_SKIPPED",
    "ISSUE_STATE_ACTIVE",
    "ISSUE_STATE_RESOLVED",
    "TRANSITION_NEW",
    "TRANSITION_UNCHANGED",
    "TRANSITION_CHANGED",
    "TRANSITION_RESOLVED",
    "TRANSITION_REOPENED",
    "_NOTIFYING_TRANSITIONS",
    "STATUS_UNREAD",
    "STATUS_READ",
    "STATUS_ACKNOWLEDGED",
    "MonitorDefinition",
    "MonitorRun",
    "Notification",
]
