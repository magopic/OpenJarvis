"""Per-conversation isolation of agent runtime state (M3.3A).

``app.state.agent`` is a single ``OrchestratorAgent`` shared by every HTTP
request. Almost all of it is safe to share -- engine, tools, executor,
prompt builder and registry are configuration, not conversation state. Two
fields are not:

  * ``_recent_successful_tools`` (M3.2C) is conversation-scoped. Shared, two
    callers trade sticky tools, and a caller opening a fresh conversation
    wipes another caller's set mid-conversation.

  * ``_loop_guard`` is request-scoped -- ``run()`` resets it as its first
    action -- so sharing it lets one request clear another in-flight
    request's loop-detection counters. That degrades a safety mechanism,
    not just behaviour.

The isolation here is structural rather than lock-based: each request gets
its own lightweight agent object that *shares* the expensive, immutable
pieces and *owns* the two mutable ones. Only the small conversation state
is persisted between turns, in a bounded, TTL'd, in-process store -- never
whole agents, and never any message content.

Scope note: this module knows nothing about OPS ONE, or about any specific
capability. It only knows that an agent has conversation-scoped state.

Deployment note: the store is per-process. With ``server.workers > 1`` two
turns of one conversation may land on different workers, which loses sticky
continuity (it never produces cross-talk, since each process is isolated).
Single-worker is the default.
"""

from __future__ import annotations

import copy
import re
import threading
import time
from typing import Any, Dict, List, Optional

# A conversation id is only ever a dictionary key here -- never a filesystem
# path, never interpolated into a query. The charset is still restricted so a
# caller cannot smuggle separators, whitespace or control characters into
# logs or future storage backends.
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_CONVERSATION_ID_LEN = 128

_DEFAULT_MAX_CONVERSATIONS = 500
_DEFAULT_TTL_SECONDS = 3600.0


class InvalidConversationId(ValueError):
    """Raised for a conversation id that fails validation."""


def validate_conversation_id(conversation_id: Any) -> str:
    """Return *conversation_id* if it is acceptable, else raise.

    Deliberately strict: unknown shapes are rejected rather than coerced,
    because this value keys server-side state.
    """
    if not isinstance(conversation_id, str):
        raise InvalidConversationId("conversation_id must be a string.")
    if not conversation_id:
        raise InvalidConversationId("conversation_id must not be empty.")
    if len(conversation_id) > _MAX_CONVERSATION_ID_LEN:
        raise InvalidConversationId(
            f"conversation_id must be at most {_MAX_CONVERSATION_ID_LEN} characters."
        )
    if not _CONVERSATION_ID_RE.match(conversation_id):
        raise InvalidConversationId(
            "conversation_id may contain only letters, digits, '-' and '_'."
        )
    return conversation_id


def _store_key(conversation_id: str, principal: Optional[str]) -> str:
    """Namespace conversation state by caller.

    Today every HTTP caller authenticates with the same service credential,
    so *principal* is constant and this is a no-op. It exists now so that
    propagating a real user identity later needs no change to the stored
    shape -- and so a conversation id, on its own, is never sufficient to
    reach another caller's state.

    A principal must come from something the server verified (the
    authenticated credential), never from a caller-supplied header.
    """
    return f"{principal or '-'}::{conversation_id}"


class ConversationStateStore:
    """Bounded, TTL'd, in-process store of conversation runtime state.

    Holds only ``{"sticky": [tool names], "last_used": ts}``. No messages,
    no user text, no credentials.

    The internal lock guards the mapping itself and is held only for the
    duration of a dictionary operation. Per-conversation turn serialization
    uses ``lock_for()`` instead, so two different conversations never block
    each other.
    """

    def __init__(
        self,
        max_conversations: int = _DEFAULT_MAX_CONVERSATIONS,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max = max(1, int(max_conversations))
        self._ttl = float(ttl_seconds)
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- internals ----------------------------------------------------

    def _expired(self, entry: Dict[str, Any], now: float) -> bool:
        return (now - entry.get("last_used", 0.0)) > self._ttl

    def _purge_locked(self, now: float) -> None:
        for key in [k for k, e in self._entries.items() if self._expired(e, now)]:
            self._entries.pop(key, None)
            self._locks.pop(key, None)
        while len(self._entries) > self._max:
            oldest = min(self._entries, key=lambda k: self._entries[k]["last_used"])
            self._entries.pop(oldest, None)
            self._locks.pop(oldest, None)

    # -- public API ---------------------------------------------------

    def lock_for(self, conversation_id: str, principal: Optional[str] = None):
        """A lock private to one conversation.

        Concurrent turns of the SAME conversation are serialized (the last
        writer would otherwise clobber the other's sticky set); turns of
        different conversations proceed in parallel.
        """
        key = _store_key(conversation_id, principal)
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def sticky_for(self, conversation_id: str, principal: Optional[str] = None) -> List[str]:
        key = _store_key(conversation_id, principal)
        now = time.monotonic()
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                return []
            if self._expired(entry, now):
                self._entries.pop(key, None)
                self._locks.pop(key, None)
                return []
            return list(entry.get("sticky") or [])

    def replace_sticky(
        self, conversation_id: str, sticky: List[str], principal: Optional[str] = None
    ) -> None:
        key = _store_key(conversation_id, principal)
        now = time.monotonic()
        with self._guard:
            self._entries[key] = {"sticky": list(sticky), "last_used": now}
            self._purge_locked(now)

    def save(self, conversation_id: str, agent: Any, principal: Optional[str] = None) -> None:
        """Persist the conversation-scoped state of a derived agent."""
        self.replace_sticky(
            conversation_id,
            list(getattr(agent, "_recent_successful_tools", None) or []),
            principal,
        )

    def reset(self, conversation_id: str, principal: Optional[str] = None) -> None:
        key = _store_key(conversation_id, principal)
        with self._guard:
            self._entries.pop(key, None)
            self._locks.pop(key, None)

    def size(self) -> int:
        now = time.monotonic()
        with self._guard:
            self._purge_locked(now)
            return len(self._entries)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Diagnostic copy. Contains tool names and timestamps only."""
        with self._guard:
            return {k: dict(v) for k, v in self._entries.items()}


def derive_conversation_agent(
    base_agent: Any,
    conversation_id: Optional[str],
    store: ConversationStateStore,
    principal: Optional[str] = None,
) -> Any:
    """Return a per-request agent isolated from every other request.

    A shallow copy deliberately: engine, tools, executor, bus and prompt
    builder are shared by reference because they are configuration and are
    not mutated per conversation. Only the two mutable fields are replaced,
    so the shared instance in ``app.state.agent`` is never written to.

    *conversation_id* of ``None`` yields an ephemeral runtime: it starts
    with no sticky state and nothing is persisted, which is what a
    stateless OpenAI-compatible caller should get -- isolated from the
    shared agent and from every other anonymous request alike.
    """
    agent = copy.copy(base_agent)

    # Request-scoped: run() resets this, so it must not be shared.
    guard = getattr(base_agent, "_loop_guard", None)
    if guard is not None:
        try:
            agent._loop_guard = type(guard)(
                getattr(guard, "_config", None), bus=getattr(guard, "_bus", None)
            )
        except Exception:
            # A guard that cannot be rebuilt is safer dropped than shared:
            # the agent treats None as "no loop guard configured".
            agent._loop_guard = None

    # Conversation-scoped: restored from the store, never inherited from the
    # shared instance.
    # Conversation-scoped: restored from the store, never inherited from the
    # shared instance. With an id present the store owns the lifecycle, so
    # the agent's own "is this a fresh conversation?" inference is switched
    # off -- otherwise a follow-up turn arriving with an empty context would
    # discard the state just restored (see
    # OrchestratorAgent._reset_conversation_state_if_new).
    if conversation_id is None:
        agent._recent_successful_tools = []
        agent._conversation_state_is_external = False
    else:
        agent._recent_successful_tools = store.sticky_for(conversation_id, principal)
        agent._conversation_state_is_external = True

    return agent


__all__ = [
    "ConversationStateStore",
    "InvalidConversationId",
    "derive_conversation_agent",
    "validate_conversation_id",
]
