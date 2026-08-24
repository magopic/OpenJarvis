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
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.types import VALID_CADENCES
from openjarvis.tools._stubs import BaseTool, ToolSpec


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
        "fingerprint": n.fingerprint,
        "transition": n.transition,
        "insight_snapshot": n.insight_snapshot,
        "created_at": n.created_at,
        "acknowledged": n.acknowledged,
        "acknowledged_at": n.acknowledged_at,
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
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_monitor_create",
            description=(
                "Create a new proactive monitor -- configuration only, this does NOT "
                "execute anything and does NOT send any external notification. The "
                "monitor is saved and enabled for its cadence, but it only actually "
                "runs if a scheduler process is configured to invoke it -- check the "
                "returned scheduler_task_id: null means nothing will run it "
                "automatically yet, in which case tell the user this was saved but "
                "is not yet actively running, rather than promising it will check "
                "'every day' on its own. Even when a cycle does run, any resulting "
                "notification is stored internally (via maia_notifications_list) -- "
                "it is NEVER pushed or delivered into a chat session automatically. "
                "Never tell the user they will be notified 'here' or 'in this chat' "
                "-- they (or you, in a later turn) must actively check for it."
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
                    "cadence": {"type": "string", "enum": sorted(VALID_CADENCES)},
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
        try:
            mon = self._service.create_monitor(
                name, source_requirements, cadence=params.get("cadence", "MANUAL")
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
                "No scheduler task is currently wired to this monitor -- it is "
                "saved and enabled, but nothing will invoke it automatically yet. "
                "Tell the user it was saved, not that it is actively checking. "
                "Any future notification would need maia_notifications_list to "
                "be checked explicitly -- it is never pushed into this chat."
            )
        return ToolResult(tool_name="maia_monitor_create", content=json.dumps(result), success=True)


@ToolRegistry.register("maia_monitor_enable")
class MonitorEnableTool(BaseTool):
    tool_id = "maia_monitor_enable"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

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
        self._service = service or MonitorService()

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


@ToolRegistry.register("maia_notifications_list")
class NotificationsListTool(BaseTool):
    tool_id = "maia_notifications_list"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notifications_list",
            description="List internal monitor notifications, optionally filtered by monitor or acknowledged state.",
            parameters={
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string"},
                    "acknowledged": {"type": "boolean"},
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        notifications = self._service.list_notifications(
            monitor_id=params.get("monitor_id"), acknowledged=params.get("acknowledged")
        )
        return ToolResult(
            tool_name="maia_notifications_list",
            content=json.dumps([_notification_full(n) for n in notifications]),
            success=True,
            metadata={"num_notifications": len(notifications)},
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
            description="Get full detail for one notification by id.",
            parameters={
                "type": "object",
                "properties": {"notification_id": {"type": "string"}},
                "required": ["notification_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        n = self._service.get_notification(params.get("notification_id", ""))
        if n is None:
            return ToolResult(tool_name="maia_notification_get", content="Notification not found.", success=False)
        return ToolResult(tool_name="maia_notification_get", content=json.dumps(_notification_full(n)), success=True)


@ToolRegistry.register("maia_notification_acknowledge")
class NotificationAcknowledgeTool(BaseTool):
    tool_id = "maia_notification_acknowledge"

    def __init__(self, service: Optional[MonitorService] = None) -> None:
        self._service = service or MonitorService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_notification_acknowledge",
            description="Mark a notification as acknowledged (seen) -- does not resolve the underlying issue.",
            parameters={
                "type": "object",
                "properties": {"notification_id": {"type": "string"}},
                "required": ["notification_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            n = self._service.acknowledge_notification(params.get("notification_id", ""))
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
    "NotificationsListTool",
    "NotificationGetTool",
    "NotificationAcknowledgeTool",
]
