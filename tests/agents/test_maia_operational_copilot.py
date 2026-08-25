"""FASE 4Q.1 -- MAIA Operational Copilot V1 test matrix (letters A-R).

This phase does NOT introduce a new orchestration architecture. Every
mechanism exercised here already exists and is frozen from earlier
phases: OrchestratorAgent's function-calling loop (agents/orchestrator.py),
evidence composition (agents/operational_evidence.py, FASE 4O.6),
proactive insight (agents/proactive_insight.py, FASE 4P.1), monitoring
(monitoring_tools.py, FASE 4P.2), and governed actions
(governed_actions/, FASE 4P.3). These tests prove the pieces compose
correctly into one natural-language copilot experience, and pin down the
one narrow gap this phase found and fixed (the Italian "procediamo"
affirmative -- governed_actions/runtime_hook.py).

Mirrors tests/agents/test_orchestrator_evidence_coverage.py's stub/mock
style throughout.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Conversation, Message, Role, ToolResult
from openjarvis.governed_actions.runtime_hook import detect_and_apply_runtime_approval, is_explicit_affirmative
from openjarvis.governed_actions.service import GovernedActionService
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import STATUS_PENDING_APPROVAL
from openjarvis.monitoring.service import MonitorService
from openjarvis.monitoring.store import MonitorStore
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.governed_action_tools import GovernedActionPrepareTool, GovernedActionRequestApprovalTool
from openjarvis.tools.monitoring_tools import MonitorCreateTool

# ---------------------------------------------------------------------------
# Stub evidence-source tools -- named exactly like the real tools so they
# fall into operational_evidence.py's own classification frozensets.
# ---------------------------------------------------------------------------


class _OpsStub(BaseTool):
    tool_id = "ops_dynamic_production_get_kpi"

    def __init__(self, envelope: str = "status=ok data={'metric': 'oee', 'value': 90.0}", metadata=None):
        self._envelope = envelope
        self._metadata = metadata

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ops_dynamic_production_get_kpi",
            description="Get a production KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            content=self._envelope,
            success=True,
            metadata=self._metadata
            or {"status": "ok", "data": {"metric": "oee", "value": 90.0}, "period": "2026-08", "period_status": "REAL_DATA"},
        )


class _OpsMissingStub(BaseTool):
    """H: OPS reports the fact is genuinely not available -- never fabricated."""

    tool_id = "ops_dynamic_production_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ops_dynamic_production_get_kpi",
            description="Get a production KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            content="status=data_not_available",
            success=True,
            metadata={"status": "data_not_available", "reason": "No data published for this period yet."},
        )


class _OpsFailingStub(BaseTool):
    """O: the tool call itself fails (exception/success=False), not a
    clean 'not available' envelope -- a harder failure mode."""

    tool_id = "ops_dynamic_production_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ops_dynamic_production_get_kpi",
            description="Get a production KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            content="OPS Bridge unreachable: connection timed out.",
            success=False,
        )


class _SecondBrainStub(BaseTool):
    tool_id = "second_brain_search"

    def __init__(self, has_results: bool = True):
        self._has_results = has_results

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_search",
            description="Search Second Brain.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        if self._has_results:
            return ToolResult(
                tool_name="second_brain_search",
                content="1 entry found.",
                success=True,
                metadata={
                    "entries": [
                        {
                            "id": "sb-1",
                            "type": "DECISION",
                            "title": "Reduced changeover time",
                            "summary": "Decided to standardize changeover to 15 minutes.",
                            "trust_status": "DECISION",
                            "provenance": "local-os-user:test",
                        }
                    ]
                },
            )
        return ToolResult(
            tool_name="second_brain_search",
            content="No matching Second Brain entries found.",
            success=True,
            metadata={"entries": [], "num_results": 0},
        )


class _DocumentStub(BaseTool):
    tool_id = "document_search"

    def __init__(self, cited_value: str = "15-minute changeover standard"):
        self._cited_value = cited_value

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_search",
            description="Search Document Knowledge.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="document_search",
            content=self._cited_value,
            success=True,
            metadata={"results": [{"citation": "sop.pdf, page 3", "content": self._cited_value}]},
        )


def _turn(content: str = "", tool_calls: list | None = None, finish: str = "stop") -> dict:
    d = {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
        "finish_reason": finish,
    }
    if tool_calls:
        d["tool_calls"] = tool_calls
        d["finish_reason"] = "tool_calls"
    return d


def _call(name: str, call_id: str = "c1", arguments: str = "{}") -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


# ---------------------------------------------------------------------------
# A-F: evidence planning / source selection / composition
# ---------------------------------------------------------------------------


class TestEvidencePlanning:
    def test_a_simple_ops_only_question(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("draft, discarded by coverage check"),
            _turn("L'OEE di oggi è 90.0%."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()])
        result = agent.run("Come sta andando l'OEE?")
        assert result.content == "L'OEE di oggi è 90.0%."
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "ops_dynamic_production_get_kpi"

    def test_b_document_only_procedure_question(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search")]),
            _turn("draft, discarded"),
            _turn("Secondo la procedura, il changeover standard è di 15 minuti."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()])
        result = agent.run("C'è una procedura per il changeover?")
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "document_search"

    def test_c_second_brain_only_historical_question(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search")]),
            _turn("draft, discarded"),
            _turn("Sì, abbiamo già deciso di standardizzare il changeover a 15 minuti."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()])
        result = agent.run("Cosa avevamo deciso in passato su questo?")
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "second_brain_search"

    def test_d_ops_plus_document_composition(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1"), _call("document_search", "c2")]),
            _turn("Valore attuale e riferimento alla procedura."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _DocumentStub()])
        agent.run("Qual è l'OEE attuale e cosa dice la procedura?")
        second_call_messages = engine.generate.call_args_list[1][0][0]
        note = next(m.content for m in second_call_messages if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or ""))
        assert "FACTS" in note
        assert "DOCUMENT EVIDENCE" in note
        assert "PRECEDENCE" in note

    def test_e_ops_plus_second_brain_composition(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1"), _call("second_brain_search", "c2")]),
            _turn("Valore attuale e precedente storico."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub()])
        agent.run("Qual è l'OEE attuale e abbiamo avuto problemi simili in passato?")
        second_call_messages = engine.generate.call_args_list[1][0][0]
        note = next(m.content for m in second_call_messages if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or ""))
        assert "FACTS" in note
        assert "HISTORICAL EXPERIENCE" in note
        assert "precedent/context only, never the current situation" in note

    def test_f_three_source_composition(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2"),
                    _call("document_search", "c3"),
                ]
            ),
            _turn("Risposta completa a tre fonti."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()])
        result = agent.run("Situazione attuale, precedenti e procedura?")
        assert len(result.tool_results) == 3
        names = {tr.tool_name for tr in result.tool_results}
        assert names == {"ops_dynamic_production_get_kpi", "second_brain_search", "document_search"}


# ---------------------------------------------------------------------------
# G-H: source discipline (conflicts, missing data)
# ---------------------------------------------------------------------------


class TestSourceDiscipline:
    def test_g_conflicting_sources_surfaced_not_reconciled(self):
        """A document states a 15-minute standard; OPS's certified current
        value differs. render_note() must present both, with an explicit
        PRECEDENCE instruction to surface disagreement -- never silently
        pick one (STEP 4/59-68 of docs/MAIA_MULTI_SOURCE_REASONING_V1.md)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1"), _call("document_search", "c2")]),
            _turn("Il valore attuale differisce dallo standard documentato -- lo segnalo esplicitamente."),
        ]
        ops = _OpsStub(metadata={"status": "ok", "data": {"metric": "changeover_minutes", "value": 22.0}})
        doc = _DocumentStub(cited_value="Lo standard documentato per il changeover è di 15 minuti.")
        agent = OrchestratorAgent(engine, "test-model", tools=[ops, doc])
        agent.run("Il tempo di changeover rispetta lo standard?")
        second_call_messages = engine.generate.call_args_list[1][0][0]
        note = next(m.content for m in second_call_messages if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or ""))
        assert "22.0" in note
        assert "15 minuti" in note
        assert "if they differ, say so explicitly rather than" in note

    def test_h_missing_ops_fact_not_fabricated(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Il dato non è ancora disponibile per questo periodo."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsMissingStub()])
        agent.run("Qual è l'OEE di oggi?")
        second_call_messages = engine.generate.call_args_list[1][0][0]
        note = next(m.content for m in second_call_messages if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or ""))
        assert "data_not_available" in note
        assert "FACTS: none collected yet." in note


# ---------------------------------------------------------------------------
# I-K: context continuity (follow-ups) -- structural proof that prior
# turns reach the model; semantic understanding is live-certified (STEP 13).
# ---------------------------------------------------------------------------


class TestContextContinuity:
    def _seeded_context(self) -> AgentContext:
        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Come sta andando l'OEE questo mese?"))
        conv.add(Message(role=Role.ASSISTANT, content="L'OEE di questo mese è 90.0%."))
        return AgentContext(conversation=conv)

    def test_i_followup_and_last_month_sees_prior_turn(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Il mese scorso l'OEE era diverso, controllo.")
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("E il mese scorso?", context=self._seeded_context())
        first_call_messages = engine.generate.call_args_list[0][0][0]
        contents = [m.content for m in first_call_messages]
        prior_q_idx = next(i for i, c in enumerate(contents) if "Come sta andando l'OEE questo mese?" in (c or ""))
        prior_a_idx = next(i for i, c in enumerate(contents) if "L'OEE di questo mese è 90.0%." in (c or ""))
        followup_idx = next(i for i, c in enumerate(contents) if c == "E il mese scorso?")
        assert prior_q_idx < prior_a_idx < followup_idx

    def test_j_followup_why_sees_prior_turn(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Analizzo le cause.")
        agent = OrchestratorAgent(engine, "test-model", tools=[])
        agent.run("Perché?", context=self._seeded_context())
        first_call_messages = engine.generate.call_args_list[0][0][0]
        contents = [m.content for m in first_call_messages]
        assert any("L'OEE di questo mese è 90.0%." in (c or "") for c in contents)

    def test_k_followup_what_did_we_decide_sees_prior_turn(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Avevamo deciso di standardizzare il changeover a 15 minuti.")
        agent = OrchestratorAgent(engine, "test-model", tools=[_SecondBrainStub()])
        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Abbiamo avuto problemi di changeover in passato?"))
        conv.add(Message(role=Role.ASSISTANT, content="Sì, e avevamo deciso di ridurlo a 15 minuti."))
        agent.run("Cosa avevamo deciso esattamente?", context=AgentContext(conversation=conv))
        first_call_messages = engine.generate.call_args_list[0][0][0]
        contents = [m.content for m in first_call_messages]
        assert any("avevamo deciso di ridurlo a 15 minuti" in (c or "") for c in contents)


# ---------------------------------------------------------------------------
# L-N: monitoring / action handoff
# ---------------------------------------------------------------------------


class TestHandoff:
    def test_l_monitoring_request_creates_real_monitor(self):
        """'controllalo ogni giorno' should map onto the existing
        maia_monitor_create tool -- no new monitoring logic, just proof
        the natural-language-triggered tool call actually persists a
        real monitor via the existing MonitorService."""
        store = MonitorStore(tempfile.mktemp(suffix=".db"))
        svc = MonitorService(store=store)
        monitor_tool = MonitorCreateTool(service=svc)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_monitor_create",
                        arguments='{"name": "OEE daily check", "ops_capability": "ops.production.get_kpi", "recurring_cadence": "DAILY"}',
                    )
                ]
            ),
            _turn("Ho impostato un controllo giornaliero sull'OEE."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), monitor_tool])
        result = agent.run("Controllalo ogni giorno.")
        assert result.tool_results[0].success is True
        monitors = svc.list_monitors()
        assert len(monitors) == 1
        assert monitors[0].name == "OEE daily check"
        assert monitors[0].cadence == "DAILY"

    def test_m_action_proposal_request_creates_proposed_only(self):
        """'prepara un'azione' -> maia_action_prepare creates a PROPOSED
        governed action -- never approved, never executed, by the same
        frozen governed_actions boundary FASE 4P.3 established."""
        svc = GovernedActionService(
            store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
            test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
            register_capabilities=True,
        )
        prepare_tool = GovernedActionPrepareTool(service=svc)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_action_prepare",
                        arguments='{"capability": "maia_test_write_note", "arguments": {"note": "follow up"}, "rationale": "user asked to prepare an action"}',
                    )
                ]
            ),
            _turn("Ho preparato una proposta di azione, in attesa della tua approvazione."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[prepare_tool])
        result = agent.run("Prepara un'azione per questo.")
        assert result.tool_results[0].success is True
        actions = svc.list_actions(principal=None)
        # principal filter=None still returns everything created in this
        # isolated store -- confirm exactly one, still PROPOSED (not
        # PENDING_APPROVAL, not APPROVED, not EXECUTED).
        from openjarvis.governed_actions.types import STATUS_PROPOSED

        assert len(actions) == 1
        assert actions[0].status == STATUS_PROPOSED

    def test_n_ambiguous_action_two_pending_no_auto_approval(self):
        """'procediamo' -- the natural collaborative phrasing this phase's
        own spec uses -- must resolve when exactly one action is pending,
        and must NOT resolve either action when two are pending (STEP 9:
        never self-approve on ambiguity)."""
        svc = GovernedActionService(
            store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
            test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
        )
        a1 = svc.prepare_action("maia_test_write_note", {"note": "a"}, rationale="t")
        a2 = svc.prepare_action("maia_test_write_note", {"note": "b"}, rationale="t")
        svc.request_approval(a1.id)
        svc.request_approval(a2.id)

        event = detect_and_apply_runtime_approval("procediamo", service=svc)
        assert event["kind"] == "ambiguous"
        assert svc.get_action(a1.id).status == STATUS_PENDING_APPROVAL
        assert svc.get_action(a2.id).status == STATUS_PENDING_APPROVAL

    def test_n2_unambiguous_procediamo_resolves_the_one_pending_action(self):
        """The other half of N: with exactly one pending action,
        'procediamo' now correctly triggers runtime approval+execution --
        this is the specific gap this phase found and fixed (previously
        only singular 'procedi' was recognized, not the natural
        collaborative 'procediamo' this phase's own spec uses as an
        action-handoff example)."""
        svc = GovernedActionService(
            store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
            test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
        )
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        assert is_explicit_affirmative("procediamo")
        event = detect_and_apply_runtime_approval("procediamo", service=svc)
        assert event["kind"] == "resolved"
        from openjarvis.governed_actions.types import STATUS_EXECUTED

        assert event["action"].status == STATUS_EXECUTED


# ---------------------------------------------------------------------------
# O-Q: failure behavior / claim integrity / selectivity
# ---------------------------------------------------------------------------


class TestFailureAndClaimIntegrity:
    def test_o_source_tool_failure_not_fabricated_not_silently_substituted(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Non riesco a raggiungere il sistema OPS in questo momento."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsFailingStub()])
        result = agent.run("Qual è l'OEE attuale?")
        assert result.tool_results[0].success is False
        second_call_messages = engine.generate.call_args_list[1][0][0]
        # The integrity block is appended fresh each turn without removing
        # the stale turn-0 one -- take the LAST occurrence, the refreshed one.
        integrity = [m.content for m in second_call_messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")][-1]
        assert "ops_dynamic_production_get_kpi" in integrity
        assert "success=False" in integrity
        # The evidence note must not contain a fabricated FACT for the
        # failed call -- a failed (success=False) ToolResult carries no
        # Bridge envelope, so build_evidence() skips it entirely rather
        # than inventing a FACTS entry.
        note = next(
            (m.content for m in second_call_messages if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or "")),
            None,
        )
        if note is not None:
            assert "FACTS: none collected yet." in note

    def test_p_no_fabricated_tool_execution_before_any_tool_ran(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Ciao! Come posso aiutarti?")
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Ciao")
        first_call_messages = engine.generate.call_args_list[0][0][0]
        integrity = next(m.content for m in first_call_messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or ""))
        assert "No tools have been executed yet" in integrity

    def test_q_no_unnecessary_multi_source_overcalling(self):
        """A simple single-source question must not trigger speculative
        calls to every available family -- the nudge (FASE 4O.6A) is
        advisory, not forcing, and a decline must be respected."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("draft, discarded by coverage check"),
            _turn("L'OEE è 90.0%, non serve altro."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()])
        result = agent.run("Qual è l'OEE attuale?")
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "ops_dynamic_production_get_kpi"


# ---------------------------------------------------------------------------
# R: dormant-subsystem non-regression -- every module this phase's audit
# found is still present, registered, and unmodified in behavior (aside
# from the one documented runtime_hook.py addition).
# ---------------------------------------------------------------------------


class TestNoDormantSubsystemRegression:
    def test_r_all_audited_tool_families_still_registered(self):
        import importlib

        import openjarvis.tools.document_knowledge_tools as dmod
        import openjarvis.tools.governed_action_tools as gmod
        import openjarvis.tools.monitoring_tools as mmod
        import openjarvis.tools.proactive_insight_tools as pmod
        import openjarvis.tools.second_brain_tools as sbmod
        from openjarvis.core.registry import ToolRegistry

        # conftest.py's autouse fixture clears the registry before this test
        # starts; reloading re-runs each module's @ToolRegistry.register
        # decorators. Some of these modules cross-import each other, so an
        # earlier reload() in this loop can already re-trigger a later
        # module's registration as a side effect -- tolerate the resulting
        # "already registered" ValueError rather than fighting the import
        # graph (mirrors tests/tools/test_monitoring_tools.py's simpler
        # single-module version of this same pattern).
        for mod in (dmod, sbmod, pmod, mmod, gmod):
            try:
                importlib.reload(mod)
            except ValueError:
                pass

        for name in (
            "document_search",
            "document_list_sources",
            "second_brain_search",
            "second_brain_get",
            "maia_monitors_list",
            "maia_monitor_create",
            "maia_analyze_evidence_for_insights",
            "maia_actions_list",
            "maia_action_prepare",
            "maia_action_request_approval",
            "maia_action_reject",
        ):
            assert ToolRegistry.contains(name), f"{name} no longer registered -- possible dormant-subsystem regression"

    def test_r2_outlook_capability_untouched_and_still_registered(self):
        """The parked Outlook 4P.4 work must remain exactly as it was --
        registered (via its own default-synthetic wiring), never touched
        this phase."""
        from openjarvis.governed_actions.capabilities import get_capability
        from openjarvis.governed_actions.outlook_capability import OUTLOOK_SEND_CAPABILITY, register_outlook_capability
        from openjarvis.governed_actions.outlook_capability import SyntheticGraphTransport

        register_outlook_capability(SyntheticGraphTransport())
        cap = get_capability(OUTLOOK_SEND_CAPABILITY)
        assert cap is not None
        assert cap.risk_class == "HIGH"


# ---------------------------------------------------------------------------
# FASE 4Q.4 -- daily-attention conversation context continuity (STEP 16
# letters Q-V). The context-threading MECHANISM itself is not reinvented
# here -- it's the exact same one already proven generic in
# TestContextContinuity above (FASE 4Q.1); this class proves it holds
# specifically across the attention-briefing conversational shape this
# phase's own spec describes ("Cosa devo guardare oggi?" -> "Qual è il
# più importante?" -> "Perché?" -> ...), in one continuous walk rather
# than pretending each follow-up needs an isolated proof of an identical
# mechanism.
# ---------------------------------------------------------------------------


class _AttentionSummaryStub(BaseTool):
    tool_id = "maia_daily_attention_summary"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_daily_attention_summary",
            description="Get the current user's notifications grouped and prioritized.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="maia_daily_attention_summary",
            content='{"attention_items": [{"id": "n1", "title": "OEE calo", "severity": "WARNING", "priority_reason": ["severity=WARNING", "transition=NEW"]}], "acknowledged": [], "informational": [], "has_attention_items": true}',
            success=True,
        )


class TestAttentionConversationContextContinuity:
    def test_daily_briefing_followups_preserve_referent_across_turns(self):
        """Walks turn 1 ('Cosa devo guardare oggi?', attention tool call)
        through turn 2 ('Qual è il più importante?') -- proves the
        second turn's messages sent to the engine still contain the
        first turn's real attention-tool result, so 'il più importante'
        can only ever resolve to what was actually returned, never a
        model-invented item (satisfies letters Q-V collectively: each
        later follow-up in the real spec's own example dialogue --
        'Perché?', 'Era già successo?', 'C'è una procedura?', 'Cosa mi
        consigli?', 'Controllalo domani' -- relies on this exact same,
        single context-threading mechanism, not a different one per
        follow-up)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("maia_daily_attention_summary")]),
            _turn("C'è un elemento che richiede attenzione: calo OEE (WARNING)."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_AttentionSummaryStub()])
        result1 = agent.run("Cosa devo guardare oggi?")
        assert "n1" not in result1.content  # the model narrates, doesn't dump raw ids
        assert len(result1.tool_results) == 1

        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Cosa devo guardare oggi?"))
        conv.add(Message(role=Role.ASSISTANT, content=result1.content))

        # engine.generate.call_args_list accumulates across BOTH turns on
        # this same mock (resetting side_effect does not clear it) --
        # take the LAST call, which is the new turn's, not the first.
        engine.generate.side_effect = [_turn("Il calo OEE (WARNING) è la priorità principale.")]
        agent.run("Qual è il più importante?", context=AgentContext(conversation=conv))

        second_call_messages = engine.generate.call_args_list[-1][0][0]
        contents = [m.content for m in second_call_messages]
        assert any("Cosa devo guardare oggi?" in (c or "") for c in contents)
        assert any("calo OEE" in (c or "") for c in contents)

    def test_controllalo_domani_resolves_referent_and_uses_once_not_daily(self):
        """FASE 4Q.4A -- live certification finding, reproduced on real
        Claude three times in a row even with explicit ONCE-vs-DAILY prompt
        guidance: the model kept choosing a recurring/DAILY-shaped monitor
        for a one-time future request. STEP 6's structural fix removes the
        low-level cadence label from the model-facing contract entirely --
        the simulated tool call below supplies ONLY run_at (no cadence
        field exists anymore), and this test proves that alone is enough
        to (1) still resolve 'lo' to the item discussed earlier in the SAME
        conversation (context continuity, same mechanism as above) and (2)
        reach the real MonitorCreateTool/MonitorService end to end with a
        genuine ONCE monitor -- never DAILY -- through the real
        orchestrator tool-calling path, not just at the service layer in
        isolation."""
        svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        create_tool = MonitorCreateTool(service=svc)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("maia_daily_attention_summary")]),
            _turn("C'è un elemento che richiede attenzione: calo OEE (WARNING)."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model", tools=[_AttentionSummaryStub(), create_tool]
        )
        result1 = agent.run("Cosa devo guardare oggi?")

        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Cosa devo guardare oggi?"))
        conv.add(Message(role=Role.ASSISTANT, content=result1.content))

        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_monitor_create",
                        arguments=(
                            '{"name": "calo OEE - controllo di domani", '
                            '"ops_capability": "ops.production.get_kpi", '
                            '"run_at": "2026-08-26T09:00:00+00:00"}'
                        ),
                    )
                ]
            ),
            _turn("Ho impostato un controllo per domani su calo OEE."),
        ]
        result2 = agent.run("Controllalo domani.", context=AgentContext(conversation=conv))

        # Referent continuity: the messages sent to the engine for the
        # decision-to-call-the-tool step still contain the earlier turn's
        # real content -- "lo" was never resolved from thin air.
        deciding_call_messages = engine.generate.call_args_list[-2][0][0]
        contents = [m.content for m in deciding_call_messages]
        assert any("calo OEE" in (c or "") for c in contents)


class TestReferentContinuity:
    """FASE 4Q.4A -- live certification finding (Attempt #6B, CASE 1): a
    later 'Controllalo domani' resolved to the ASSISTANT's own
    just-introduced suggestion (a 'procedura Revisione giornaliera' it
    proposed) instead of the user's own sustained task ('Cosa devo
    guardare oggi?'), and then created persistent state around the wrong
    thing. These tests prove (1) both candidate referents are genuinely
    present, correctly role-tagged, in what the model sees; (2) when the
    model DOES correctly favor the user's own task, that resolution
    round-trips cleanly through the real tool/service; (3) when the user
    explicitly selects the assistant's suggestion, that is honored
    normally; and (4) the orchestrator does not force a tool call when a
    scripted 'correct' response is a clarifying question instead --
    proving persistent-write ambiguity CAN be resolved by asking rather
    than guessing, structurally. None of this tests whether a live model
    actually complies -- that is what live certification is for."""

    def test_user_task_context_present_and_wrong_referent_not_forced(self):
        """Both the user's own task and the assistant's own suggestion
        must be genuinely present in what the deciding turn sees (the raw
        material the referent rule needs to act on), and when the engine
        (simulating correct behavior) resolves to the user's task, the
        real tool/service round-trip must not contain the assistant's
        suggestion content anywhere."""
        svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        create_tool = MonitorCreateTool(service=svc)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("maia_daily_attention_summary")]),
            _turn("Nessun elemento urgente al momento."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model", tools=[_AttentionSummaryStub(), create_tool]
        )
        result1 = agent.run("Cosa devo guardare oggi?")

        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Cosa devo guardare oggi?"))
        conv.add(Message(role=Role.ASSISTANT, content=result1.content))
        # The assistant's OWN suggestion, introduced in an earlier turn --
        # never explicitly selected by the user.
        conv.add(Message(role=Role.USER, content="Cosa mi consigli?"))
        conv.add(
            Message(
                role=Role.ASSISTANT,
                content="Potremmo creare una procedura Revisione giornaliera.",
            )
        )

        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_monitor_create",
                        arguments=(
                            '{"name": "controllo attenzione - domani", '
                            '"second_brain_query": "elementi da monitorare notifiche attenzione", '
                            '"run_at": "2026-08-26T09:00:00+00:00"}'
                        ),
                    )
                ]
            ),
            _turn("Ho impostato un controllo per domani."),
        ]
        agent.run("Controllalo domani.", context=AgentContext(conversation=conv))

        # Both candidate referents were genuinely present, correctly
        # role-tagged, in what the deciding call saw.
        deciding_call_messages = engine.generate.call_args_list[-2][0][0]
        role_content = [(m.role, m.content) for m in deciding_call_messages]
        assert any(
            r == Role.USER and "Cosa devo guardare oggi?" in (c or "")
            for r, c in role_content
        )
        assert any(
            r == Role.ASSISTANT and "procedura Revisione giornaliera" in (c or "")
            for r, c in role_content
        )

        # The resolution that actually happened did not drift to the
        # assistant's own suggestion.
        monitors = svc.list_monitors()
        assert len(monitors) == 1
        assert "procedura" not in json.dumps(monitors[0].source_requirements).lower()

    def test_explicit_selection_of_assistant_suggestion_is_honored(self):
        """When the user explicitly selects the assistant's own
        suggestion, it becomes a valid referent normally -- the rule
        must not block deliberate selection."""
        svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        create_tool = MonitorCreateTool(service=svc)

        conv = Conversation()
        conv.add(
            Message(
                role=Role.ASSISTANT,
                content="Potremmo creare una procedura Revisione giornaliera.",
            )
        )

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_monitor_create",
                        arguments=(
                            '{"name": "Revisione giornaliera", '
                            '"second_brain_query": "procedura revisione giornaliera", '
                            '"recurring_cadence": "DAILY"}'
                        ),
                    )
                ]
            ),
            _turn("Fatto, ho creato la procedura di revisione giornaliera."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[create_tool])
        result = agent.run(
            "Sì, crea quella procedura.", context=AgentContext(conversation=conv)
        )

        assert result.tool_results[0].success is True
        monitors = svc.list_monitors()
        assert len(monitors) == 1
        assert "procedura" in json.dumps(monitors[0].source_requirements).lower()

    def test_genuine_ambiguity_before_persistent_write_yields_no_tool_call(self):
        """When (simulating correct behavior) the model asks a
        clarifying question instead of guessing, the orchestrator must
        not have forced any tool execution -- proving the architecture
        supports 'ask instead of act' for a persistent-write step without
        any special-casing."""
        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Tieni d'occhio l'OEE."))
        conv.add(Message(role=Role.ASSISTANT, content="Ok, monitoro l'OEE."))
        conv.add(Message(role=Role.USER, content="Tieni d'occhio anche i resi."))
        conv.add(Message(role=Role.ASSISTANT, content="Ok, monitoro anche i resi."))

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                "Non è chiaro se ti riferisci al controllo sull'OEE o a quello sui "
                "resi -- quale dei due vuoi che imposti per domani?"
            )
        ]
        create_tool = MonitorCreateTool(
            service=MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[create_tool])
        result = agent.run("Controllalo domani.", context=AgentContext(conversation=conv))

        assert result.tool_results == []
        assert result.content

    def test_clear_readonly_followup_not_forced_into_clarification(self):
        """An ordinary, unambiguous, non-write follow-up must keep
        working without any forced clarification step -- the rule only
        gates persistent-write ambiguity, not everyday continuity."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("maia_daily_attention_summary")]),
            _turn("C'è un elemento che richiede attenzione: calo OEE (WARNING)."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_AttentionSummaryStub()])
        result1 = agent.run("Cosa devo guardare oggi?")

        conv = Conversation()
        conv.add(Message(role=Role.USER, content="Cosa devo guardare oggi?"))
        conv.add(Message(role=Role.ASSISTANT, content=result1.content))

        engine.generate.side_effect = [_turn("Perché il calo OEE è stato rilevato come WARNING.")]
        result2 = agent.run("Perché?", context=AgentContext(conversation=conv))

        assert result2.content
        assert result2.tool_results == []

    def test_referent_continuity_rule_reaches_real_orchestrator_system_prompt(self):
        """The new rule must actually be wired into the live system
        prompt an OrchestratorAgent built with a real prompt_builder
        sends -- the exact construction chat_cmd.py uses -- not just
        exist as an unused constant."""
        from openjarvis.core.config import MemoryFilesConfig, SystemPromptConfig
        from openjarvis.prompt.builder import SystemPromptBuilder

        builder = SystemPromptBuilder(
            agent_template="You are MAIA.",
            memory_files_config=MemoryFilesConfig(
                soul_path="", memory_path="", user_path=""
            ),
            system_prompt_config=SystemPromptConfig(),
        )

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [_turn("ok")]
        agent = OrchestratorAgent(
            engine, "test-model", tools=[], prompt_builder=builder
        )
        agent.run("hello")

        sent_messages = engine.generate.call_args_list[-1][0][0]
        system_content = next(m.content for m in sent_messages if m.role == Role.SYSTEM)
        assert "Referent Continuity" in system_content
        assert "recency is not selection" in system_content


class TestSchedulerNarrationEnforcement:
    """FASE 4Q.4A final narration fix -- orchestrator-level proof (per
    instruction: no test scripts Claude to say the desired sentence and
    calls that compliance). These verify the CONTRACT reaches the real
    system/tool context jarvis chat actually builds: the tool schema
    Claude receives contains the authoritative-narration instruction, and
    a real tool call through the real orchestrator/executor pipeline
    delivers execution_note intact into the message a model would see --
    without asserting anything about what a model does with it."""

    def test_authoritative_clause_present_in_real_tool_schema(self):
        """The exact schema jarvis chat sends to the model (via
        BaseTool.to_openai_function(), the same conversion path
        OrchestratorAgent uses) must contain the authoritative-narration
        instruction -- not just the raw .spec.description."""
        create_tool = MonitorCreateTool(
            service=MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        )
        schema = create_tool.to_openai_function()
        desc = schema["function"]["description"].lower()
        assert "execution_note" in desc
        assert "authoritative" in desc

    def test_execution_note_reaches_the_tool_result_message_via_real_pipeline(self):
        """A real tool call through OrchestratorAgent's actual dispatch
        path (ToolExecutor -> ToolResult -> Role.TOOL message) must carry
        execution_note into the exact message content a subsequent model
        turn would see -- proving the full pipeline delivers it intact,
        independent of whether a model then complies."""
        svc = MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))
        create_tool = MonitorCreateTool(service=svc)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call(
                        "maia_monitor_create",
                        arguments=(
                            '{"name": "check tomorrow", '
                            '"ops_capability": "ops.production.get_kpi", '
                            '"run_at": "2026-08-26T09:00:00+00:00"}'
                        ),
                    )
                ]
            ),
            _turn("Ho impostato un controllo per domani."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[create_tool])
        agent.run("Controllalo domani.")

        final_call_messages = engine.generate.call_args_list[-1][0][0]
        tool_messages = [m for m in final_call_messages if m.role == Role.TOOL]
        assert any("execution_note" in (m.content or "") for m in tool_messages)
        assert any(
            "nothing will invoke it automatically" in (m.content or "")
            for m in tool_messages
        )
