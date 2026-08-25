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

    def test_execution_note_present_when_scheduler_task_id_none(self):
        """FASE 4Q.4A final narration fix, TEST 1 -- a successful creation
        with no scheduler binding must include execution_note."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="x", ops_capability="ops.production.get_kpi"
        )
        assert result.success is True
        body = json.loads(result.content)
        assert body["scheduler_task_id"] is None
        assert "execution_note" in body

    def test_execution_note_states_no_automatic_invocation(self):
        """TEST 2 -- the note must explicitly rule out automatic
        execution, not just describe the monitor as saved."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="x", ops_capability="ops.production.get_kpi"
        )
        note = json.loads(result.content)["execution_note"].lower()
        assert "nothing will" in note or "must not" in note
        assert "automatically" in note

    def test_description_declares_execution_note_authoritative(self):
        """TEST 3 -- the model-facing description must say execution_note
        is authoritative and must not be contradicted/reinterpreted."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        desc = MonitorCreateTool(service=_svc()).spec.description.lower()
        assert "execution_note" in desc
        assert "authoritative" in desc
        assert "contradict" in desc

    def test_description_decouples_enabled_status_from_scheduler_execution(self):
        """TEST 4 -- enabled=true/status=active must not be presentable as
        proof of scheduler execution; scheduler_task_id is the sole
        authority."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        desc = MonitorCreateTool(service=_svc()).spec.description.lower()
        assert "enabled" in desc and "status" in desc
        assert "sole authority" in desc

    def test_execution_note_absent_of_no_invocation_language_when_scheduler_bound(self):
        """TEST 5 -- when a real scheduler_task_id exists, the note must
        NOT say nothing will invoke it automatically -- the opposite
        branch must actually fire."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        class _FakeTask:
            def __init__(self, id: str) -> None:
                self.id = id

        class _FakeScheduler:
            def get_task(self, task_id: str):
                return None

            def create_task(self, **kwargs):
                return _FakeTask(kwargs["task_id"])

        svc = MonitorService(
            store=MonitorStore(tempfile.mktemp(suffix=".db")), scheduler=_FakeScheduler()
        )
        result = MonitorCreateTool(service=svc).execute(
            name="x", ops_capability="ops.production.get_kpi", recurring_cadence="DAILY"
        )
        body = json.loads(result.content)
        assert body["scheduler_task_id"] is not None
        note = body["execution_note"].lower()
        assert "nothing will invoke it automatically" not in note
        assert "scheduler task was created" in note

    def test_run_at_only_calls_backend_with_once_and_preserves_run_at(self):
        """FASE 4Q.4A STEP 6, TEST 1 -- supplying run_at alone (the new
        model-facing contract) must produce a real ONCE monitor with the
        exact requested timestamp, never DAILY."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="check tomorrow",
            ops_capability="ops.production.get_kpi",
            run_at="2026-08-26T09:00:00+00:00",
        )
        assert result.success is True
        body = json.loads(result.content)
        assert body["cadence"] == "ONCE"
        assert body["cadence"] != "DAILY"
        assert body["run_at"] == "2026-08-26T09:00:00+00:00"

    def test_recurring_cadence_daily_only(self):
        """TEST 2."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="daily check",
            ops_capability="ops.production.get_kpi",
            recurring_cadence="DAILY",
        )
        assert result.success is True
        body = json.loads(result.content)
        assert body["cadence"] == "DAILY"
        assert body["run_at"] is None

    def test_recurring_cadence_hourly_only(self):
        """TEST 3."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="hourly check",
            ops_capability="ops.production.get_kpi",
            recurring_cadence="HOURLY",
        )
        assert result.success is True
        body = json.loads(result.content)
        assert body["cadence"] == "HOURLY"
        assert body["run_at"] is None

    def test_neither_field_defaults_to_manual(self):
        """TEST 4."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="manual check", ops_capability="ops.production.get_kpi"
        )
        assert result.success is True
        body = json.loads(result.content)
        assert body["cadence"] == "MANUAL"
        assert body["run_at"] is None

    def test_run_at_and_recurring_cadence_together_rejected_nothing_persisted(self):
        """TEST 5 -- mutually exclusive; must fail before persistence and
        must never touch the scheduler either."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool, MonitorsListTool

        class _SpyScheduler:
            def __init__(self) -> None:
                self.create_calls = 0

            def get_task(self, task_id):
                return None

            def create_task(self, **kwargs):
                self.create_calls += 1
                raise AssertionError("scheduler must not be touched")

        scheduler = _SpyScheduler()
        svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")), scheduler=scheduler)
        result = MonitorCreateTool(service=svc).execute(
            name="conflicting",
            ops_capability="ops.production.get_kpi",
            run_at="2026-08-26T09:00:00+00:00",
            recurring_cadence="DAILY",
        )
        assert result.success is False
        assert "mutually exclusive" in result.content.lower()
        assert json.loads(MonitorsListTool(service=svc).execute().content) == []
        assert scheduler.create_calls == 0

    def test_recurring_cadence_once_rejected(self):
        """TEST 6 -- ONCE is not a nameable recurring_cadence value; it is
        implied structurally by run_at instead."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="x", ops_capability="ops.production.get_kpi", recurring_cadence="ONCE"
        )
        assert result.success is False

    def test_recurring_cadence_manual_rejected(self):
        """TEST 7 -- MANUAL is not a nameable recurring_cadence value; it
        is implied structurally by supplying neither field."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        result = MonitorCreateTool(service=_svc()).execute(
            name="x", ops_capability="ops.production.get_kpi", recurring_cadence="MANUAL"
        )
        assert result.success is False

    def test_old_cadence_parameter_absent_from_schema(self):
        """TEST 8."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        spec = MonitorCreateTool(service=_svc()).spec
        assert "cadence" not in spec.parameters["properties"]

    def test_recurring_cadence_enum_excludes_once_and_manual(self):
        """TEST 9."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        spec = MonitorCreateTool(service=_svc()).spec
        enum = set(spec.parameters["properties"]["recurring_cadence"]["enum"])
        assert enum == {"HOURLY", "DAILY"}

    def test_run_at_still_exposed_in_schema(self):
        """TEST 10."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        spec = MonitorCreateTool(service=_svc()).spec
        assert "run_at" in spec.parameters["properties"]

    def test_description_decouples_scheduling_from_daily_named_subject(self):
        """FASE 4Q.4A -- the live certification failure, repeated twice:
        the model chose a recurring DAILY-shaped check for 'Controllalo
        domani' after building a monitor around a 'daily attention
        summary' subject (maia_daily_attention_summary, whose own name
        contains 'daily'). The description must explicitly say scheduling
        is independent of the monitor's subject/name/tool, and must name
        this exact confound."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        desc = MonitorCreateTool(service=_svc()).spec.description
        assert "subject" in desc.lower() or "name" in desc.lower()
        assert "maia_daily_attention_summary" in desc
        assert "controllalo domani" in desc.lower()
        assert "run_at" in desc

    def test_description_still_maps_genuinely_recurring_intent_to_daily(self):
        """The fix must not overcorrect -- a genuinely recurring 'every
        day' request must still map to recurring_cadence='DAILY'."""
        from openjarvis.tools.monitoring_tools import MonitorCreateTool

        desc = MonitorCreateTool(service=_svc()).spec.description.lower()
        assert "ogni giorno" in desc or "giornalmente" in desc
        assert "'daily'" in desc

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
        # FASE 4Q.3: the notification tools now resolve the REAL runtime
        # principal internally (never a tool argument) -- pin it here so
        # the monitor created directly via svc.create_monitor() (bypassing
        # MonitorCreateTool, which is what normally binds this) matches
        # what the read-side tools will filter by.
        import openjarvis.second_brain.identity as identity_mod

        monkeypatch.setattr(identity_mod, "resolve_runtime_principal", lambda: "test-principal")

        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"}, principal="test-principal")
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
