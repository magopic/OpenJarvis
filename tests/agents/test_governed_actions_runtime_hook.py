"""FASE 4P.3 STEP 21 -- runtime-hook letters Q, R, S, T, X, Y, Z, AA.

Mirrors the mock-engine style of tests/agents/test_orchestrator.py and
tests/agents/test_orchestrator_proactive_analysis.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.governed_actions.runtime_hook import (
    detect_and_apply_runtime_approval,
    is_explicit_affirmative,
)
from openjarvis.governed_actions.service import GovernedActionService
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import STATUS_EXECUTED, STATUS_PENDING_APPROVAL


def _svc() -> GovernedActionService:
    return GovernedActionService(
        store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
        test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
    )


def _turn(content: str = "", finish: str = "stop") -> dict:
    return {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
        "finish_reason": finish,
    }


class TestAffirmativeDetection:
    def test_exact_affirmative_matches(self):
        for phrase in ("yes", "Yes!", "do it", "GO AHEAD", "confermo", "ok", "sì"):
            assert is_explicit_affirmative(phrase), phrase

    def test_affirmative_inside_a_longer_sentence_does_not_match(self):
        """'yes but wait' must NOT count as approval -- avoids the
        substring-match trap that would misread a hedge/negation as
        approval."""
        for phrase in ("yes but don't do that", "well, do it later maybe", "I don't think so, no"):
            assert not is_explicit_affirmative(phrase), phrase

    def test_ordinary_question_does_not_match(self):
        assert not is_explicit_affirmative("What is the current OEE?")


class TestZeroAndSinglePendingDetection:
    def test_zero_pending_no_effect(self):
        svc = _svc()
        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event is None

    def test_aa_exactly_one_pending_plus_explicit_approval_executes(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event["kind"] == "resolved"
        assert event["action"].status == STATUS_EXECUTED

    def test_z_multiple_pending_ambiguous_do_it_no_auto_approval(self):
        svc = _svc()
        a1 = svc.prepare_action("maia_test_write_note", {"note": "a"}, rationale="t")
        a2 = svc.prepare_action("maia_test_write_note", {"note": "b"}, rationale="t")
        svc.request_approval(a1.id)
        svc.request_approval(a2.id)
        event = detect_and_apply_runtime_approval("do it", service=svc)
        assert event["kind"] == "ambiguous"
        # Neither action was touched.
        assert svc.get_action(a1.id).status == STATUS_PENDING_APPROVAL
        assert svc.get_action(a2.id).status == STATUS_PENDING_APPROVAL

    def test_non_affirmative_input_never_triggers_even_with_one_pending(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        event = detect_and_apply_runtime_approval("What is the current OEE?", service=svc)
        assert event is None
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL


class TestOrchestratorIntegration:
    def test_x_claim_integrity_pre_execution_no_tools_executed(self):
        """Before any governed action is resolved, [ACTUALLY_EXECUTED_TOOLS]
        must show nothing executed -- the governed-action event is a
        SEPARATE block, never faked as a tool execution."""
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Understood, I executed it as the runtime reported.")
        agent = OrchestratorAgent(engine, "test-model", tools=[])

        import openjarvis.agents.orchestrator as orch_mod

        original = orch_mod.detect_and_apply_runtime_approval
        orch_mod.detect_and_apply_runtime_approval = lambda text: original(text, service=svc)
        try:
            agent.run("yes")
        finally:
            orch_mod.detect_and_apply_runtime_approval = original

        first_call_messages = engine.generate.call_args_list[0][0][0]
        integrity = next(m.content for m in first_call_messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or ""))
        assert "No tools have been executed yet" in integrity

    def test_y_claim_integrity_post_execution_governed_event_block_present(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Done -- I ran the approved action.")
        agent = OrchestratorAgent(engine, "test-model", tools=[])

        import openjarvis.agents.orchestrator as orch_mod

        original = orch_mod.detect_and_apply_runtime_approval
        orch_mod.detect_and_apply_runtime_approval = lambda text: original(text, service=svc)
        try:
            agent.run("yes")
        finally:
            orch_mod.detect_and_apply_runtime_approval = original

        first_call_messages = engine.generate.call_args_list[0][0][0]
        event_block = next(m.content for m in first_call_messages if "[GOVERNED_ACTION_EVENT]" in (m.content or ""))
        assert "EXECUTED" in event_block
        assert a.id in event_block
        assert svc.get_action(a.id).status == STATUS_EXECUTED


class TestModelCannotForgeApprovalFields:
    def test_q_no_tool_accepts_approved_by(self):
        import openjarvis.tools.governed_action_tools as mod

        for cls_name in ("GovernedActionsListTool", "GovernedActionGetTool", "GovernedActionPrepareTool", "GovernedActionRequestApprovalTool", "GovernedActionRejectTool"):
            cls = getattr(mod, cls_name)
            spec = cls().spec
            assert "approved_by" not in spec.parameters.get("properties", {})

    def test_r_no_tool_accepts_approval_hash_or_can_set_status(self):
        """GovernedActionsListTool's `status` parameter is a READ-side
        filter ('show me PROPOSED actions'), not a way to SET an action's
        status -- excluded here deliberately. The write-capable tools
        (prepare/request_approval/reject) must have neither an
        arguments_hash nor any status-setting parameter at all."""
        import openjarvis.tools.governed_action_tools as mod

        for cls_name in ("GovernedActionPrepareTool", "GovernedActionRequestApprovalTool", "GovernedActionRejectTool"):
            cls = getattr(mod, cls_name)
            spec = cls().spec
            props = spec.parameters.get("properties", {})
            assert "arguments_hash" not in props
            assert "status" not in props

    def test_no_approve_tool_registered_at_all(self):
        import openjarvis.tools.governed_action_tools as mod

        assert not hasattr(mod, "GovernedActionApproveTool")
        assert "approve" not in [n.lower() for n in mod.__all__]

    def test_no_generic_execute_tool_registered(self):
        import openjarvis.tools.governed_action_tools as mod

        forbidden = ("execute", "run_action", "do_it")
        for name in mod.__all__:
            lowered = name.lower()
            for bad in forbidden:
                assert bad not in lowered, name


class TestMonitoringCannotApproveOrExecute:
    def test_s_monitoring_service_never_calls_approve(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        assert "governed_actions" not in src
        assert ".approve(" not in src

    def test_t_monitor_check_agent_never_calls_execute(self):
        import inspect

        import openjarvis.agents.monitor_check_agent as mod

        src = inspect.getsource(mod)
        assert "governed_actions" not in src
        assert ".execute(" not in src

    def test_proactive_insight_never_auto_promotes_to_approved(self):
        """STEP 19: ProactiveReasoningService (proactive_insight.py) has
        zero coupling to governed_actions -- a ProposedAction it creates
        is inert data, never automatically becomes a GovernedAction, let
        alone an APPROVED one."""
        import inspect

        import openjarvis.agents.proactive_insight as mod

        src = inspect.getsource(mod)
        assert "governed_actions" not in src
