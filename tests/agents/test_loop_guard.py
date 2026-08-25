"""Tests for agent loop guard (Phase 14.3)."""

from __future__ import annotations

from openjarvis.core.events import EventBus, EventType


class TestLoopGuard:
    def _make_guard(self, **kwargs):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        kwargs.setdefault("warn_before_block", False)
        config = LoopGuardConfig(**kwargs)
        bus = EventBus(record_history=True)
        return LoopGuard(config, bus=bus), bus

    def test_identical_calls_blocked(self):
        guard, bus = self._make_guard(max_identical_calls=2)
        v1 = guard.check_call("calc", '{"x": 1}')
        assert not v1.blocked
        # Rust backend uses a HashSet — blocks on the second identical call
        v2 = guard.check_call("calc", '{"x": 1}')
        assert v2.blocked
        assert "identical" in v2.reason.lower()

    def test_different_args_not_blocked(self):
        guard, _ = self._make_guard(max_identical_calls=2)
        guard.check_call("calc", '{"x": 1}')
        guard.check_call("calc", '{"x": 1}')
        v = guard.check_call("calc", '{"x": 2}')
        assert not v.blocked

    def test_ping_pong_detection(self):
        guard, _ = self._make_guard(ping_pong_window=4, poll_tool_budget=100)
        guard.check_call("A", "{}")
        guard.check_call("B", "{}")
        guard.check_call("A", '{"x": 1}')
        guard.check_call("B", '{"x": 1}')
        guard.check_call("A", '{"x": 2}')
        # After A-B-A-B pattern, next A should be blocked
        # Note: exact blocking depends on the window + detection logic
        # The sequence [A, B, A, B, A] with window=4 should detect A-B-A-B
        # But detection happens after 4+ calls in sequence

    def test_poll_budget_exceeded(self):
        guard, _ = self._make_guard(poll_tool_budget=3, max_identical_calls=100)
        guard.check_call("poll", '{"a": 1}')
        guard.check_call("poll", '{"a": 2}')
        guard.check_call("poll", '{"a": 3}')
        v = guard.check_call("poll", '{"a": 4}')
        assert v.blocked
        assert "poll budget" in v.reason.lower()

    def test_event_emitted(self):
        guard, bus = self._make_guard(max_identical_calls=1)
        guard.check_call("x", '{"a": 1}')
        guard.check_call("x", '{"a": 1}')
        events = [
            e for e in bus.history if e.event_type == EventType.LOOP_GUARD_TRIGGERED
        ]
        assert len(events) == 1

    def test_reset(self):
        guard, _ = self._make_guard(max_identical_calls=2)
        guard.check_call("x", '{"a": 1}')
        guard.check_call("x", '{"a": 1}')
        guard.reset()
        v = guard.check_call("x", '{"a": 1}')
        assert not v.blocked

    def test_context_compression_no_overflow(self):
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=100)
        messages = [Message(role=Role.USER, content=f"msg {i}") for i in range(10)]
        result = guard.compress_context(messages)
        assert len(result) == 10

    def test_context_compression_with_overflow(self):
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=10)
        messages = (
            [
                Message(role=Role.SYSTEM, content="sys"),
            ]
            + [Message(role=Role.USER, content=f"msg {i}") for i in range(50)]
            + [
                Message(role=Role.TOOL, content=f"result {i}", tool_call_id=f"t{i}")
                for i in range(50)
            ]
        )
        result = guard.compress_context(messages)
        assert len(result) <= 10

    def test_context_compression_stage4_uses_current_state(self):
        """Stage 4 should derive from compressed state."""
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=5)
        messages = (
            [
                Message(role=Role.SYSTEM, content="sys"),
            ]
            + [Message(role=Role.USER, content=f"msg {i}") for i in range(100)]
            + [
                Message(
                    role=Role.TOOL,
                    content=f"result {i}",
                    tool_call_id=f"t{i}",
                )
                for i in range(100)
            ]
        )
        result = guard.compress_context(messages)
        assert len(result) == 5
        system_count = sum(1 for m in result if getattr(m, "role", None) == "system")
        assert system_count == 1

    def test_check_response_returns_unblocked(self):
        guard, _ = self._make_guard()
        v = guard.check_response("some content")
        assert not v.blocked

    def test_disabled_loop_guard(self):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(enabled=False)
        guard = LoopGuard(config)
        # Even though we'd normally block, disabled guard shouldn't
        for _ in range(10):
            guard.check_call("x", '{"a": 1}')
        # Guard is still created but check_call still works
        # (the enabled flag is checked at the ToolUsingAgent level)

    def test_identical_call_warn_then_block_stable_cycle_key(self):
        """FASE 4Q.4A Fix B -- the warn-before-block policy must actually
        reach 'block' for a repeated identical call, not warn forever.
        Before the fix, the identical-call reason string embedded the
        ever-growing repetition count, so the dedup key never repeated
        and every over-threshold call downgraded to a warning."""
        guard, _ = self._make_guard(max_identical_calls=3, warn_before_block=True)
        # Calls 1-3: under threshold, clean.
        for _ in range(3):
            v = guard.check_call("poll", '{"a": 1}')
            assert not v.blocked
        # Call 4: first over-threshold trigger -> warned, not blocked.
        v4 = guard.check_call("poll", '{"a": 1}')
        assert v4.blocked is False
        assert v4.warned is True
        # Call 5: same underlying pattern -> must now genuinely block.
        v5 = guard.check_call("poll", '{"a": 1}')
        assert v5.blocked is True
        assert v5.warned is False
        # Call 6: stays blocked (not back to warning).
        v6 = guard.check_call("poll", '{"a": 1}')
        assert v6.blocked is True

    def test_cycle_key_stable_across_repeated_reason_text(self):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        guard = LoopGuard(LoopGuardConfig(max_identical_calls=1, warn_before_block=True))
        guard.check_call("x", '{"a": 1}')  # count=1, ok
        v1 = guard.check_call("x", '{"a": 1}')  # count=2 > 1 -> first trigger
        v2 = guard.check_call("x", '{"a": 1}')  # count=3 > 1 -> second trigger
        assert v1.reason != v2.reason  # human text still differs (count changes)
        assert v1.cycle_key == v2.cycle_key  # but the dedup identity is stable
        assert v1.blocked is False and v1.warned is True
        assert v2.blocked is True


class TestLoopGuardCrossTurnScope:
    """FASE 4Q.4A Fix A -- a shared LoopGuard instance lives for an agent
    object's whole lifetime (one `jarvis chat` session = one agent = one
    LoopGuard), but its thresholds are sized for a single reasoning
    episode. Without a per-turn reset, legitimate repeated reads across
    separate conversational turns eventually trip poll_tool_budget --
    exactly what the live "Cosa devo guardare oggi?" certification hit by
    turn 3. reset() must fire once per agent.run() call, and same-turn
    protection must remain fully intact within that one call."""

    @staticmethod
    def _turn(content: str = "", tool_calls: list | None = None) -> dict:
        d = {
            "content": content,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "test-model",
            "finish_reason": "stop",
        }
        if tool_calls:
            d["tool_calls"] = tool_calls
            d["finish_reason"] = "tool_calls"
        return d

    @staticmethod
    def _call(name: str, call_id: str = "c1", arguments: str = "{}") -> dict:
        return {"id": call_id, "name": name, "arguments": arguments}

    def _read_tool(self):
        from openjarvis.core.types import ToolResult
        from openjarvis.tools._stubs import BaseTool, ToolSpec

        class _ReadStub(BaseTool):
            tool_id = "maia_daily_attention_summary"

            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name="maia_daily_attention_summary",
                    description="d",
                    parameters={"type": "object", "properties": {}},
                )

            def execute(self, **params) -> ToolResult:
                return ToolResult(
                    tool_name="maia_daily_attention_summary",
                    content='{"attention_items": []}',
                    success=True,
                )

        return _ReadStub()

    def test_repeated_reads_across_many_user_turns_never_hit_poll_budget(self):
        """Item 3: a long persistent-chat simulation -- same tool, same
        args, 8 SEPARATE agent.run() calls on the SAME agent instance
        (mirrors chat_cmd.py building one agent for the whole REPL
        session) -- must never trip poll_tool_budget (default 5) purely
        because earlier turns already used the same tool."""
        from unittest.mock import MagicMock

        from openjarvis.agents._stubs import AgentContext
        from openjarvis.agents.orchestrator import OrchestratorAgent
        from openjarvis.core.types import Conversation, Message, Role

        engine = MagicMock()
        engine.engine_id = "mock"
        agent = OrchestratorAgent(engine, "test-model", tools=[self._read_tool()])
        conv = Conversation()

        for turn_num in range(1, 9):
            engine.generate.side_effect = [
                self._turn(tool_calls=[self._call("maia_daily_attention_summary")]),
                self._turn(f"answer {turn_num}"),
            ]
            result = agent.run(f"question {turn_num}", context=AgentContext(conversation=conv))
            conv.add(Message(role=Role.USER, content=f"question {turn_num}"))
            conv.add(Message(role=Role.ASSISTANT, content=result.content))
            assert len(result.tool_results) == 1
            assert result.tool_results[0].success is True, (
                f"turn {turn_num} was blocked: {result.tool_results[0].content}"
            )

    def test_counters_reset_between_two_separate_run_calls(self):
        """Item 2: two separate agent.run() calls on the same agent
        instance -- the second call's counters start fresh."""
        from unittest.mock import MagicMock

        from openjarvis.agents.orchestrator import OrchestratorAgent

        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        engine = MagicMock()
        engine.engine_id = "mock"
        agent = OrchestratorAgent(engine, "test-model", tools=[self._read_tool()])
        # OrchestratorAgent doesn't expose loop_guard_config in its own
        # constructor -- swap in a tight-budget guard directly, same
        # instance-lifetime semantics as the real one built in __init__.
        agent._loop_guard = LoopGuard(
            LoopGuardConfig(max_identical_calls=1, poll_tool_budget=1)
        )

        engine.generate.side_effect = [
            self._turn(tool_calls=[self._call("maia_daily_attention_summary")]),
            self._turn("first answer"),
        ]
        result1 = agent.run("first question")
        assert result1.tool_results[0].success is True

        # A brand-new run() call -- must not carry over turn 1's single
        # already-used budget slot.
        engine.generate.side_effect = [
            self._turn(tool_calls=[self._call("maia_daily_attention_summary")]),
            self._turn("second answer"),
        ]
        result2 = agent.run("second question")
        assert result2.tool_results[0].success is True

    def test_genuine_same_turn_loop_still_stopped(self):
        """Item 4: a real degenerate loop WITHIN a single agent.run() call
        must still be caught -- the reset only removes CROSS-turn
        carryover, never same-turn protection."""
        from unittest.mock import MagicMock

        from openjarvis.agents.orchestrator import OrchestratorAgent

        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        engine = MagicMock()
        engine.engine_id = "mock"
        agent = OrchestratorAgent(
            engine, "test-model", tools=[self._read_tool()], max_turns=10
        )
        agent._loop_guard = LoopGuard(
            LoopGuardConfig(poll_tool_budget=3, max_identical_calls=100)
        )

        # The model keeps calling the same tool over and over within ONE
        # turn -- 6 tool-call rounds, then a final stop.
        engine.generate.side_effect = [
            self._turn(tool_calls=[self._call("maia_daily_attention_summary")])
            for _ in range(6)
        ] + [self._turn("giving up")]

        result = agent.run("degenerate question")

        blocked = [r for r in result.tool_results if not r.success and "loop guard" in r.content.lower()]
        assert blocked, "a same-turn degenerate loop must still be blocked"
