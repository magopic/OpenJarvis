"""Model-independent principal resolution for Second Brain authorization
(FASE 4N.2A).

STEP 1's audit found the only existing canonical identity concept in
OpenJarvis -- ``sessions.session.SessionIdentity`` (``user_id``,
``display_name``, ``channel_ids``) -- is wired exclusively into the
``jarvis serve`` / channel runtime (``system/builder.py``,
``server/*``, ``cli/serve.py``). It never reaches ``jarvis ask`` or
``jarvis chat``, which is the runtime this whole MAIA engagement has
actually used end-to-end (FASE 4M.5C onward) -- confirmed in FASE
4N.1's environment audit: no ``[user]`` config section, no session
store touched by either CLI command. There is no existing identity
system to reuse on this path, so this module does not invent a
*second* one -- it fills a gap where there was none.

This is intentionally NOT a real authentication system. It exists to
satisfy exactly one requirement: the authorization principal for
Second Brain PRIVATE entries must be resolved by the runtime, never
supplied by the model. For the current single-user MAIA development
environment, the OS login account is a reasonable, deterministic stand-in
-- the same real person running `jarvis ask` twice gets the same
principal both times, and a different OS account gets a different one.
It reuses ``getpass.getuser()`` (an existing, general Python stdlib
primitive) rather than hardcoding any personal name into business logic.

Upgrade path: replace ``resolve_runtime_principal()``'s body with a
real authenticated identity (SSO token, ``SessionIdentity.user_id``
threaded through from a future CLI login, etc.) -- every caller here
already treats the return value as an opaque string, so nothing above
this function needs to change.
"""

from __future__ import annotations

import getpass
import os

# Prefixed and clearly labeled so a principal string is self-describing
# in the audit log / PRIVATE-visibility checks -- "this came from the
# OS-account fallback," not mistakeable for a real authenticated id.
_DEV_PRINCIPAL_PREFIX = "local-os-user:"


def resolve_runtime_principal() -> str:
    """Return the current runtime's Second Brain authorization principal.

    Deterministic per OS account on this machine -- calling this twice
    in separate ``jarvis ask`` processes under the same login returns
    the exact same string, which is the whole point (FASE 4N.2 found
    the model had no way to guarantee that on its own).
    """
    username = os.environ.get("OPENJARVIS_PRINCIPAL_OVERRIDE")
    if username:
        # Explicit escape hatch for tests/CI/multi-account dev boxes --
        # never read by the model, only by whoever controls the process
        # environment the runtime actually executes in.
        return username
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    return f"{_DEV_PRINCIPAL_PREFIX}{username}"


__all__ = ["resolve_runtime_principal"]
