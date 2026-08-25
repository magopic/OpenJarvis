"""FASE 4P.2 STEP 13 -- minimal governed monitor/notification management
tools. Configuration and read tools only -- creating/enabling a monitor
is configuration, never business execution. No arbitrary scheduler code,
no free-form callable, no execution tool (mirrors FASE 4P.1's own
execution-boundary discipline exactly)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.attention import build_attention_summary
from openjarvis.monitoring.service import MonitorService, get_default_task_scheduler
from openjarvis.monitoring.types import (
    CADENCE_DAILY,
    CADENCE_HOURLY,
    CADENCE_MANUAL,
    CADENCE_ONCE,
)

# FASE 4Q.4A STEP 6 -- the only recurring cadences a model may name
# directly on maia_monitor_create's new recurring_cadence field. ONCE and
# MANUAL are deliberately excluded: ONCE is now implied structurally by
# supplying run_at (never a label the model picks), and MANUAL is implied
# by supplying neither field -- see MonitorCreateTool.execute().
_RECURRING_CADENCES = frozenset({CADENCE_HOURLY, CADENCE_DAILY})
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _default_scheduled_monitor_service() -> MonitorService:
    """FASE 4Q.2: the real, scheduler-bound MonitorService used by the
    three tools that actually create/enable/disable monitors --
    MonitorService's own bare default (used everywhere else: list/get/
    run_now/notifications, and every test) deliberately stays
    scheduler=None so it never has a surprise side effect on an isolated
    test store. get_default_task_scheduler() itself respects
    config.scheduler.enabled (default False) and never raises."""
    return MonitorService(scheduler=get_default_task_scheduler())


def _monitor_brief(m: Any) -> Dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "enabled": m.enabled,
        "cadence": m.cadence,
        "status": m.status,
        "last_run_at": m.last_run_at,
        "last_success_at": m.last_success_at,
        "consecutive_failures": m.consecutive_failures,
    }


def _monitor_full(m: Any) -> Dict[str, Any]:
    d = _monitor_brief(m)
    d.update(
        {
            "source_requirements": m.source_requirements,
            "detector_scope": m.detector_scope,
            "principal": m.principal,
            "created_at": m.created_at,
            "run_at": getattr(m, "run_at", None),
            "bounds": m.bounds,
            # FASE 4Q.1 claim-integrity review: structural, not narrated --
            # the model must see this directly rather than assume automatic
            # execution. None means no scheduler task is actually wired for
            # this monitor (the default MonitorService() every model-facing
            # tool constructs never receives a real scheduler -- see
            # register_default_capabilities-style wiring in tools/__init__.py),
            # so nothing will invoke it on cadence until one is.
            "scheduler_task_id": getattr(m, "scheduler_task_id", None),
        }
    )
    return d


def _notification_full(n: Any) -> Dict[str, Any]:
    return {
        "id": n.id,
        "monitor_id": n.monitor_id,
        "source_type": n.source_type,
        "source_id": n.source_id,
        "fingerprint": n.fingerprint,
        "transition": n.transition,
        "severity": n.severity,
        "title": n.title,
        "summary": n.summary,
        "insight_snapshot": n.insight_snapshot,
        "created_at": n.created_at,
        "read_at": n.read_at,
        "acknowledged": n.acknowledged,
        "acknowledged_at": n.acknowledged_at,
        "status": n.status,
    }


def _notification_brief(n: Any) -> Dict[str, Any]:
    """FASE 4Q.3: a compact shape for list views -- omits insight_snapshot
    (the raw evidence blob) so a natural 'do I have notifications?' list
    doesn't flood context with every insight's full detail; get_notification
    still returns the full shape including insight_snapshot."""
    return {
        "id": n.id,
        "monitor_id": n.monitor_id,
        "transition": n.transition,
        "severity": n.severity,
        "title": n.title,
        "summary": n.summary,
        "created_at": n.created_at,
        "status": n.status,
    }


@ToolRegistry.register("maia_monitors_list")
class MonitorsListTool(BaseTool):
    tool_id = "maia_monitors_list"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitors_list",
            description="List configured proactive monitors, optionally filtered by enabled state.",
            parameters={
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        enabled = params.get("enabled")
        monitors = self._service.list_monitors(enabled=enabled)
        return ToolResult(
            tool_name="maia_monitors_list",
            content=json.dumps([_monitor_brief(m) for m in monitors]),
            success=True,
            metadata={"num_monitors": len(monitors)},
        )


@ToolRegistry.register("maia_monitor_get")
class MonitorGetTool(BaseTool):
    tool_id = "maia_monitor_get"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_get",
            description="Get full configuration and status for one monitor by id.",
            parameters={
                "type": "object",
                "properties": {"monitor_id": {"type": "string"}},
                "required": ["monitor_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        monitor_id = params.get("monitor_id", "")
        mon = self._service.get_monitor(monitor_id)
        if mon is None:
            return ToolResult(tool_name="maia_monitor_get", content=f"Monitor not found: {monitor_id}", success=False)
        return ToolResult(tool_name="maia_monitor_get", content=json.dumps(_monitor_full(mon)), success=True)


@ToolRegistry.register("maia_monitor_create")
class MonitorCreateTool(BaseTool):
    tool_id = "maia_monitor_create"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or _default_scheduled_monitor_service()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_create",
            description=(
                "Create a new proactive monitor -- configuration only, this does NOT "
                "execute anything and does NOT send any external notification. Check "
                "the returned scheduler_task_id: null means nothing will run it "
                "automatically -- tell the user it was saved but not actively "
                "running. A non-null scheduler_task_id means it IS scheduled to run "
                "on its cadence, but that still only actually happens while the "
                "separate scheduler runtime process is running (not automatic just "
                "because this chat session is open) -- do not claim it is 'checking "
                "every day' as an unconditional guarantee. Even when a cycle does "
                "run, any resulting notification is stored internally (via "
                "maia_notifications_list) -- it is NEVER pushed or delivered into a "
                "chat session automatically. Never tell the user they will be "
                "notified 'here' or 'in this chat' -- they (or you, in a later turn) "
                "must actively check for it.\n\n"
                "On a successful creation, the result's execution_note field is the "
                "AUTHORITATIVE statement of scheduler-execution state -- reproduce "
                "its meaning faithfully in your response; do not contradict, "
                "reinterpret, weaken, strengthen, or replace it. When "
                "scheduler_task_id is null, you MUST state that nothing will "
                "execute the monitor automatically, and you MUST NOT say or imply "
                "it 'will run', 'will check', 'will execute tomorrow', 'is "
                "scheduled to run automatically', or any equivalent phrase -- even "
                "though the monitor's own enabled/status fields read 'true'/"
                "'active'. Those two fields describe only whether the monitor "
                "DEFINITION is enabled/active -- never whether anything will "
                "actually invoke it. scheduler_task_id (and execution_note derived "
                "from it) is the sole authority on scheduler binding.\n\n"
                "Scheduling is controlled by TWO separate, mutually exclusive "
                "fields -- there is no generic 'cadence' parameter to choose "
                "from. Decide which kind of check the user asked for, then "
                "supply exactly ONE of these two (or neither):\n\n"
                "- run_at: for a SINGLE future check ('controllalo domani', "
                "'ricontrollalo lunedì alle 15', 'verificalo tra due ore', "
                "'controllalo una volta domani'). Supply a full ISO 8601 "
                "datetime with a UTC offset (e.g. '2026-08-26T09:00:00+00:00') "
                "for the exact future moment requested -- compute the concrete "
                "date/time yourself from the conversation's current date; never "
                "pass a relative word like 'tomorrow' as the value itself, and "
                "never supply recurring_cadence alongside it.\n\n"
                "- recurring_cadence: for a check that repeats forever "
                "('controllalo ogni giorno' / 'controllalo giornalmente' -> "
                "'DAILY'; 'controllalo ogni ora' -> 'HOURLY'). Never supply "
                "run_at alongside it.\n\n"
                "- Neither field: a configuration-only monitor with no automatic "
                "schedule at all ('salvalo ma non eseguirlo automaticamente').\n\n"
                "This choice depends ONLY on the user's requested execution "
                "timing -- never on this monitor's subject, name, query, or the "
                "name of the capability/tool it checks. A monitor whose subject "
                "is itself a 'daily attention summary' (e.g. built around "
                "maia_daily_attention_summary, whose own name contains the word "
                "'daily') still takes run_at (not recurring_cadence) if the user "
                "asked for a one-time future check -- the word 'daily' appearing "
                "in what is being checked does not make the check itself "
                "recurring. If this call fails, quote its returned error text "
                "verbatim to the user rather than guessing or inventing a reason."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ops_capability": {"type": "string"},
                    "ops_params": {"type": "object"},
                    "second_brain_query": {"type": "string"},
                    "second_brain_domains": {"type": "array", "items": {"type": "string"}},
                    "document_query": {"type": "string"},
                    "run_at": {
                        "type": "string",
                        "description": (
                            "For a single future execution ONLY: the exact ISO "
                            "8601 datetime with UTC offset. Never combine with "
                            "recurring_cadence."
                        ),
                    },
                    "recurring_cadence": {
                        "type": "string",
                        "enum": sorted(_RECURRING_CADENCES),
                        "description": (
                            "For a check that repeats forever ONLY. Never "
                            "combine with run_at."
                        ),
                    },
                },
                "required": ["name"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        name = params.get("name")
        if not name:
            return ToolResult(tool_name="maia_monitor_create", content="Missing required parameter: name.", success=False)
        source_requirements = {
            k: params.get(k)
            for k in ("ops_capability", "ops_params", "second_brain_query", "second_brain_domains", "document_query")
            if params.get(k)
        }
        if not source_requirements:
            return ToolResult(
                tool_name="maia_monitor_create",
                content=(
                    "At least one of ops_capability/second_brain_query/"
                    "second_brain_domains/document_query is required -- a monitor "
                    "with no source to check would never produce a grounded insight."
                ),
                success=False,
            )
        # FASE 4Q.4A STEP 6 -- deterministic normalization from the new
        # model-facing run_at/recurring_cadence pair to the existing,
        # UNCHANGED MonitorService.create_monitor(cadence=..., run_at=...)
        # contract. The model never picks the low-level cadence label
        # itself: ONCE is a structural consequence of supplying run_at,
        # MANUAL a structural consequence of supplying neither.
        run_at = params.get("run_at")
        recurring_cadence = params.get("recurring_cadence")
        if run_at is not None and recurring_cadence is not None:
            return ToolResult(
                tool_name="maia_monitor_create",
                content=(
                    "run_at and recurring_cadence are mutually exclusive -- "
                    "supply run_at for a single future check OR "
                    "recurring_cadence for a repeating one, never both."
                ),
                success=False,
            )
        if recurring_cadence is not None and recurring_cadence not in _RECURRING_CADENCES:
            return ToolResult(
                tool_name="maia_monitor_create",
                content=(
                    f"Invalid recurring_cadence {recurring_cadence!r}; must be "
                    f"one of {sorted(_RECURRING_CADENCES)}."
                ),
                success=False,
            )
        if run_at is not None:
            cadence = CADENCE_ONCE
        elif recurring_cadence is not None:
            cadence = recurring_cadence
        else:
            cadence = CADENCE_MANUAL

        # FASE 4Q.3: bind the monitor to the REAL creating identity, never
        # the generic "monitor:default" MonitorService.create_monitor()'s
        # own signature would otherwise leave in place. This is what makes
        # notification principal isolation meaningful -- without it every
        # monitor created via this tool would share one identity, and
        # isolating notifications "by principal" would isolate nothing.
        # Same certified, non-model-settable mechanism Second Brain
        # already uses -- no principal parameter exists on this tool's
        # spec for the model to override.
        from openjarvis.second_brain.identity import resolve_runtime_principal

        try:
            mon = self._service.create_monitor(
                name,
                source_requirements,
                cadence=cadence,
                principal=resolve_runtime_principal(),
                run_at=run_at,
            )
        except ValueError as exc:
            return ToolResult(tool_name="maia_monitor_create", content=str(exc), success=False)
        result = _monitor_full(mon)
        # FASE 4Q.1 claim-integrity review: an explicit, structural note the
        # model sees every time -- not something it has to remember from the
        # tool description alone (mirrors the LIMITATIONS pattern used by
        # agents/operational_evidence.py's build_evidence()). Kept as an
        # additional top-level key (not a nested wrapper) so existing callers
        # reading the monitor's own fields directly (e.g. result["id"]) are
        # unaffected.
        if result.get("scheduler_task_id") is None:
            result["execution_note"] = (
                "AUTHORITATIVE scheduler-execution state -- reproduce this "
                "meaning faithfully, do not contradict/reinterpret/weaken/"
                "strengthen/replace it. No scheduler task is currently wired to "
                "this monitor -- it is saved and enabled (that describes only "
                "the monitor DEFINITION, not execution), but nothing will invoke "
                "it automatically yet. You MUST state that nothing will execute "
                "it automatically. You MUST NOT say or imply it 'will run', "
                "'will check', 'will execute tomorrow', 'is scheduled to run "
                "automatically', or any equivalent phrase. Tell the user it was "
                "saved, not that it is actively checking. Any future "
                "notification would need maia_notifications_list to be checked "
                "explicitly -- it is never pushed into this chat."
            )
        else:
            result["execution_note"] = (
                "AUTHORITATIVE scheduler-execution state -- reproduce this "
                "meaning faithfully, do not contradict/reinterpret/weaken/"
                "strengthen/replace it. A scheduler task was created for this "
                "monitor, but it only actually fires while the separate "
                "scheduler runtime process is running -- tell the user it is "
                "scheduled, not that it is guaranteed to run unless you know "
                "that process is active. Any resulting notification is never "
                "pushed into this chat -- it must be checked explicitly via "
                "maia_notifications_list."
            )
        return ToolResult(tool_name="maia_monitor_create", content=json.dumps(result), success=True)


@ToolRegistry.register("maia_monitor_enable")
class MonitorEnableTool(BaseTool):
    tool_id = "maia_monitor_enable"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or _default_scheduled_monitor_service()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_enable",
            description="Enable a disabled monitor so it resumes running on its cadence.",
            parameters={
                "type": "object",
                "properties": {"monitor_id": {"type": "string"}},
                "required": ["monitor_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            mon = self._service.enable_monitor(params.get("monitor_id", ""))
        except KeyError as exc:
            return ToolResult(tool_name="maia_monitor_enable", content=str(exc), success=False)
        return ToolResult(tool_name="maia_monitor_enable", content=json.dumps(_monitor_brief(mon)), success=True)


@ToolRegistry.register("maia_monitor_disable")
class MonitorDisableTool(BaseTool):
    tool_id = "maia_monitor_disable"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or _default_scheduled_monitor_service()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_disable",
            description="Disable a monitor -- it will not run again until re-enabled.",
            parameters={
                "type": "object",
                "properties": {"monitor_id": {"type": "string"}},
                "required": ["monitor_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            mon = self._service.disable_monitor(params.get("monitor_id", ""))
        except KeyError as exc:
            return ToolResult(tool_name="maia_monitor_disable", content=str(exc), success=False)
        return ToolResult(tool_name="maia_monitor_disable", content=json.dumps(_monitor_brief(mon)), success=True)


@ToolRegistry.register("maia_monitor_run_now")
class MonitorRunNowTool(BaseTool):
    tool_id = "maia_monitor_run_now"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_run_now",
            description=(
                "Run one monitor cycle immediately (does not wait for its cadence). "
                "Still read-only and still only produces an internal notification for "
                "an actual change -- never an external side effect."
            ),
            parameters={
                "type": "object",
                "properties": {"monitor_id": {"type": "string"}},
                "required": ["monitor_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        monitor_id = params.get("monitor_id", "")
        try:
            run, notifications = self._service.run_cycle(monitor_id)
        except KeyError as exc:
            return ToolResult(tool_name="maia_monitor_run_now", content=str(exc), success=False)
        return ToolResult(
            tool_name="maia_monitor_run_now",
            content=json.dumps(
                {
                    "run_status": run.status,
                    "evidence_collected": run.evidence_collected,
                    "insights_generated": run.insights_generated,
                    "errors": run.errors,
                    "notifications": [_notification_full(n) for n in notifications],
                }
            ),
            success=run.status != "failed",
        )


@ToolRegistry.register("maia_daily_attention_summary")
class DailyAttentionSummaryTool(BaseTool):
    """FASE 4Q.4 -- the one small primitive the architecture audit found
    genuinely missing: a deterministic, explainable grouping/priority
    ordering over the current user's own notifications. Pure computation
    over MonitorService.list_notifications() -- no new storage, no new
    service, reuses Notification Runtime V1 exactly as it already is."""

    tool_id = "maia_daily_attention_summary"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_daily_attention_summary",
            description=(
                "Get the current user's notifications already grouped and "
                "prioritized -- this is the right first call for a natural "
                "'what should I look at today / is there anything I need to "
                "know about' question, instead of guessing from a raw list. "
                "Returns three groups: attention_items (genuinely need review, "
                "ordered by severity then how new/reopened then recency -- each "
                "with an explicit priority_reason you can quote, never an "
                "opaque score), acknowledged (already seen -- do not present "
                "these as new), and informational (e.g. a RESOLVED transition -- "
                "useful context, not a current problem). If attention_items is "
                "empty, say so plainly rather than inventing urgency -- you may "
                "then offer informational status if it's actually useful. This "
                "reflects only what is genuinely persisted; it does not mean "
                "anything was pushed to the user, and marking/acknowledging a "
                "notification never resolves the underlying issue or performs "
                "any business action."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        notifications = self._service.list_notifications(principal=resolve_runtime_principal())
        summary = build_attention_summary(notifications)
        return ToolResult(
            tool_name="maia_daily_attention_summary",
            content=json.dumps(summary),
            success=True,
            metadata={
                "num_attention_items": len(summary["attention_items"]),
                "num_acknowledged": len(summary["acknowledged"]),
                "num_informational": len(summary["informational"]),
            },
        )


@ToolRegistry.register("maia_notifications_list")
class NotificationsListTool(BaseTool):
    tool_id = "maia_notifications_list"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notifications_list",
            description=(
                "List internal notifications for the current user -- optionally "
                "filtered by monitor, acknowledged state, severity, or unread_only. "
                "These are stored internally only; nothing is pushed or delivered "
                "anywhere automatically -- this tool is how you actually find out "
                "whether anything is waiting for attention. Never claim the user "
                "will be notified elsewhere; only what this list actually returns."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string"},
                    "acknowledged": {"type": "boolean"},
                    "unread_only": {"type": "boolean", "description": "Only notifications not yet marked read."},
                    "severity": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        # FASE 4Q.3 STEP 4: principal always comes from the real runtime
        # identity -- there is deliberately no "principal" parameter in
        # this tool's own schema for the model to supply or override.
        from openjarvis.second_brain.identity import resolve_runtime_principal

        notifications = self._service.list_notifications(
            principal=resolve_runtime_principal(),
            monitor_id=params.get("monitor_id"),
            acknowledged=params.get("acknowledged"),
            unread_only=bool(params.get("unread_only", False)),
            severity=params.get("severity"),
            limit=params.get("limit"),
        )
        return ToolResult(
            tool_name="maia_notifications_list",
            content=json.dumps([_notification_brief(n) for n in notifications]),
            success=True,
            metadata={"num_notifications": len(notifications)},
        )


@ToolRegistry.register("maia_notifications_unread_count")
class NotificationsUnreadCountTool(BaseTool):
    tool_id = "maia_notifications_unread_count"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notifications_unread_count",
            description="How many of the current user's notifications are unread right now.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        count = self._service.count_unread_notifications(principal=resolve_runtime_principal())
        return ToolResult(
            tool_name="maia_notifications_unread_count",
            content=json.dumps({"unread_count": count}),
            success=True,
        )


@ToolRegistry.register("maia_notification_get")
class NotificationGetTool(BaseTool):
    tool_id = "maia_notification_get"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notification_get",
            description="Get full detail (including raw evidence) for one of the current user's notifications by id.",
            parameters={
                "type": "object",
                "properties": {"notification_id": {"type": "string"}},
                "required": ["notification_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        n = self._service.get_notification(
            params.get("notification_id", ""), principal=resolve_runtime_principal()
        )
        if n is None:
            # FASE 4Q.3 STEP 4: identical wording whether the id is
            # genuinely unknown or belongs to a different principal --
            # never confirms a notification exists that isn't this
            # principal's to see.
            return ToolResult(tool_name="maia_notification_get", content="Notification not found.", success=False)
        return ToolResult(tool_name="maia_notification_get", content=json.dumps(_notification_full(n)), success=True)


@ToolRegistry.register("maia_notification_mark_read")
class NotificationMarkReadTool(BaseTool):
    tool_id = "maia_notification_mark_read"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notification_mark_read",
            description=(
                "Mark one of the current user's notifications as read -- purely "
                "informational, distinct from acknowledging it. Does not resolve "
                "anything and never implies any business action was taken."
            ),
            parameters={
                "type": "object",
                "properties": {"notification_id": {"type": "string"}},
                "required": ["notification_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        try:
            n = self._service.mark_notification_read(
                params.get("notification_id", ""), principal=resolve_runtime_principal()
            )
        except KeyError as exc:
            return ToolResult(tool_name="maia_notification_mark_read", content=str(exc), success=False)
        return ToolResult(
            tool_name="maia_notification_mark_read", content=json.dumps(_notification_full(n)), success=True
        )


@ToolRegistry.register("maia_notification_acknowledge")
class NotificationAcknowledgeTool(BaseTool):
    tool_id = "maia_notification_acknowledge"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notification_acknowledge",
            description=(
                "Mark one of the current user's notifications as acknowledged "
                "(the user has explicitly seen and accepted it). This is strictly "
                "informational -- it NEVER means the underlying issue was resolved, "
                "a corrective action was taken, a governed action was approved, the "
                "monitor itself was resolved, or any business/ERP system was "
                "updated. Only ever say the notification was acknowledged, nothing "
                "more."
            ),
            parameters={
                "type": "object",
                "properties": {"notification_id": {"type": "string"}},
                "required": ["notification_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        try:
            n = self._service.acknowledge_notification(
                params.get("notification_id", ""), principal=resolve_runtime_principal()
            )
        except KeyError as exc:
            return ToolResult(tool_name="maia_notification_acknowledge", content=str(exc), success=False)
        return ToolResult(
            tool_name="maia_notification_acknowledge", content=json.dumps(_notification_full(n)), success=True
        )


__all__ = [
    "MonitorsListTool",
    "MonitorGetTool",
    "MonitorCreateTool",
    "MonitorEnableTool",
    "MonitorDisableTool",
    "MonitorRunNowTool",
    "DailyAttentionSummaryTool",
    "NotificationsListTool",
    "NotificationsUnreadCountTool",
    "NotificationGetTool",
    "NotificationMarkReadTool",
    "NotificationAcknowledgeTool",
]
