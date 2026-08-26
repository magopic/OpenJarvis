"""M1.2 -- Governed Action Approval Binding: runtime-bound session scope.

Problem this closes: ``GovernedActionService.list_pending_approval()`` (via
``governed_actions/runtime_hook.py::detect_and_apply_runtime_approval``) only
ever filtered by ``principal`` (see ``second_brain.identity.resolve_runtime_principal``
-- deterministic per OS account, shared by every process/conversation running
under that account). A short affirmative reply ("yes"/"ok"/"confermo") in
ANY conversation could therefore approve+execute a governed action prepared
in a COMPLETELY DIFFERENT conversation (interactive session, or an
unattended managed-agent tick) as long as both happened to share the same
principal and there was exactly one action pending -- a cross-conversation
"confused deputy" risk.

This module adds the second half of the binding: a session/conversation
scope, captured from the REAL runtime context (never from the model, never
from a tool argument -- mirrors ``resolve_runtime_principal()``'s own
"deterministic runtime-derived value, not a supplied one" discipline
exactly). Implemented as a ``contextvars.ContextVar`` rather than a new
parameter threaded through every call site, because the shared-singleton
runtime (``jarvis serve``'s HTTP agent, one instance for every concurrent
request) makes constructor-time or attribute-based binding structurally
impossible to get right -- a ContextVar set once per genuine
turn/request/tick boundary is isolated per asyncio task/thread without
requiring changes to every intermediate function signature between the
runtime entry point and ``GovernedActionService``.

Bound by (see each call site's own comment for why that value is correct):
  - ``cli/chat_cmd.py``  -- once per REPL process (one process = one
    conversation for this runtime).
  - ``cli/ask.py``       -- once per one-shot invocation.
  - ``agents/executor.py`` (managed-agent tick) -- ``managed:<agent_id>``,
    the managed agent's own persistent id.
  - ``server/agent_manager_routes.py`` (managed-agent SSE) -- same
    ``managed:<agent_id>`` value, so an action prepared during one
    execution mode of a given managed agent remains approvable by another
    execution mode of that SAME agent, never by an unrelated session.

Deliberately NOT bound by ``jarvis serve``'s ``/v1/chat/completions``: that
endpoint's request contract (``ChatCompletionRequest``) carries no
conversation/session identifier at all today, and the agent instance
serving it is a process-wide singleton shared by every concurrent request --
there is no genuine, already-existing signal to bind. An action prepared
over HTTP therefore always gets ``session_scope=None`` and can never be
auto-approved by the generic-phrase mechanism (see
``resolve_runtime_session_scope()``'s own contract below) -- the same
fail-closed treatment as a pre-M1.2 legacy action with no binding at all.
This is a known, deliberate limitation, not an oversight; adding a real
per-conversation identifier to the HTTP contract is out of M1.2's scope.
"""

from __future__ import annotations

import contextvars
from typing import Optional

_SESSION_SCOPE: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "governed_action_session_scope", default=None
)


def bind_runtime_session_scope(scope: Optional[str]) -> "contextvars.Token[Optional[str]]":
    """Bind the session/conversation scope for the current execution
    context (current thread, or current asyncio task and everything it
    awaits/spawns via ``asyncio.to_thread``). Call once per genuine
    conversation/turn/tick boundary -- never per governed-action call
    itself. Returns a token; pass to ``reset_runtime_session_scope()`` for
    scoped cleanup, or ignore it if the binding is meant to live for the
    rest of the current process/task (the common case for CLI runtimes)."""
    return _SESSION_SCOPE.set(scope)


def reset_runtime_session_scope(token: "contextvars.Token[Optional[str]]") -> None:
    _SESSION_SCOPE.reset(token)


def resolve_runtime_session_scope() -> Optional[str]:
    """The session/conversation scope the current runtime bound for this
    execution context, or ``None`` if nothing ever bound one here.

    ``None`` must always be treated as "no genuine scope available" --
    never as a wildcard, and never as a value that matches another
    ``None``. Two actions/checks that both have ``session_scope is None``
    are NOT considered a match by this module's own callers (see
    ``governed_actions/runtime_hook.py``) -- that would silently
    reconstruct exactly the cross-conversation confusion this module
    exists to close.
    """
    return _SESSION_SCOPE.get()


__all__ = [
    "bind_runtime_session_scope",
    "reset_runtime_session_scope",
    "resolve_runtime_session_scope",
]
