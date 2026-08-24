"""FASE 4Q.3 -- MAIA Notification Runtime & Attention Center V1 test
matrix (letters A-FF). Covers the attention layer built on top of the
existing, frozen monitoring notification persistence: principal
isolation (previously entirely absent), the UNREAD/READ/ACKNOWLEDGED
lifecycle (previously only a flat acknowledged boolean existed), and
structural non-regression (no push claim, no governed-action execution,
no Second Brain write, no Outlook/email/calendar side effect).

Every test uses an isolated tempfile SQLite store -- never the real
~/.openjarvis/monitoring.db. Mirrors the fixture/patch style already
established in tests/monitoring/test_monitoring_service.py and
tests/monitoring/test_scheduler_runtime.py.
"""

from __future__ import annotations

import tempfile
from typing import Any, Dict

import pytest

import openjarvis.tools.proactive_insight_tools as pit
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.monitoring.types import (
    STATUS_ACKNOWLEDGED,
    STATUS_READ,
    STATUS_UNREAD,
    TRANSITION_NEW,
    TRANSITION_REOPENED,
    TRANSITION_RESOLVED,
)


def _svc(store: MonitorStore | None = None) -> MonitorService:
    return MonitorService(store=store or MonitorStore(tempfile.mktemp(suffix=".db")))


def _ops_envelope(value: float = 40.0, threshold: float = 70.0) -> Dict[str, Any]:
    return {
        "status": "ok",
        "period": "2026-07",
        "source": {},
        "data": {"metric": "oee", "value": value, "threshold": threshold, "threshold_type": "min"},
    }


def _patch_ops(monkeypatch: pytest.MonkeyPatch, envelope_or_fn: Any) -> None:
    def fake_call_ops(self, capability: str, params: Dict[str, Any]) -> ToolResult:
        envelope = envelope_or_fn(capability, params) if callable(envelope_or_fn) else envelope_or_fn
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            success=envelope.get("status") == "ok",
            content="stub",
            metadata=envelope,
        )

    monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)


# ---------------------------------------------------------------------------
# A-C: creation and persistence
# ---------------------------------------------------------------------------


class TestCreationAndPersistence:
    def test_a_notification_created_from_real_transition(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        run, notifications = svc.run_cycle(mon.id)
        assert len(notifications) == 1
        assert notifications[0].transition == TRANSITION_NEW
        assert notifications[0].monitor_id == mon.id
        assert notifications[0].principal == "p1"

    def test_b_notification_persisted(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        db_path = tempfile.mktemp(suffix=".db")
        svc = _svc(MonitorStore(db_path))
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        raw = MonitorStore(db_path).get_notification(notifications[0].id)
        assert raw is not None
        assert raw["principal"] == "p1"

    def test_c_restart_persistence(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        db_path = tempfile.mktemp(suffix=".db")
        svc1 = _svc(MonitorStore(db_path))
        mon = svc1.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc1.run_cycle(mon.id)

        svc2 = _svc(MonitorStore(db_path))
        n = svc2.get_notification(notifications[0].id, principal="p1")
        assert n is not None
        assert n.id == notifications[0].id


# ---------------------------------------------------------------------------
# D-J: attention lifecycle
# ---------------------------------------------------------------------------


class TestAttentionLifecycle:
    def test_d_default_state_unread(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        assert notifications[0].status == STATUS_UNREAD

    def test_e_mark_read(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        n = svc.mark_notification_read(notifications[0].id, principal="p1")
        assert n.status == STATUS_READ
        assert n.read_at is not None

    def test_f_repeated_mark_read_idempotent(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        n1 = svc.mark_notification_read(notifications[0].id, principal="p1")
        n2 = svc.mark_notification_read(notifications[0].id, principal="p1")
        assert n1.read_at == n2.read_at

    def test_g_acknowledge(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        n = svc.acknowledge_notification(notifications[0].id, principal="p1")
        assert n.status == STATUS_ACKNOWLEDGED
        assert n.acknowledged is True
        assert n.acknowledged_at is not None

    def test_h_repeated_acknowledge_idempotent(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        n1 = svc.acknowledge_notification(notifications[0].id, principal="p1")
        n2 = svc.acknowledge_notification(notifications[0].id, principal="p1")
        assert n1.acknowledged_at == n2.acknowledged_at

    def test_i_read_does_not_imply_acknowledge(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        n = svc.mark_notification_read(notifications[0].id, principal="p1")
        assert n.status == STATUS_READ
        assert n.acknowledged_at is None
        assert n.acknowledged is False

    def test_j_acknowledge_does_not_imply_business_action(self):
        """Structural: MonitorService's notification methods never touch
        governed_actions, Second Brain writes, or any business-execution
        surface -- acknowledging is informational only."""
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod.MonitorService.acknowledge_notification)
        assert "governed_actions" not in src
        assert "approve" not in src
        assert "execute" not in src.replace("acknowledge_notification", "")


# ---------------------------------------------------------------------------
# K-N: query service filtering
# ---------------------------------------------------------------------------


class TestQueryService:
    def test_k_list_unread(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        svc.mark_notification_read(notifications[0].id, principal="p1")
        unread = svc.list_notifications(principal="p1", unread_only=True)
        assert unread == []

    def test_l_unread_count(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        assert svc.count_unread_notifications(principal="p1") == 1

    def test_m_severity_filter(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        real_severity = notifications[0].severity
        matching = svc.list_notifications(principal="p1", severity=real_severity)
        assert len(matching) == 1
        none_matching = svc.list_notifications(principal="p1", severity="NOT_A_REAL_SEVERITY")
        assert none_matching == []

    def test_n_monitor_filter(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon_a = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        mon_b = svc.create_monitor("b", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon_a.id)
        svc.run_cycle(mon_b.id)
        only_a = svc.list_notifications(principal="p1", monitor_id=mon_a.id)
        assert len(only_a) == 1
        assert only_a[0].monitor_id == mon_a.id


# ---------------------------------------------------------------------------
# O-R: principal isolation
# ---------------------------------------------------------------------------


class TestPrincipalIsolation:
    def test_o_principal_a_cannot_list_principal_b(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon_a = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="principal-a")
        mon_b = svc.create_monitor("b", {"ops_capability": "ops.production.get_kpi"}, principal="principal-b")
        svc.run_cycle(mon_a.id)
        svc.run_cycle(mon_b.id)

        as_a = svc.list_notifications(principal="principal-a")
        assert len(as_a) == 1
        assert as_a[0].monitor_id == mon_a.id

    def test_p_principal_a_cannot_get_b_notification(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon_b = svc.create_monitor("b", {"ops_capability": "ops.production.get_kpi"}, principal="principal-b")
        _, notifications_b = svc.run_cycle(mon_b.id)

        result = svc.get_notification(notifications_b[0].id, principal="principal-a")
        assert result is None  # fail closed -- looks identical to "not found"

    def test_q_principal_a_cannot_mutate_b_notification(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon_b = svc.create_monitor("b", {"ops_capability": "ops.production.get_kpi"}, principal="principal-b")
        _, notifications_b = svc.run_cycle(mon_b.id)

        with pytest.raises(KeyError):
            svc.acknowledge_notification(notifications_b[0].id, principal="principal-a")
        with pytest.raises(KeyError):
            svc.mark_notification_read(notifications_b[0].id, principal="principal-a")

        # Confirm B's notification is genuinely untouched by A's attempts.
        untouched = svc.get_notification(notifications_b[0].id, principal="principal-b")
        assert untouched.status == STATUS_UNREAD

    def test_r_runtime_principal_cannot_be_spoofed_by_tool_args(self):
        """None of the notification tools expose a `principal` parameter
        in their own schema -- the model has no argument to override it
        with. Mirrors the same check already established for governed
        actions (test_governed_actions_runtime_hook.py's
        TestModelCannotForgeApprovalFields)."""
        import openjarvis.tools.monitoring_tools as mt

        for cls_name in (
            "NotificationsListTool",
            "NotificationsUnreadCountTool",
            "NotificationGetTool",
            "NotificationMarkReadTool",
            "NotificationAcknowledgeTool",
        ):
            cls = getattr(mt, cls_name)
            spec = cls().spec
            assert "principal" not in spec.parameters.get("properties", {}), cls_name


# ---------------------------------------------------------------------------
# S-V: monitoring lifecycle dedup preserved
# ---------------------------------------------------------------------------


class TestLifecyclePreserved:
    def test_s_unchanged_no_duplicate_notification(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        _, second = svc.run_cycle(mon.id)
        assert second == []
        assert len(svc.list_notifications(principal="p1")) == 1

    def test_t_new_lifecycle_preserved(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc.run_cycle(mon.id)
        assert notifications[0].transition == TRANSITION_NEW

    def test_u_resolved_lifecycle_preserved(self, monkeypatch):
        state = {"present": True}

        def envelope(cap, params):
            return _ops_envelope(value=40.0) if state["present"] else _ops_envelope(value=90.0)

        _patch_ops(monkeypatch, envelope)
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        state["present"] = False
        _, notifications = svc.run_cycle(mon.id)
        assert any(n.transition == TRANSITION_RESOLVED for n in notifications)

    def test_v_reopened_lifecycle_preserved(self, monkeypatch):
        state = {"present": True}

        def envelope(cap, params):
            return _ops_envelope(value=40.0) if state["present"] else _ops_envelope(value=90.0)

        _patch_ops(monkeypatch, envelope)
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        state["present"] = False
        svc.run_cycle(mon.id)
        state["present"] = True
        _, notifications = svc.run_cycle(mon.id)
        assert any(n.transition == TRANSITION_REOPENED for n in notifications)


# ---------------------------------------------------------------------------
# W-X: restart retains read/ack state
# ---------------------------------------------------------------------------


class TestRestartRetainsState:
    def test_w_restart_retains_read_state(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        db_path = tempfile.mktemp(suffix=".db")
        svc1 = _svc(MonitorStore(db_path))
        mon = svc1.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc1.run_cycle(mon.id)
        svc1.mark_notification_read(notifications[0].id, principal="p1")

        svc2 = _svc(MonitorStore(db_path))
        n = svc2.get_notification(notifications[0].id, principal="p1")
        assert n.status == STATUS_READ

    def test_x_restart_retains_acknowledgement_state(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        db_path = tempfile.mktemp(suffix=".db")
        svc1 = _svc(MonitorStore(db_path))
        mon = svc1.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _, notifications = svc1.run_cycle(mon.id)
        svc1.acknowledge_notification(notifications[0].id, principal="p1")

        svc2 = _svc(MonitorStore(db_path))
        n = svc2.get_notification(notifications[0].id, principal="p1")
        assert n.status == STATUS_ACKNOWLEDGED


# ---------------------------------------------------------------------------
# Y: malformed/missing id
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_y_missing_notification_id_fails_honestly(self):
        svc = _svc()
        assert svc.get_notification("does-not-exist", principal="p1") is None
        with pytest.raises(KeyError):
            svc.acknowledge_notification("does-not-exist", principal="p1")
        with pytest.raises(KeyError):
            svc.mark_notification_read("does-not-exist", principal="p1")


# ---------------------------------------------------------------------------
# Z-DD: structural non-regression
# ---------------------------------------------------------------------------


class TestStructuralNonRegression:
    def test_z_no_external_push_claim_in_tool_descriptions(self):
        """Checks for an AFFIRMATIVE push-delivery claim, not the bare
        word 'push' -- the correct, desired language explicitly says
        'nothing is pushed', which legitimately contains that word."""
        import openjarvis.tools.monitoring_tools as mt

        for cls_name in ("NotificationsListTool", "NotificationGetTool", "NotificationAcknowledgeTool"):
            desc = getattr(mt, cls_name)().spec.description.lower()
            forbidden = ("will be pushed", "we will notify you", "will notify you automatically", "sms", "websocket", "sse")
            for bad in forbidden:
                assert bad not in desc, (cls_name, bad)

    def test_aa_no_governed_action_approval(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        assert "governed_actions" not in src

    def test_bb_no_business_write(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        forbidden = ("ops.actions.create", "requests.post", "httpx.post")
        for bad in forbidden:
            assert bad not in src, bad

    def test_cc_no_second_brain_write(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod.MonitorService._collect_evidence)
        forbidden = ("ProposeEntry", "ConfirmEntry", "ArchiveTool", "LinkTool")
        for bad in forbidden:
            assert bad not in src, bad

    def test_dd_no_outlook_email_calendar_side_effect(self):
        import inspect

        import openjarvis.monitoring.service as mod
        import openjarvis.tools.monitoring_tools as mt

        for src in (inspect.getsource(mod), inspect.getsource(mt)):
            lowered = src.lower()
            for bad in ("outlook", "send_mail", "calendar", "smtp"):
                assert bad not in lowered, bad


# ---------------------------------------------------------------------------
# EE-FF: dedup intact / maia_manage compatibility
# ---------------------------------------------------------------------------


class TestDedupAndGatewayCompatibility:
    def test_ee_dedup_across_many_unchanged_cycles(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("oee", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        for _ in range(5):
            _, notifications = svc.run_cycle(mon.id)
            assert notifications == []
        assert len(svc.list_notifications(principal="p1")) == 1

    def test_ff_maia_manage_notification_ops_registered(self):
        from openjarvis.tools.maia_manage import _ALL_OPERATIONS

        for op in (
            "NOTIFICATION_LIST",
            "NOTIFICATION_UNREAD_COUNT",
            "NOTIFICATION_GET",
            "NOTIFICATION_MARK_READ",
            "NOTIFICATION_ACKNOWLEDGE",
        ):
            assert op in _ALL_OPERATIONS
