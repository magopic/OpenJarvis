"""FASE 4P.3 STEP 10/13/19/20 -- the orchestrator-facing runtime hook.

This is the ONLY place `GovernedActionService.approve()` is ever called
from an interactive session. It is deterministic, structural, and
entirely independent of anything the model decides -- called once per
turn, on the ORIGINAL user input, BEFORE the model ever generates
anything this turn.

STEP 10's exact rule, implemented literally: a short affirmative utterance
only counts as approval when (a) the ENTIRE trimmed input matches one of
a small, fixed, generic affirmative-phrase list (never "contains" a
matching word inside a longer sentence -- avoids misreading "yes but
don't do that" as approval), AND (b) there is EXACTLY ONE action
currently PENDING_APPROVAL for the real runtime principal. Zero pending
actions: this is just an ordinary message, nothing happens. More than
one: explicit ambiguity is surfaced structurally, nothing is approved.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjarvis.governed_actions.service import GovernedActionService
from openjarvis.governed_actions.types import GovernedAction

_AFFIRMATIVE_PHRASES = frozenset(
    {
        # English
        "yes", "yep", "yeah", "yup", "do it", "go ahead", "goahead",
        "confirmed", "confirm", "approved", "approve", "ok", "okay",
        "sure", "proceed", "affirmative",
        # Italian
        "si", "sì", "fallo", "procedi", "procediamo", "confermo", "confermato",
        "va bene", "vai", "d'accordo", "daccordo",
    }
)


def is_explicit_affirmative(text: str) -> bool:
    """The ENTIRE trimmed, lowercased, punctuation-stripped input must
    match a known affirmative phrase -- never a substring match inside a
    longer sentence. 'yes' approves; 'yes but wait' does not."""
    if not text:
        return False
    normalized = text.strip().lower().rstrip(".!?,;: ")
    return normalized in _AFFIRMATIVE_PHRASES


def detect_and_apply_runtime_approval(
    user_input: str, *, service: Optional[GovernedActionService] = None
) -> Optional[Dict[str, Any]]:
    """Called once per turn, on the ORIGINAL user input, before any model
    generation this turn. Returns a dict describing what the runtime did
    (for structural rendering), or None if nothing governed-action-
    related applies this turn -- in which case the orchestrator injects
    nothing extra and the turn proceeds exactly as it always has.

    M1.2 -- Governed Action Approval Binding: principal alone is no longer
    sufficient. ``current_scope`` is this exact call's own runtime-bound
    conversation/session identity (see governed_actions/session_scope.py).
    An action only counts as a real candidate for this turn's approval if
    its OWN bound ``session_scope`` is not None AND equals this call's
    ``current_scope`` -- also not None. Two unscoped things (current_scope
    is None, or an action's session_scope is None) never match each other;
    that would silently reconstruct the exact cross-conversation confusion
    this mechanism exists to close. This closes the "confused deputy" gap:
    an action prepared in one conversation (interactive or an unattended
    managed-agent tick) can no longer be approved by a bare "yes" in a
    DIFFERENT conversation that merely happens to share the same
    principal.
    """
    if not is_explicit_affirmative(user_input):
        return None

    from openjarvis.governed_actions.session_scope import (
        resolve_runtime_session_scope,
    )
    from openjarvis.second_brain.identity import resolve_runtime_principal

    svc = service or GovernedActionService()
    principal = resolve_runtime_principal()
    current_scope = resolve_runtime_session_scope()

    all_pending: List[GovernedAction] = svc.list_pending_approval(principal=principal)
    if current_scope is None:
        # No genuine session scope was bound by this runtime for this call
        # (e.g. HTTP serve today, or any other caller that never binds
        # one) -- fail closed. Never treat "no scope" as "matches
        # anything" or "matches other no-scope actions".
        pending: List[GovernedAction] = []
    else:
        pending = [a for a in all_pending if a.session_scope == current_scope]

    if not pending:
        return None
    if len(pending) > 1:
        return {"kind": "ambiguous", "pending": pending}

    action = pending[0]
    approved = svc.approve(action.id, principal=principal)
    executed = svc.execute(approved.id, principal=principal)
    return {"kind": "resolved", "action": executed}


def render_governed_action_event(event: Optional[Dict[str, Any]]) -> Optional[str]:
    """STEP 13: this block, not the model, is the source of truth for
    whether/what was executed. The model may explain it but must not
    contradict it -- mirrors [GOVERNED_PROACTIVE_ANALYSIS]'s own
    discipline (FASE 4P.1A)."""
    if event is None:
        return None

    lines = ["[GOVERNED_ACTION_EVENT]"]
    if event["kind"] == "ambiguous":
        lines.append(
            "Multiple actions are pending your approval this session -- cannot "
            "determine which one the user means:"
        )
        for a in event["pending"]:
            lines.append(f"  - id={a.id} capability={a.capability}: {a.rationale}")
        lines.append(
            "Ask the user which specific action id they mean before doing "
            "anything else. Do not guess, and do not approve/execute either one."
        )
        return "\n".join(lines)

    action: GovernedAction = event["action"]
    if action.status == "EXECUTED":
        lines.append(
            f"Action {action.id} (capability={action.capability}) was approved "
            f"and executed by the runtime, not by you."
        )
        lines.append(f"Result: {action.execution_result}")
    elif action.status == "FAILED":
        lines.append(
            f"Action {action.id} (capability={action.capability}) was approved "
            f"but execution FAILED."
        )
        lines.append(f"Failure: {action.failure}")
    else:
        lines.append(
            f"Action {action.id} (capability={action.capability}) is now in "
            f"status {action.status} after runtime approval processing."
        )
    lines.append(
        "This block was generated deterministically by the runtime. Report "
        "exactly this outcome -- do not describe a different result, and never "
        "claim an action executed unless this block says EXECUTED."
    )
    return "\n".join(lines)


__all__ = [
    "is_explicit_affirmative",
    "detect_and_apply_runtime_approval",
    "render_governed_action_event",
]
