"""M3.3A — per-conversation isolation of MAIA runtime state.

The HTTP surface was built as a stateless OpenAI-compatible endpoint: the
caller carries the history in ``messages[]``, so conversations never shared
history. But ``app.state.agent`` is ONE ``OrchestratorAgent`` instance
shared by every request, and two of its fields are not shareable:

  * ``_recent_successful_tools`` (M3.2C) is conversation-scoped. Two
    concurrent callers would trade sticky tools, and a caller opening a
    fresh conversation would wipe another caller's set mid-conversation.

  * ``_loop_guard`` is request-scoped -- ``run()`` calls ``reset()`` on it
    as its first action. Shared, one request's reset clears another
    in-flight request's loop-detection counters, which degrades a *safety*
    mechanism, not merely behaviour.

The fix keeps one MAIA: engine, tools, executor and registry stay shared
(they are configuration, not conversation state). Only the two fields above
are per-request, restored from and saved to a bounded per-conversation
store. Nothing here duplicates the runtime and nothing here knows anything
about OPS ONE.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import ToolResult
from openjarvis.server.conversation_state import (
    _MAX_CONVERSATION_ID_LEN,
    ConversationStateStore,
    InvalidConversationId,
    derive_conversation_agent,
    validate_conversation_id,
)
from openjarvis.tools._stubs import BaseTool, ToolSpec

_PRODUCTION = "ops_dynamic_production_get_kpi"
_WAREHOUSE = "ops_dynamic_warehouse_get_status"
_ACTIONS = "ops_dynamic_actions_list"
_REGISTRY = "ops_dynamic_registry_list_capabilities"


def _tool(name: str, description: str, *, succeeds: bool = True) -> BaseTool:
    class _Stub(BaseTool):
        tool_id = name

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=name,
                description=description,
                parameters={"type": "object", "properties": {}},
            )

        def execute(self, **kw) -> ToolResult:
            return ToolResult(tool_name=name, content="{}", success=succeeds)

    return _Stub()


def _engine_calling(tool_name: str) -> MagicMock:
    engine = MagicMock()
    engine.generate.side_effect = [
        {
            "content": "",
            "tool_calls": [{"id": "c1", "name": tool_name, "arguments": "{}"}],
            "finish_reason": "tool_calls",
            "usage": {},
        },
        {"content": "Done.", "finish_reason": "stop", "usage": {}},
    ]
    return engine


def _engine_plain() -> MagicMock:
    engine = MagicMock()
    engine.generate.return_value = {"content": "Done.", "finish_reason": "stop", "usage": {}}
    return engine


def _tools_sent(engine: MagicMock, call_index: int = 0) -> list[str]:
    _, kwargs = engine.generate.call_args_list[call_index]
    return [t["function"]["name"] for t in (kwargs.get("tools") or [])]


@pytest.fixture
def base_agent() -> OrchestratorAgent:
    """The shared singleton, as ``app.state.agent`` holds it."""
    return OrchestratorAgent(
        _engine_plain(),
        "test-model",
        tools=[
            _tool(_PRODUCTION, "Get a production KPI such as OEE."),
            _tool(_WAREHOUSE, "Get warehouse inventory status."),
        ],
    )


@pytest.fixture
def store() -> ConversationStateStore:
    return ConversationStateStore(max_conversations=16, ttl_seconds=3600)


def _run(base, store, conversation_id, query, engine):
    """One HTTP turn: derive an isolated agent, run it, persist its state."""
    agent = derive_conversation_agent(base, conversation_id, store)
    agent._engine = engine
    result = agent.run(query)
    store.save(conversation_id, agent)
    return result


# ── SESSION ISOLATION ─────────────────────────────────────────────────────


class TestSessionIsolation:
    def test_1_2_conversation_a_makes_production_sticky(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        assert store.sticky_for("conv-A") == [_PRODUCTION]

    def test_3_a_new_conversation_does_not_inherit(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        assert store.sticky_for("conv-B") == []
        engine_b = _engine_plain()
        _run(base_agent, store, "conv-B", "zzz qqq", engine_b)
        assert _PRODUCTION not in _tools_sent(engine_b)

    def test_4_5_conversation_b_makes_warehouse_sticky(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-B", "magazzino", _engine_calling(_WAREHOUSE))
        assert store.sticky_for("conv-B") == [_WAREHOUSE]

    def test_6_a_keeps_production_and_never_inherits_warehouse(
        self, base_agent, store
    ) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        _run(base_agent, store, "conv-B", "magazzino", _engine_calling(_WAREHOUSE))
        engine_a2 = _engine_plain()
        _run(base_agent, store, "conv-A", "e rispetto all'anno precedente?", engine_a2)
        sent = _tools_sent(engine_a2)
        assert _PRODUCTION in sent
        assert _WAREHOUSE not in sent

    def test_7_interleaved_conversations_stay_isolated(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        _run(base_agent, store, "conv-B", "magazzino", _engine_calling(_WAREHOUSE))
        _run(base_agent, store, "conv-A", "ancora produzione", _engine_calling(_PRODUCTION))
        assert store.sticky_for("conv-A") == [_PRODUCTION]
        assert store.sticky_for("conv-B") == [_WAREHOUSE]

    def test_8_same_conversation_keeps_sticky_across_turns(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        engine2 = _engine_plain()
        _run(base_agent, store, "conv-A", "e rispetto all'anno precedente?", engine2)
        assert _PRODUCTION in _tools_sent(engine2)

    def test_9_reset_drops_only_that_conversation(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        _run(base_agent, store, "conv-B", "magazzino", _engine_calling(_WAREHOUSE))
        store.reset("conv-A")
        assert store.sticky_for("conv-A") == []
        assert store.sticky_for("conv-B") == [_WAREHOUSE]

    def test_10_unknown_conversation_inherits_nothing(self, base_agent, store) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        assert store.sticky_for("never-seen") == []
        derived = derive_conversation_agent(base_agent, "never-seen", store)
        assert derived._recent_successful_tools == []

    def test_the_shared_singleton_is_never_mutated(self, base_agent, store) -> None:
        """The whole point: the object in app.state must stay pristine."""
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        assert base_agent._recent_successful_tools == []


# ── BACKWARD COMPATIBILITY ────────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_11_no_conversation_id_still_works(self, base_agent, store) -> None:
        agent = derive_conversation_agent(base_agent, None, store)
        engine = _engine_calling(_PRODUCTION)
        agent._engine = engine
        result = agent.run("produzione OEE")
        assert result.tool_results[0].success is True

    def test_12_anonymous_requests_do_not_share_sticky(self, base_agent, store) -> None:
        """Two ID-less requests are two independent ephemeral runtimes."""
        a1 = derive_conversation_agent(base_agent, None, store)
        a1._engine = _engine_calling(_PRODUCTION)
        a1.run("produzione OEE")
        assert a1._recent_successful_tools == [_PRODUCTION]

        a2 = derive_conversation_agent(base_agent, None, store)
        engine2 = _engine_plain()
        a2._engine = engine2
        a2.run("zzz qqq")
        assert a2._recent_successful_tools == []
        assert _PRODUCTION not in _tools_sent(engine2)
        assert store.size() == 0  # nothing persisted for anonymous callers

    def test_13_derived_agent_preserves_the_shared_configuration(
        self, base_agent, store
    ) -> None:
        agent = derive_conversation_agent(base_agent, "conv-A", store)
        assert agent._tools is base_agent._tools
        assert agent._executor is base_agent._executor
        assert agent._model == base_agent._model
        assert agent._max_turns == base_agent._max_turns
        assert agent._mode == base_agent._mode

    def test_14_each_derived_agent_gets_its_own_loop_guard(
        self, base_agent, store
    ) -> None:
        """Request-scoped by nature: run() resets it, so sharing one across
        concurrent requests lets them clear each other's counters."""
        a = derive_conversation_agent(base_agent, "conv-A", store)
        b = derive_conversation_agent(base_agent, "conv-B", store)
        if base_agent._loop_guard is not None:
            assert a._loop_guard is not base_agent._loop_guard
            assert a._loop_guard is not b._loop_guard


# ── CONCURRENCY ───────────────────────────────────────────────────────────


class TestConcurrency:
    def test_15_parallel_conversations_do_not_share_state(self, base_agent) -> None:
        import concurrent.futures

        store = ConversationStateStore(max_conversations=64, ttl_seconds=3600)
        plan = {"conv-A": _PRODUCTION, "conv-B": _WAREHOUSE}

        def turn(cid: str) -> None:
            for _ in range(6):
                _run(base_agent, store, cid, "q", _engine_calling(plan[cid]))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(turn, ["conv-A", "conv-B"]))

        assert store.sticky_for("conv-A") == [_PRODUCTION]
        assert store.sticky_for("conv-B") == [_WAREHOUSE]

    def test_16_same_conversation_is_serialized_not_interleaved(self, base_agent) -> None:
        """Defined behaviour for concurrent turns on ONE conversation: they
        take that conversation's lock in turn. Different conversations are
        never serialized against each other (test 15 runs them in parallel)."""
        import concurrent.futures

        store = ConversationStateStore(max_conversations=64, ttl_seconds=3600)
        overlaps = {"max": 0, "cur": 0}

        def turn(_i: int) -> None:
            with store.lock_for("conv-A"):
                overlaps["cur"] += 1
                overlaps["max"] = max(overlaps["max"], overlaps["cur"])
                time.sleep(0.01)
                overlaps["cur"] -= 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(turn, range(8)))

        assert overlaps["max"] == 1

    def test_different_conversations_are_not_serialized(self, base_agent) -> None:
        store = ConversationStateStore(max_conversations=64, ttl_seconds=3600)
        assert store.lock_for("conv-A") is not store.lock_for("conv-B")


# ── SECURITY ──────────────────────────────────────────────────────────────


class TestSecurity:
    def test_17_18_19_governance_survives_derivation(self, base_agent, store) -> None:
        """Sticky can only ever re-offer tools still in the authorized set,
        so M3.1A/M3.2C guarantees hold identically on the derived agent."""
        agent = derive_conversation_agent(base_agent, "conv-A", store)
        agent._remember_successful_tools(
            [
                ToolResult(tool_name=_ACTIONS, content="", success=True),
                ToolResult(tool_name=_REGISTRY, content="", success=True),
                ToolResult(tool_name=_PRODUCTION, content="", success=False),
            ]
        )
        assert agent._recent_successful_tools == []

    def test_20_a_poisoned_store_cannot_widen_the_tool_surface(
        self, base_agent, store
    ) -> None:
        """Even if a stored name were tampered with, the union only ever
        resolves against the authorized set."""
        store.replace_sticky("conv-A", [_ACTIONS, _REGISTRY, "totally_made_up"])
        agent = derive_conversation_agent(base_agent, "conv-A", store)
        engine = _engine_plain()
        agent._engine = engine
        agent.run("zzz qqq")
        sent = set(_tools_sent(engine))
        assert _ACTIONS not in sent
        assert _REGISTRY not in sent
        assert "totally_made_up" not in sent

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "a/b", "../etc/passwd", "a\\b", "a b", "a\nb", "ID;drop", "é"],
    )
    def test_21_invalid_conversation_id_is_rejected(self, bad: str) -> None:
        with pytest.raises(InvalidConversationId):
            validate_conversation_id(bad)

    def test_22_oversized_conversation_id_is_rejected(self) -> None:
        assert validate_conversation_id("a" * _MAX_CONVERSATION_ID_LEN)
        with pytest.raises(InvalidConversationId):
            validate_conversation_id("a" * (_MAX_CONVERSATION_ID_LEN + 1))

    def test_valid_ids_are_accepted(self) -> None:
        for good in ["conv-A", "abc_123", "A" * 10, "0", "a-b_c-9"]:
            assert validate_conversation_id(good) == good

    def test_23_the_store_never_holds_message_content(self, base_agent, store) -> None:
        """Only runtime state is kept server-side; history stays with the
        caller, so no user text can leak between conversations through it."""
        _run(base_agent, store, "conv-A", "segreto-non-loggare", _engine_calling(_PRODUCTION))
        blob = repr(store.snapshot())
        assert "segreto-non-loggare" not in blob
        assert blob.count(_PRODUCTION) >= 1


# ── LIFECYCLE ─────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_24_the_store_is_bounded_and_evicts_least_recent(self, base_agent) -> None:
        store = ConversationStateStore(max_conversations=3, ttl_seconds=3600)
        for i in range(5):
            _run(base_agent, store, f"conv-{i}", "produzione OEE", _engine_calling(_PRODUCTION))
        assert store.size() == 3
        assert store.sticky_for("conv-0") == []  # evicted
        assert store.sticky_for("conv-4") == [_PRODUCTION]

    def test_25_expired_conversations_release_their_state(self, base_agent) -> None:
        store = ConversationStateStore(max_conversations=16, ttl_seconds=0.05)
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))
        assert store.sticky_for("conv-A") == [_PRODUCTION]
        time.sleep(0.08)
        assert store.sticky_for("conv-A") == []
        assert store.size() == 0

    def test_26_a_failing_turn_does_not_corrupt_other_conversations(
        self, base_agent, store
    ) -> None:
        _run(base_agent, store, "conv-A", "produzione OEE", _engine_calling(_PRODUCTION))

        boom = MagicMock()
        boom.generate.side_effect = RuntimeError("engine exploded")
        agent_b = derive_conversation_agent(base_agent, "conv-B", store)
        agent_b._engine = boom
        with pytest.raises(RuntimeError):
            agent_b.run("magazzino")

        assert store.sticky_for("conv-A") == [_PRODUCTION]
        assert base_agent._recent_successful_tools == []
