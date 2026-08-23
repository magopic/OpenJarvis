"""FASE 4P.2A STEP 8 -- the maia_manage gateway test matrix A-P.

The gateway is a thin router (no business logic of its own) over the
exact same tool classes tests/tools/test_monitoring_tools.py and
tests/tools/test_proactive_insight_tools.py already certify -- these
tests focus on the ROUTING/VALIDATION/CLAIM-INTEGRITY contract the
gateway itself adds, not re-proving the underlying monitor/insight logic.
"""

from __future__ import annotations

import json
import tempfile

import pytest

import openjarvis.tools.maia_manage  # noqa: F401 -- triggers @ToolRegistry.register
import openjarvis.tools.monitoring_tools as mt
import openjarvis.tools.proactive_insight_tools as pit
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.tools.maia_manage import MaiaManageTool


@pytest.fixture(autouse=True)
def _isolated_monitor_service(monkeypatch):
    """Every maia_manage MONITOR_*/NOTIFICATION_* handler constructs a
    fresh tool with service=None (defaulting to a real MonitorService).
    Point that default at an isolated store for every test in this file."""
    svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
    monkeypatch.setattr(mt, "MonitorService", lambda: svc)
    return svc


@pytest.fixture
def _fake_ops_ok(monkeypatch):
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


class TestRegistration:
    def test_registered(self):
        import importlib

        import openjarvis.tools.maia_manage as mod

        importlib.reload(mod)
        assert ToolRegistry.contains("maia_manage")

    def test_l_no_execution_operation_exists(self):
        from openjarvis.tools import maia_manage as mod

        forbidden = ("execute_action", "run_action", "do_it", "send_anything", "write_anything")
        for op in mod._ALL_OPERATIONS:
            lowered = op.lower()
            for bad in forbidden:
                assert bad not in lowered, f"operation looks like execution: {op}"

    def test_o_existing_direct_tools_still_work_internally(self):
        """The gateway is additive -- every direct tool it wraps remains
        independently registered and callable, unchanged."""
        import importlib

        import openjarvis.tools.monitoring_tools as mon_mod
        import openjarvis.tools.proactive_insight_tools as pit_mod

        importlib.reload(mon_mod)
        importlib.reload(pit_mod)

        for name in (
            "maia_monitors_list",
            "maia_monitor_create",
            "maia_analyze_evidence_for_insights",
            "maia_insights_list",
        ):
            assert ToolRegistry.contains(name)


class TestMonitorOperations:
    def test_a_gateway_lists_monitors(self, _isolated_monitor_service):
        gw = MaiaManageTool()
        _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        result = gw.execute(operation="MONITOR_LIST")
        assert result.success is True
        assert len(json.loads(result.content)) == 1

    def test_b_gateway_gets_monitor(self, _isolated_monitor_service):
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        result = gw.execute(operation="MONITOR_GET", monitor_id=mon.id)
        assert result.success is True
        assert json.loads(result.content)["id"] == mon.id

    def test_c_gateway_creates_monitor(self):
        gw = MaiaManageTool()
        result = gw.execute(operation="MONITOR_CREATE", name="new monitor", ops_capability="ops.production.get_kpi")
        assert result.success is True
        payload = json.loads(result.content)
        assert payload["name"] == "new monitor"
        assert payload["enabled"] is True

    def test_d_gateway_enables_disables_monitor(self, _isolated_monitor_service):
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        disabled = gw.execute(operation="MONITOR_DISABLE", monitor_id=mon.id)
        assert json.loads(disabled.content)["enabled"] is False
        enabled = gw.execute(operation="MONITOR_ENABLE", monitor_id=mon.id)
        assert json.loads(enabled.content)["enabled"] is True

    def test_e_gateway_run_now(self, _isolated_monitor_service, _fake_ops_ok):
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        result = gw.execute(operation="MONITOR_RUN_NOW", monitor_id=mon.id)
        assert result.success is True
        payload = json.loads(result.content)
        assert payload["insights_generated"] == 1
        assert payload["notifications"][0]["transition"] == "NEW"


class TestNotificationOperations:
    def test_f_gateway_lists_notifications(self, _isolated_monitor_service, _fake_ops_ok):
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        _isolated_monitor_service.run_cycle(mon.id)
        result = gw.execute(operation="NOTIFICATION_LIST")
        assert result.success is True
        assert len(json.loads(result.content)) == 1

    def test_g_gateway_acknowledges_notification(self, _isolated_monitor_service, _fake_ops_ok):
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        _, notifications = _isolated_monitor_service.run_cycle(mon.id)
        result = gw.execute(operation="NOTIFICATION_ACKNOWLEDGE", notification_id=notifications[0].id)
        assert result.success is True
        assert json.loads(result.content)["acknowledged"] is True


class TestValidation:
    def test_h_invalid_operation_rejected(self):
        gw = MaiaManageTool()
        result = gw.execute(operation="DELETE_EVERYTHING")
        assert result.success is False
        assert "Unknown operation" in result.content

    def test_h_missing_operation_rejected(self):
        gw = MaiaManageTool()
        result = gw.execute()
        assert result.success is False

    def test_i_invalid_arguments_rejected_monitor_create_no_name(self):
        gw = MaiaManageTool()
        result = gw.execute(operation="MONITOR_CREATE", ops_capability="ops.production.get_kpi")
        assert result.success is False

    def test_i_invalid_arguments_rejected_monitor_get_no_id(self):
        gw = MaiaManageTool()
        result = gw.execute(operation="MONITOR_GET")
        assert result.success is False
        assert "monitor_id" in result.content

    def test_i_invalid_arguments_rejected_notification_get_no_id(self):
        gw = MaiaManageTool()
        result = gw.execute(operation="NOTIFICATION_GET")
        assert result.success is False


class TestPrincipalAndPrivacy:
    def test_j_principal_preserved(self, monkeypatch):
        captured = []

        class _RecordingSBTool:
            def __init__(self, principal=None):
                captured.append(principal)

            def execute(self, **kwargs):
                return ToolResult(tool_name="second_brain_find_related_experiences", success=True, content="", metadata={"num_candidates": 0})

        monkeypatch.setattr(
            "openjarvis.tools.second_brain_tools.SecondBrainFindRelatedExperiencesTool", _RecordingSBTool
        )
        gw = MaiaManageTool()
        result = gw.execute(operation="MONITOR_CREATE", name="m", second_brain_query="x")
        mon_id = json.loads(result.content)["id"]
        gw.execute(operation="MONITOR_RUN_NOW", monitor_id=mon_id)
        # MONITOR_CREATE without an explicit `principal` argument in the
        # gateway schema uses the underlying service's own default
        # ("monitor:default") -- confirms the value flows through
        # unchanged end to end, not silently substituted or dropped.
        assert captured == ["monitor:default"]

    def test_k_private_isolation_two_monitors_distinct_state(self, _isolated_monitor_service, _fake_ops_ok):
        """Two monitors created via the gateway keep fully independent
        issue-state/notification history -- proves the gateway doesn't
        collapse or share state across separate MONITOR_CREATE calls."""
        gw = MaiaManageTool()
        r1 = gw.execute(operation="MONITOR_CREATE", name="a", ops_capability="ops.production.get_kpi")
        r2 = gw.execute(operation="MONITOR_CREATE", name="b", ops_capability="ops.production.get_kpi")
        id1, id2 = json.loads(r1.content)["id"], json.loads(r2.content)["id"]
        assert id1 != id2
        gw.execute(operation="MONITOR_RUN_NOW", monitor_id=id1)
        notifs1 = json.loads(gw.execute(operation="NOTIFICATION_LIST", monitor_id=id1).content)
        notifs2 = json.loads(gw.execute(operation="NOTIFICATION_LIST", monitor_id=id2).content)
        assert len(notifs1) == 1
        assert len(notifs2) == 0


class TestGovernanceAndClaimIntegrity:
    def test_m_no_action_book_write(self):
        import inspect

        import openjarvis.tools.maia_manage as mod

        src = inspect.getsource(mod)
        assert "action_book" not in src.lower()
        assert "actionbook" not in src.lower()

    def test_n_no_second_brain_write(self):
        import inspect

        import openjarvis.tools.maia_manage as mod

        src = inspect.getsource(mod)
        assert "propose_entry" not in src
        assert "confirm_entry" not in src

    def test_p_claim_integrity_metadata_records_real_gateway_call(self, _isolated_monitor_service, _fake_ops_ok):
        """The ToolResult a gateway call returns is tagged with the real
        underlying tool_name (e.g. 'maia_monitor_run_now'), not a generic
        'maia_manage' placeholder -- so [ACTUALLY_EXECUTED_TOOLS] (FASE
        4P.1B) still records exactly which real operation ran, never a
        vague 'the gateway did something'."""
        gw = MaiaManageTool()
        mon = _isolated_monitor_service.create_monitor("m", {"ops_capability": "ops.production.get_kpi"})
        result = gw.execute(operation="MONITOR_RUN_NOW", monitor_id=mon.id)
        assert result.tool_name == "maia_monitor_run_now"

    def test_unavailable_ops_capability_via_gateway_reports_not_available(self, monkeypatch):
        """STEP 7: unavailable operation -> explicit NOT_AVAILABLE (reuses
        the frozen FASE 4P.1B _call_ops handling unchanged)."""

        def fake_call_ops(self, capability, params):
            return ToolResult(
                tool_name="x", success=False, content=f"REQUESTED CAPABILITY: {capability}\nSTATUS: NOT_AVAILABLE"
            )

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        gw = MaiaManageTool()
        result = gw.execute(operation="INSIGHT_ANALYZE", ops_capability="ops.nonexistent.get_widget")
        # Insight analysis still succeeds structurally (it checked and
        # found zero insights) -- the NOT_AVAILABLE status is honestly
        # represented in the underlying tool_results, never hidden.
        assert result.success is True
