"""FASE 4P.2 STEP 14 -- MonitorService test matrix A-S (T is tool-level,
see tests/tools/test_monitoring_tools.py; O -- no execution tool exposed
-- is also covered at the tool-registration level there).

Every test uses an isolated tempfile SQLite store -- never the real
~/.openjarvis/monitoring.db -- and monkeypatches ProactiveAnalyzeTool's
own _call_ops so no real network call happens (mirrors the pattern
already established in tests/tools/test_proactive_insight_tools.py).
"""

from __future__ import annotations

import tempfile
from typing import Any, Dict, List, Optional

import pytest

import openjarvis.tools.proactive_insight_tools as pit
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.monitoring.types import (
    ISSUE_STATE_ACTIVE,
    ISSUE_STATE_RESOLVED,
    MONITOR_STATUS_DISABLED,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_SUCCESS,
    TRANSITION_CHANGED,
    TRANSITION_NEW,
    TRANSITION_REOPENED,
    TRANSITION_RESOLVED,
)


def _svc() -> MonitorService:
    return MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))


def _ops_ok(value: float = 60.0, threshold: float = 70.0, metric: str = "oee", period: str = "2026-07") -> Dict[str, Any]:
    return {
        "status": "ok",
        "period": period,
        "source": {"function_area": "production"},
        "data": {"metric": metric, "value": value, "threshold": threshold, "threshold_type": "min"},
    }


def _patch_ops(monkeypatch: pytest.MonkeyPatch, envelope_or_fn: Any) -> None:
    """Patches ProactiveAnalyzeTool._call_ops (the exact call site
    MonitorService._collect_evidence reuses) to return a fixed envelope,
    or call a function(capability, params) -> envelope for dynamic tests."""

    def fake_call_ops(self, capability: str, params: Dict[str, Any]) -> ToolResult:
        envelope = envelope_or_fn(capability, params) if callable(envelope_or_fn) else envelope_or_fn
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            success=envelope.get("status") == "ok",
            content="stub",
            metadata=envelope,
        )

    monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)


class TestMonitorCreation:
    def test_a_create_monitor(self, monkeypatch):
        svc = _svc()
        mon = svc.create_monitor("prod check", {"ops_capability": "ops.production.get_kpi"}, cadence="MANUAL")
        assert mon.id
        assert mon.enabled is True
        assert mon.cadence == "MANUAL"
        assert svc.get_monitor(mon.id) is not None

    def test_invalid_cadence_rejected(self):
        svc = _svc()
        with pytest.raises(ValueError):
            svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"}, cadence="EVERY_SECOND")

    def test_empty_source_requirements_rejected(self):
        svc = _svc()
        with pytest.raises(ValueError):
            svc.create_monitor("x", {})

    def test_unknown_detector_scope_rejected(self):
        svc = _svc()
        with pytest.raises(ValueError):
            svc.create_monitor(
                "x", {"ops_capability": "ops.production.get_kpi"}, detector_scope=["not_a_real_detector"]
            )

    def test_m_duplicate_monitor_definition_handling(self, monkeypatch):
        """Creating two monitors with the same name/source is allowed (they
        get distinct ids) -- 'duplicate' is a naming concern for the human,
        not a structural error; each has its own independent state."""
        svc = _svc()
        m1 = svc.create_monitor("same name", {"ops_capability": "ops.production.get_kpi"})
        m2 = svc.create_monitor("same name", {"ops_capability": "ops.production.get_kpi"})
        assert m1.id != m2.id
        assert len(svc.list_monitors()) == 2


class TestMonitorLifecycleRun:
    def test_b_disabled_monitor_does_not_run(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"}, enabled=False)
        run, notifications = svc.run_cycle(mon.id)
        assert run.status == RUN_STATUS_FAILED
        assert "disabled" in run.errors[0]
        assert notifications == []
        assert run.evidence_collected == 0

    def test_c_manual_run(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=90.0))  # no breach
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"}, cadence="MANUAL")
        run, notifications = svc.run_cycle(mon.id)
        assert run.status == RUN_STATUS_SUCCESS
        assert run.evidence_collected == 1

    def test_d_first_issue_new_plus_notification(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        run, notifications = svc.run_cycle(mon.id)
        assert run.insights_generated == 1
        assert len(notifications) == 1
        assert notifications[0].transition == TRANSITION_NEW

    def test_e_identical_next_run_unchanged_no_duplicate_notification(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)
        run2, notifications2 = svc.run_cycle(mon.id)
        assert notifications2 == []
        assert run2.insights_generated == 1  # still detected, just not re-notified

    def test_f_different_value_is_new_plus_resolved_not_a_bare_change(self, monkeypatch):
        """ThresholdBreachDetector's fingerprint includes the current
        VALUE by design (frozen, FASE 4P.1) -- a different value is a
        genuinely different fingerprint, not 'the same issue changed'.
        Correct behavior: the old fingerprint resolves, a new one is
        reported NEW -- never miscounted as a single CHANGED notification."""
        state = {"value": 60.0}
        _patch_ops(monkeypatch, lambda cap, params: _ops_ok(value=state["value"], threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)
        state["value"] = 20.0  # still breaching, but a different value
        run2, notifications2 = svc.run_cycle(mon.id)
        transitions = sorted(n.transition for n in notifications2)
        assert transitions == sorted([TRANSITION_NEW, TRANSITION_RESOLVED])

    def test_f_material_change_same_fingerprint_diff_logic(self):
        """Direct test of MonitorService's own diff logic (STEP 5's
        'issue changes materially -> updated notification'): when the
        SAME fingerprint reappears with a different severity/confidence
        (the only fields the diff treats as a material change for an
        otherwise-identical identity), it must be reported as CHANGED,
        not silently folded into UNCHANGED."""
        from openjarvis.agents.proactive_insight import ProactiveInsight

        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})

        def _insight(severity: str, confidence: str) -> ProactiveInsight:
            return ProactiveInsight(
                id="fixed-fingerprint-for-this-test",
                title="t",
                summary="s",
                severity=severity,
                confidence=confidence,
                evidence=[],
            )

        n1 = svc._diff_and_notify(mon, [_insight("WARNING", "MEDIUM")])
        assert [x.transition for x in n1] == [TRANSITION_NEW]
        n2 = svc._diff_and_notify(mon, [_insight("WARNING", "MEDIUM")])
        assert n2 == []  # UNCHANGED, no notification
        n3 = svc._diff_and_notify(mon, [_insight("CRITICAL", "HIGH")])
        assert [x.transition for x in n3] == [TRANSITION_CHANGED]

    def test_g_issue_disappears_resolved(self, monkeypatch):
        state = {"value": 60.0}
        _patch_ops(monkeypatch, lambda cap, params: _ops_ok(value=state["value"], threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)
        state["value"] = 90.0  # no longer breaching
        run2, notifications2 = svc.run_cycle(mon.id)
        assert len(notifications2) == 1
        assert notifications2[0].transition == TRANSITION_RESOLVED
        assert run2.insights_generated == 0

    def test_h_issue_returns_reopened(self, monkeypatch):
        state = {"value": 60.0}
        _patch_ops(monkeypatch, lambda cap, params: _ops_ok(value=state["value"], threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)  # NEW
        state["value"] = 90.0
        svc.run_cycle(mon.id)  # RESOLVED
        state["value"] = 60.0
        run3, notifications3 = svc.run_cycle(mon.id)
        assert len(notifications3) == 1
        assert notifications3[0].transition == TRANSITION_REOPENED

    def test_r_deterministic_fingerprint(self, monkeypatch):
        """Re-analyzing the identical evidence twice (fresh MonitorService
        instances, same store) yields the identical fingerprint -- proves
        the fingerprint is derived from governed fields, not a fresh
        random id or wall-clock-derived value."""
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        store = MonitorStore(tempfile.mktemp(suffix=".db"))
        svc1 = MonitorService(store=store)
        mon = svc1.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        run1, n1 = svc1.run_cycle(mon.id)
        svc2 = MonitorService(store=store)
        run2, n2 = svc2.run_cycle(mon.id)
        state1 = store.get_issue_state(mon.id)
        assert len(state1) == 1
        fp = next(iter(state1))
        assert n1[0].fingerprint == fp


class TestFailureHandling:
    def test_i_source_unavailable_monitor_health_error_no_fabricated_insight(self, monkeypatch):
        def raising(cap, params):
            raise RuntimeError("connection refused")

        def fake_call_ops(self, capability, params):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        run, notifications = svc.run_cycle(mon.id)
        assert run.status == RUN_STATUS_FAILED
        assert run.insights_generated == 0
        assert notifications == []
        assert any("connection refused" in e for e in run.errors)

    def test_j_partial_source_failure_limitations_preserved(self, monkeypatch):
        """One source (OPS) works, another (Second Brain) fails -- the run
        is PARTIAL, the OPS evidence is still analyzed, and the failure is
        recorded in run.errors rather than silently dropped."""

        def fake_call_ops(self, capability, params):
            return ToolResult(tool_name="x", success=True, content="ok", metadata=_ops_ok(value=60.0, threshold=70.0))

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)

        class _RaisingSBTool:
            def __init__(self, principal=None):
                pass

            def execute(self, **kwargs):
                raise RuntimeError("second brain unavailable")

        monkeypatch.setattr(
            "openjarvis.tools.second_brain_tools.SecondBrainFindRelatedExperiencesTool", _RaisingSBTool
        )
        svc = _svc()
        mon = svc.create_monitor(
            "x",
            {"ops_capability": "ops.production.get_kpi", "second_brain_query": "changeover"},
        )
        run, notifications = svc.run_cycle(mon.id)
        assert run.status == RUN_STATUS_PARTIAL
        assert run.evidence_collected == 1  # only OPS succeeded
        assert run.insights_generated == 1  # still analyzed the OPS evidence
        assert any("second brain unavailable" in e for e in run.errors)

    def test_consecutive_failures_auto_disable(self, monkeypatch):
        def fake_call_ops(self, capability, params):
            raise RuntimeError("down")

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc()
        mon = svc.create_monitor(
            "x", {"ops_capability": "ops.production.get_kpi"}, bounds={"max_consecutive_failures": 2}
        )
        svc.run_cycle(mon.id)
        svc.run_cycle(mon.id)
        mon_after = svc.get_monitor(mon.id)
        assert mon_after.enabled is False
        assert mon_after.status == MONITOR_STATUS_DISABLED


class TestGovernance:
    def test_k_private_evidence_isolation(self, monkeypatch):
        """Different monitors with different principals only see what
        their own principal is authorized to see -- exercised by
        verifying the configured principal is actually threaded into the
        Second Brain tool construction, not silently dropped or shared."""
        captured_principals: List[Optional[str]] = []

        class _RecordingSBTool:
            def __init__(self, principal=None):
                captured_principals.append(principal)

            def execute(self, **kwargs):
                return ToolResult(
                    tool_name="second_brain_find_related_experiences",
                    success=True,
                    content="",
                    metadata={"num_candidates": 0},
                )

        monkeypatch.setattr(
            "openjarvis.tools.second_brain_tools.SecondBrainFindRelatedExperiencesTool", _RecordingSBTool
        )
        svc = _svc()
        mon_a = svc.create_monitor("a", {"second_brain_query": "x"}, principal="user:alice")
        mon_b = svc.create_monitor("b", {"second_brain_query": "x"}, principal="user:bob")
        svc.run_cycle(mon_a.id)
        svc.run_cycle(mon_b.id)
        assert captured_principals == ["user:alice", "user:bob"]

    def test_l_principal_isolation_no_ambient_fallback(self, monkeypatch):
        """A monitor's principal is explicit and required -- it must never
        fall back to whatever ambient/runtime principal an interactive
        session happens to have."""
        svc = _svc()
        mon = svc.create_monitor("x", {"second_brain_query": "y"})
        assert mon.principal == "monitor:default"  # explicit default, not None/ambient

    def test_p_no_second_brain_write(self, monkeypatch):
        """The evidence-collection path only ever constructs
        SecondBrainFindRelatedExperiencesTool -- never propose_entry/
        confirm_entry -- verified by import inspection of the service
        module's source, not just behavior."""
        import inspect

        import openjarvis.monitoring.service as svc_module

        src = inspect.getsource(svc_module)
        assert "propose_entry" not in src
        assert "confirm_entry" not in src

    def test_q_no_action_book_write(self):
        """Nothing in monitoring/ references OPS ONE's Action Book at
        all -- Action Book stays entirely outside this phase's scope."""
        import inspect

        import openjarvis.monitoring.service as svc_module
        import openjarvis.monitoring.store as store_module
        import openjarvis.monitoring.types as types_module

        for mod in (svc_module, store_module, types_module):
            src = inspect.getsource(mod)
            assert "action_book" not in src.lower()
            assert "actionbook" not in src.lower()


class TestNotifications:
    def test_notification_default_unacknowledged(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        run, notifications = svc.run_cycle(mon.id)
        assert notifications[0].acknowledged is False

    def test_t_notification_acknowledgment(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        run, notifications = svc.run_cycle(mon.id)
        n = svc.acknowledge_notification(notifications[0].id, principal=mon.principal)
        assert n.acknowledged is True
        assert n.acknowledged_at is not None
        fetched = svc.get_notification(notifications[0].id, principal=mon.principal)
        assert fetched.acknowledged is True

    def test_unchanged_never_creates_notification_even_across_many_cycles(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc.run_cycle(mon.id)
        for _ in range(5):
            _, notifications = svc.run_cycle(mon.id)
            assert notifications == []
        assert len(svc.list_notifications(principal=mon.principal, monitor_id=mon.id)) == 1  # only the original NEW


class TestPersistence:
    def test_n_scheduler_restart_persistence(self, monkeypatch):
        """A fresh MonitorService pointed at the same store file sees
        everything a prior instance created/observed -- simulating a
        process restart, since MonitorStore is plain SQLite on disk."""
        _patch_ops(monkeypatch, _ops_ok(value=60.0, threshold=70.0))
        db_path = tempfile.mktemp(suffix=".db")
        svc1 = MonitorService(store=MonitorStore(db_path))
        mon = svc1.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})
        svc1.run_cycle(mon.id)

        svc2 = MonitorService(store=MonitorStore(db_path))
        mon_again = svc2.get_monitor(mon.id)
        assert mon_again is not None
        assert mon_again.last_run_at is not None
        notifications_again = svc2.list_notifications(principal=mon.principal, monitor_id=mon.id)
        assert len(notifications_again) == 1

    def test_s_cadence_bounded(self):
        """No sub-minute cadence exists anywhere in the cadence vocabulary
        -- HOURLY=3600s, DAILY=86400s, MANUAL=never auto-scheduled."""
        from openjarvis.monitoring.types import _CADENCE_SECONDS, CADENCE_HOURLY, CADENCE_DAILY

        assert _CADENCE_SECONDS[CADENCE_HOURLY] == 3600
        assert _CADENCE_SECONDS[CADENCE_DAILY] == 86400
        assert min(_CADENCE_SECONDS.values()) >= 3600  # nothing sub-minute, nothing even sub-hour
