"""FASE 4Q.6 — MAIA Runtime Governance Contract, TEST D + TEST F/G (managed tick).

``AgentExecutor.execute_tick()`` never applied ``capability_policy`` before
FASE 4Q.6 -- an accidental governance gap, unlike jarvis ask/serve which
already did. This exercises the real, observable behavior: the resolved
``ToolExecutor._capability_policy`` a ticked agent instance actually holds,
and (TEST F/G) that a tool with ``required_capabilities`` is actually
blocked/allowed by it during a real tick -- not merely that a kwarg was
passed somewhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.agents.executor import AgentExecutor
from openjarvis.agents.manager import AgentManager
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry, ToolRegistry
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from tests.agents.fake_engine import FakeEngine


class _FakeCapabilityPolicy:
    """Duck-typed stand-in for security.capabilities.CapabilityPolicy.

    The real class requires the compiled ``openjarvis_rust`` extension,
    which isn't guaranteed to be built in every dev/CI environment (see
    ``tests/security/test_capabilities.py`` -- most of its own tests hit
    the same dependency). ``ToolExecutor.execute()`` only ever calls
    ``self._capability_policy.check(agent_id, cap, tool_name)`` (see
    ``tools/_stubs.py``), so a minimal object with that method exercises
    the exact same real enforcement code path.
    """

    def __init__(self) -> None:
        self._granted: set[tuple[str, str]] = set()

    def grant(self, agent_id: str, capability: str) -> None:
        self._granted.add((agent_id, capability))

    def check(self, agent_id: str, capability: str, tool_name: str) -> bool:
        return (agent_id, capability) in self._granted


class _CapabilityCapturingTickAgent(ToolUsingAgent):
    agent_id = "capability_capturing_tick_agent"
    accepts_tools = True

    def run(self, input, context: AgentContext | None = None, **kwargs):
        type(self).captured_policy = self._executor._capability_policy
        type(self).captured_agent_id = self._executor._agent_id
        return AgentResult(content="ticked", turns=1)


class _AdminOnlyTickTool(BaseTool):
    """A tool declaring a capability no default managed-agent policy grants."""

    tool_id = "admin_only_tick_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="admin_only_tick_tool",
            description="Requires system:admin.",
            required_capabilities=["system:admin"],
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="admin_only_tick_tool", content="executed!", success=True
        )


class _OpenTickTool(BaseTool):
    """A tool with no required_capabilities -- must always be callable."""

    tool_id = "open_tick_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="open_tick_tool", description="No capability gate.")

    def execute(self, **params) -> ToolResult:
        return ToolResult(tool_name="open_tick_tool", content="executed!", success=True)


class _GatedExecutionTickAgent(ToolUsingAgent):
    agent_id = "gated_execution_tick_agent"
    accepts_tools = True

    def run(self, input, context: AgentContext | None = None, **kwargs):
        admin_result = self._executor.execute(
            ToolCall(id="admin", name="admin_only_tick_tool", arguments="{}")
        )
        open_result = self._executor.execute(
            ToolCall(id="open", name="open_tick_tool", arguments="{}")
        )
        type(self).admin_result = admin_result
        type(self).open_result = open_result
        return AgentResult(
            content="ok",
            tool_results=[admin_result, open_result],
            turns=1,
        )


def _make_system(capability_policy) -> SimpleNamespace:
    return SimpleNamespace(
        engine=FakeEngine([{"content": "unused"}]),
        model="test-model",
        memory_backend=None,
        channel_backend=None,
        tool_executor=None,
        mcp_tools=[],
        _mcp_clients=[],
        config=None,
        session_store=None,
        capability_policy=capability_policy,
    )


def test_capability_policy_reaches_managed_agent_tick(tmp_path) -> None:
    """TEST D: a scheduled/manual tick's ToolExecutor holds the same
    capability_policy the JarvisSystem carries."""
    AgentRegistry.register_value(
        "capability_capturing_tick_agent", _CapabilityCapturingTickAgent
    )
    policy = _FakeCapabilityPolicy()
    policy.grant("some-other-agent", "system:admin")

    system = _make_system(policy)
    manager = AgentManager(db_path=str(tmp_path / "agents.db"))
    agent = manager.create_agent(
        "capability-tick",
        agent_type="capability_capturing_tick_agent",
        config={"model": "test-model"},
    )

    try:
        AgentExecutor(manager, EventBus(), system=system).execute_tick(agent["id"])
        assert _CapabilityCapturingTickAgent.captured_policy is policy
        # The managed-agent UUID, not the class-level agent_id -- confirms
        # capability_policy checks would run against the record's own id.
        assert _CapabilityCapturingTickAgent.captured_agent_id == agent["id"]
    finally:
        manager.close()


def test_no_capability_policy_when_system_has_none(tmp_path) -> None:
    """Regression guard: a JarvisSystem/FakeSystem with no policy configured
    must not synthesize one -- default managed-agent behavior stays a
    RBAC no-op, exactly as before FASE 4Q.6."""
    AgentRegistry.register_value(
        "capability_capturing_tick_agent", _CapabilityCapturingTickAgent
    )
    system = _make_system(None)
    manager = AgentManager(db_path=str(tmp_path / "agents.db"))
    agent = manager.create_agent(
        "capability-tick-none",
        agent_type="capability_capturing_tick_agent",
        config={"model": "test-model"},
    )

    try:
        AgentExecutor(manager, EventBus(), system=system).execute_tick(agent["id"])
        assert _CapabilityCapturingTickAgent.captured_policy is None
    finally:
        manager.close()


def test_unauthorized_tool_blocked_and_authorized_tool_runs_in_managed_tick(
    tmp_path,
) -> None:
    """TEST F + TEST G together: a real tick, real ToolExecutor, real
    CapabilityPolicy.check() -- the admin-gated tool is actually denied
    (CAPABILITY_DENIED-style failure, not just "policy object exists"),
    while the ungated tool still executes normally in the same tick."""
    AgentRegistry.register_value(
        "gated_execution_tick_agent", _GatedExecutionTickAgent
    )
    ToolRegistry.register_value("admin_only_tick_tool", _AdminOnlyTickTool)
    ToolRegistry.register_value("open_tick_tool", _OpenTickTool)

    policy = _FakeCapabilityPolicy()
    # Deliberately no grant of system:admin to this agent's id.

    system = _make_system(policy)
    manager = AgentManager(db_path=str(tmp_path / "agents.db"))
    agent = manager.create_agent(
        "gated-tick",
        agent_type="gated_execution_tick_agent",
        config={
            "model": "test-model",
            "tools": ["admin_only_tick_tool", "open_tick_tool"],
        },
    )

    try:
        AgentExecutor(manager, EventBus(), system=system).execute_tick(agent["id"])

        admin_result = _GatedExecutionTickAgent.admin_result
        open_result = _GatedExecutionTickAgent.open_result

        # TEST F: required_capabilities not granted -> blocked.
        assert admin_result.success is False
        assert "executed!" not in admin_result.content

        # TEST G: no required_capabilities -> unaffected by the same policy.
        assert open_result.success is True
        assert open_result.content == "executed!"
    finally:
        manager.close()


def test_authorized_tool_runs_when_capability_granted_in_managed_tick(
    tmp_path,
) -> None:
    """TEST G's positive counterpart: once the managed agent's own id is
    explicitly granted the capability, the previously-blocked tool runs."""
    AgentRegistry.register_value(
        "gated_execution_tick_agent", _GatedExecutionTickAgent
    )
    ToolRegistry.register_value("admin_only_tick_tool", _AdminOnlyTickTool)
    ToolRegistry.register_value("open_tick_tool", _OpenTickTool)

    manager = AgentManager(db_path=str(tmp_path / "agents.db"))
    agent = manager.create_agent(
        "gated-tick-granted",
        agent_type="gated_execution_tick_agent",
        config={
            "model": "test-model",
            "tools": ["admin_only_tick_tool", "open_tick_tool"],
        },
    )

    policy = _FakeCapabilityPolicy()
    policy.grant(agent["id"], "system:admin")
    system = _make_system(policy)

    try:
        AgentExecutor(manager, EventBus(), system=system).execute_tick(agent["id"])

        admin_result = _GatedExecutionTickAgent.admin_result
        assert admin_result.success is True
        assert admin_result.content == "executed!"
    finally:
        manager.close()
