"""FASE 4P.2 STEP 14 -- tool-registration-level coverage (O: no execution
tool exposed) plus basic per-tool smoke tests. Service-level lifecycle/
dedup/failure/governance coverage lives in tests/monitoring/."""

from __future__ import annotations

import json
import tempfile

import openjarvis.tools.monitoring_tools  # noqa: F401 -- triggers @ToolRegistry.register
from openjarvis.core.registry import ToolRegistry
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore


def _svc() -> MonitorService:
    return MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))


class TestToolRegistration:
    def test_all_nine_tools_registered(self):
        """The project's own autouse fixture (tests/conftest.py) clears
        every registry before each test; module import-time
        @ToolRegistry.register side effects only ran once, at first
        import. importlib.reload re-runs the decorators -- matching the
        established pattern in tests/tools/test_proactive_insight_tools.py."""
        import importlib

        import openjarvis.tools.monitoring_tools as mod

        importlib.reload(mod)

        for name in (
            "maia_monitors_list",
            "maia_monitor_get",
            "maia_monitor_create",
            "maia_monitor_enable",
            "maia_monitor_disable",
            "maia_monitor_run_now",
            "maia_notifications_list",
            "maia_notification_get",
            "maia_notification_acknowledge",
        ):
            assert ToolRegistry.contains(name), f"{name} not registered"

    def test_o_no_execution_tool_registered(self):
        """STEP 13's hard requirement, mirrored from FASE 4P.1's own
        boundary: no generic execute_action/run_action/do_it-shaped tool
        exists anywhere in this module."""
        import openjarvis.tools.monitoring_tools as mod

        forbidden_substrings = ("execute_action", "run_action", "do_it", "send_anything", "write_anything")
        for name in dir(mod):
            lowered = name.lower()
            for bad in forbidden_substrings:
                assert bad not in lowered, f"found forbidden-looking tool name: {name}"


class TestCreateListGetEnableDisableRunNow:
    def test_create_then_list_then_get(self):
        from openjarvis.tools.monitoring_tools import MonitorCreateTool, MonitorGetTool, MonitorsListTool

        svc = _svc()
        create = MonitorCreateTool(service=svc)
        result = create.execute(name="test monitor", ops_capability="ops.production.get_kpi")
        assert result.success is True
        mon_id = json.loads(result.content)["id"]

        listed = MonitorsListTool(service=svc).execute()
        assert json.loads(listed.content)[0]["id"] == mon_id

        got = MonitorGetTool(service=svc).execute(monitor_id=mon_id)
        assert got.success is True
        assert json.loads(got.content)["source_requirements"]["ops_capability"] == "ops.production.get_kpi"

    def test_create_requires_at_least_one_source(self):
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(name="empty")
        assert result.success is False

    def test_enable_disable_round_trip(self):
        from openjarvis.tools.monitoring_tools import MonitorCreateTool, MonitorDisableTool, MonitorEnableTool

        svc = _svc()
        mon_id = json.loads(
            MonitorCreateTool(service=svc).execute(name="x", ops_capability="ops.production.get_kpi").content
        )["id"]
        disabled = MonitorDisableTool(service=svc).execute(monitor_id=mon_id)
        assert json.loads(disabled.content)["enabled"] is False
        enabled = MonitorEnableTool(service=svc).execute(monitor_id=mon_id)
        assert json.loads(enabled.content)["enabled"] is True

    def test_get_unknown_monitor_fails_honestly(self):
        from openjarvis.tools.monitoring_tools import MonitorGetTool

        result = MonitorGetTool(service=_svc()).execute(monitor_id="nonexistent")
        assert result.success is False

    def test_run_now_via_tool(self, monkeypatch):
        import openjarvis.tools.proactive_insight_tools as pit
        from openjarvis.core.types import ToolResult
        from openjarvis.tools.monitoring_tools import MonitorCreateTool, MonitorRunNowTool

        def fake_call_ops(self, capability, params):
            return ToolResult(
                tool_name="x",
                success=True,
                content="ok",
                metadata={
                    "status": "ok",
                    "period": "2026-07",
                    "source": {},
                    "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
                },
            )

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc()
        mon_id = json.loads(
            MonitorCreateTool(service=svc).execute(name="x", ops_capability="ops.production.get_kpi").content
        )["id"]
        result = MonitorRunNowTool(service=svc).execute(monitor_id=mon_id)
        assert result.success is True
        payload = json.loads(result.content)
        assert payload["insights_generated"] == 1
        assert len(payload["notifications"]) == 1
        assert payload["notifications"][0]["transition"] == "NEW"


class TestNotificationTools:
    def test_list_get_acknowledge_round_trip(self, monkeypatch):
        import openjarvis.tools.proactive_insight_tools as pit
        from openjarvis.core.types import ToolResult
        from openjarvis.tools.monitoring_tools import (
            NotificationAcknowledgeTool,
            NotificationGetTool,
            NotificationsListTool,
        )

        def fake_call_ops(self, capability, params):
            return ToolResult(
                tool_name="x",
                success=True,
                content="ok",
                metadata={
                    "status": "ok",
                    "period": "2026-07",
                    "source": {},
                    "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
                },
            )

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)

        listed = NotificationsListTool(service=svc).execute()
        notifications = json.loads(listed.content)
        assert len(notifications) == 1
        nid = notifications[0]["id"]

        got = NotificationGetTool(service=svc).execute(notification_id=nid)
        assert got.success is True

        ack = NotificationAcknowledgeTool(service=svc).execute(notification_id=nid)
        assert json.loads(ack.content)["acknowledged"] is True

        unacked = NotificationsListTool(service=svc).execute(acknowledged=False)
        assert json.loads(unacked.content) == []
