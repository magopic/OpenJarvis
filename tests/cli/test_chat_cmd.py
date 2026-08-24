"""Tests for ``jarvis chat`` interactive REPL command."""

from __future__ import annotations

import time
from unittest import mock
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from openjarvis.agents._stubs import (
    AgentContext,
    AgentResult,
    BaseAgent,
    ToolUsingAgent,
)
from openjarvis.cli.chat_cmd import _read_input, chat
from openjarvis.core.config import JarvisConfig
from openjarvis.core.events import Event, EventBus, EventType
from openjarvis.core.registry import AgentRegistry, ToolRegistry
from openjarvis.core.types import Role, ToolCall, ToolResult
from openjarvis.engine import EngineConnectionError
from openjarvis.memory.store import LocalFactStore
from openjarvis.tools._stubs import BaseTool, ToolSpec


class _SimpleChatAgent(BaseAgent):
    agent_id = "simple_chat_agent"

    def run(self, input, context: AgentContext | None = None, **kwargs):
        return AgentResult(content="simple ok", turns=1)


class _DangerousChatTool(BaseTool):
    tool_id = "dangerous_chat"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dangerous_chat",
            description="Confirmation-gated chat tool.",
            requires_confirmation=True,
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="dangerous_chat",
            content="chat executed!",
            success=True,
        )


class _ToolChatAgent(ToolUsingAgent):
    agent_id = "tool_chat_agent"

    def run(self, input, context: AgentContext | None = None, **kwargs):
        result = self._executor.execute(
            ToolCall(id="chat", name="dangerous_chat", arguments="{}")
        )
        return AgentResult(content=result.content, tool_results=[result], turns=1)


class TestChatCommand:
    """Test the Click command definition and help output."""

    def test_command_exists(self) -> None:
        result = CliRunner().invoke(chat, ["--help"])
        assert result.exit_code == 0
        assert "interactive" in result.output.lower() or "chat" in result.output.lower()

    def test_options(self) -> None:
        result = CliRunner().invoke(chat, ["--help"])
        assert result.exit_code == 0
        assert "--engine" in result.output
        assert "--model" in result.output
        assert "--agent" in result.output
        assert "--tools" in result.output
        assert "--system" in result.output

    def test_slash_commands_listed(self) -> None:
        result = CliRunner().invoke(chat, ["--help"])
        assert result.exit_code == 0
        assert "/quit" in result.output


class TestReadInput:
    """Test the _read_input helper function."""

    def test_read_input_eof(self) -> None:
        with mock.patch("builtins.input", side_effect=EOFError):
            assert _read_input() is None

    def test_read_input_keyboard_interrupt(self) -> None:
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            assert _read_input() is None

    def test_read_input_normal(self) -> None:
        with mock.patch("builtins.input", return_value="hello"):
            assert _read_input() == "hello"


class TestChatAgents:
    def test_direct_chat_injects_auto_memory_facts(self, tmp_path) -> None:
        facts_path = tmp_path / "facts.jsonl"
        LocalFactStore(facts_path).add(
            "The user's favorite color is blue",
            source="auto",
        )

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {"content": "Blue."}
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.memory.enabled = True
        config.memory.facts_path = str(facts_path)
        config.agent.context_from_memory = True

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
            patch("openjarvis.memory.build_memory_service", return_value=None),
            patch("openjarvis.cli.ask._get_memory_backend", return_value=None),
        ):
            result = CliRunner().invoke(
                chat,
                ["--model", "test-model"],
                input="What is my favorite color?\n/quit\n",
            )

        assert result.exit_code == 0
        messages = engine.generate.call_args.args[0]
        assert messages[0].role.value == "system"
        assert "favorite color is blue" in messages[0].content

    def test_chat_generation_survives_fact_store_failure(self) -> None:
        class _FailingMemoryService:
            def start(self) -> None:
                pass

            def stop(self, timeout: float = 2.0) -> None:
                pass

            def list_facts(self):
                raise OSError("fact store unavailable")

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {"content": "Still working."}
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.memory.enabled = True
        config.agent.context_from_memory = True

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
            patch(
                "openjarvis.memory.build_memory_service",
                return_value=_FailingMemoryService(),
            ),
            patch("openjarvis.cli.ask._get_memory_backend", return_value=None),
        ):
            result = CliRunner().invoke(
                chat,
                ["--model", "test-model"],
                input="hello\n/quit\n",
            )

        assert result.exit_code == 0
        assert "Still working." in result.output
        engine.generate.assert_called_once()

    def test_simple_agent_does_not_receive_tool_only_kwargs(self) -> None:
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {"content": "engine fallback"}
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"

        AgentRegistry.register_value("simple_chat_agent", _SimpleChatAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "simple_chat_agent", "--model", "test-model"],
                input="hello\n/quit\n",
            )

        assert result.exit_code == 0
        assert "simple ok" in result.output
        assert "failed" not in result.output.lower()

    def test_agent_receives_prior_turn_history(self) -> None:
        """Multi-turn chat must pass prior turns to agent.run() via AgentContext."""

        captured_contexts: list[AgentContext | None] = []

        class _CapturingAgent(BaseAgent):
            agent_id = "capturing_chat_agent"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                captured_contexts.append(context)
                return AgentResult(content=f"reply-{len(captured_contexts)}", turns=1)

        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"

        AgentRegistry.register_value("capturing_chat_agent", _CapturingAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "capturing_chat_agent", "--model", "test-model"],
                input="first turn\nsecond turn\n/quit\n",
            )

        assert result.exit_code == 0
        assert len(captured_contexts) == 2

        first_turn_context, second_turn_context = captured_contexts
        assert first_turn_context is not None
        assert first_turn_context.conversation.messages == []

        assert second_turn_context is not None
        prior_texts = [m.content for m in second_turn_context.conversation.messages]
        assert "first turn" in prior_texts
        assert "reply-1" in prior_texts

    def test_agent_memory_context_precedes_prior_turn_history(self, tmp_path) -> None:
        """Memory system context must remain ahead of prior conversation turns."""

        captured_contexts: list[AgentContext | None] = []

        class _CapturingAgent(BaseAgent):
            agent_id = "capturing_memory_chat_agent"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                captured_contexts.append(context)
                return AgentResult(content=f"reply-{len(captured_contexts)}", turns=1)

        facts_path = tmp_path / "facts.jsonl"
        LocalFactStore(facts_path).add("The user likes jazz", source="auto")

        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.memory.enabled = True
        config.memory.facts_path = str(facts_path)
        config.agent.context_from_memory = True

        AgentRegistry.register_value(
            "capturing_memory_chat_agent",
            _CapturingAgent,
        )

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
            patch("openjarvis.memory.build_memory_service", return_value=None),
            patch("openjarvis.cli.ask._get_memory_backend", return_value=None),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "capturing_memory_chat_agent", "--model", "test-model"],
                input="first turn\nsecond turn\n/quit\n",
            )

        assert result.exit_code == 0
        assert len(captured_contexts) == 2

        second_turn_context = captured_contexts[1]
        assert second_turn_context is not None
        messages = second_turn_context.conversation.messages
        assert [message.role for message in messages] == [
            Role.SYSTEM,
            Role.USER,
            Role.ASSISTANT,
        ]
        assert "user likes jazz" in messages[0].content
        assert [message.content for message in messages[1:]] == [
            "first turn",
            "reply-1",
        ]

    def test_memory_service_started_fed_and_stopped(self) -> None:
        """The REPL starts memory, publishes each turn, and stops it."""

        class _SpyMemoryService:
            def __init__(self, bus: EventBus) -> None:
                self.bus = bus
                self.started = False
                self.stopped = False
                self.submissions: list[tuple[str, str]] = []

            def start(self) -> None:
                self.started = True
                self.bus.subscribe(
                    EventType.CHAT_EXCHANGE_COMPLETED,
                    self._on_completed_exchange,
                )

            def _on_completed_exchange(self, event: Event) -> None:
                self.submissions.append(
                    (
                        event.data["user_text"],
                        event.data.get("assistant_text", ""),
                    )
                )

            def stop(self, timeout: float = 2.0) -> None:
                self.stopped = True
                self.bus.unsubscribe(
                    EventType.CHAT_EXCHANGE_COMPLETED,
                    self._on_completed_exchange,
                )

        spy: _SpyMemoryService | None = None

        def _build_memory_service(*args, event_bus: EventBus | None = None, **kwargs):
            nonlocal spy
            assert event_bus is not None
            spy = _SpyMemoryService(event_bus)
            return spy

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {"content": "engine fallback"}
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"

        AgentRegistry.register_value("simple_chat_agent", _SimpleChatAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
            patch(
                "openjarvis.memory.build_memory_service",
                side_effect=_build_memory_service,
            ),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "simple_chat_agent", "--model", "test-model"],
                input="hello\n/quit\n",
            )

        assert result.exit_code == 0
        assert spy is not None
        assert spy.started is True
        assert spy.stopped is True
        assert spy.submissions == [("hello", "simple ok")]

    def test_tool_agent_uses_legacy_agent_tools_and_prompts_confirmation(self) -> None:
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.agent.tools = "dangerous_chat"
        config.agent.max_turns = 3

        AgentRegistry.register_value("tool_chat_agent", _ToolChatAgent)
        ToolRegistry.register_value("dangerous_chat", _DangerousChatTool)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "tool_chat_agent", "--model", "test-model"],
                input="run tool\ny\n/quit\n",
            )

        assert result.exit_code == 0
        assert "Confirm:" in result.output
        assert "chat executed!" in result.output


class TestChatEngineSelectionParity:
    """FASE 4Q.1A -- letters A-F. ``jarvis chat`` must resolve engine+model
    exactly like ``jarvis ask`` (FASE 4P.3A's strict pairing), never
    silently substitute a different engine, and the banner must always
    reflect the ACTUALLY resolved engine/model, not raw CLI flags."""

    def test_a_explicit_cloud_plus_explicit_model_passed_through_strictly(self) -> None:
        """A: --engine cloud --model claude-sonnet-4-6 must reach
        get_engine() as an explicit (engine_key, model) pair -- the exact
        shape that activates FASE 4P.3A's strict-pairing guard. The guard's
        own correctness is covered by
        tests/engine/test_cloud_engine_selection_integrity.py (frozen,
        untouched); this only proves chat wires up to it, unlike before."""
        engine = MagicMock()
        engine.engine_id = "cloud"
        engine.generate.return_value = {"content": "OK"}
        config = JarvisConfig()

        captured_calls = []

        def _fake_get_engine(cfg, engine_key=None, model=None):
            captured_calls.append((engine_key, model))
            return ("cloud", engine)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", side_effect=_fake_get_engine),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--engine", "cloud", "--model", "claude-sonnet-4-6"],
                input="hi\n/quit\n",
            )

        assert result.exit_code == 0
        assert captured_calls == [("cloud", "claude-sonnet-4-6")]

    def test_b_explicit_cloud_unavailable_explicit_error_no_local_fallback(self) -> None:
        """B: get_engine() raising EngineConnectionError (the strict
        pairing's own failure mode) must surface as a clear CLI error and
        exit non-zero -- never silently proceed with any other engine."""
        config = JarvisConfig()

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch(
                "openjarvis.engine.get_engine",
                side_effect=EngineConnectionError(
                    "Requested engine 'cloud' is not usable (health check failed)."
                ),
            ),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--engine", "cloud", "--model", "claude-sonnet-4-6"],
                input="hi\n/quit\n",
            )

        assert result.exit_code != 0
        assert "not usable" in result.output
        assert "llamacpp" not in result.output.lower()

    def test_c_explicit_model_not_serviceable_explicit_error(self) -> None:
        """C: the OTHER strict-pairing failure mode (engine reachable, but
        cannot serve the specific requested model) -- same hard-stop
        contract as B, distinct message."""
        config = JarvisConfig()

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch(
                "openjarvis.engine.get_engine",
                side_effect=EngineConnectionError(
                    "Requested engine 'cloud' cannot serve model 'claude-sonnet-4-6'."
                ),
            ),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--engine", "cloud", "--model", "claude-sonnet-4-6"],
                input="hi\n/quit\n",
            )

        assert result.exit_code != 0
        assert "cannot serve model" in result.output

    def test_d_default_auto_behavior_legacy_fallback_preserved(self) -> None:
        """D: with no --engine at all (only --model, exactly like every
        pre-existing chat test in this file), get_engine() must be called
        with engine_key=None -- never spuriously activating the strict
        branch just because a model happens to be set. Undirected/
        auto-fallback selection (#73's behavior) stays intact."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = {"content": "fallback ok"}
        config = JarvisConfig()

        captured_calls = []

        def _fake_get_engine(cfg, engine_key=None, model=None):
            captured_calls.append((engine_key, model))
            return ("mock", engine)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", side_effect=_fake_get_engine),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--model", "test-model"],
                input="hi\n/quit\n",
            )

        assert result.exit_code == 0
        assert captured_calls == [(None, "test-model")]

    def test_e_banner_reports_actual_resolved_engine(self) -> None:
        """E: the banner must show the engine key get_engine() actually
        returned, not the raw --engine flag value -- proven by resolving to
        a DIFFERENT key than what was requested (an undirected request that
        happens to land on 'cloud')."""
        engine = MagicMock()
        engine.engine_id = "cloud"
        engine.generate.return_value = {"content": "OK"}
        config = JarvisConfig()

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("cloud", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--model", "test-model"],
                input="hi\n/quit\n",
            )

        assert result.exit_code == 0
        assert "Engine: cloud" in result.output

    def test_f_model_propagates_into_agent_construction(self) -> None:
        """F: the resolved model string must reach the agent's own
        self._model (via BaseAgent.__init__), not just the direct
        engine.generate() path."""
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()

        class _ModelEchoAgent(BaseAgent):
            agent_id = "model_echo_agent"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                return AgentResult(content=f"model-was-{self._model}", turns=1)

        AgentRegistry.register_value("model_echo_agent", _ModelEchoAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "model_echo_agent", "--model", "claude-sonnet-4-6"],
                input="hi\n/quit\n",
            )

        assert result.exit_code == 0
        assert "model-was-claude-sonnet-4-6" in result.output


class TestChatTurnTimeout:
    """FASE 4Q.1A -- letters G-J. One chat turn must have a real wall-clock
    bound; a timeout must never fabricate assistant output, must leave
    prior history intact, and must let the user continue afterward."""

    def test_g_overlong_agent_turn_is_bounded(self) -> None:
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()

        class _SlowChatAgent(BaseAgent):
            agent_id = "slow_chat_agent_g"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                time.sleep(2.0)
                return AgentResult(content="should never be seen", turns=1)

        AgentRegistry.register_value("slow_chat_agent_g", _SlowChatAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "slow_chat_agent_g", "--model", "test-model", "--turn-timeout", "0.2"],
                input="hello\n/quit\n",
            )

        assert result.exit_code == 0
        assert "timed out" in result.output.lower()

    def test_i_timeout_produces_no_fabricated_reply(self) -> None:
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()

        class _SlowChatAgent(BaseAgent):
            agent_id = "slow_chat_agent_i"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                time.sleep(2.0)
                return AgentResult(content="should never be seen", turns=1)

        AgentRegistry.register_value("slow_chat_agent_i", _SlowChatAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "slow_chat_agent_i", "--model", "test-model", "--turn-timeout", "0.2"],
                input="hello\n/quit\n",
            )

        assert result.exit_code == 0
        assert "should never be seen" not in result.output

    def test_h_prior_history_survives_a_timed_out_turn(self) -> None:
        """H: turn 1 succeeds, turn 2 times out, turn 3 succeeds again --
        turn 3's AgentContext must contain turn 1's real exchange, the
        timed-out turn's user message (asked, honestly unanswered), and
        crucially NO fabricated assistant reply for turn 2."""
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()

        captured_contexts: list[AgentContext | None] = []
        calls = {"n": 0}

        class _SometimesSlowAgent(BaseAgent):
            agent_id = "sometimes_slow_agent_h"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                calls["n"] += 1
                captured_contexts.append(context)
                if calls["n"] == 2:
                    time.sleep(2.0)
                    return AgentResult(content="never seen", turns=1)
                return AgentResult(content=f"reply-{calls['n']}", turns=1)

        AgentRegistry.register_value("sometimes_slow_agent_h", _SometimesSlowAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "sometimes_slow_agent_h", "--model", "test-model", "--turn-timeout", "0.2"],
                input="first turn\nsecond turn\nthird turn\n/quit\n",
            )

        assert result.exit_code == 0
        assert "reply-1" in result.output
        assert "timed out" in result.output.lower()
        assert len(captured_contexts) == 3

        third_context = captured_contexts[2]
        assert third_context is not None
        prior_texts = [m.content for m in third_context.conversation.messages]
        assert "first turn" in prior_texts
        assert "reply-1" in prior_texts
        # The timed-out turn's own user message survives (asked, not erased)...
        assert "second turn" in prior_texts
        # ...but no fabricated assistant reply was ever inserted for it.
        assert "never seen" not in prior_texts

    def test_j_user_can_continue_after_a_timeout(self) -> None:
        """J: the session does not crash or exit on a timeout -- the user
        can keep chatting, and a clean /quit still exits 0 afterward."""
        engine = MagicMock()
        engine.engine_id = "mock"
        config = JarvisConfig()
        calls = {"n": 0}

        class _SometimesSlowAgent(BaseAgent):
            agent_id = "sometimes_slow_agent_j"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    time.sleep(2.0)
                    return AgentResult(content="never seen", turns=1)
                return AgentResult(content="recovered fine", turns=1)

        AgentRegistry.register_value("sometimes_slow_agent_j", _SometimesSlowAgent)

        with (
            patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
            patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
            patch("openjarvis.intelligence.register_builtin_models"),
        ):
            result = CliRunner().invoke(
                chat,
                ["--agent", "sometimes_slow_agent_j", "--model", "test-model", "--turn-timeout", "0.2"],
                input="first turn\nsecond turn\n/quit\n",
            )

        assert result.exit_code == 0
        assert calls["n"] == 2
        assert "recovered fine" in result.output
