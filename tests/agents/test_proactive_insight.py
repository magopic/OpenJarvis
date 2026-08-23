"""FASE 4P.1 -- ProactiveReasoningService test matrix (STEP 14, A-P).

Unit-level, synthetic ToolResult fixtures. Mirrors the style of
tests/agents/test_operational_evidence.py.
"""

from __future__ import annotations

from openjarvis.agents.operational_evidence import build_evidence
from openjarvis.agents.proactive_insight import (
    ACTION_STATUS_PROPOSED,
    SEVERITY_ATTENTION,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    ProactiveReasoningService,
    ProposedAction,
)
from openjarvis.core.types import ToolResult

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _ops_fact(
    metric="oee",
    value=88.0,
    threshold=None,
    threshold_type=None,
    critical_threshold=None,
    period="2026-07",
    tool_name="ops_dynamic_production_get_kpi",
    is_certified_alert=None,
    alert_severity=None,
    alert_message=None,
):
    data = {"metric": metric, "value": value}
    if threshold is not None:
        data["threshold"] = threshold
    if threshold_type is not None:
        data["threshold_type"] = threshold_type
    if critical_threshold is not None:
        data["critical_threshold"] = critical_threshold
    if is_certified_alert is not None:
        data["is_certified_alert"] = is_certified_alert
    if alert_severity is not None:
        data["alert_severity"] = alert_severity
    if alert_message is not None:
        data["alert_message"] = alert_message
    return ToolResult(
        tool_name=tool_name,
        content="status=ok",
        success=True,
        metadata={
            "status": "ok",
            "period": period,
            "source": {"function_area": "production"},
            "data": data,
        },
    )


def _ops_missing(tool_name="ops_dynamic_production_get_kpi", period="2026-07", reason="no data"):
    return ToolResult(
        tool_name=tool_name,
        content="status=data_not_available",
        success=False,
        metadata={"status": "data_not_available", "period": period, "reason": reason},
    )


def _second_brain_result():
    return ToolResult(
        tool_name="second_brain_find_related_experiences",
        content="found some",
        success=True,
        metadata={
            "num_candidates": 1,
            "candidates": [
                {
                    "entry_id": "e1",
                    "type": "PROBLEM",
                    "retrieval_level": "STRUCTURED",
                    "active_or_superseded": "ACTIVE",
                    "matched_domains": ["production"],
                }
            ],
        },
    )


def _document_result():
    return ToolResult(
        tool_name="document_search",
        content="found a doc",
        success=True,
        metadata={
            "num_results": 1,
            "results": [
                {
                    "citation": "procedure.md, section \"Standard\"",
                    "filename": "procedure.md",
                    "content": "Do not exceed 15 minutes.",
                }
            ],
        },
    )


# ---------------------------------------------------------------------------
# A-P
# ---------------------------------------------------------------------------


class TestMatrix:
    def test_a_current_fact_plus_certified_threshold_breach_generates_insight(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze([tr])
        assert len(insights) == 1
        assert insights[0].severity == SEVERITY_WARNING
        assert insights[0].evidence  # evidence-grounded, never empty

    def test_b_current_fact_no_threshold_is_observation_only_no_invented_severity(self):
        tr = _ops_fact(value=88.61)  # no threshold key at all
        insights = ProactiveReasoningService().analyze([tr])
        # No detector has a certified condition to fire on -- correctly zero
        # insights, not an invented WARNING/CRITICAL.
        assert insights == []

    def test_c_historical_experience_only_no_current_operational_alert(self):
        insights = ProactiveReasoningService().analyze([_second_brain_result()])
        assert insights == []

    def test_d_document_procedure_only_no_current_operational_alert(self):
        insights = ProactiveReasoningService().analyze([_document_result()])
        assert insights == []

    def test_e_current_fact_plus_historical_precedent_clearly_separated(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze([tr, _second_brain_result()])
        assert len(insights) == 1
        insight = insights[0]
        # The current-fact finding and the historical context are both
        # present but distinguishable: reasoning_basis explicitly labels
        # the historical line as precedent, not current-cause proof.
        assert any("threshold" in line.lower() for line in insight.reasoning_basis)
        assert any("historical precedent" in line.lower() for line in insight.reasoning_basis)

    def test_f_current_fact_plus_document_procedure_clearly_separated(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze([tr, _document_result()])
        assert len(insights) == 1
        insight = insights[0]
        assert any("threshold" in line.lower() for line in insight.reasoning_basis)
        assert any("document context" in line.lower() for line in insight.reasoning_basis)

    def test_g_all_evidence_classes_correctly_composed(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze(
            [tr, _second_brain_result(), _document_result()]
        )
        assert len(insights) == 1
        insight = insights[0]
        source_classes = {item.source_class for item in insight.evidence}
        assert "CURRENT_OPERATIONAL_FACT" in source_classes
        assert "HISTORICAL_EXPERIENCE" in source_classes
        assert "DOCUMENT_EVIDENCE" in source_classes

    def test_h_conflicting_evidence_surfaced(self):
        tr1 = _ops_fact(metric="oee", value=88.0, tool_name="ops_dynamic_production_get_kpi")
        tr2 = _ops_fact(metric="oee", value=91.0, tool_name="ops_dynamic_production_get_kpi_v2")
        insights = ProactiveReasoningService().analyze([tr1, tr2])
        conflict = [i for i in insights if i.title.startswith("oee: conflicting")]
        assert len(conflict) == 1
        assert conflict[0].severity == SEVERITY_ATTENTION
        assert "88.0" in conflict[0].summary and "91.0" in conflict[0].summary

    def test_h2_different_periods_same_metric_is_not_a_conflict(self):
        """Live-found bug (FASE 4P.1A STEP 10): June OEE vs July OEE are
        genuinely different periods with genuinely different values --
        this is normal change over time, never a 'certified sources
        disagree' conflict. Only the SAME metric AND SAME period
        disagreeing is a real conflict."""
        tr_june = _ops_fact(metric="oee", value=87.9, period="2026-06")
        tr_july = _ops_fact(metric="oee", value=88.61, period="2026-07")
        insights = ProactiveReasoningService().analyze([tr_june, tr_july])
        assert not any(i.title.startswith("oee: conflicting") for i in insights)

    def test_i_missing_evidence_limitation_preserved(self):
        insights = ProactiveReasoningService().analyze([_ops_missing(reason="no filters matched")])
        assert len(insights) == 1
        assert insights[0].severity == SEVERITY_INFO
        assert "no filters matched" in insights[0].limitations

    def test_j_proposed_action_always_proposed(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze([tr])
        assert insights[0].proposed_actions
        for action in insights[0].proposed_actions:
            assert action.status == ACTION_STATUS_PROPOSED

    def test_k_side_effect_capable_proposal_requires_confirmation(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        insights = ProactiveReasoningService().analyze([tr])
        for action in insights[0].proposed_actions:
            assert action.requires_confirmation is True
            assert action.execution_capability is None

    def test_l_no_execution_capability_exposed_structural(self):
        import openjarvis.agents.proactive_insight as mod

        # No execute-shaped symbol anywhere in the module's public surface.
        forbidden = {"execute_action", "run_action", "do_it", "send_anything", "write_anything"}
        assert forbidden.isdisjoint(set(mod.__all__))
        # ProposedAction has no callable execute()/run() method.
        action = ProposedAction(id="x", title="t", description="d", action_type="review")
        assert not hasattr(action, "execute")
        assert not hasattr(action, "run")

    def test_m_second_brain_unaffected_by_analysis(self):
        # analyze() takes ToolResults already gathered elsewhere -- it never
        # imports or calls any Second Brain *write* path.
        import openjarvis.agents.proactive_insight as mod
        import inspect

        source = inspect.getsource(mod)
        assert "create_entry" not in source
        assert "confirm_entry" not in source
        assert "second_brain" not in source.lower()

    def test_o_repeated_identical_evidence_deterministic_identity(self):
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        svc = ProactiveReasoningService()
        first = svc.analyze([tr])
        second = svc.analyze([tr])
        assert first[0].id == second[0].id
        assert first[0].severity == second[0].severity
        assert first[0].title == second[0].title

    def test_p_evidence_only_flows_through_governed_service_layer(self):
        """Structural: this module never imports a raw store (SQLite,
        SecondBrainStore, FileStateStore) -- only build_evidence() and the
        ToolResult contract, so it cannot bypass whatever
        authorization/visibility filtering already happened inside the
        tools that produced those ToolResults."""
        import inspect

        import openjarvis.agents.proactive_insight as mod

        source = inspect.getsource(mod)
        for forbidden in ("SecondBrainStore", "FileStateStore", "sqlite3", "KnowledgeStore"):
            assert forbidden not in source

    def test_unresolved_proposal_detector_needs_explicit_history(self):
        prior = ProposedAction(
            id="prior-1", title="Check die wear", description="d", action_type="review"
        )
        svc = ProactiveReasoningService()
        no_history = svc.analyze([_ops_fact(value=88.0)], prior_proposals=None)
        with_history = svc.analyze([_ops_fact(value=88.0)], prior_proposals=[prior])
        assert not any("Still open" in i.title for i in no_history)
        assert any("Still open" in i.title for i in with_history)

    def test_certified_alert_detector_dormant_without_self_identifying_source(self):
        tr = _ops_fact(value=88.0)  # no is_certified_alert flag
        insights = ProactiveReasoningService().analyze([tr])
        assert not any("Certified alert" in i.title for i in insights)

    def test_certified_alert_detector_fires_when_source_self_identifies(self):
        tr = _ops_fact(value=88.0, is_certified_alert=True, alert_severity="CRITICAL", alert_message="msg")
        insights = ProactiveReasoningService().analyze([tr])
        alerts = [i for i in insights if i.title.startswith("Certified alert")]
        assert len(alerts) == 1
        assert alerts[0].severity == "CRITICAL"

    def test_build_evidence_reused_not_reimplemented(self):
        """analyze() with no evidence passed builds it via the frozen
        build_evidence(), never a parallel implementation."""
        tr = _ops_fact(value=60.0, threshold=70.0, threshold_type="min")
        evidence = build_evidence([tr])
        insights_a = ProactiveReasoningService().analyze([tr], evidence)
        insights_b = ProactiveReasoningService().analyze([tr])
        assert insights_a[0].id == insights_b[0].id
