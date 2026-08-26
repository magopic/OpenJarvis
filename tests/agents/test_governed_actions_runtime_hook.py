"""FASE 4P.3 STEP 21 -- runtime-hook letters Q, R, S, T, X, Y, Z, AA.

Mirrors the mock-engine style of tests/agents/test_orchestrator.py and
tests/agents/test_orchestrator_proactive_analysis.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.governed_actions.runtime_hook import (
    detect_and_apply_runtime_approval,
    is_explicit_affirmative,
)
from openjarvis.governed_actions.service import GovernedActionService
from openjarvis.governed_actions.session_scope import bind_runtime_session_scope
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import STATUS_EXECUTED, STATUS_PENDING_APPROVAL


def _svc() -> GovernedActionService:
    return GovernedActionService(
        store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
        test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
    )


@pytest.fixture(autouse=True)
def _bind_default_test_session_scope():
    """M1.2: every test in this file exercises a single logical
    conversation (prepare + approve happen back-to-back, same call
    stack) -- bind a scope once so the pre-existing "happy path" tests
    keep asserting exactly what they always did. Tests that specifically
    exercise cross-session denial bind their OWN distinct scopes inline,
    overriding this default within their own body."""
    bind_runtime_session_scope("test-default-session")
    yield


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


class TestApprovalBinding:
    """M1.2 -- Governed Action Approval Binding. Covers the confused-deputy
    scenarios: an action prepared in one conversation/session must never
    be auto-approvable by a bare affirmative in a DIFFERENT one, even when
    both share the same principal (the only dimension the pre-M1.2
    mechanism checked)."""

    def test_1_same_principal_same_session_approves(self):
        svc = _svc()
        bind_runtime_session_scope("session-same")
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event["kind"] == "resolved"
        assert event["action"].status == STATUS_EXECUTED

    def test_2_same_principal_different_session_denied(self):
        """The exact confused-deputy scenario: SESSION A prepares, SESSION
        B (same principal, different conversation) says 'yes' -- must NOT
        approve."""
        svc = _svc()
        bind_runtime_session_scope("session-A")
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)

        bind_runtime_session_scope("session-B")
        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event is None
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_3_different_principal_same_session_id_denied(self):
        """Even if two different principals somehow shared the same
        session_scope string, list_pending_approval's principal filter
        (unchanged) means the second principal never even sees the
        first's pending action."""
        svc = _svc()
        bind_runtime_session_scope("shared-scope")
        a = svc.prepare_action(
            "maia_test_write_note", {"note": "x"}, rationale="t", principal="user:alice"
        )
        svc.request_approval(a.id)

        pending_for_bob = svc.list_pending_approval(principal="user:bob")
        assert pending_for_bob == []
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_4_missing_session_binding_fails_closed(self):
        """No runtime ever bound a scope for this call at all (current_scope
        is None) -- must never match, even though it is the 'only' pending
        action for this principal."""
        svc = _svc()
        bind_runtime_session_scope(None)
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        assert a.session_scope is None

        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event is None
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_5_managed_tick_pending_interactive_yes_denied(self):
        """STEP 9's mandatory scenario: a managed-agent tick prepares an
        action (managed:<agent_id> scope), then an unrelated interactive
        session (same principal, cli-chat:<uuid> scope) says 'yes' --
        must NOT approve."""
        svc = _svc()
        bind_runtime_session_scope("managed:agent-abc123")
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)

        bind_runtime_session_scope("cli-chat:def456")
        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event is None
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_6_two_pending_same_session_ambiguous(self):
        svc = _svc()
        bind_runtime_session_scope("session-multi")
        a1 = svc.prepare_action("maia_test_write_note", {"note": "a"}, rationale="t")
        a2 = svc.prepare_action("maia_test_write_note", {"note": "b"}, rationale="t")
        svc.request_approval(a1.id)
        svc.request_approval(a2.id)

        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event["kind"] == "ambiguous"
        assert svc.get_action(a1.id).status == STATUS_PENDING_APPROVAL
        assert svc.get_action(a2.id).status == STATUS_PENDING_APPROVAL

    def test_6b_two_pending_different_sessions_not_ambiguous(self):
        """A pending action from a DIFFERENT session must not count toward
        this session's ambiguity check -- only same-scope actions are
        candidates at all."""
        svc = _svc()
        bind_runtime_session_scope("session-other")
        other = svc.prepare_action("maia_test_write_note", {"note": "other"}, rationale="t")
        svc.request_approval(other.id)

        bind_runtime_session_scope("session-mine")
        mine = svc.prepare_action("maia_test_write_note", {"note": "mine"}, rationale="t")
        svc.request_approval(mine.id)

        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event["kind"] == "resolved"
        assert event["action"].id == mine.id
        assert svc.get_action(other.id).status == STATUS_PENDING_APPROVAL

    def test_7_one_valid_pending_same_session_passes(self):
        svc = _svc()
        bind_runtime_session_scope("session-valid")
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        event = detect_and_apply_runtime_approval("confermo", service=svc)
        assert event["kind"] == "resolved"
        assert event["action"].id == a.id

    def test_11_restart_persistence_preserves_binding(self):
        """A fresh GovernedActionService instance against the SAME db file
        (simulating a process restart) must still honor the persisted
        session_scope -- proving it survived on disk, not just in memory."""
        db_path = tempfile.mktemp(suffix=".db")
        notes_path = Path(tempfile.mktemp(suffix=".txt"))
        svc1 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes_path)
        bind_runtime_session_scope("session-restart")
        a = svc1.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc1.request_approval(a.id)

        # Fresh service instance, same store file -- simulates restart.
        svc2 = GovernedActionService(
            store=GovernedActionStore(db_path), test_notes_path=notes_path, register_capabilities=False
        )
        reloaded = svc2.get_action(a.id)
        assert reloaded.session_scope == "session-restart"

        event = detect_and_apply_runtime_approval("yes", service=svc2)
        assert event["kind"] == "resolved"
        assert event["action"].status == STATUS_EXECUTED

    def test_12_legacy_pending_without_binding_not_approvable_generically(self):
        """Simulates a row persisted before M1.2 (no session_scope column
        value at all) -- must behave exactly like test_4 (fail closed),
        even with a real scope now bound for the CURRENT call."""
        svc = _svc()
        bind_runtime_session_scope("session-legacy-caller")
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        # Simulate a legacy row: directly blank out session_scope in storage,
        # as if it had been persisted before this column existed.
        d = svc._store.get_action(a.id)
        d["session_scope"] = None
        svc._store.save_action(d)
        assert svc.get_action(a.id).session_scope is None

        event = detect_and_apply_runtime_approval("yes", service=svc)
        assert event is None
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_13_no_explicit_action_id_approval_bypass_exists(self):
        """STEP 8: verifies (does not need to build) that no model-callable
        or otherwise-bypassable path exists that could approve by
        action_id while skipping principal/session checks -- approve() is
        still reachable only from detect_and_apply_runtime_approval's own
        scope-checked selection."""
        import inspect

        import openjarvis.governed_actions.runtime_hook as hook_mod

        src = inspect.getsource(hook_mod.detect_and_apply_runtime_approval)
        # The only real call site is "svc.approve(" inside this one
        # function, downstream of the session_scope filter -- not a
        # second, parallel entry point. (Counting within the function
        # body only, not the module docstring, which separately mentions
        # "approve()" in prose.)
        assert src.count("svc.approve(") == 1


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
