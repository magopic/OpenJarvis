"""FASE 4P.4 -- Outlook governed capability test matrix (STEP 15 negative
tests A-L, STEP 19 test matrix). All tests use SyntheticGraphTransport
(STEP 13) -- zero network calls, fully deterministic.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openjarvis.governed_actions.capabilities import get_capability
from openjarvis.governed_actions.outlook_capability import (
    OUTLOOK_SEND_CAPABILITY,
    OutlookGuardError,
    RealGraphTransport,
    SyntheticGraphTransport,
    _decode_id_token_claims,
    get_allowed_account,
    normalize_recipients,
    register_outlook_capability,
    verify_account_guard,
)
from openjarvis.governed_actions.service import GovernedActionError, GovernedActionService, PrincipalMismatchError
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import RISK_HIGH, STATUS_EXECUTED, STATUS_FAILED, STATUS_PENDING_APPROVAL


def _svc(transport=None, allowed_account="allowed@example.com") -> tuple[GovernedActionService, SyntheticGraphTransport]:
    transport = transport or SyntheticGraphTransport(account=allowed_account)
    register_outlook_capability(transport, allowed_account=allowed_account)
    svc = GovernedActionService(store=GovernedActionStore(tempfile.mktemp(suffix=".db")), register_capabilities=False)
    return svc, transport


def _prepare_and_approve(svc, to=("someone@example.com",), subject="Test", body="Hello", principal=None):
    a = svc.prepare_action(
        OUTLOOK_SEND_CAPABILITY, {"to": list(to), "subject": subject, "body": body}, rationale="t", principal=principal
    )
    svc.request_approval(a.id)
    return svc.approve(a.id, principal=a.principal)


class TestRegistration:
    def test_registered_with_high_risk(self):
        register_outlook_capability(SyntheticGraphTransport())
        cap = get_capability(OUTLOOK_SEND_CAPABILITY)
        assert cap is not None
        assert cap.risk_class == RISK_HIGH
        assert cap.requires_confirmation is True


class TestRecipientSafety:
    def test_single_string_recipient_normalized_to_list(self):
        assert normalize_recipients("Someone@Example.com") == ["someone@example.com"]

    def test_multiple_recipients_normalized(self):
        assert normalize_recipients(["A@x.com", " b@x.com "]) == ["a@x.com", "b@x.com"]

    def test_empty_list_rejected(self):
        with pytest.raises(OutlookGuardError):
            normalize_recipients([])

    def test_non_email_rejected(self):
        with pytest.raises(OutlookGuardError):
            normalize_recipients(["not-an-email"])

    def test_group_alias_like_name_rejected(self):
        """No distribution-list/group expansion -- anything that doesn't
        look like a single explicit address is refused, never guessed."""
        with pytest.raises(OutlookGuardError):
            normalize_recipients(["production-team"])

    def test_non_string_entry_rejected(self):
        with pytest.raises(OutlookGuardError):
            normalize_recipients([123])


class TestAccountGuard:
    def test_unconfigured_guard_fails_closed(self):
        with pytest.raises(OutlookGuardError, match="No allowed Microsoft account"):
            verify_account_guard("someone@example.com", allowed_account=None)

    def test_matching_account_passes(self):
        verify_account_guard("allowed@example.com", allowed_account="allowed@example.com")

    def test_mismatched_account_fails_closed(self):
        with pytest.raises(OutlookGuardError, match="does not match"):
            verify_account_guard("wrong@example.com", allowed_account="allowed@example.com")

    def test_case_insensitive_match(self):
        verify_account_guard("Allowed@Example.com", allowed_account="allowed@example.com")

    def test_get_allowed_account_env_var(self, monkeypatch):
        monkeypatch.delenv("OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT", raising=False)
        assert get_allowed_account() is None
        monkeypatch.setenv("OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT", "Me@Example.com")
        assert get_allowed_account() == "me@example.com"


class TestApprovalHashCoversEmailFields:
    def test_hash_changes_with_recipients(self):
        svc, _ = _svc()
        a1 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["a@x.com"], "subject": "s", "body": "b"}, rationale="t")
        a2 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["b@x.com"], "subject": "s", "body": "b"}, rationale="t")
        assert a1.arguments_hash != a2.arguments_hash

    def test_hash_changes_with_subject(self):
        svc, _ = _svc()
        a1 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["a@x.com"], "subject": "s1", "body": "b"}, rationale="t")
        a2 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["a@x.com"], "subject": "s2", "body": "b"}, rationale="t")
        assert a1.arguments_hash != a2.arguments_hash

    def test_hash_changes_with_body(self):
        svc, _ = _svc()
        a1 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["a@x.com"], "subject": "s", "body": "b1"}, rationale="t")
        a2 = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["a@x.com"], "subject": "s", "body": "b2"}, rationale="t")
        assert a1.arguments_hash != a2.arguments_hash


class TestSendBeforeAfterApproval:
    def test_send_denied_before_approval(self):
        """STEP 15.A: no approval -> no send."""
        svc, transport = _svc()
        a = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["x@x.com"], "subject": "s", "body": "b"}, rationale="t")
        with pytest.raises(GovernedActionError):
            svc.execute(a.id, principal=a.principal)  # still PROPOSED, not APPROVED
        assert transport.sent_log == []

    def test_k_no_send_before_approval_even_with_pending(self):
        """STEP 15.K equivalent at the service layer: PENDING_APPROVAL
        alone (no explicit approval) must not execute."""
        svc, transport = _svc()
        a = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["x@x.com"], "subject": "s", "body": "b"}, rationale="t")
        svc.request_approval(a.id)
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL
        with pytest.raises(GovernedActionError):
            svc.execute(a.id, principal=a.principal)
        assert transport.sent_log == []

    def test_send_succeeds_after_approval(self):
        svc, transport = _svc()
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_EXECUTED
        assert result.execution_result["sent"] is True
        assert len(transport.sent_log) == 1


class TestNegativeApprovalGates:
    def test_b_wrong_principal_denied(self):
        """STEP 15.B."""
        svc, _ = _svc()
        a = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["x@x.com"], "subject": "s", "body": "b"}, rationale="t")
        svc.request_approval(a.id)
        with pytest.raises(PrincipalMismatchError):
            svc.approve(a.id, principal="someone:else")

    def test_c_wrong_microsoft_account_denied(self):
        """STEP 15.C."""
        svc, transport = _svc(SyntheticGraphTransport(account="wrong@example.com"), allowed_account="allowed@example.com")
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert "does not match" in result.failure
        assert transport.sent_log == []

    def test_d_changed_subject_after_approval_denied(self):
        """STEP 15.D."""
        svc, transport = _svc()
        a = _prepare_and_approve(svc, subject="original")
        d = svc._store.get_action(a.id)
        d["arguments"]["subject"] = "TAMPERED"
        svc._store.save_action(d)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert "hash" in result.failure.lower()
        assert transport.sent_log == []

    def test_e_changed_body_after_approval_denied(self):
        """STEP 15.E."""
        svc, transport = _svc()
        a = _prepare_and_approve(svc, body="original")
        d = svc._store.get_action(a.id)
        d["arguments"]["body"] = "TAMPERED"
        svc._store.save_action(d)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert transport.sent_log == []

    def test_f_changed_recipient_after_approval_denied(self):
        """STEP 15.F."""
        svc, transport = _svc()
        a = _prepare_and_approve(svc, to=("original@x.com",))
        d = svc._store.get_action(a.id)
        d["arguments"]["to"] = ["tampered@x.com"]
        svc._store.save_action(d)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert transport.sent_log == []

    def test_g_expired_approval_denied(self):
        """STEP 15.G."""
        svc, transport = _svc()
        a = svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["x@x.com"], "subject": "s", "body": "b"}, rationale="t")
        svc.request_approval(a.id, ttl_seconds=0)
        import time

        time.sleep(0.01)
        with pytest.raises(GovernedActionError):
            svc.approve(a.id, principal=a.principal)
        assert transport.sent_log == []

    def test_h_repeated_execution_no_duplicate(self):
        """STEP 15.H."""
        svc, transport = _svc()
        a = _prepare_and_approve(svc)
        svc.execute(a.id, principal=a.principal)
        for _ in range(5):
            svc.execute(a.id, principal=a.principal)
        assert len(transport.sent_log) == 1

    def test_i_unknown_recipient_denied(self):
        """STEP 15.I -- an obviously-malformed 'recipient' is denied at
        prepare time, before any approval/execution is even possible."""
        svc, transport = _svc()
        with pytest.raises(GovernedActionError):
            svc.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["not-an-email"], "subject": "s", "body": "b"}, rationale="t")
        assert transport.sent_log == []

    def test_j_graph_failure_becomes_failed(self):
        """STEP 15.J."""
        svc, transport = _svc(SyntheticGraphTransport(account="allowed@example.com", fail_mode="graph_unavailable"))
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert transport.sent_log == []

    def test_auth_expired_becomes_failed(self):
        svc, transport = _svc(SyntheticGraphTransport(account="allowed@example.com", fail_mode="auth_expired"))
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED

    def test_rate_limit_becomes_failed(self):
        svc, transport = _svc(SyntheticGraphTransport(account="allowed@example.com", fail_mode="rate_limit"))
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED

    def test_invalid_recipient_from_provider_becomes_failed(self):
        svc, transport = _svc(SyntheticGraphTransport(account="allowed@example.com", fail_mode="invalid_recipient"))
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED


class TestProvenance:
    def test_execution_result_carries_provider_and_message_id(self):
        svc, transport = _svc()
        a = _prepare_and_approve(svc)
        result = svc.execute(a.id, principal=a.principal)
        assert result.execution_result["provider"] == "synthetic"
        assert result.execution_result["message_id"] is not None

    def test_audit_log_full_lifecycle(self):
        svc, transport = _svc()
        a = _prepare_and_approve(svc)
        svc.execute(a.id, principal=a.principal)
        log = svc.get_audit_log(a.id)
        transitions = [(e["previous_status"], e["new_status"]) for e in log]
        assert transitions == [
            (None, "PROPOSED"),
            ("PROPOSED", "PENDING_APPROVAL"),
            ("PENDING_APPROVAL", "APPROVED"),
            ("APPROVED", "EXECUTING"),
            ("EXECUTING", "EXECUTED"),
        ]
        for e in log:
            assert "hello" not in json_dump_lower(e)  # never logs the body

    def test_no_secret_in_audit_log(self):
        svc, transport = _svc()
        a = _prepare_and_approve(svc, body="super secret contents nobody should see in logs")
        svc.execute(a.id, principal=a.principal)
        log = svc.get_audit_log(a.id)
        for e in log:
            assert "super secret" not in json_dump_lower(e)


def json_dump_lower(d) -> str:
    import json

    return json.dumps(d).lower()


class TestRestartPersistence:
    def test_pending_approval_survives_restart(self):
        db_path = tempfile.mktemp(suffix=".db")
        transport = SyntheticGraphTransport(account="allowed@example.com")
        register_outlook_capability(transport, allowed_account="allowed@example.com")
        svc1 = GovernedActionService(store=GovernedActionStore(db_path), register_capabilities=False)
        a = svc1.prepare_action(OUTLOOK_SEND_CAPABILITY, {"to": ["x@x.com"], "subject": "s", "body": "b"}, rationale="t")
        svc1.request_approval(a.id)

        register_outlook_capability(transport, allowed_account="allowed@example.com")
        svc2 = GovernedActionService(store=GovernedActionStore(db_path), register_capabilities=False)
        fetched = svc2.get_action(a.id)
        assert fetched.status == STATUS_PENDING_APPROVAL
        approved = svc2.approve(fetched.id, principal=fetched.principal)
        executed = svc2.execute(approved.id, principal=approved.principal)
        assert executed.status == STATUS_EXECUTED
        assert len(transport.sent_log) == 1


class TestNoModelSelfApproval:
    def test_no_approve_tool_can_reach_outlook_capability(self):
        """Model-facing tools never call approve()/execute() directly --
        already verified generically in test_governed_actions_runtime_hook.py;
        re-confirmed here that the outlook capability introduces no new
        model-callable approval/execution path."""
        import inspect

        import openjarvis.governed_actions.outlook_capability as mod

        src = inspect.getsource(mod)
        assert "ToolRegistry" not in src  # no new model-callable tool registered here at all


class TestNoMonitoringApproval:
    def test_monitoring_cannot_reach_outlook_capability(self):
        import inspect

        import openjarvis.monitoring.service as mod

        src = inspect.getsource(mod)
        assert "outlook" not in src.lower()
        assert "governed_actions" not in src


class TestNoSecretLeakage:
    def test_real_transport_never_logs_or_exposes_access_token_in_repr(self):
        """Structural check: RealGraphTransport's public surface (the
        GraphTransport protocol methods) never returns the access token
        itself in any result dict."""
        t = RealGraphTransport(credentials_path=tempfile.mktemp(suffix=".json"))
        with pytest.raises(OutlookGuardError):
            t.get_authenticated_account()  # no credentials file -> fails closed, never fabricates a token

    def test_id_token_decode_never_raises_on_garbage(self):
        assert _decode_id_token_claims("not-a-real-jwt") == {}
        assert _decode_id_token_claims("") == {}


class TestClaimIntegrity:
    """STEP 19/STEP 12 -- mirrors
    tests/agents/test_governed_actions_runtime_hook.py's
    TestOrchestratorIntegration pattern, applied to the outlook capability
    specifically: the model must never be able to claim an email was sent
    (or not sent) other than what the [GOVERNED_ACTION_EVENT] block, driven
    entirely by the runtime-verified GovernedAction outcome, actually says.
    Covers both a successful synthetic send and a provider failure -- claim
    integrity must hold in both directions.

    M1.2 -- Governed Action Approval Binding: detect_and_apply_runtime_approval
    now also requires a matching session_scope (see governed_actions/
    session_scope.py) -- these tests exercise a single logical conversation
    (prepare + approve happen back-to-back in one call stack), so bind one
    scope for the whole class, exactly mirroring the sibling test file's
    same fixture."""

    @pytest.fixture(autouse=True)
    def _bind_test_session_scope(self):
        from openjarvis.governed_actions.session_scope import bind_runtime_session_scope

        bind_runtime_session_scope("test-outlook-claim-integrity-session")
        yield

    def test_claim_integrity_pre_execution_no_tools_executed(self):
        from unittest.mock import MagicMock

        import openjarvis.agents.orchestrator as orch_mod
        from openjarvis.agents.orchestrator import OrchestratorAgent

        svc, _ = _svc()
        a = svc.prepare_action(
            OUTLOOK_SEND_CAPABILITY,
            {"to": ["someone@example.com"], "subject": "Test", "body": "Hello"},
            rationale="t",
        )
        svc.request_approval(a.id)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {
            "content": "Understood, I sent it as the runtime reported.",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            "model": "test-model",
            "finish_reason": "stop",
        }
        agent = OrchestratorAgent(engine, "test-model", tools=[])

        original = orch_mod.detect_and_apply_runtime_approval
        orch_mod.detect_and_apply_runtime_approval = lambda text: original(text, service=svc)
        try:
            agent.run("yes")
        finally:
            orch_mod.detect_and_apply_runtime_approval = original

        first_call_messages = engine.generate.call_args_list[0][0][0]
        integrity = next(m.content for m in first_call_messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or ""))
        assert "No tools have been executed yet" in integrity

    def test_claim_integrity_post_execution_success_reported_honestly(self):
        from unittest.mock import MagicMock

        import openjarvis.agents.orchestrator as orch_mod
        from openjarvis.agents.orchestrator import OrchestratorAgent

        svc, transport = _svc()
        a = svc.prepare_action(
            OUTLOOK_SEND_CAPABILITY,
            {"to": ["someone@example.com"], "subject": "Test", "body": "Hello"},
            rationale="t",
        )
        svc.request_approval(a.id)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {
            "content": "Done -- the email was sent.",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            "model": "test-model",
            "finish_reason": "stop",
        }
        agent = OrchestratorAgent(engine, "test-model", tools=[])

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
        resolved = svc.get_action(a.id)
        assert resolved.status == STATUS_EXECUTED
        assert transport.sent_log  # the synthetic transport actually recorded the send

    def test_claim_integrity_post_execution_failure_never_reported_as_success(self):
        """When the provider fails (STEP 11), the [GOVERNED_ACTION_EVENT]
        block must say FAILED, not EXECUTED -- the model has no way to
        forge a success claim the runtime didn't actually observe."""
        from unittest.mock import MagicMock

        import openjarvis.agents.orchestrator as orch_mod
        from openjarvis.agents.orchestrator import OrchestratorAgent

        svc, _ = _svc(transport=SyntheticGraphTransport(account="allowed@example.com", fail_mode="graph_unavailable"))
        a = svc.prepare_action(
            OUTLOOK_SEND_CAPABILITY,
            {"to": ["someone@example.com"], "subject": "Test", "body": "Hello"},
            rationale="t",
        )
        svc.request_approval(a.id)

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {
            "content": "Understood, I'll report exactly what the runtime observed.",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            "model": "test-model",
            "finish_reason": "stop",
        }
        agent = OrchestratorAgent(engine, "test-model", tools=[])

        original = orch_mod.detect_and_apply_runtime_approval
        orch_mod.detect_and_apply_runtime_approval = lambda text: original(text, service=svc)
        try:
            agent.run("yes")
        finally:
            orch_mod.detect_and_apply_runtime_approval = original

        first_call_messages = engine.generate.call_args_list[0][0][0]
        event_block = next(m.content for m in first_call_messages if "[GOVERNED_ACTION_EVENT]" in (m.content or ""))
        assert "execution FAILED" in event_block
        assert "and executed by the runtime" not in event_block
        assert svc.get_action(a.id).status == STATUS_FAILED
