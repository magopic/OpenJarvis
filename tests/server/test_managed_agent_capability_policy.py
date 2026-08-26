"""FASE 4Q.6 — MAIA Runtime Governance Contract, TEST E (+ F/G for SSE).

``_stream_managed_agent`` never applied ``capability_policy`` before FASE
4Q.6 -- an accidental governance gap, unlike jarvis ask/serve which already
did. Modeled directly on
``tests/server/test_managed_agent_resolved_tools.py``'s full end-to-end
pattern (real ``_stream_managed_agent`` call, real ``ToolExecutor``, a fake
streaming engine that requests a specific tool call) so this exercises real,
observable behavior -- an actually-denied tool call -- not just that a kwarg
was passed somewhere.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from openjarvis.core.registry import ToolRegistry  # noqa: E402
from openjarvis.core.types import Role, ToolResult  # noqa: E402
from openjarvis.engine._stubs import StreamChunk  # noqa: E402
from openjarvis.tools._stubs import BaseTool, ToolSpec  # noqa: E402


class _FakeCapabilityPolicy:
    """Duck-typed stand-in for security.capabilities.CapabilityPolicy --
    see tests/agents/test_executor_capability_policy.py for the identical
    rationale (avoids the openjarvis_rust dependency the real class
    requires; ToolExecutor.execute() only ever calls
    ``self._capability_policy.check(agent_id, cap, tool_name)``)."""

    def __init__(self) -> None:
        self._granted: set[tuple[str, str]] = set()

    def grant(self, agent_id: str, capability: str) -> None:
        self._granted.add((agent_id, capability))

    def check(self, agent_id: str, capability: str, tool_name: str) -> bool:
        return (agent_id, capability) in self._granted


class _AdminOnlySSETool(BaseTool):
    tool_id = "admin_only_sse_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="admin_only_sse_tool",
            description="Requires system:admin.",
            parameters={"type": "object", "properties": {}},
            required_capabilities=["system:admin"],
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="admin_only_sse_tool", content="executed!", success=True
        )


class _SingleToolCallEngine:
    """Requests one named tool call, then observes the tool-role result."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.turns = 0
        self.observed_tool_result = ""

    async def stream_full(self, messages, *, model, **kwargs):
        self.turns += 1
        if self.turns == 1:
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": f"call-{self.tool_name}",
                        "type": "function",
                        "function": {"name": self.tool_name, "arguments": "{}"},
                    }
                ],
                finish_reason="tool_calls",
            )
            return
        tool_messages = [m for m in messages if m.role is Role.TOOL]
        self.observed_tool_result = tool_messages[-1].content
        yield StreamChunk(content="complete")
        yield StreamChunk(finish_reason="stop")


def _app_state(capability_policy) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(memory_files=None, system_prompt=None),
        memory_backend=None,
        channel_backend=None,
        channel_bridge=None,
        _mcp_clients=[],
        _mcp_tools_cache=([], {}),
        capability_policy=capability_policy,
    )


async def _run_and_drain(*, agent_record, engine, app_state):
    from openjarvis.server.agent_manager_routes import _stream_managed_agent

    manager = MagicMock()
    manager.list_messages.return_value = []

    response = await _stream_managed_agent(
        manager=manager,
        agent_record=agent_record,
        user_content="Use the admin-only tool",
        message_id="message-capability",
        engine=engine,
        bus=None,
        app_state=app_state,
    )

    async for _ in response.body_iterator:
        pass


@pytest.mark.asyncio
async def test_sse_blocks_unauthorized_tool_via_capability_policy() -> None:
    """TEST E + TEST F: capability_policy reaches the SSE ToolExecutor and
    actually denies a required_capabilities call the agent's id was never
    granted."""
    ToolRegistry.register_value("admin_only_sse_tool", _AdminOnlySSETool)

    policy = _FakeCapabilityPolicy()
    # Deliberately no grant for this agent's id.

    engine = _SingleToolCallEngine("admin_only_sse_tool")
    agent_record = {
        "id": "agent-capability-sse",
        "name": "Capability SSE Agent",
        "agent_type": "simple",
        "config": {
            "model": "test-model",
            "max_turns": 3,
            "tools": ["admin_only_sse_tool"],
        },
    }

    await _run_and_drain(
        agent_record=agent_record,
        engine=engine,
        app_state=_app_state(policy),
    )

    assert engine.turns == 2
    assert "denied" in engine.observed_tool_result.lower()
    assert "executed!" not in engine.observed_tool_result


@pytest.mark.asyncio
async def test_sse_allows_authorized_tool_via_capability_policy() -> None:
    """TEST G: once the agent's own id is explicitly granted the
    capability, the same tool executes normally through SSE."""
    ToolRegistry.register_value("admin_only_sse_tool", _AdminOnlySSETool)

    agent_id = "agent-capability-sse-granted"
    policy = _FakeCapabilityPolicy()
    policy.grant(agent_id, "system:admin")

    engine = _SingleToolCallEngine("admin_only_sse_tool")
    agent_record = {
        "id": agent_id,
        "name": "Capability SSE Agent Granted",
        "agent_type": "simple",
        "config": {
            "model": "test-model",
            "max_turns": 3,
            "tools": ["admin_only_sse_tool"],
        },
    }

    await _run_and_drain(
        agent_record=agent_record,
        engine=engine,
        app_state=_app_state(policy),
    )

    assert engine.turns == 2
    assert "executed!" in engine.observed_tool_result


@pytest.mark.asyncio
async def test_sse_no_capability_policy_when_app_state_has_none() -> None:
    """Regression guard: no app_state.capability_policy (or none of it set,
    matching a server booted with security.capabilities.enabled=False)
    must not block a gated tool -- exactly the pre-4Q.6 default-open
    behavior, now reachable through the same wiring instead of never
    reaching a policy check at all."""
    ToolRegistry.register_value("admin_only_sse_tool", _AdminOnlySSETool)

    engine = _SingleToolCallEngine("admin_only_sse_tool")
    agent_record = {
        "id": "agent-capability-sse-none",
        "name": "Capability SSE Agent None",
        "agent_type": "simple",
        "config": {
            "model": "test-model",
            "max_turns": 3,
            "tools": ["admin_only_sse_tool"],
        },
    }

    await _run_and_drain(
        agent_record=agent_record,
        engine=engine,
        app_state=_app_state(None),
    )

    assert engine.turns == 2
    assert "executed!" in engine.observed_tool_result
