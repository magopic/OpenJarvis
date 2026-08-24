"""FASE 4P.3 -- GovernedActionService: proposal -> approval -> authorized
execution -> auditable result.

STEP 5: principal binding reuses the already-certified, deterministic
`second_brain.identity.resolve_runtime_principal()` unchanged -- never a
new principal mechanism, never something the model can set or spoof.

STEP 11's steer ("audit whether approval should even be model-callable")
is answered structurally here: `approve()` is a RUNTIME-ONLY method.
Nothing in `tools/governed_action_tools.py` (the model-callable surface)
calls it. Only `orchestrator.py`'s deterministic approval-detection path
(FASE 4P.3 STEP 10) calls `approve()`, after it has independently
verified the "exactly one pending action + explicit human affirmative"
condition -- the model is never in that call chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.governed_actions.capabilities import (
    get_capability,
    register_default_capabilities,
    validate_arguments,
)
from openjarvis.governed_actions.store import GovernedActionStore
from openjarvis.governed_actions.types import (
    DEFAULT_APPROVAL_TTL_SECONDS,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_EXECUTING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING_APPROVAL,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    GovernedAction,
    _EXECUTABLE_IN_V1,
    compute_arguments_hash,
)


class PrincipalMismatchError(PermissionError):
    """Raised whenever an operation's caller principal does not match
    the action's own bound principal -- fails closed, never silently
    substitutes or ignores the mismatch (STEP 5)."""


class GovernedActionError(ValueError):
    """Raised for any invalid-transition/invalid-capability/invalid-
    argument request. Always paired with an audit entry when it
    represents a real attempted transition (never a silent failure)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    from openjarvis.core.paths import get_config_dir

    return str(Path(get_config_dir()) / "governed_actions.db")


class GovernedActionService:
    def __init__(
        self,
        store: Optional[GovernedActionStore] = None,
        *,
        register_capabilities: bool = True,
        test_notes_path: Optional[Path] = None,
    ) -> None:
        self._store = store or GovernedActionStore(_default_db_path())
        if register_capabilities:
            # Idempotent-by-name: re-registering just overwrites the same
            # entries, safe to call from multiple GovernedActionService
            # instances in the same process (e.g. tests).
            register_default_capabilities(test_notes_path)

    # -- STEP 3/9.1-3: PROPOSED -> PENDING_APPROVAL ---------------------------

    def prepare_action(
        self,
        capability: str,
        arguments: Dict[str, Any],
        *,
        rationale: str,
        supporting_evidence: Optional[List[str]] = None,
        proposal_id: Optional[str] = None,
        principal: Optional[str] = None,
    ) -> GovernedAction:
        """Model-callable (via governed_action_tools.py). Creates a
        PROPOSED action -- drafting only, never a step toward approval by
        itself."""
        from openjarvis.second_brain.identity import resolve_runtime_principal

        principal = principal or resolve_runtime_principal()
        cap = get_capability(capability)
        if cap is None:
            raise GovernedActionError(f"Unknown capability: {capability!r}")
        arg_error = validate_arguments(cap, arguments)
        if arg_error:
            raise GovernedActionError(arg_error)

        action = GovernedAction(
            id=uuid.uuid4().hex[:16],
            principal=principal,
            capability=capability,
            arguments=arguments,
            arguments_hash=compute_arguments_hash(capability, arguments, principal),
            rationale=rationale,
            status=STATUS_PROPOSED,
            proposal_id=proposal_id,
            supporting_evidence=supporting_evidence or [],
            created_at=_now_iso(),
        )
        self._store.save_action(action.to_dict())
        self._audit(action, None, "prepared")
        return action

    def request_approval(
        self, action_id: str, *, ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS
    ) -> GovernedAction:
        """Model-callable. STEP 19/20: this is the ONLY way a
        ProposedAction (FASE 4P.1) or a monitor-generated insight (FASE
        4P.2) can ever reach PENDING_APPROVAL -- explicit, one call,
        never automatic. Still does not approve anything."""
        action = self._require(action_id)
        if action.status != STATUS_PROPOSED:
            raise GovernedActionError(
                f"Cannot request approval from status {action.status!r} (must be PROPOSED)"
            )
        prev = action.status
        action.status = STATUS_PENDING_APPROVAL
        action.expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        self._store.save_action(action.to_dict())
        self._audit(action, prev, "approval requested")
        return action

    # -- STEP 4/5/9.4-6: RUNTIME-ONLY approval --------------------------------

    def approve(self, action_id: str, *, principal: str, reason: str = "runtime-detected explicit approval") -> GovernedAction:
        """RUNTIME-ONLY (STEP 11). Never called from any model-callable
        tool. `principal` must be the REAL caller's principal (from
        `resolve_runtime_principal()`), never a value the model supplied."""
        action = self._require(action_id)
        if action.status == STATUS_EXPIRED:
            raise GovernedActionError("Action already expired")
        if self._is_expired(action):
            self._transition(action, STATUS_EXPIRED, principal, "expired before approval")
            raise GovernedActionError("Action expired")
        if action.status != STATUS_PENDING_APPROVAL:
            raise GovernedActionError(
                f"Cannot approve from status {action.status!r} (must be PENDING_APPROVAL)"
            )
        if action.principal != principal:
            # STEP 5: fail closed, never approve across principals.
            raise PrincipalMismatchError(
                f"Principal {principal!r} may not approve an action bound to {action.principal!r}"
            )
        prev = action.status
        action.status = STATUS_APPROVED
        action.approved_at = _now_iso()
        action.approved_by = principal
        self._store.save_action(action.to_dict())
        self._audit(action, prev, reason)
        return action

    def reject(self, action_id: str, *, principal: Optional[str] = None, reason: str = "rejected") -> GovernedAction:
        """Model-callable -- rejection is safe: it can only ever reduce
        capability, never grant it, so letting the model relay a user's
        'no'/'cancel' here (rather than requiring the same strict runtime
        gate STEP 10 requires for approval) does not weaken the boundary."""
        from openjarvis.second_brain.identity import resolve_runtime_principal

        principal = principal or resolve_runtime_principal()
        action = self._require(action_id)
        if action.status in (STATUS_EXECUTED, STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED):
            raise GovernedActionError(f"Cannot reject a terminal action (status={action.status!r})")
        prev = action.status
        action.status = STATUS_REJECTED
        self._store.save_action(action.to_dict())
        self._audit(action, prev, reason, principal=principal)
        return action

    def cancel(self, action_id: str, *, principal: Optional[str] = None, reason: str = "cancelled") -> GovernedAction:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        principal = principal or resolve_runtime_principal()
        action = self._require(action_id)
        if action.status in (STATUS_EXECUTED, STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED):
            raise GovernedActionError(f"Cannot cancel a terminal action (status={action.status!r})")
        prev = action.status
        action.status = STATUS_CANCELLED
        self._store.save_action(action.to_dict())
        self._audit(action, prev, reason, principal=principal)
        return action

    # -- STEP 9.7-12/14: RUNTIME-ONLY execution -------------------------------

    def execute(self, action_id: str, *, principal: str) -> GovernedAction:
        """RUNTIME-ONLY (STEP 12). Revalidates everything fresh
        immediately before running the capability (STEP 9 step 8, STEP
        15's 'process restart between approval and execution' case):
        principal, capability still registered + still LOW risk,
        arguments-hash still matches, status still APPROVED, not expired.

        STEP 14 idempotency: an already-EXECUTED action returns itself
        unchanged -- no second invocation of the handler, ever, no matter
        how many times this is called with the same id."""
        action = self._require(action_id)

        if action.status == STATUS_EXECUTED:
            return action  # idempotent no-op -- the whole point of STEP 14

        if action.status != STATUS_APPROVED:
            raise GovernedActionError(
                f"Cannot execute from status {action.status!r} (must be APPROVED)"
            )
        if action.principal != principal:
            raise PrincipalMismatchError(
                f"Principal {principal!r} may not execute an action bound to {action.principal!r}"
            )
        if self._is_expired(action):
            self._transition(action, STATUS_EXPIRED, principal, "expired before execution")
            raise GovernedActionError("Approval expired before execution")

        cap = get_capability(action.capability)
        if cap is None:
            self._transition(action, STATUS_FAILED, principal, "capability no longer registered")
            action.failure = "capability no longer registered"
            self._store.save_action(action.to_dict())
            return action
        if cap.risk_class not in _EXECUTABLE_IN_V1:
            self._transition(action, STATUS_FAILED, principal, f"risk class {cap.risk_class} not executable in V1")
            action.failure = f"risk class {cap.risk_class} not executable in V1"
            self._store.save_action(action.to_dict())
            return action

        recomputed = compute_arguments_hash(action.capability, action.arguments, action.principal)
        if recomputed != action.arguments_hash:
            # STEP 4: arguments (or the record itself) drifted since
            # approval -- the approval is void, never execute drifted args.
            self._transition(action, STATUS_FAILED, principal, "argument hash mismatch -- approval invalidated")
            action.failure = "argument hash mismatch -- approval invalidated"
            self._store.save_action(action.to_dict())
            return action

        self._transition(action, STATUS_EXECUTING, principal, "execution starting")

        try:
            if cap.handler is None:
                raise RuntimeError("capability has no handler (should be unreachable for LOW-risk capabilities)")
            result = cap.handler(action.arguments)
            action.status = STATUS_EXECUTED
            action.executed_at = _now_iso()
            action.execution_result = result
            self._store.save_action(action.to_dict())
            self._audit(action, STATUS_EXECUTING, "execution succeeded", principal=principal)
        except Exception as exc:
            action.status = STATUS_FAILED
            action.failure = str(exc)
            self._store.save_action(action.to_dict())
            self._audit(action, STATUS_EXECUTING, f"execution failed: {exc}", principal=principal)

        return action

    # -- Reads -----------------------------------------------------------

    def get_action(self, action_id: str) -> Optional[GovernedAction]:
        d = self._store.get_action(action_id)
        return GovernedAction.from_dict(d) if d else None

    def list_actions(self, *, principal: Optional[str] = None, status: Optional[str] = None) -> List[GovernedAction]:
        return [GovernedAction.from_dict(d) for d in self._store.list_actions(principal=principal, status=status)]

    def list_pending_approval(self, *, principal: str) -> List[GovernedAction]:
        """The exact structural query STEP 10's runtime approval-detection
        needs: every action currently awaiting THIS principal's decision."""
        return self.list_actions(principal=principal, status=STATUS_PENDING_APPROVAL)

    def get_audit_log(self, action_id: str) -> List[Dict[str, Any]]:
        return self._store.list_audit(action_id)

    # -- Internals -----------------------------------------------------------

    def _require(self, action_id: str) -> GovernedAction:
        action = self.get_action(action_id)
        if action is None:
            raise GovernedActionError(f"Governed action not found: {action_id}")
        return action

    @staticmethod
    def _is_expired(action: GovernedAction) -> bool:
        if not action.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(action.expires_at)
        except ValueError:
            return False
        return datetime.now(timezone.utc) >= expires

    def _transition(self, action: GovernedAction, new_status: str, principal: str, reason: str) -> None:
        prev = action.status
        action.status = new_status
        self._store.save_action(action.to_dict())
        self._audit(action, prev, reason, principal=principal)

    def _audit(
        self, action: GovernedAction, previous_status: Optional[str], reason: str, *, principal: Optional[str] = None
    ) -> None:
        self._store.append_audit(
            {
                "action_id": action.id,
                "timestamp": _now_iso(),
                "previous_status": previous_status,
                "new_status": action.status,
                "principal": principal or action.principal,
                "reason": reason,
                "capability": action.capability,
                "arguments_hash": action.arguments_hash,
            }
        )


__all__ = ["GovernedActionService", "GovernedActionError", "PrincipalMismatchError"]
