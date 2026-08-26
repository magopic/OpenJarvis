"""Tests for ``jarvis ask --agent`` CLI integration."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.cli import cli
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_ask_mod = importlib.import_module("openjarvis.cli.ask")


def _mock_engine(content="Hello from engine"):
    """Create a mock engine that returns content."""
    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["test-model"]
    engine.generate.return_value = {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
        "finish_reason": "stop",
    }
    return engine


def _register_agents():
    """Re-register agents after registry clear."""
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.agents.simple import SimpleAgent
    from openjarvis.core.registry import AgentRegistry

    for name, cls in [
        ("simple", SimpleAgent),
        ("orchestrator", OrchestratorAgent),
    ]:
        if not AgentRegistry.contains(name):
            AgentRegistry.register_value(name, cls)


def _register_tools():
    """Re-register tools after registry clear."""
    from openjarvis.core.registry import ToolRegistry
    from openjarvis.tools.calculator import CalculatorTool
    from openjarvis.tools.file_read import FileReadTool
    from openjarvis.tools.llm_tool import LLMTool
    from openjarvis.tools.retrieval import RetrievalTool
    from openjarvis.tools.think import ThinkTool

    for name, cls in [
        ("calculator", CalculatorTool),
        ("think", ThinkTool),
        ("retrieval", RetrievalTool),
        ("llm", LLMTool),
        ("file_read", FileReadTool),
    ]:
        if not ToolRegistry.contains(name):
            ToolRegistry.register_value(name, cls)


class _DangerousTool(BaseTool):
    tool_id = "dangerous"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dangerous",
            description="Confirmation-gated test tool.",
            requires_confirmation=True,
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="dangerous",
            content="executed!",
            success=True,
        )


class _ConfirmingAgent(ToolUsingAgent):
    agent_id = "confirming_agent"

    def run(self, input, context: AgentContext | None = None, **kwargs):
        result = self._executor.execute(
            ToolCall(id="confirm", name="dangerous", arguments="{}")
        )
        return AgentResult(
            content=result.content,
            tool_results=[result],
            turns=1,
        )


@dataclass
class _EngineSetup:
    engine: MagicMock
    config: object


@pytest.fixture
def agent_setup():
    from openjarvis.core.config import JarvisConfig
    from openjarvis.core.registry import AgentRegistry, ToolRegistry

    engine = _mock_engine("unused")
    config = JarvisConfig()
    config.intelligence.default_model = "test-model"
    config.agent.max_turns = 3

    AgentRegistry.register_value("confirming_agent", _ConfirmingAgent)
    ToolRegistry.register_value("dangerous", _DangerousTool)

    with (
        patch.object(_ask_mod, "load_config", return_value=config),
        patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
        patch.object(_ask_mod, "discover_engines", return_value=[("mock", engine)]),
        patch.object(
            _ask_mod,
            "discover_models",
            return_value={"mock": ["test-model"]},
        ),
        patch.object(_ask_mod, "register_builtin_models"),
        patch.object(_ask_mod, "merge_discovered_models"),
    ):
        yield _EngineSetup(engine=engine, config=config)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_setup():
    """Patch engine discovery to avoid needing a running engine."""
    engine = _mock_engine()
    _register_agents()
    _register_tools()
    with (
        patch.object(_ask_mod, "load_config") as mock_cfg,
        patch.object(_ask_mod, "get_engine") as mock_ge,
        patch.object(_ask_mod, "discover_engines") as mock_de,
        patch.object(_ask_mod, "discover_models") as mock_dm,
        patch.object(_ask_mod, "register_builtin_models"),
        patch.object(_ask_mod, "merge_discovered_models"),
    ):
        from openjarvis.core.config import JarvisConfig

        mock_cfg.return_value = JarvisConfig()
        mock_ge.return_value = ("mock", engine)
        mock_de.return_value = [("mock", engine)]
        mock_dm.return_value = {"mock": ["test-model"]}
        yield engine


class TestAskAgentOption:
    def test_help_shows_agent_option(self, runner):
        result = runner.invoke(cli, ["ask", "--help"])
        assert "--agent" in result.output or "-a" in result.output

    def test_help_shows_tools_option(self, runner):
        result = runner.invoke(cli, ["ask", "--help"])
        assert "--tools" in result.output

    def test_agent_simple(self, runner, mock_setup):
        result = runner.invoke(cli, ["ask", "--agent", "simple", "Hello"])
        assert result.exit_code == 0
        assert "Hello from engine" in result.output

    def test_agent_orchestrator_no_tools(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            ["ask", "--agent", "orchestrator", "Hello"],
        )
        assert result.exit_code == 0

    def test_agent_orchestrator_with_tools(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            [
                "ask",
                "--agent",
                "orchestrator",
                "--tools",
                "calculator,think",
                "What is 2+2?",
            ],
        )
        assert result.exit_code == 0

    def test_agent_json_output(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            ["ask", "--agent", "simple", "--json", "Hello"],
        )
        assert result.exit_code == 0
        assert '"content"' in result.output
        assert '"turns"' in result.output

    def test_unknown_agent(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            ["ask", "--agent", "nonexistent", "Hello"],
        )
        assert result.exit_code != 0

    def test_no_agent_flag_falls_back_to_config_default_agent(self, runner, mock_setup):
        """When --agent is omitted, ``config.agent.default_agent`` is used.

        The default ``JarvisConfig`` sets ``default_agent = "simple"``, so
        ``jarvis ask "..."`` should route through SimpleAgent rather than
        the direct-to-engine path. Without this fallback, persona settings
        (``default_system_prompt`` and SOUL.md/MEMORY.md/USER.md) would be
        silently bypassed.
        """
        result = runner.invoke(cli, ["ask", "Hello"])
        assert result.exit_code == 0
        assert "Hello from engine" in result.output

    def test_explicit_empty_agent_opts_out_of_agent_mode(self, runner, mock_setup):
        """``--agent ""`` is the explicit opt-out: use direct-to-engine."""
        result = runner.invoke(cli, ["ask", "--agent", "", "Hello"])
        assert result.exit_code == 0
        assert "Hello from engine" in result.output

    def test_no_agent_with_blank_config_default_uses_direct_mode(
        self, runner, mock_setup, monkeypatch
    ):
        """When config's ``default_agent`` is blank and --agent is omitted,
        the original direct-to-engine path is preserved."""
        from openjarvis.core.config import JarvisConfig

        cfg = JarvisConfig()
        cfg.agent.default_agent = ""
        monkeypatch.setattr(_ask_mod, "load_config", lambda *a, **kw: cfg)
        result = runner.invoke(cli, ["ask", "Hello"])
        assert result.exit_code == 0
        assert "Hello from engine" in result.output

    def test_agent_simple_with_model(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            ["ask", "--agent", "simple", "-m", "test-model", "Hello"],
        )
        assert result.exit_code == 0

    def test_agent_simple_with_temperature(self, runner, mock_setup):
        result = runner.invoke(
            cli,
            ["ask", "--agent", "simple", "-t", "0.1", "Hello"],
        )
        assert result.exit_code == 0

    @pytest.mark.parametrize(
        ("tools_enabled", "agent_tools"),
        [
            (["dangerous"], ""),
            ("dangerous", ""),
            ("", "dangerous"),
        ],
    )
    def test_agent_uses_configured_tools_by_default(
        self,
        runner,
        agent_setup,
        tools_enabled,
        agent_tools,
    ):
        agent_setup.config.tools.enabled = tools_enabled
        agent_setup.config.agent.tools = agent_tools

        result = runner.invoke(
            cli,
            ["ask", "--agent", "confirming_agent", "Hello"],
        )

        assert result.exit_code == 0
        assert "executed!" in result.output
        agent_setup.engine.generate.assert_not_called()


class TestAskMaiaSafeAgentUpgrade:
    """FASE 4Q.4A -- same root cause and same fix as chat_cmd.py's
    identical "MAIA-safe upgrade" gate: a plain ``jarvis ask`` (no
    --agent) must upgrade a non-tool-capable default agent to
    "orchestrator" when MAIA's own always-safe tool family (Second
    Brain/Document Knowledge/Proactive Insight/Monitoring/maia_manage/
    Governed Action -- resolve_tool_names() in cli/_tool_names.py) is
    available, not only when OPS Bridge tools have passed governance."""

    def test_default_agent_upgrades_to_orchestrator_for_maia_tool_family(
        self, runner
    ) -> None:
        from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
        from openjarvis.core.config import JarvisConfig
        from openjarvis.core.registry import AgentRegistry, ToolRegistry

        class _NonToolStub(BaseAgent):
            agent_id = "non_tool_stub_ask"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                return AgentResult(content="non-tool reply", turns=1)

        class _OrchestratorStub(ToolUsingAgent):
            agent_id = "orchestrator"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                names = sorted(t.spec.name for t in self._tools)
                return AgentResult(
                    content=f"orchestrator reply tools={names}", turns=1
                )

        class _MaiaAttentionStub(BaseTool):
            tool_id = "maia_daily_attention_summary"

            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name="maia_daily_attention_summary",
                    description="stub",
                )

            def execute(self, **params) -> ToolResult:
                return ToolResult(
                    tool_name="maia_daily_attention_summary",
                    content="{}",
                    success=True,
                )

        engine = _mock_engine("unused")
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.agent.default_agent = "non_tool_stub_ask"

        AgentRegistry.register_value("non_tool_stub_ask", _NonToolStub)
        AgentRegistry.register_value("orchestrator", _OrchestratorStub)
        ToolRegistry.register_value(
            "maia_daily_attention_summary", _MaiaAttentionStub
        )

        with (
            patch.object(_ask_mod, "load_config", return_value=config),
            patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
            patch.object(
                _ask_mod, "discover_engines", return_value=[("mock", engine)]
            ),
            patch.object(
                _ask_mod,
                "discover_models",
                return_value={"mock": ["test-model"]},
            ),
            patch.object(_ask_mod, "register_builtin_models"),
            patch.object(_ask_mod, "merge_discovered_models"),
            patch(
                "openjarvis.tools.ops_bridge_generic.get_auto_enabled_ops_tool_ids",
                return_value=[],
            ),
        ):
            result = runner.invoke(cli, ["ask", "Hello"])

        assert result.exit_code == 0
        assert "orchestrator reply" in result.output
        assert "maia_daily_attention_summary" in result.output

    def test_default_agent_stays_non_tool_capable_when_nothing_registered(
        self, runner
    ) -> None:
        from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
        from openjarvis.core.config import JarvisConfig
        from openjarvis.core.registry import AgentRegistry

        class _NonToolStub(BaseAgent):
            agent_id = "non_tool_stub_ask_2"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                return AgentResult(content="non-tool reply", turns=1)

        class _OrchestratorStub(ToolUsingAgent):
            agent_id = "orchestrator"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                return AgentResult(content="orchestrator reply", turns=1)

        engine = _mock_engine("unused")
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"
        config.agent.default_agent = "non_tool_stub_ask_2"

        AgentRegistry.register_value("non_tool_stub_ask_2", _NonToolStub)
        AgentRegistry.register_value("orchestrator", _OrchestratorStub)

        with (
            patch.object(_ask_mod, "load_config", return_value=config),
            patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
            patch.object(
                _ask_mod, "discover_engines", return_value=[("mock", engine)]
            ),
            patch.object(
                _ask_mod,
                "discover_models",
                return_value={"mock": ["test-model"]},
            ),
            patch.object(_ask_mod, "register_builtin_models"),
            patch.object(_ask_mod, "merge_discovered_models"),
            patch(
                "openjarvis.tools.ops_bridge_generic.get_auto_enabled_ops_tool_ids",
                return_value=[],
            ),
        ):
            result = runner.invoke(cli, ["ask", "Hello"])

        assert result.exit_code == 0
        assert "non-tool reply" in result.output
        assert "orchestrator reply" not in result.output


class TestBuildTools:
    def test_build_calculator(self, mock_setup):
        from openjarvis.cli.ask import _build_tools
        from openjarvis.core.config import JarvisConfig

        _register_tools()
        config = JarvisConfig()
        tools = _build_tools(["calculator"], config, mock_setup, "test-model")
        assert len(tools) == 1
        assert tools[0].tool_id == "calculator"

    def test_build_think(self, mock_setup):
        from openjarvis.cli.ask import _build_tools
        from openjarvis.core.config import JarvisConfig

        _register_tools()
        config = JarvisConfig()
        tools = _build_tools(["think"], config, mock_setup, "test-model")
        assert len(tools) == 1
        assert tools[0].tool_id == "think"

    def test_build_unknown_tool_skipped(self, mock_setup):
        from openjarvis.cli.ask import _build_tools
        from openjarvis.core.config import JarvisConfig

        config = JarvisConfig()
        tools = _build_tools(["nonexistent"], config, mock_setup, "test-model")
        assert len(tools) == 0

    def test_build_empty_names(self, mock_setup):
        from openjarvis.cli.ask import _build_tools
        from openjarvis.core.config import JarvisConfig

        config = JarvisConfig()
        tools = _build_tools(["", " "], config, mock_setup, "test-model")
        assert len(tools) == 0

    def test_build_multiple_tools(self, mock_setup):
        from openjarvis.cli.ask import _build_tools
        from openjarvis.core.config import JarvisConfig

        _register_tools()
        config = JarvisConfig()
        tools = _build_tools(["calculator", "think"], config, mock_setup, "test-model")
        assert len(tools) == 2


class TestPersonaFilesReachModel:
    """End-to-end coverage for SOUL.md / MEMORY.md / USER.md integration.

    Without these tests, the ``SystemPromptBuilder`` and the persona files
    documented in the README are present in the codebase but never reach
    the model — the bug this suite is meant to prevent regressing into.
    """

    def test_soul_md_content_reaches_engine_in_simple_agent(
        self, runner, monkeypatch, tmp_path
    ):
        """SOUL.md content must appear in the system message sent to the engine."""
        from openjarvis.core.config import JarvisConfig

        # Write a SOUL.md with a unique sentinel string we can grep for
        soul = tmp_path / "SOUL.md"
        soul.write_text("PERSONA_SENTINEL_zh_jarvis", encoding="utf-8")
        memory = tmp_path / "MEMORY.md"
        memory.write_text("MEMORY_SENTINEL", encoding="utf-8")
        user = tmp_path / "USER.md"
        user.write_text("USER_SENTINEL", encoding="utf-8")

        cfg = JarvisConfig()
        cfg.memory_files.soul_path = str(soul)
        cfg.memory_files.memory_path = str(memory)
        cfg.memory_files.user_path = str(user)
        cfg.agent.default_system_prompt = "BASELINE_TEMPLATE"
        # Disable memory context injection so it doesn't add another SYSTEM
        # message and confuse the assertion.
        cfg.agent.context_from_memory = False

        engine = _mock_engine()
        _register_agents()
        _register_tools()
        with (
            patch.object(_ask_mod, "load_config", return_value=cfg),
            patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
            patch.object(_ask_mod, "discover_engines", return_value=[("mock", engine)]),
            patch.object(
                _ask_mod, "discover_models", return_value={"mock": ["test-model"]}
            ),
            patch.object(_ask_mod, "register_builtin_models"),
            patch.object(_ask_mod, "merge_discovered_models"),
        ):
            result = runner.invoke(cli, ["ask", "--agent", "simple", "Hello"])

        assert result.exit_code == 0, result.output
        # Grab the messages passed to engine.generate
        engine.generate.assert_called()
        call_args = engine.generate.call_args
        messages = (
            call_args.args[0] if call_args.args else call_args.kwargs.get("messages")
        )
        assert messages is not None and len(messages) >= 2
        system_messages = [m for m in messages if str(m.role).endswith("SYSTEM")]
        assert system_messages, f"No SYSTEM message in {messages!r}"
        joined = "\n".join(m.content for m in system_messages)
        assert "BASELINE_TEMPLATE" in joined
        assert "PERSONA_SENTINEL_zh_jarvis" in joined
        assert "MEMORY_SENTINEL" in joined
        assert "USER_SENTINEL" in joined

    def test_orchestrator_accepts_persona_prompt_builder(
        self, runner, monkeypatch, tmp_path
    ):
        """Orchestrator explicitly accepts and applies persona wiring."""
        from openjarvis.core.config import JarvisConfig

        soul = tmp_path / "SOUL.md"
        soul.write_text("ORCH_PERSONA_SENTINEL", encoding="utf-8")

        cfg = JarvisConfig()
        cfg.memory_files.soul_path = str(soul)
        cfg.agent.context_from_memory = False

        engine = _mock_engine()
        _register_agents()
        _register_tools()
        with (
            patch.object(_ask_mod, "load_config", return_value=cfg),
            patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
            patch.object(_ask_mod, "discover_engines", return_value=[("mock", engine)]),
            patch.object(
                _ask_mod, "discover_models", return_value={"mock": ["test-model"]}
            ),
            patch.object(_ask_mod, "register_builtin_models"),
            patch.object(_ask_mod, "merge_discovered_models"),
        ):
            result = runner.invoke(cli, ["ask", "--agent", "orchestrator", "Hello"])

        assert result.exit_code == 0, result.output
        messages = engine.generate.call_args.args[0]
        assert "ORCH_PERSONA_SENTINEL" in messages[0].content


class TestAskCapabilityPolicyRegression:
    """FASE 4Q.6 — MAIA Runtime Governance Contract, TEST B / TEST 8.

    ``jarvis ask`` already applied ``capability_policy`` before FASE 4Q.6.
    This phase touched ask.py's wiring too (see
    ``agents._stubs.constructor_accepts_kwarg``/``apply_capability_policy``)
    to fix a latent ``TypeError`` for agent classes (OrchestratorAgent,
    NativeReActAgent, ...) that don't declare ``capability_policy`` in
    their own ``__init__`` -- this is a no-regression proof that ask's
    *observable* behavior (the resolved ``ToolExecutor._capability_policy``)
    is unchanged for a class that DOES accept it directly.

    Mocks ``setup_security`` directly (mirrors
    ``tests/cli/test_serve_capability_policy.py``) rather than driving the
    real, Rust-backed ``CapabilityPolicy`` through config -- that machinery
    isn't available in every dev/CI environment.
    """

    def test_capability_policy_reaches_ask_agent_executor(self, runner) -> None:
        captured_policies: list[object] = []

        class _CapabilityCapturingAskAgent(ToolUsingAgent):
            agent_id = "capability_capturing_ask_agent"

            def run(self, input, context: AgentContext | None = None, **kwargs):
                captured_policies.append(self._executor._capability_policy)
                return AgentResult(content="ok", turns=1)

        from openjarvis.core.config import JarvisConfig
        from openjarvis.core.registry import AgentRegistry

        engine = _mock_engine("unused")
        config = JarvisConfig()
        config.intelligence.default_model = "test-model"

        AgentRegistry.register_value(
            "capability_capturing_ask_agent",
            _CapabilityCapturingAskAgent,
        )

        _policy_sentinel = object()
        sec = MagicMock()
        sec.engine = engine
        sec.capability_policy = _policy_sentinel
        sec.audit_logger = None

        with (
            patch.object(_ask_mod, "load_config", return_value=config),
            patch.object(_ask_mod, "get_engine", return_value=("mock", engine)),
            patch.object(_ask_mod, "discover_engines", return_value=[("mock", engine)]),
            patch.object(
                _ask_mod, "discover_models", return_value={"mock": ["test-model"]}
            ),
            patch.object(_ask_mod, "register_builtin_models"),
            patch.object(_ask_mod, "merge_discovered_models"),
            patch("openjarvis.security.setup_security", return_value=sec),
        ):
            result = runner.invoke(
                cli,
                ["ask", "--agent", "capability_capturing_ask_agent", "Hello"],
            )

        assert result.exit_code == 0, result.output
        assert len(captured_policies) == 1
        assert captured_policies[0] is _policy_sentinel
