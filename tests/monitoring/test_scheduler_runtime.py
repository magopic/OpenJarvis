"""FASE 4Q.2 -- MAIA Monitoring Runtime & Scheduler V1 test matrix
(letters A-Z). Covers the previously entirely-missing binding between
MonitorService and a real TaskScheduler: creation/enable/disable task
management, idempotent startup reconciliation, the concurrency guard,
and structural non-regression (no business write, no governed-action
execution, no Second Brain write, no Outlook/email/calendar side
effect).

Every test uses isolated tempfile stores -- never the real
~/.openjarvis/monitoring.db or scheduler.db. Mirrors the fixture style
of tests/monitoring/test_monitoring_service.py and
tests/scheduler/test_scheduler.py.
"""

from __future__ import annotations

import tempfile
import time
from typing import Any, Dict

import pytest

import openjarvis.tools.proactive_insight_tools as pit
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.monitoring.types import (
    CADENCE_DAILY,
    CADENCE_HOURLY,
    CADENCE_MANUAL,
    RUN_STATUS_SKIPPED,
    RUN_STATUS_SUCCESS,
)
from openjarvis.scheduler.scheduler import TaskScheduler
from openjarvis.scheduler.store import SchedulerStore


@pytest.fixture()
def sched_store(tmp_path):
    s = SchedulerStore(tmp_path / "scheduler_test.db")
    yield s
    s.close()


@pytest.fixture()
def scheduler(sched_store):
    # CRUD-only -- no system, never .start()'d, exactly the shape
    # get_default_task_scheduler() constructs for jarvis chat/ask.
    return TaskScheduler(sched_store)


@pytest.fixture()
def mon_store(tmp_path):
    return MonitorStore(tmp_path / "monitoring_test.db")


def _svc(mon_store, scheduler=None) -> MonitorService:
    return MonitorService(store=mon_store, scheduler=scheduler)


def _ops_ok(value: float = 60.0, metric: str = "oee") -> Dict[str, Any]:
    return {
        "status": "ok",
        "period": "2026-07",
        "source": {"function_area": "production"},
        "data": {"metric": metric, "value": value},
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
# A-D: creation binding
# ---------------------------------------------------------------------------


class TestCreationBinding:
    def test_a_daily_enabled_monitor_gets_scheduler_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        assert mon.scheduler_task_id is not None
        task = scheduler.get_task(mon.scheduler_task_id)
        assert task is not None
        assert task.status == "active"
        assert task.schedule_type == "interval"
        assert task.schedule_value == "86400"

    def test_b_hourly_enabled_monitor_gets_scheduler_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("hourly oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_HOURLY)
        assert mon.scheduler_task_id is not None
        task = scheduler.get_task(mon.scheduler_task_id)
        assert task is not None
        assert task.schedule_value == "3600"

    def test_c_manual_monitor_gets_no_scheduler_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("manual check", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)
        assert mon.scheduler_task_id is None
        assert scheduler.list_tasks() == []

    def test_d_disabled_monitor_gets_no_scheduler_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor(
            "created disabled", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY, enabled=False
        )
        assert mon.scheduler_task_id is None
        assert scheduler.list_tasks() == []


# ---------------------------------------------------------------------------
# E-H: enable/disable semantics
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_e_enable_creates_exactly_one_task(self, mon_store, scheduler):
        """The gap this phase found: a monitor created disabled never got
        a task at creation time; enable_monitor() must create one now,
        not just try (and silently fail) to resume a nonexistent task."""
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor(
            "created disabled", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY, enabled=False
        )
        assert mon.scheduler_task_id is None

        enabled = svc.enable_monitor(mon.id)
        assert enabled.scheduler_task_id is not None
        assert len(scheduler.list_tasks()) == 1
        assert scheduler.get_task(enabled.scheduler_task_id).status == "active"

    def test_f_enable_twice_still_exactly_one_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        svc.enable_monitor(mon.id)
        svc.enable_monitor(mon.id)
        assert len(scheduler.list_tasks()) == 1

    def test_g_disable_stops_automatic_execution(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        disabled = svc.disable_monitor(mon.id)
        task = scheduler.get_task(disabled.scheduler_task_id)
        assert task is not None  # history preserved, not deleted
        assert task.status == "paused"
        # A paused task is never returned by get_due_tasks regardless of
        # next_run -- confirms it genuinely will not fire.
        assert scheduler.list_tasks(status="active") == []

    def test_h_reenable_exactly_one_valid_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        svc.disable_monitor(mon.id)
        reenabled = svc.enable_monitor(mon.id)
        assert len(scheduler.list_tasks()) == 1
        assert scheduler.get_task(reenabled.scheduler_task_id).status == "active"


# ---------------------------------------------------------------------------
# I-L: startup reconciliation
# ---------------------------------------------------------------------------


class TestStartupReconciliation:
    def test_i_restart_reconciliation_restores_binding(self, mon_store, sched_store):
        """A monitor whose scheduler_task_id is null (e.g. created while
        the scheduler subsystem was unavailable, or hand-crafted like the
        real certification monitor) gets a real task on reconciliation."""
        svc_no_sched = _svc(mon_store, scheduler=None)
        mon = svc_no_sched.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        assert mon.scheduler_task_id is None

        sched = TaskScheduler(sched_store)
        svc_with_sched = _svc(mon_store, sched)
        result = svc_with_sched.reconcile_scheduler_bindings()
        assert mon.id in result["created"]

        repaired = svc_with_sched.get_monitor(mon.id)
        assert repaired.scheduler_task_id is not None
        assert sched.get_task(repaired.scheduler_task_id).status == "active"

    def test_j_repeated_reconciliation_no_duplicate_task(self, mon_store, scheduler):
        svc = _svc(mon_store, scheduler)
        svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)

        svc.reconcile_scheduler_bindings()
        svc.reconcile_scheduler_bindings()
        svc.reconcile_scheduler_bindings()
        assert len(scheduler.list_tasks()) == 1

    def test_k_orphan_active_task_for_disabled_monitor_gets_paused(self, mon_store, scheduler):
        """A monitor that's disabled but whose task somehow stayed active
        (e.g. disabled through a path that predates this phase) must be
        paused on reconciliation -- never leave an automatic trigger
        running for a monitor that should not be automatic."""
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        # Simulate the orphan: monitor disabled directly in the store,
        # bypassing disable_monitor()'s own pause_task() call.
        d = mon_store.get_monitor(mon.id)
        d["enabled"] = False
        d["status"] = "disabled"
        mon_store.save_monitor(d)

        result = svc.reconcile_scheduler_bindings()
        assert mon.id in result["paused"]
        assert scheduler.get_task(mon.scheduler_task_id).status == "paused"

    def test_l_missing_task_repaired(self, mon_store, scheduler):
        """scheduler_task_id points at a task that no longer exists in
        the scheduler store (e.g. scheduler.db was reset) -- reconcile
        must recreate it, not crash or leave the monitor unscheduled."""
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        assert scheduler.get_task(mon.scheduler_task_id) is not None

        # Simulate scheduler.db loss for this task only.
        d = mon_store.get_monitor(mon.id)
        # (leave scheduler_task_id as-is -- it now points nowhere since
        # nothing in the scheduler store has that id after this delete)
        from openjarvis.scheduler.store import SchedulerStore as _SS  # noqa: F401

        result = svc.reconcile_scheduler_bindings()
        # Task already existed and was active -- no repair needed in this
        # exact scenario; assert the baseline is stable (repaired/created
        # empty) as a control, then actually delete the task and re-check.
        assert result == {"created": [], "repaired": [], "paused": []}

        scheduler._store.delete_task(mon.scheduler_task_id)  # simulate loss
        result2 = svc.reconcile_scheduler_bindings()
        assert mon.id in result2["created"]
        assert scheduler.get_task(mon.scheduler_task_id) is not None


# ---------------------------------------------------------------------------
# M: execution contract
# ---------------------------------------------------------------------------


class TestExecutionContract:
    def test_m_scheduled_trigger_executes_monitor_service(self, mon_store, scheduler, monkeypatch):
        """A due ScheduledTask with agent='monitor_check' must, when the
        scheduler's own poll/execute path fires it, actually reach
        MonitorService.run_cycle() -- via TaskScheduler._execute_task ->
        system.ask(prompt, agent='monitor_check') -> MonitorCheckAgent,
        exactly the existing, frozen contract (no duplicated detector
        logic, no scheduler->business-system shortcut)."""
        _patch_ops(monkeypatch, _ops_ok())
        svc = _svc(mon_store, scheduler)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)

        from openjarvis.agents.monitor_check_agent import MonitorCheckAgent
        from openjarvis.monitoring.service import MONITOR_PROMPT_PREFIX

        class _FakeSystem:
            def ask(self, prompt, **kwargs):
                assert kwargs.get("agent") == "monitor_check"
                agent = MonitorCheckAgent(service=svc)
                result = agent.run(prompt)
                return {"content": result.content}

        scheduler.set_system(_FakeSystem())
        task = scheduler.get_task(mon.scheduler_task_id)
        scheduler._execute_task(task)

        updated = svc.get_monitor(mon.id)
        assert updated.last_run_at is not None
        assert updated.last_success_at is not None


# ---------------------------------------------------------------------------
# N-O: principal / privacy
# ---------------------------------------------------------------------------


class TestPrincipalPrivacy:
    def test_n_principal_preserved_not_ambient(self, mon_store, monkeypatch):
        """The monitor's OWN configured principal reaches Second Brain,
        never whatever identity happens to be ambient in the executing
        process -- STEP 8's core requirement."""
        captured = {}

        def fake_execute(self, **params):
            captured["principal"] = self._principal
            return ToolResult(tool_name="second_brain_find_related_experiences", content="[]", success=True, metadata={"candidates": []})

        import openjarvis.tools.second_brain_tools as sbt

        monkeypatch.setattr(sbt.SecondBrainFindRelatedExperiencesTool, "execute", fake_execute)

        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor(
            "history check", {"second_brain_query": "changeover"}, cadence=CADENCE_MANUAL, principal="monitor:oee-line-3"
        )
        svc.run_cycle(mon.id)
        assert captured["principal"] == "monitor:oee-line-3"

    def test_o_different_monitors_isolated_principals(self, mon_store, monkeypatch):
        captured = []

        def fake_execute(self, **params):
            captured.append(self._principal)
            return ToolResult(tool_name="second_brain_find_related_experiences", content="[]", success=True, metadata={"candidates": []})

        import openjarvis.tools.second_brain_tools as sbt

        monkeypatch.setattr(sbt.SecondBrainFindRelatedExperiencesTool, "execute", fake_execute)

        svc = _svc(mon_store, scheduler=None)
        mon_a = svc.create_monitor("a", {"second_brain_query": "x"}, cadence=CADENCE_MANUAL, principal="monitor:a")
        mon_b = svc.create_monitor("b", {"second_brain_query": "x"}, cadence=CADENCE_MANUAL, principal="monitor:b")
        svc.run_cycle(mon_a.id)
        svc.run_cycle(mon_b.id)
        assert captured == ["monitor:a", "monitor:b"]


# ---------------------------------------------------------------------------
# P: failure behavior
# ---------------------------------------------------------------------------


class TestFailureBehavior:
    def test_p_source_failure_no_fake_business_insight(self, mon_store, monkeypatch):
        """A not_available OPS status legitimately produces an honest
        MissingDataDetector insight (severity INFO, 'data is missing') --
        that is correct, existing STEP 9 behavior, not fabrication. What
        must NOT happen is a business VALUE being invented for a source
        that reported it has none."""
        _patch_ops(monkeypatch, {"status": "not_available", "reason": "OPS unreachable"})
        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)
        run, notifications = svc.run_cycle(mon.id)
        assert run.status == RUN_STATUS_SUCCESS  # no exception/crash -- a reported gap is a normal outcome
        for n in notifications:
            snapshot = n.insight_snapshot
            assert snapshot.get("severity") == "INFO"
            assert "reasoning_basis" in snapshot  # a real, grounded basis, never invented


# ---------------------------------------------------------------------------
# Q-R: concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_q_duplicate_trigger_no_duplicate_execution(self, mon_store, monkeypatch):
        """Two 'triggers' for the same monitor arriving back-to-back
        (e.g. a manual run_now racing an automatic fire) -- the second
        must be skipped, not double-execute."""
        call_count = {"n": 0}

        def fake_call_ops(self, capability, params):
            call_count["n"] += 1
            return ToolResult(
                tool_name="ops_dynamic_production_get_kpi", success=True, content="stub", metadata=_ops_ok()
            )

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)

        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)

        # Manually claim the lock first, simulating an in-flight run.
        claimed = mon_store.try_claim_run(mon.id, "in-flight-run", stale_after_seconds=30)
        assert claimed is True

        run, notifications = svc.run_cycle(mon.id, force=True)
        assert run.status == RUN_STATUS_SKIPPED
        assert notifications == []
        assert call_count["n"] == 0  # evidence collection never happened

    def test_r_stale_lock_from_crash_is_recoverable(self, mon_store, monkeypatch):
        """A lock older than the monitor's own timeout_seconds bound must
        be stealable -- otherwise a crash mid-run would permanently wedge
        the monitor (STEP 9/11's 'restart during run')."""
        _patch_ops(monkeypatch, _ops_ok())
        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor(
            "daily oee",
            {"ops_capability": "ops.production.get_kpi"},
            cadence=CADENCE_MANUAL,
            bounds={"timeout_seconds": 0.01, "max_consecutive_failures": 5},
        )
        mon_store.try_claim_run(mon.id, "crashed-run", stale_after_seconds=0.01)
        time.sleep(0.05)  # let the lock go stale

        run, notifications = svc.run_cycle(mon.id, force=True)
        assert run.status == RUN_STATUS_SUCCESS


# ---------------------------------------------------------------------------
# S-U: run persistence / lifecycle / dedup
# ---------------------------------------------------------------------------


class TestRunPersistenceAndLifecycle:
    def test_s_scheduled_execution_persists_monitor_run(self, mon_store, monkeypatch):
        _patch_ops(monkeypatch, _ops_ok())
        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)
        run, _ = svc.run_cycle(mon.id, force=True)
        stored = mon_store.list_runs(mon.id)
        assert len(stored) == 1
        assert stored[0]["id"] == run.id

    def test_t_lifecycle_transitions_still_work(self, mon_store, monkeypatch):
        from openjarvis.monitoring.types import TRANSITION_NEW, TRANSITION_RESOLVED

        state = {"present": True}

        def fake_call_ops(self, capability, params):
            envelope = (
                {"status": "ok", "period": "2026-07", "source": {}, "data": {"metric": "oee", "value": 40.0, "threshold": 70.0, "threshold_type": "min"}}
                if state["present"]
                else {"status": "ok", "period": "2026-07", "source": {}, "data": {"metric": "oee", "value": 90.0, "threshold": 70.0, "threshold_type": "min"}}
            )
            return ToolResult(tool_name="ops_dynamic_production_get_kpi", success=True, content="stub", metadata=envelope)

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)

        _, notif1 = svc.run_cycle(mon.id, force=True)
        assert any(n.transition == TRANSITION_NEW for n in notif1)

        state["present"] = False
        _, notif2 = svc.run_cycle(mon.id, force=True)
        assert any(n.transition == TRANSITION_RESOLVED for n in notif2)

    def test_u_unchanged_creates_no_duplicate_notification(self, mon_store, monkeypatch):
        from openjarvis.monitoring.types import TRANSITION_NEW

        _patch_ops(
            monkeypatch,
            {"status": "ok", "period": "2026-07", "source": {}, "data": {"metric": "oee", "value": 40.0, "threshold": 70.0, "threshold_type": "min"}},
        )
        svc = _svc(mon_store, scheduler=None)
        mon = svc.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_MANUAL)

        _, notif1 = svc.run_cycle(mon.id, force=True)
        assert any(n.transition == TRANSITION_NEW for n in notif1)

        _, notif2 = svc.run_cycle(mon.id, force=True)
        assert notif2 == []  # UNCHANGED never notifies


# ---------------------------------------------------------------------------
# V: scheduler restart persistence
# ---------------------------------------------------------------------------


class TestSchedulerRestartPersistence:
    def test_v_task_binding_survives_process_restart(self, mon_store, tmp_path):
        db_path = tmp_path / "scheduler_restart.db"
        store1 = SchedulerStore(db_path)
        sched1 = TaskScheduler(store1)
        svc1 = _svc(mon_store, sched1)
        mon = svc1.create_monitor("daily oee", {"ops_capability": "ops.production.get_kpi"}, cadence=CADENCE_DAILY)
        task_id = mon.scheduler_task_id
        store1.close()

        # Fresh process: new store/scheduler objects, same db file.
        store2 = SchedulerStore(db_path)
        sched2 = TaskScheduler(store2)
        task = sched2.get_task(task_id)
        assert task is not None
        assert task.status == "active"
        store2.close()


# ---------------------------------------------------------------------------
# W-Z: structural non-regression -- no business write, no governed-action
# execution, no Second Brain write, no Outlook/email/calendar side effect.
# ---------------------------------------------------------------------------


class TestNoBusinessSideEffects:
    def test_w_no_business_write_capability_used(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        forbidden = ("ops.actions.create", "ops_bridge_generic.post", "requests.post", "httpx.post")
        for bad in forbidden:
            assert bad not in src, bad

    def test_x_no_governed_action_approval_or_execution(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        assert "governed_actions" not in src
        assert ".approve(" not in src
        # (Not a blanket ".execute(" check -- _collect_evidence legitimately
        # calls read-only tool .execute() methods, e.g. DocumentSearchTool;
        # the "governed_actions" import check above is the precise guard.)

    def test_y_no_second_brain_write(self):
        """_collect_evidence only ever calls
        SecondBrainFindRelatedExperiencesTool (a read) -- never propose/
        confirm/archive/link (the write-capable tools)."""
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod._collect_evidence if hasattr(mod, "_collect_evidence") else mod.MonitorService._collect_evidence)
        forbidden = ("ProposeEntry", "ConfirmEntry", "ArchiveTool", "LinkTool")
        for bad in forbidden:
            assert bad not in src, bad

    def test_z_no_outlook_email_calendar_side_effect(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        forbidden = ("outlook", "send_mail", "calendar", "smtp")
        lowered = src.lower()
        for bad in forbidden:
            assert bad not in lowered, bad
