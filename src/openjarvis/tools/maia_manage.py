"""FASE 4P.2A -- a single, typed gateway over MAIA's insight/monitor/
notification management surface, mirroring the established `_manage`
convention already used by `memory_manage.py`/`user_profile_manage.py`/
`skill_manage.py` (one tool, an `operation` enum, dispatch internally) --
STEP 1 found this pattern already exists and is already proven in
production, so this reuses it rather than inventing something new.

STEP 4/6: this is a thin ROUTER, not a reimplementation. Every operation
constructs and calls the exact same already-tested tool object FASE
4P.1/4P.2 built (`ProactiveAnalyzeTool`, `ProactiveInsightsListTool`,
`MonitorsListTool`, etc.) -- zero business logic is duplicated here. The
underlying direct tools (`maia_analyze_evidence_for_insights`,
`maia_monitor_*`, `maia_notification_*`, ...) remain registered and
usable; this is an ADDITIONAL, consolidated surface, not a replacement.

STEP 5: no arbitrary method names, no free-form Python, no generic
reflection -- `operation` is a closed enum, checked against an explicit
allowlist dict; each operation has its own small, explicit argument
validation.

STEP 7: dispatches to tools whose ToolResult.success/content already
carry the frozen FASE 4P.1B claim-integrity contract (NOT_AVAILABLE for
an unsupported OPS capability, honest failure for a missing monitor/
notification id) -- this gateway does not add or weaken that; an invalid
top-level `operation` gets the same explicit-rejection treatment.
"""

from __future__ import annotations

from typing import Any, Dict

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

OP_INSIGHT_ANALYZE = "INSIGHT_ANALYZE"
OP_INSIGHT_LIST = "INSIGHT_LIST"
OP_INSIGHT_GET = "INSIGHT_GET"
OP_ACTION_PROPOSALS_LIST = "ACTION_PROPOSALS_LIST"
OP_ACTION_PROPOSAL_GET = "ACTION_PROPOSAL_GET"
OP_MONITOR_LIST = "MONITOR_LIST"
OP_MONITOR_GET = "MONITOR_GET"
OP_MONITOR_CREATE = "MONITOR_CREATE"
OP_MONITOR_ENABLE = "MONITOR_ENABLE"
OP_MONITOR_DISABLE = "MONITOR_DISABLE"
OP_MONITOR_RUN_NOW = "MONITOR_RUN_NOW"
OP_NOTIFICATION_LIST = "NOTIFICATION_LIST"
OP_NOTIFICATION_UNREAD_COUNT = "NOTIFICATION_UNREAD_COUNT"
OP_NOTIFICATION_GET = "NOTIFICATION_GET"
OP_NOTIFICATION_MARK_READ = "NOTIFICATION_MARK_READ"
OP_NOTIFICATION_ACKNOWLEDGE = "NOTIFICATION_ACKNOWLEDGE"

_ALL_OPERATIONS = (
    OP_INSIGHT_ANALYZE,
    OP_INSIGHT_LIST,
    OP_INSIGHT_GET,
    OP_ACTION_PROPOSALS_LIST,
    OP_ACTION_PROPOSAL_GET,
    OP_MONITOR_LIST,
    OP_MONITOR_GET,
    OP_MONITOR_CREATE,
    OP_MONITOR_ENABLE,
    OP_MONITOR_DISABLE,
    OP_MONITOR_RUN_NOW,
    OP_NOTIFICATION_LIST,
    OP_NOTIFICATION_UNREAD_COUNT,
    OP_NOTIFICATION_GET,
    OP_NOTIFICATION_MARK_READ,
    OP_NOTIFICATION_ACKNOWLEDGE,
)


@ToolRegistry.register("maia_manage")
class MaiaManageTool(BaseTool):
    """One typed gateway over insight/monitor/notification management.
    No business execution -- every operation here is read/detect/
    configuration, exactly matching the union of what the 14 individual
    maia_* tools already exposed (see proactive_insight_tools.py,
    monitoring_tools.py). No email/calendar/ERP; no operation here can
    ever leave a business system's boundary."""

    tool_id = "maia_manage"

    def __init__(self) -> None:
        pass

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_manage",
            description=(
                "Single gateway for all MAIA insight/monitor/notification "
                "management operations. Choose one `operation` and pass only "
                "the arguments it needs. This never executes a business action "
                "or sends anything externally -- every operation is read, "
                "detect, or configuration only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": list(_ALL_OPERATIONS)},
                    "ops_capability": {"type": "string", "description": "INSIGHT_ANALYZE"},
                    "ops_params": {"type": "object", "description": "INSIGHT_ANALYZE"},
                    "second_brain_query": {"type": "string", "description": "INSIGHT_ANALYZE / MONITOR_CREATE"},
                    "second_brain_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "INSIGHT_ANALYZE / MONITOR_CREATE",
                    },
                    "document_query": {"type": "string", "description": "INSIGHT_ANALYZE / MONITOR_CREATE"},
                    "insight_id": {"type": "string", "description": "INSIGHT_GET"},
                    "action_id": {"type": "string", "description": "ACTION_PROPOSAL_GET"},
                    "monitor_id": {
                        "type": "string",
                        "description": "MONITOR_GET / MONITOR_ENABLE / MONITOR_DISABLE / MONITOR_RUN_NOW",
                    },
                    "name": {"type": "string", "description": "MONITOR_CREATE"},
                    "cadence": {"type": "string", "description": "MONITOR_CREATE (HOURLY/DAILY/MANUAL)"},
                    "enabled": {"type": "boolean", "description": "MONITOR_LIST filter"},
                    "notification_id": {
                        "type": "string",
                        "description": "NOTIFICATION_GET / NOTIFICATION_MARK_READ / NOTIFICATION_ACKNOWLEDGE",
                    },
                    "acknowledged": {"type": "boolean", "description": "NOTIFICATION_LIST filter"},
                    "unread_only": {"type": "boolean", "description": "NOTIFICATION_LIST filter"},
                    "severity": {"type": "string", "description": "NOTIFICATION_LIST filter"},
                    "limit": {"type": "integer", "description": "NOTIFICATION_LIST filter"},
                },
                "required": ["operation"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        operation = params.get("operation")
        if operation not in _ALL_OPERATIONS:
            return ToolResult(
                tool_name="maia_manage",
                content=f"Unknown operation: {operation!r}. Valid operations: {sorted(_ALL_OPERATIONS)}",
                success=False,
            )
        handler = _HANDLERS[operation]
        return handler(params)


def _require(params: Dict[str, Any], key: str) -> "str | None":
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _missing_arg_result(operation: str, key: str) -> ToolResult:
    return ToolResult(
        tool_name="maia_manage",
        content=f"{operation} requires a non-empty string argument {key!r}.",
        success=False,
    )


def _op_insight_analyze(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.proactive_insight_tools import ProactiveAnalyzeTool

    return ProactiveAnalyzeTool().execute(
        ops_capability=params.get("ops_capability"),
        ops_params=params.get("ops_params"),
        second_brain_query=params.get("second_brain_query"),
        second_brain_domains=params.get("second_brain_domains"),
        document_query=params.get("document_query"),
    )


def _op_insight_list(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.proactive_insight_tools import ProactiveInsightsListTool

    return ProactiveInsightsListTool().execute()


def _op_insight_get(params: Dict[str, Any]) -> ToolResult:
    insight_id = _require(params, "insight_id")
    if insight_id is None:
        return _missing_arg_result(OP_INSIGHT_GET, "insight_id")
    from openjarvis.tools.proactive_insight_tools import ProactiveInsightGetTool

    return ProactiveInsightGetTool().execute(insight_id=insight_id)


def _op_action_proposals_list(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.proactive_insight_tools import ProactiveActionProposalsListTool

    return ProactiveActionProposalsListTool().execute()


def _op_action_proposal_get(params: Dict[str, Any]) -> ToolResult:
    action_id = _require(params, "action_id")
    if action_id is None:
        return _missing_arg_result(OP_ACTION_PROPOSAL_GET, "action_id")
    from openjarvis.tools.proactive_insight_tools import ProactiveActionProposalGetTool

    return ProactiveActionProposalGetTool().execute(action_id=action_id)


def _op_monitor_list(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.monitoring_tools import MonitorsListTool

    return MonitorsListTool().execute(enabled=params.get("enabled"))


def _op_monitor_get(params: Dict[str, Any]) -> ToolResult:
    monitor_id = _require(params, "monitor_id")
    if monitor_id is None:
        return _missing_arg_result(OP_MONITOR_GET, "monitor_id")
    from openjarvis.tools.monitoring_tools import MonitorGetTool

    return MonitorGetTool().execute(monitor_id=monitor_id)


def _op_monitor_create(params: Dict[str, Any]) -> ToolResult:
    name = _require(params, "name")
    if name is None:
        return _missing_arg_result(OP_MONITOR_CREATE, "name")
    from openjarvis.tools.monitoring_tools import MonitorCreateTool

    return MonitorCreateTool().execute(
        name=name,
        ops_capability=params.get("ops_capability"),
        ops_params=params.get("ops_params"),
        second_brain_query=params.get("second_brain_query"),
        second_brain_domains=params.get("second_brain_domains"),
        document_query=params.get("document_query"),
        cadence=params.get("cadence", "MANUAL"),
    )


def _op_monitor_enable(params: Dict[str, Any]) -> ToolResult:
    monitor_id = _require(params, "monitor_id")
    if monitor_id is None:
        return _missing_arg_result(OP_MONITOR_ENABLE, "monitor_id")
    from openjarvis.tools.monitoring_tools import MonitorEnableTool

    return MonitorEnableTool().execute(monitor_id=monitor_id)


def _op_monitor_disable(params: Dict[str, Any]) -> ToolResult:
    monitor_id = _require(params, "monitor_id")
    if monitor_id is None:
        return _missing_arg_result(OP_MONITOR_DISABLE, "monitor_id")
    from openjarvis.tools.monitoring_tools import MonitorDisableTool

    return MonitorDisableTool().execute(monitor_id=monitor_id)


def _op_monitor_run_now(params: Dict[str, Any]) -> ToolResult:
    monitor_id = _require(params, "monitor_id")
    if monitor_id is None:
        return _missing_arg_result(OP_MONITOR_RUN_NOW, "monitor_id")
    from openjarvis.tools.monitoring_tools import MonitorRunNowTool

    return MonitorRunNowTool().execute(monitor_id=monitor_id)


def _op_notification_list(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.monitoring_tools import NotificationsListTool

    return NotificationsListTool().execute(
        monitor_id=params.get("monitor_id"),
        acknowledged=params.get("acknowledged"),
        unread_only=params.get("unread_only"),
        severity=params.get("severity"),
        limit=params.get("limit"),
    )


def _op_notification_unread_count(params: Dict[str, Any]) -> ToolResult:
    from openjarvis.tools.monitoring_tools import NotificationsUnreadCountTool

    return NotificationsUnreadCountTool().execute()


def _op_notification_get(params: Dict[str, Any]) -> ToolResult:
    notification_id = _require(params, "notification_id")
    if notification_id is None:
        return _missing_arg_result(OP_NOTIFICATION_GET, "notification_id")
    from openjarvis.tools.monitoring_tools import NotificationGetTool

    return NotificationGetTool().execute(notification_id=notification_id)


def _op_notification_mark_read(params: Dict[str, Any]) -> ToolResult:
    notification_id = _require(params, "notification_id")
    if notification_id is None:
        return _missing_arg_result(OP_NOTIFICATION_MARK_READ, "notification_id")
    from openjarvis.tools.monitoring_tools import NotificationMarkReadTool

    return NotificationMarkReadTool().execute(notification_id=notification_id)


def _op_notification_acknowledge(params: Dict[str, Any]) -> ToolResult:
    notification_id = _require(params, "notification_id")
    if notification_id is None:
        return _missing_arg_result(OP_NOTIFICATION_ACKNOWLEDGE, "notification_id")
    from openjarvis.tools.monitoring_tools import NotificationAcknowledgeTool

    return NotificationAcknowledgeTool().execute(notification_id=notification_id)


_HANDLERS = {
    OP_INSIGHT_ANALYZE: _op_insight_analyze,
    OP_INSIGHT_LIST: _op_insight_list,
    OP_INSIGHT_GET: _op_insight_get,
    OP_ACTION_PROPOSALS_LIST: _op_action_proposals_list,
    OP_ACTION_PROPOSAL_GET: _op_action_proposal_get,
    OP_MONITOR_LIST: _op_monitor_list,
    OP_MONITOR_GET: _op_monitor_get,
    OP_MONITOR_CREATE: _op_monitor_create,
    OP_MONITOR_ENABLE: _op_monitor_enable,
    OP_MONITOR_DISABLE: _op_monitor_disable,
    OP_MONITOR_RUN_NOW: _op_monitor_run_now,
    OP_NOTIFICATION_LIST: _op_notification_list,
    OP_NOTIFICATION_UNREAD_COUNT: _op_notification_unread_count,
    OP_NOTIFICATION_GET: _op_notification_get,
    OP_NOTIFICATION_MARK_READ: _op_notification_mark_read,
    OP_NOTIFICATION_ACKNOWLEDGE: _op_notification_acknowledge,
}


__all__ = [
    "MaiaManageTool",
    "OP_INSIGHT_ANALYZE",
    "OP_INSIGHT_LIST",
    "OP_INSIGHT_GET",
    "OP_ACTION_PROPOSALS_LIST",
    "OP_ACTION_PROPOSAL_GET",
    "OP_MONITOR_LIST",
    "OP_MONITOR_GET",
    "OP_MONITOR_CREATE",
    "OP_MONITOR_ENABLE",
    "OP_MONITOR_DISABLE",
    "OP_MONITOR_RUN_NOW",
    "OP_NOTIFICATION_LIST",
    "OP_NOTIFICATION_GET",
    "OP_NOTIFICATION_ACKNOWLEDGE",
]
