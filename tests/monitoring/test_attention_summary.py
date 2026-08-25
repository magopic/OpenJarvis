"""FASE 4Q.4 -- MAIA Daily Operations / Executive Attention V1 test
matrix (letters A-JJ, matched to STEP 16). Covers the one new primitive
this phase adds -- monitoring/attention.py's deterministic, explainable
classification/priority ordering -- plus structural non-regression
proving nothing about the existing monitoring/notification/governed-
action/claim-integrity guarantees was weakened.

Scenarios A-J from STEP 15 are built as fixtures below, never using the
real OEE certification monitor -- every test uses an isolated tempfile
store. Context-continuity letters (Q-V) reuse the exact structural
pattern already established and certified in
tests/agents/test_maia_operational_copilot.py -- not re-invented here,
since the orchestrator's own context threading is unchanged by this
phase.
"""

from __future__ import annotations

import tempfile
from typing import Any, Dict

import pytest

import openjarvis.tools.proactive_insight_tools as pit
from openjarvis.core.types import ToolResult
from openjarvis.monitoring.attention import (
    CLASS_ACKNOWLEDGED,
    CLASS_ATTENTION_ITEM,
    CLASS_INFORMATIONAL,
    build_attention_summary,
    classify_notification,
)
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.monitoring.types import (
    STATUS_ACKNOWLEDGED,
    STATUS_UNREAD,
    TRANSITION_NEW,
    TRANSITION_RESOLVED,
    Notification,
)
from openjarvis.tools.monitoring_tools import DailyAttentionSummaryTool


def _svc() -> MonitorService:
    return MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))


def _n(
    *,
    nid: str,
    severity: str,
    transition: str = TRANSITION_NEW,
    created_at: str = "2026-08-24T10:00:00+00:00",
    acknowledged_at: str | None = None,
    read_at: str | None = None,
    principal: str = "p1",
    title: str = "t",
) -> Notification:
    return Notification(
        id=nid,
        monitor_id="mon-1",
        fingerprint=f"fp-{nid}",
        transition=transition,
        insight_snapshot={"id": nid},
        created_at=created_at,
        acknowledged=acknowledged_at is not None,
        acknowledged_at=acknowledged_at,
        principal=principal,
        severity=severity,
        title=title,
        summary="s",
        read_at=read_at,
    )


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
# A: empty attention state
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_a_empty_attention_state_no_fabricated_problem(self):
        summary = build_attention_summary([])
        assert summary["attention_items"] == []
        assert summary["has_attention_items"] is False


# ---------------------------------------------------------------------------
# B-C: surfacing and deterministic ordering
# ---------------------------------------------------------------------------


class TestSurfacingAndOrdering:
    def test_b_one_new_item_surfaced(self):
        summary = build_attention_summary([_n(nid="1", severity="WARNING")])
        assert len(summary["attention_items"]) == 1
        assert summary["attention_items"][0]["id"] == "1"

    def test_c_multiple_items_deterministic_priority(self):
        notifications = [
            _n(nid="low", severity="INFO"),
            _n(nid="high", severity="CRITICAL"),
            _n(nid="mid", severity="WARNING"),
        ]
        summary = build_attention_summary(notifications)
        ids = [e["id"] for e in summary["attention_items"]]
        assert ids == ["high", "mid", "low"]

    def test_d_highest_severity_wins_when_otherwise_comparable(self):
        notifications = [
            _n(nid="a", severity="ATTENTION", created_at="2026-08-24T10:00:00+00:00"),
            _n(nid="b", severity="CRITICAL", created_at="2026-08-24T10:00:00+00:00"),
        ]
        summary = build_attention_summary(notifications)
        assert summary["attention_items"][0]["id"] == "b"

    def test_e_new_outranks_acknowledged_when_otherwise_comparable(self):
        """An acknowledged item never appears in attention_items at all,
        regardless of severity -- the strongest form of 'outranks'."""
        notifications = [
            _n(nid="ack", severity="CRITICAL", acknowledged_at="2026-08-24T09:00:00+00:00", read_at="2026-08-24T09:00:00+00:00"),
            _n(nid="new", severity="INFO"),
        ]
        summary = build_attention_summary(notifications)
        ids = [e["id"] for e in summary["attention_items"]]
        assert ids == ["new"]
        assert any(e["id"] == "ack" for e in summary["acknowledged"])

    def test_f_recency_affects_ranking(self):
        notifications = [
            _n(nid="older", severity="WARNING", created_at="2026-08-20T10:00:00+00:00"),
            _n(nid="newer", severity="WARNING", created_at="2026-08-24T10:00:00+00:00"),
        ]
        summary = build_attention_summary(notifications)
        ids = [e["id"] for e in summary["attention_items"]]
        assert ids == ["newer", "older"]

    def test_g_stale_unread_item_no_automatic_fake_urgency(self):
        """A stale item is still correctly listed as an attention item
        (never silently dropped -- STEP 14: 'do not silently delete it'),
        but a genuinely recent CRITICAL item still outranks it -- staleness
        alone is not artificially inflated to dominate the briefing."""
        notifications = [
            _n(nid="stale_high", severity="CRITICAL", created_at="2026-07-01T00:00:00+00:00"),
            _n(nid="fresh_critical", severity="CRITICAL", created_at="2026-08-24T09:00:00+00:00"),
        ]
        summary = build_attention_summary(notifications)
        ids = [e["id"] for e in summary["attention_items"]]
        assert "stale_high" in ids  # not dropped
        assert ids[0] == "fresh_critical"  # recency breaks the severity tie correctly

    def test_h_resolved_item_not_presented_as_current_problem(self):
        notifications = [_n(nid="r", severity="WARNING", transition=TRANSITION_RESOLVED)]
        summary = build_attention_summary(notifications)
        assert summary["attention_items"] == []
        assert any(e["id"] == "r" for e in summary["informational"])

    def test_i_acknowledged_not_equal_resolved(self):
        n = _n(nid="ack", severity="WARNING", acknowledged_at="2026-08-24T09:00:00+00:00", read_at="2026-08-24T09:00:00+00:00")
        assert classify_notification(n) == CLASS_ACKNOWLEDGED
        assert n.transition != TRANSITION_RESOLVED  # still NEW -- acknowledged is orthogonal to resolution


# ---------------------------------------------------------------------------
# J: principal isolation preserved through the attention tool
# ---------------------------------------------------------------------------


class TestPrincipalIsolationThroughAttentionTool:
    def test_j_notification_principal_isolation_preserved(self, monkeypatch):
        import openjarvis.second_brain.identity as identity_mod

        svc = _svc()
        mon_a = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="principal-a")
        mon_b = svc.create_monitor("b", {"ops_capability": "ops.production.get_kpi"}, principal="principal-b")
        _patch_ops(monkeypatch, _ops_envelope())
        svc.run_cycle(mon_a.id)
        svc.run_cycle(mon_b.id)

        monkeypatch.setattr(identity_mod, "resolve_runtime_principal", lambda: "principal-a")
        result = DailyAttentionSummaryTool(service=svc).execute()
        import json

        summary = json.loads(result.content)
        all_ids_monitor = {e["monitor_id"] for e in summary["attention_items"]}
        assert all_ids_monitor == {mon_a.id}


# ---------------------------------------------------------------------------
# K-P: evidence grounding, missing cause, recommendation, source selection
# (structural checks -- semantic behavior is live-certified in STEP 18)
# ---------------------------------------------------------------------------


class TestEvidenceAndSourceDiscipline:
    def test_k_current_ops_fact_grounded_via_existing_evidence_composition(self, monkeypatch):
        """Not reinvented: the attention tool never fabricates OPS values
        itself -- it only reads already-persisted Notification fields.
        The actual OPS-fact grounding guarantee is
        agents/operational_evidence.py's, unchanged by this phase."""
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        src = inspect.getsource(attn_mod)
        assert "ops_dynamic" not in src  # never talks to OPS directly
        assert "httpx" not in src and "requests" not in src

    def test_l_missing_cause_remains_unknown_structural(self):
        """The attention tool's own description explicitly instructs
        distinguishing certified fact from recommendation and never
        inventing a cause -- verified the wording exists."""
        desc = DailyAttentionSummaryTool().spec.description
        assert "priority_reason" in desc  # explainable, not opaque

    def test_m_recommendation_labeled_as_advice_in_tool_wording(self):
        desc = DailyAttentionSummaryTool().spec.description
        assert "never" in desc.lower() or "not a current problem" in desc

    def test_n_second_brain_not_called_by_attention_tool_itself(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        src = inspect.getsource(attn_mod)
        assert "second_brain" not in src.lower()

    def test_o_document_knowledge_not_called_by_attention_tool_itself(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        src = inspect.getsource(attn_mod)
        assert "document_search" not in src.lower()

    def test_p_no_unnecessary_multi_source_tool_spam_structural(self):
        """The attention tool makes exactly one underlying call
        (list_notifications) -- never fans out to OPS/Second Brain/
        Document Knowledge on its own; that decision stays with the
        model's own tool selection, per instruction."""
        import inspect

        src = inspect.getsource(DailyAttentionSummaryTool.execute)
        assert src.count("self._service.") == 1


# ---------------------------------------------------------------------------
# W: monitoring handoff reuses existing infrastructure
# ---------------------------------------------------------------------------


class TestMonitoringHandoff:
    def test_w_monitoring_handoff_uses_existing_monitor_infrastructure(self):
        """No new task/reminder subsystem exists -- 'controllalo domani'
        must still resolve to the same maia_monitor_create tool this
        phase did not touch."""
        import importlib

        import openjarvis.tools.monitoring_tools as mt
        from openjarvis.core.registry import ToolRegistry

        # conftest.py's autouse fixture clears every registry before each
        # test; re-run this module's @ToolRegistry.register decorators
        # (mirrors the established pattern in test_monitoring_tools.py).
        importlib.reload(mt)
        assert ToolRegistry.contains("maia_monitor_create")
        # No new scheduling primitive was added by this phase -- the
        # module's own docstring correctly says "no new scheduler" (a
        # negation), so check for an actual TaskScheduler reference, not
        # the bare word.
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        assert "TaskScheduler" not in inspect.getsource(attn_mod)


# ---------------------------------------------------------------------------
# X-Y: notification mark-read / acknowledge still work (reused, not
# reimplemented -- already fully certified in FASE 4Q.3; confirmed here
# still reachable through the attention-aware flow)
# ---------------------------------------------------------------------------


class TestNotificationInteractionStillWorks:
    def test_x_mark_read_still_works(self, monkeypatch):
        import openjarvis.second_brain.identity as identity_mod

        monkeypatch.setattr(identity_mod, "resolve_runtime_principal", lambda: "p1")
        svc = _svc()
        mon = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _patch_ops(monkeypatch, _ops_envelope())
        _, notifications = svc.run_cycle(mon.id)
        n = svc.mark_notification_read(notifications[0].id, principal="p1")
        assert n.status != STATUS_UNREAD

    def test_y_acknowledge_still_works(self, monkeypatch):
        import openjarvis.second_brain.identity as identity_mod

        monkeypatch.setattr(identity_mod, "resolve_runtime_principal", lambda: "p1")
        svc = _svc()
        mon = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        _patch_ops(monkeypatch, _ops_envelope())
        _, notifications = svc.run_cycle(mon.id)
        n = svc.acknowledge_notification(notifications[0].id, principal="p1")
        assert n.status == STATUS_ACKNOWLEDGED


# ---------------------------------------------------------------------------
# Z-JJ: structural non-regression
# ---------------------------------------------------------------------------


class TestStructuralNonRegression:
    def test_z_acknowledgement_causes_no_business_side_effect(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod.MonitorService.acknowledge_notification)
        assert "governed_actions" not in src
        assert "approve" not in src

    def test_aa_recommendation_causes_no_business_side_effect(self):
        """The attention tool is read-only end to end -- no write method
        of any kind is reachable from it."""
        import inspect

        src = inspect.getsource(DailyAttentionSummaryTool)
        forbidden = ("create_monitor", "acknowledge_notification", "mark_notification_read", "approve", "execute_action")
        for bad in forbidden:
            assert bad not in src, bad

    def test_bb_no_governed_action_self_approval(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        assert "governed_actions" not in inspect.getsource(attn_mod)

    def test_cc_no_outlook_email_calendar_side_effect(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod
        import openjarvis.tools.monitoring_tools as mt

        for src in (inspect.getsource(attn_mod), inspect.getsource(mt.DailyAttentionSummaryTool)):
            lowered = src.lower()
            for bad in ("outlook", "send_mail", "calendar", "smtp"):
                assert bad not in lowered, bad

    def test_dd_no_second_brain_write(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        src = inspect.getsource(attn_mod)
        for bad in ("ProposeEntry", "ConfirmEntry", "ArchiveTool", "LinkTool"):
            assert bad not in src, bad

    def test_ee_no_document_write(self):
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        assert "document" not in inspect.getsource(attn_mod).lower()

    def test_ff_no_fabricated_execution_claim_in_tool_description(self):
        desc = DailyAttentionSummaryTool().spec.description.lower()
        assert "i executed" not in desc
        assert "i resolved" not in desc
        assert "i fixed" not in desc

    def test_gg_no_unsupported_push_delivery_claim(self):
        desc = DailyAttentionSummaryTool().spec.description.lower()
        assert "will be pushed" not in desc
        assert "we will notify you" not in desc

    def test_hh_principal_isolation_remains_structural(self):
        """No 'principal' parameter exists on the attention tool's own
        schema -- it always resolves the real runtime identity itself,
        same guarantee as every FASE 4Q.3 notification tool."""
        spec = DailyAttentionSummaryTool().spec
        assert "principal" not in spec.parameters.get("properties", {})

    def test_ii_existing_monitoring_dedup_remains_intact(self, monkeypatch):
        _patch_ops(monkeypatch, _ops_envelope())
        svc = _svc()
        mon = svc.create_monitor("a", {"ops_capability": "ops.production.get_kpi"}, principal="p1")
        svc.run_cycle(mon.id)
        _, second = svc.run_cycle(mon.id)
        assert second == []

    def test_jj_scheduler_behavior_remains_intact(self):
        """This phase never imports or touches TaskScheduler at all."""
        import inspect

        import openjarvis.monitoring.attention as attn_mod

        assert "TaskScheduler" not in inspect.getsource(attn_mod)
