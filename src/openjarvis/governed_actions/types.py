"""FASE 4P.3 -- governed execution contracts.

STEP 1's audit found the existing ``ApprovalStore``/``PendingAction``
(``tools/approval_store.py``) genuinely insufficient for THIS layer's
security requirements -- not because it is bad code (it works correctly
for its own purpose, the ProactiveAgent's personal-assistant email/SMS/
calendar tier-based permission memory), but because it structurally
lacks four things this layer requires: (1) no ``approved_by``/principal
field at all -- nothing binds an approval to WHO gave it; (2) no
argument-immutability protection -- ``payload`` can be freely replaced
under the same id before execution; (3) no capability allowlist --
``action_type`` is a free string dispatched by
``ExecutePendingActionsTool``'s own hardcoded if/elif, not a registered,
schema-validated capability; (4) no full transition audit trail -- only
current ``status`` plus one ``decision_at`` timestamp. Building a new,
dedicated contract here (STEP 2's decision) rather than reusing or
weakening the existing one, which stays untouched and still serves its
own purpose unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# -- Status lifecycle (STEP 3/9) --------------------------------------------

STATUS_PROPOSED = "PROPOSED"
STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_EXECUTING = "EXECUTING"
STATUS_EXECUTED = "EXECUTED"
STATUS_FAILED = "FAILED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
STATUS_EXPIRED = "EXPIRED"

_TERMINAL_STATUSES = frozenset(
    {STATUS_EXECUTED, STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED}
)

# -- Risk classes (STEP 7) -- assigned only at capability REGISTRATION,
# never by model judgment at request time. --------------------------------

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_PROHIBITED = "PROHIBITED"
VALID_RISK_CLASSES = frozenset({RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_PROHIBITED})

# Only LOW-risk capabilities actually execute in V1 (STEP 7). MEDIUM/HIGH
# may be registered and exercised structurally by tests, but
# GovernedActionService.execute() refuses them. PROHIBITED never executes,
# ever, regardless of any future change to this set.
_EXECUTABLE_IN_V1 = frozenset({RISK_LOW})

DEFAULT_APPROVAL_TTL_SECONDS = 900  # STEP 16 -- bounded, configurable per call


def compute_arguments_hash(capability: str, arguments: Dict[str, Any], principal: str) -> str:
    """STEP 4: deterministic fingerprint of exactly what was approved --
    capability + normalized (sorted-key JSON) arguments + principal.
    Recomputed and compared immediately before execution (STEP 9 step 8);
    any drift invalidates the approval."""
    normalized = json.dumps(
        {"capability": capability, "arguments": arguments, "principal": principal},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass
class GovernedAction:
    """STEP 3's action contract. One row = one proposed-to-terminal
    lifecycle for one capability invocation."""

    id: str
    principal: str
    capability: str
    arguments: Dict[str, Any]
    arguments_hash: str
    rationale: str
    status: str = STATUS_PROPOSED
    proposal_id: Optional[str] = None
    supporting_evidence: List[str] = field(default_factory=list)
    created_at: str = ""
    expires_at: Optional[str] = None
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    executed_at: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None
    failure: Optional[str] = None
    # M1.2 -- Governed Action Approval Binding. The conversation/session
    # scope the runtime bound when this action was prepared (see
    # governed_actions/session_scope.py), or None for an action prepared
    # where no genuine scope was available (e.g. HTTP serve today, or any
    # pre-M1.2 legacy row). None is never a wildcard: runtime_hook.py's
    # matching logic treats a None-scoped action as never auto-approvable
    # via the generic affirmative-phrase path, fail-closed by design.
    session_scope: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "principal": self.principal,
            "capability": self.capability,
            "arguments": self.arguments,
            "arguments_hash": self.arguments_hash,
            "rationale": self.rationale,
            "status": self.status,
            "proposal_id": self.proposal_id,
            "supporting_evidence": self.supporting_evidence,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "executed_at": self.executed_at,
            "execution_result": self.execution_result,
            "failure": self.failure,
            "session_scope": self.session_scope,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GovernedAction":
        return cls(
            id=d["id"],
            principal=d["principal"],
            capability=d["capability"],
            arguments=d.get("arguments") or {},
            arguments_hash=d["arguments_hash"],
            rationale=d.get("rationale", ""),
            status=d.get("status", STATUS_PROPOSED),
            proposal_id=d.get("proposal_id"),
            supporting_evidence=d.get("supporting_evidence") or [],
            created_at=d.get("created_at", ""),
            expires_at=d.get("expires_at"),
            approved_at=d.get("approved_at"),
            approved_by=d.get("approved_by"),
            executed_at=d.get("executed_at"),
            execution_result=d.get("execution_result"),
            failure=d.get("failure"),
            # Absent for any row persisted before M1.2 -- a legacy row must
            # default to None (unscoped), never to a value that could
            # accidentally match a real scope.
            session_scope=d.get("session_scope"),
        )


@dataclass
class AuditEntry:
    """STEP 17: one immutable row per lifecycle transition. Never logs
    raw argument values beyond what the action's own arguments_hash
    already summarizes -- the hash is the auditable fingerprint, not a
    secrets-bearing payload dump."""

    id: str
    action_id: str
    timestamp: str
    previous_status: Optional[str]
    new_status: str
    principal: str
    reason: str
    capability: str
    arguments_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action_id": self.action_id,
            "timestamp": self.timestamp,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "principal": self.principal,
            "reason": self.reason,
            "capability": self.capability,
            "arguments_hash": self.arguments_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AuditEntry":
        return cls(
            id=d["id"],
            action_id=d["action_id"],
            timestamp=d["timestamp"],
            previous_status=d.get("previous_status"),
            new_status=d["new_status"],
            principal=d["principal"],
            reason=d.get("reason", ""),
            capability=d["capability"],
            arguments_hash=d.get("arguments_hash", ""),
        )


__all__ = [
    "STATUS_PROPOSED",
    "STATUS_PENDING_APPROVAL",
    "STATUS_APPROVED",
    "STATUS_EXECUTING",
    "STATUS_EXECUTED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "STATUS_CANCELLED",
    "STATUS_EXPIRED",
    "_TERMINAL_STATUSES",
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_HIGH",
    "RISK_PROHIBITED",
    "VALID_RISK_CLASSES",
    "_EXECUTABLE_IN_V1",
    "DEFAULT_APPROVAL_TTL_SECONDS",
    "compute_arguments_hash",
    "GovernedAction",
    "AuditEntry",
]
