"""FASE 4Q.6 — MAIA Runtime Governance Contract: shared wiring helpers.

``agents._stubs.constructor_accepts_kwarg``/``apply_capability_policy`` are
the two small, reusable primitives every capability_policy-applying caller
(cli/chat_cmd.py, cli/ask.py, cli/serve.py, agents/executor.py,
server/agent_manager_routes.py's DeepResearchAgent branch) shares, so a
``TypeError``-prone class (OrchestratorAgent, NativeReActAgent -- neither
declares ``capability_policy`` in its own ``__init__`` and neither has
``**kwargs``) never crashes construction, while its already-built
``ToolExecutor`` still receives the policy afterward.

Covers checkpoint items 1-5 from the FASE 4Q.6 fix-approval message:
1. security.capabilities.enabled + OrchestratorAgent -> no TypeError.
2. + NativeReActAgent -> no TypeError.
3. + SimpleAgent -> no TypeError.
4. an agent that accepts capability_policy in its constructor receives it.
5. an agent that doesn't accept it never gets it passed as a kwarg (and
   still receives real enforcement via the post-construction path).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents._stubs import (
    AgentContext,
    AgentResult,
    ToolUsingAgent,
    apply_capability_policy,
    constructor_accepts_kwarg,
)
from openjarvis.agents.native_react import NativeReActAgent
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.simple import SimpleAgent
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


class _FakeCapabilityPolicy:
    """See tests/agents/test_executor_capability_policy.py for rationale."""

    def __init__(self) -> None:
        self._granted: set[tuple[str, str]] = set()

    def grant(self, agent_id: str, capability: str) -> None:
        self._granted.add((agent_id, capability))

    def check(self, agent_id: str, capability: str, tool_name: str) -> bool:
        return (agent_id, capability) in self._granted


class _DeclaresCapabilityPolicyAgent(ToolUsingAgent):
    """A ToolUsingAgent subclass with no __init__ override -- inherits
    ToolUsingAgent's own signature, which DOES declare capability_policy."""

    agent_id = "declares_capability_policy_agent"

    def run(self, input, context: AgentContext | None = None, **kwargs):
        return AgentResult(content="ok", turns=1)


def _make_engine() -> MagicMock:
    engine = MagicMock()
    engine.engine_id = "mock"
    return engine


def _build_agent_kwargs_the_wired_way(agent_cls: type, capability_policy) -> dict:
    """Mirrors exactly what cli/chat_cmd.py, cli/ask.py, cli/serve.py, and
    agents/executor.py now all do: gate the constructor kwarg by
    constructor_accepts_kwarg(), never assume."""
    kwargs: dict = {"bus": None}
    if getattr(agent_cls, "accepts_tools", False):
        kwargs["tools"] = []
        kwargs["max_turns"] = 3
    if capability_policy is not None and constructor_accepts_kwarg(
        agent_cls, "capability_policy"
    ):
        kwargs["capability_policy"] = capability_policy
    return kwargs


class TestConstructorAcceptsKwarg:
    def test_orchestrator_agent_does_not_declare_capability_policy(self) -> None:
        assert constructor_accepts_kwarg(OrchestratorAgent, "capability_policy") is False

    def test_native_react_agent_does_not_declare_capability_policy(self) -> None:
        assert constructor_accepts_kwarg(NativeReActAgent, "capability_policy") is False

    def test_simple_agent_does_not_declare_capability_policy(self) -> None:
        assert constructor_accepts_kwarg(SimpleAgent, "capability_policy") is False

    def test_tool_using_agent_without_override_declares_capability_policy(
        self,
    ) -> None:
        assert (
            constructor_accepts_kwarg(
                _DeclaresCapabilityPolicyAgent, "capability_policy"
            )
            is True
        )


class TestNoTypeErrorForRealAgentClasses:
    """Checkpoint items 1-3: construction must never crash, regardless of
    whether the class declares capability_policy."""

    def test_orchestrator_agent_construction_does_not_raise(self) -> None:
        policy = _FakeCapabilityPolicy()
        kwargs = _build_agent_kwargs_the_wired_way(OrchestratorAgent, policy)
        agent = OrchestratorAgent(_make_engine(), "test-model", **kwargs)
        apply_capability_policy(agent, policy)
        # Checkpoint item 4: accepts_tools=True classes have _executor;
        # the real enforcement path receives the policy regardless of
        # whether the constructor itself declared the parameter.
        assert agent._executor._capability_policy is policy

    def test_native_react_agent_construction_does_not_raise(self) -> None:
        policy = _FakeCapabilityPolicy()
        kwargs = _build_agent_kwargs_the_wired_way(NativeReActAgent, policy)
        agent = NativeReActAgent(_make_engine(), "test-model", **kwargs)
        apply_capability_policy(agent, policy)
        assert agent._executor._capability_policy is policy

    def test_simple_agent_construction_does_not_raise(self) -> None:
        policy = _FakeCapabilityPolicy()
        kwargs = _build_agent_kwargs_the_wired_way(SimpleAgent, policy)
        # SimpleAgent has no `tools`/`bus` kwarg either (BaseAgent.__init__
        # doesn't accept `tools`) -- the wiring helper must not have added
        # capability_policy, and construction must not raise for the kwargs
        # BaseAgent.__init__ *does* accept.
        assert "capability_policy" not in kwargs
        agent = SimpleAgent(_make_engine(), "test-model", bus=None)
        apply_capability_policy(agent, policy)  # must be a safe no-op
        assert not hasattr(agent, "_executor")


class TestCapabilityPolicyPassedWhenAccepted:
    """Checkpoint item 4/5: an agent whose __init__ genuinely declares
    capability_policy receives it as a real constructor kwarg (not only
    via the post-construction fallback)."""

    def test_declared_capability_policy_reaches_constructor(self) -> None:
        policy = _FakeCapabilityPolicy()
        assert constructor_accepts_kwarg(
            _DeclaresCapabilityPolicyAgent, "capability_policy"
        )
        kwargs = _build_agent_kwargs_the_wired_way(
            _DeclaresCapabilityPolicyAgent, policy
        )
        assert kwargs["capability_policy"] is policy
        agent = _DeclaresCapabilityPolicyAgent(
            _make_engine(), "test-model", **kwargs
        )
        assert agent._executor._capability_policy is policy


class _AdminOnlyWiringTool(BaseTool):
    tool_id = "admin_only_wiring_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="admin_only_wiring_tool",
            description="Requires system:admin.",
            required_capabilities=["system:admin"],
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="admin_only_wiring_tool", content="executed!", success=True
        )


class TestRealEnforcementAfterWiring:
    """Checkpoint items 6/7, at the unit level: once wired the way ask/
    chat/serve/executor all now do, an unauthorized call is really
    blocked and an authorized one really runs -- for a class (Orchestrator)
    that never accepted the kwarg directly."""

    def test_unauthorized_tool_blocked_for_orchestrator_agent(self) -> None:
        policy = _FakeCapabilityPolicy()  # no grants
        agent = OrchestratorAgent(
            _make_engine(),
            "test-model",
            tools=[_AdminOnlyWiringTool()],
            bus=None,
        )
        apply_capability_policy(agent, policy)

        result = agent._executor.execute(
            ToolCall(id="c1", name="admin_only_wiring_tool", arguments="{}")
        )
        assert result.success is False
        assert "denied" in result.content.lower()

    def test_authorized_tool_runs_for_orchestrator_agent(self) -> None:
        policy = _FakeCapabilityPolicy()
        agent = OrchestratorAgent(
            _make_engine(),
            "test-model",
            tools=[_AdminOnlyWiringTool()],
            bus=None,
        )
        # OrchestratorAgent.__init__ doesn't forward agent_id to super()
        # either (same shape as capability_policy) -- ToolUsingAgent falls
        # back to the class-level `agent_id = "orchestrator"` attribute,
        # which is what agent._executor._agent_id actually ends up as.
        policy.grant(agent._executor._agent_id, "system:admin")
        apply_capability_policy(agent, policy)

        result = agent._executor.execute(
            ToolCall(id="c1", name="admin_only_wiring_tool", arguments="{}")
        )
        assert result.success is True
        assert result.content == "executed!"
