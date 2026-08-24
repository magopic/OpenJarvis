"""FASE 4P.3 STEP 21 -- GovernedActionService test matrix (service-layer
letters: A-P, U-W, BB, CC, DD). Runtime-hook letters Q-T/X/Y/Z/AA live in
tests/agents/test_governed_actions_runtime_hook.py; tool-surface letters
live in tests/tools/test_governed_action_tools.py.

Every test uses an isolated tempfile SQLite store and an isolated notes
file for the synthetic test capability -- never the real config dir.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from openjarvis.governed_actions.service import (
    GovernedActionError,
    GovernedActionService,
    PrincipalMismatchError,
)
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import (
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING_APPROVAL,
    STATUS_PROPOSED,
    STATUS_REJECTED,
)


def _svc(notes_path=None) -> GovernedActionService:
    return GovernedActionService(
        store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
        test_notes_path=notes_path or Path(tempfile.mktemp(suffix=".txt")),
    )


class TestProposalToApproval:
    def test_a_proposal_to_pending_approval(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        assert a.status == STATUS_PROPOSED
        a = svc.request_approval(a.id)
        assert a.status == STATUS_PENDING_APPROVAL
        assert a.expires_at is not None

    def test_b_approval_by_correct_principal(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        a = svc.approve(a.id, principal=a.principal)
        assert a.status == STATUS_APPROVED
        assert a.approved_by == a.principal

    def test_c_rejection(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        a = svc.reject(a.id, principal=a.principal)
        assert a.status == STATUS_REJECTED

    def test_d_approval_principal_mismatch_denied(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        with pytest.raises(PrincipalMismatchError):
            svc.approve(a.id, principal="someone:else")
        # Fail-closed: status must NOT have changed.
        assert svc.get_action(a.id).status == STATUS_PENDING_APPROVAL

    def test_e_changed_args_invalidate_approval(self):
        """Simulates a record whose arguments drifted after approval
        (e.g. a bug or a direct DB edit) -- execute() must recompute the
        hash and refuse rather than trust the stored hash blindly."""
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "original"}, rationale="t")
        svc.request_approval(a.id)
        a = svc.approve(a.id, principal=a.principal)
        # Simulate drift: overwrite arguments without recomputing the hash.
        d = svc._store.get_action(a.id)
        d["arguments"] = {"note": "TAMPERED"}
        svc._store.save_action(d)
        result = svc.execute(a.id, principal=a.principal)
        assert result.status == STATUS_FAILED
        assert "hash" in result.failure.lower()

    def test_f_unknown_capability_denied(self):
        svc = _svc()
        with pytest.raises(GovernedActionError):
            svc.prepare_action("not_a_real_capability", {}, rationale="t")

    def test_g_prohibited_capability_denied(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_prohibited_capability", {"value": "x"}, rationale="t")
        svc.request_approval(a.id)
        a = svc.approve(a.id, principal=a.principal)
        a = svc.execute(a.id, principal=a.principal)
        assert a.status == STATUS_FAILED
        assert "PROHIBITED" in a.failure

    def test_medium_high_also_denied_in_v1(self):
        svc = _svc()
        for cap in ("maia_test_medium_capability", "maia_test_high_capability"):
            a = svc.prepare_action(cap, {"value": "x"}, rationale="t")
            svc.request_approval(a.id)
            a = svc.approve(a.id, principal=a.principal)
            a = svc.execute(a.id, principal=a.principal)
            assert a.status == STATUS_FAILED


class TestExecution:
    def test_h_approved_harmless_capability_executes(self):
        notes = Path(tempfile.mktemp(suffix=".txt"))
        svc = _svc(notes)
        a = svc.prepare_action("maia_test_write_note", {"note": "hello"}, rationale="t")
        svc.request_approval(a.id)
        a = svc.approve(a.id, principal=a.principal)
        a = svc.execute(a.id, principal=a.principal)
        assert a.status == STATUS_EXECUTED
        assert "hello" in notes.read_text()

    def test_i_execution_result_persisted(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        svc.approve(a.id, principal=a.principal)
        svc.execute(a.id, principal=a.principal)
        fetched = svc.get_action(a.id)
        assert fetched.status == STATUS_EXECUTED
        assert fetched.execution_result["written"] is True

    def test_j_capability_failure_becomes_failed(self):
        svc = _svc()

        def raising_handler(arguments):
            raise RuntimeError("simulated capability crash")

        from openjarvis.governed_actions.capabilities import CapabilityDefinition, register_capability
        from openjarvis.governed_actions.types import RISK_LOW

        register_capability(
            CapabilityDefinition(
                name="maia_test_raising_capability",
                description="test",
                argument_schema={"x": "str"},
                required_arguments=["x"],
                risk_class=RISK_LOW,
                handler=raising_handler,
            )
        )
        a = svc.prepare_action("maia_test_raising_capability", {"x": "y"}, rationale="t")
        svc.request_approval(a.id)
        svc.approve(a.id, principal=a.principal)
        a = svc.execute(a.id, principal=a.principal)
        assert a.status == STATUS_FAILED
        assert "simulated capability crash" in a.failure

    def test_k_timeout_style_failure(self):
        """No real timeout wall-clock enforcement in V1 (STEP 6's registry
        only records `timeout_seconds` as metadata) -- but a handler that
        raises a TimeoutError is represented identically to any other
        capability failure: FAILED, never silently swallowed."""
        svc = _svc()

        def timeout_handler(arguments):
            raise TimeoutError("simulated timeout")

        from openjarvis.governed_actions.capabilities import CapabilityDefinition, register_capability
        from openjarvis.governed_actions.types import RISK_LOW

        register_capability(
            CapabilityDefinition(
                name="maia_test_timeout_capability",
                description="test",
                argument_schema={"x": "str"},
                required_arguments=["x"],
                risk_class=RISK_LOW,
                handler=timeout_handler,
            )
        )
        a = svc.prepare_action("maia_test_timeout_capability", {"x": "y"}, rationale="t")
        svc.request_approval(a.id)
        svc.approve(a.id, principal=a.principal)
        a = svc.execute(a.id, principal=a.principal)
        assert a.status == STATUS_FAILED
        assert "timeout" in a.failure.lower()

    def test_l_duplicate_execute_no_second_side_effect(self):
        notes = Path(tempfile.mktemp(suffix=".txt"))
        svc = _svc(notes)
        a = svc.prepare_action("maia_test_write_note", {"note": "once"}, rationale="t")
        svc.request_approval(a.id)
        svc.approve(a.id, principal=a.principal)
        svc.execute(a.id, principal=a.principal)
        for _ in range(5):
            svc.execute(a.id, principal=a.principal)  # repeated "do it"/retry/refresh
        lines = notes.read_text().strip().split("\n")
        assert len(lines) == 1  # the handler ran exactly once, ever


class TestRestartPersistence:
    def test_m_restart_before_approval(self):
        db_path = tempfile.mktemp(suffix=".db")
        notes = Path(tempfile.mktemp(suffix=".txt"))
        svc1 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        a = svc1.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc1.request_approval(a.id)

        svc2 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        fetched = svc2.get_action(a.id)
        assert fetched.status == STATUS_PENDING_APPROVAL
        fetched = svc2.approve(fetched.id, principal=fetched.principal)
        assert fetched.status == STATUS_APPROVED

    def test_n_restart_after_approval_still_revalidates(self):
        db_path = tempfile.mktemp(suffix=".db")
        notes = Path(tempfile.mktemp(suffix=".txt"))
        svc1 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        a = svc1.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc1.request_approval(a.id)
        svc1.approve(a.id, principal=a.principal)

        svc2 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        fetched = svc2.get_action(a.id)
        assert fetched.status == STATUS_APPROVED
        executed = svc2.execute(fetched.id, principal=fetched.principal)
        assert executed.status == STATUS_EXECUTED

    def test_o_restart_after_execution_remains_terminal(self):
        db_path = tempfile.mktemp(suffix=".db")
        notes = Path(tempfile.mktemp(suffix=".txt"))
        svc1 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        a = svc1.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc1.request_approval(a.id)
        svc1.approve(a.id, principal=a.principal)
        svc1.execute(a.id, principal=a.principal)

        svc2 = GovernedActionService(store=GovernedActionStore(db_path), test_notes_path=notes)
        fetched = svc2.get_action(a.id)
        assert fetched.status == STATUS_EXECUTED
        # Re-execute after restart -- still idempotent, no second write.
        svc2.execute(fetched.id, principal=fetched.principal)
        assert len(notes.read_text().strip().split("\n")) == 1


class TestExpiration:
    def test_p_expired_approval_denied(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id, ttl_seconds=0)  # expires immediately
        time.sleep(0.01)
        with pytest.raises(GovernedActionError):
            svc.approve(a.id, principal=a.principal)
        assert svc.get_action(a.id).status == "EXPIRED"

    def test_expired_after_approval_denies_execution(self):
        """TTL governs the PENDING_APPROVAL window; if execute() is
        reached after that window elapsed (e.g. a slow/stalled runtime),
        it must also refuse rather than silently execute a stale approval."""
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id, ttl_seconds=3600)
        a = svc.approve(a.id, principal=a.principal)
        # Simulate an approval that has since aged past its own expiry.
        d = svc._store.get_action(a.id)
        from datetime import datetime, timedelta, timezone

        d["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        svc._store.save_action(d)
        with pytest.raises(GovernedActionError):
            svc.execute(a.id, principal=a.principal)
        assert svc.get_action(a.id).status == "EXPIRED"


class TestGovernanceInvariants:
    def test_u_second_brain_unchanged(self):
        import inspect

        import openjarvis.governed_actions.service as mod

        src = inspect.getsource(mod)
        assert "propose_entry" not in src
        assert "confirm_entry" not in src

    def test_v_action_book_unchanged(self):
        import inspect

        import openjarvis.governed_actions.service as svc_mod
        import openjarvis.governed_actions.capabilities as cap_mod

        for mod in (svc_mod, cap_mod):
            src = inspect.getsource(mod)
            assert "action_book" not in src.lower()
            assert "actionbook" not in src.lower()

    def test_w_ops_data_unchanged_no_ops_bridge_calls(self):
        """Nothing in the governed-actions engine calls the OPS Bridge --
        the only registered V1 capability writes a local test file."""
        import inspect

        import openjarvis.governed_actions.service as mod

        src = inspect.getsource(mod)
        assert "_call_bridge" not in src
        assert "ops_bridge" not in src.lower()


class TestHashDeterminism:
    def test_bb_argument_normalization_hash_determinism(self):
        from openjarvis.governed_actions.types import compute_arguments_hash

        h1 = compute_arguments_hash("cap", {"a": 1, "b": 2}, "principal:x")
        h2 = compute_arguments_hash("cap", {"b": 2, "a": 1}, "principal:x")  # different key order
        assert h1 == h2  # sorted-key JSON normalization
        h3 = compute_arguments_hash("cap", {"a": 1, "b": 3}, "principal:x")  # different value
        assert h1 != h3
        h4 = compute_arguments_hash("cap", {"a": 1, "b": 2}, "principal:y")  # different principal
        assert h1 != h4


class TestAuditChain:
    def test_cc_audit_chain_full_lifecycle(self):
        svc = _svc()
        a = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t")
        svc.request_approval(a.id)
        svc.approve(a.id, principal=a.principal)
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
            assert e["principal"]
            assert e["timestamp"]
            assert e["capability"] == "maia_test_write_note"


class TestPrivateIsolation:
    def test_dd_principal_isolation(self):
        svc = _svc()
        a1 = svc.prepare_action("maia_test_write_note", {"note": "x"}, rationale="t", principal="user:alice")
        a2 = svc.prepare_action("maia_test_write_note", {"note": "y"}, rationale="t", principal="user:bob")
        svc.request_approval(a1.id)
        svc.request_approval(a2.id)
        alice_pending = svc.list_pending_approval(principal="user:alice")
        bob_pending = svc.list_pending_approval(principal="user:bob")
        assert [a.id for a in alice_pending] == [a1.id]
        assert [a.id for a in bob_pending] == [a2.id]
        with pytest.raises(PrincipalMismatchError):
            svc.approve(a1.id, principal="user:bob")
