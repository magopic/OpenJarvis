"""M3.2C — a tool used successfully must stay offerable in the follow-up.

M3.2B proved the failure end-to-end. Turn 1 ("Qual e' l'OEE dell'ultimo
periodo disponibile?") routes `ops_dynamic_production_get_kpi` in with its
full schema and succeeds. Turn 2 ("E rispetto all'anno precedente?") is
anaphoric -- it tokenizes to {rispetto, anno, precedente}, none of which
appear in any OPS tool's name or description -- so every OPS tool scores 0
and the model is handed only the registry fallback. It then calls the tool
it remembers from Turn 1 without ever having been given its schema, and
guesses the arguments: `period` as a string, `kpi` instead of `metric`.

The router is not wrong to narrow; it is wrong to decide availability from
the current utterance alone, because follow-ups are anaphoric by nature.
The fix keeps the router a pure stateless narrowing function and gives the
Orchestrator -- which already owns per-conversation state -- a small,
bounded memory of what actually worked.

Security is enforced by a single rule, tested below: a tool can only be
sticky if it is still a member of `self._tools`, the authorized set for
this session. Everything the phase forbids (owner_only capabilities, the
internal-only registry tool, anything not chat-authorized, tools the model
merely remembers) is outside that set by construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.orchestrator import _STICKY_TOOL_LIMIT, OrchestratorAgent
from openjarvis.core.types import Conversation, Message, Role, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_PRODUCTION = "ops_dynamic_production_get_kpi"
_WAREHOUSE = "ops_dynamic_warehouse_get_status"
_ACTIONS = "ops_dynamic_actions_list"
_REGISTRY = "ops_dynamic_registry_list_capabilities"

# The real published schema, as M3.2A-OPS deployed it to production.
_PERIOD_SCHEMA = {
    "type": "object",
    "description": "Optional { year?, month? }. Omitted -> latest available month.",
    "properties": {
        "year": {"type": "integer", "description": "Four-digit year, e.g. 2026."},
        "month": {
            "type": "integer",
            "description": "Month number, 1-12.",
            "minimum": 1,
            "maximum": 12,
        },
    },
}


def _ops_tool(name: str, description: str, *, succeeds: bool = True, params=None) -> BaseTool:
    class _Stub(BaseTool):
        tool_id = name

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=name,
                description=description,
                parameters=params or {"type": "object", "properties": {}},
            )

        def execute(self, **kw) -> ToolResult:
            return ToolResult(tool_name=name, content="{}", success=succeeds)

    return _Stub()


def _production_tool(**kw) -> BaseTool:
    return _ops_tool(
        _PRODUCTION,
        "Get a production KPI (e.g. OEE) for a period.",
        params={
            "type": "object",
            "properties": {
                "period": _PERIOD_SCHEMA,
                "metric": {
                    "type": "string",
                    "enum": ["oee", "produttivita", "attivita", "resa", "ore_fermi", "kg_prodotti"],
                },
            },
        },
        **kw,
    )


def _engine_calling(tool_name: str, args: str = "{}") -> MagicMock:
    """An engine that calls `tool_name` once, then answers."""
    engine = MagicMock()
    engine.generate.side_effect = [
        {
            "content": "",
            "tool_calls": [{"id": "c1", "name": tool_name, "arguments": args}],
            "finish_reason": "tool_calls",
            "usage": {},
        },
        {"content": "Done.", "finish_reason": "stop", "usage": {}},
    ]
    return engine


def _engine_no_tools(text: str = "Done.") -> MagicMock:
    engine = MagicMock()
    engine.generate.return_value = {"content": text, "finish_reason": "stop", "usage": {}}
    return engine


def _tools_sent(engine: MagicMock, call_index: int = 0) -> list[str]:
    """Tool names actually handed to the engine on a given generate() call."""
    _, kwargs = engine.generate.call_args_list[call_index]
    return [t["function"]["name"] for t in (kwargs.get("tools") or [])]


def _followup_context() -> AgentContext:
    """A non-empty conversation, i.e. a genuine follow-up turn."""
    conv = Conversation()
    conv.add(Message(role=Role.USER, content="Qual e' l'OEE dell'ultimo periodo disponibile?"))
    conv.add(Message(role=Role.ASSISTANT, content="OEE 88,61%."))
    return AgentContext(conversation=conv)


_ANAPHORIC = "E rispetto all'anno precedente?"


class TestMultiTurnContinuity:
    def test_5_turn_1_uses_the_tool_successfully(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION), "m", tools=[_production_tool()]
        )
        result = agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")
        assert [r.tool_name for r in result.tool_results] == [_PRODUCTION]
        assert result.tool_results[0].success is True

    def test_6_anaphoric_follow_up_keeps_the_tool_available(self) -> None:
        """The defect this phase exists for."""
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION), "m", tools=[_production_tool()]
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _PRODUCTION in _tools_sent(engine2)

    def test_7_the_sticky_tool_appears_exactly_once(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION), "m", tools=[_production_tool()]
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        # A query that ALSO routes the tool in normally -- union must dedupe.
        agent.run("produzione OEE ancora", context=_followup_context())
        sent = _tools_sent(engine2)
        assert sent.count(_PRODUCTION) == 1

    def test_8_a_failed_tool_call_does_not_become_sticky(self) -> None:
        failing = _ops_tool(_PRODUCTION, "Get a production KPI (e.g. OEE).", succeeds=False)
        agent = OrchestratorAgent(_engine_calling(_PRODUCTION), "m", tools=[failing])
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _PRODUCTION not in _tools_sent(engine2)

    def test_9_a_tool_never_invoked_does_not_become_sticky(self) -> None:
        agent = OrchestratorAgent(
            _engine_no_tools(),
            "m",
            tools=[_production_tool(), _ops_tool(_WAREHOUSE, "Get warehouse inventory status.")],
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _tools_sent(engine2) == []

    def test_10_the_sticky_set_is_bounded(self) -> None:
        many = [
            _ops_tool(f"ops_dynamic_kpi_{i}", "Get a production KPI such as OEE.")
            for i in range(_STICKY_TOOL_LIMIT + 5)
        ]
        agent = OrchestratorAgent(_engine_no_tools(), "m", tools=many)
        for t in many:
            agent._remember_successful_tools([ToolResult(tool_name=t.spec.name, content="", success=True)])
        assert len(agent._recent_successful_tools) == _STICKY_TOOL_LIMIT
        # Recent-first: the newest survives, the oldest is evicted.
        assert agent._recent_successful_tools[0] == many[-1].spec.name
        assert many[0].spec.name not in agent._recent_successful_tools

    def test_11_a_new_domain_is_added_without_immediately_losing_the_first(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION),
            "m",
            tools=[_production_tool(), _ops_tool(_WAREHOUSE, "Get warehouse inventory status.")],
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")
        agent._engine = _engine_calling(_WAREHOUSE)
        agent.run("E il magazzino?", context=_followup_context())

        engine3 = _engine_no_tools()
        agent._engine = engine3
        agent.run(_ANAPHORIC, context=_followup_context())
        sent = _tools_sent(engine3)
        assert _PRODUCTION in sent and _WAREHOUSE in sent

    def test_12_a_new_conversation_resets_the_sticky_state(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION), "m", tools=[_production_tool()]
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")
        assert agent._recent_successful_tools

        engine2 = _engine_no_tools()
        agent._engine = engine2
        # No context (and an empty conversation) means a fresh conversation,
        # which is exactly what `/clear` produces in the chat CLI.
        agent.run(_ANAPHORIC)
        assert agent._recent_successful_tools == []
        assert _PRODUCTION not in _tools_sent(engine2)


class TestStickySecurity:
    """A tool may only become sticky if it is still in the authorized set."""

    def test_13_an_owner_only_tool_cannot_become_sticky(self) -> None:
        """`ops.actions.list` is owner_only, so M3.1A keeps it out of the
        chat-facing set entirely -- it is never in `self._tools`, and a
        remembered name can never put it back."""
        agent = OrchestratorAgent(
            _engine_no_tools(), "m", tools=[_production_tool()]
        )
        agent._remember_successful_tools(
            [ToolResult(tool_name=_ACTIONS, content="", success=True)]
        )
        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _ACTIONS not in _tools_sent(engine2)
        assert _ACTIONS not in agent._recent_successful_tools

    def test_14_the_internal_only_registry_tool_cannot_become_sticky(self) -> None:
        agent = OrchestratorAgent(
            _engine_no_tools(), "m", tools=[_production_tool()]
        )
        agent._remember_successful_tools(
            [ToolResult(tool_name=_REGISTRY, content="", success=True)]
        )
        assert _REGISTRY not in agent._recent_successful_tools

    def test_15_a_non_chat_authorized_tool_cannot_re_enter_via_sticky(self) -> None:
        """Even a name recorded while the tool was authorized cannot survive
        the tool leaving the authorized set."""
        agent = OrchestratorAgent(
            _engine_calling(_WAREHOUSE),
            "m",
            tools=[_ops_tool(_WAREHOUSE, "Get warehouse inventory status.")],
        )
        agent.run("magazzino")
        assert _WAREHOUSE in agent._recent_successful_tools

        agent._tools = [_production_tool()]  # capability revoked for this session
        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _WAREHOUSE not in _tools_sent(engine2)

    def test_16_the_registry_fallback_is_not_promoted_to_a_sticky_business_tool(self) -> None:
        """The fallback is resolved from ToolRegistry, not from `self._tools`,
        so using it can never make it sticky."""
        agent = OrchestratorAgent(
            _engine_calling(_REGISTRY), "m", tools=[_production_tool()]
        )
        agent.run("zzz qqq xxx")
        assert _REGISTRY not in agent._recent_successful_tools


class TestOpsCaseEndToEnd:
    def test_17_and_18_the_M32B_scenario_now_keeps_production_available(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION, '{"metric": "oee"}'),
            "m",
            tools=[_production_tool()],
        )
        turn1 = agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")
        assert _PRODUCTION in _tools_sent(agent._engine)
        assert turn1.tool_results[0].success is True

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        assert _PRODUCTION in _tools_sent(engine2)

    def test_19_and_20_the_nested_period_schema_and_enum_survive_the_union(self) -> None:
        agent = OrchestratorAgent(
            _engine_calling(_PRODUCTION), "m", tools=[_production_tool()]
        )
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())

        _, kwargs = engine2.generate.call_args_list[0]
        sent = {t["function"]["name"]: t["function"]["parameters"] for t in kwargs["tools"]}
        period = sent[_PRODUCTION]["properties"]["period"]
        assert period["type"] == "object"
        assert period["properties"]["year"]["type"] == "integer"
        assert period["properties"]["month"]["type"] == "integer"
        assert period["properties"]["month"]["minimum"] == 1
        assert period["properties"]["month"]["maximum"] == 12
        assert sent[_PRODUCTION]["properties"]["metric"]["enum"][0] == "oee"

    def test_21_sticky_never_widens_the_authorized_set(self) -> None:
        """The union can only ever re-offer tools already in `self._tools`,
        so the auto-enabled baseline cannot grow."""
        tools = [_production_tool(), _ops_tool(_WAREHOUSE, "Get warehouse inventory status.")]
        agent = OrchestratorAgent(_engine_calling(_PRODUCTION), "m", tools=tools)
        agent.run("Qual e' l'OEE dell'ultimo periodo disponibile?")

        engine2 = _engine_no_tools()
        agent._engine = engine2
        agent.run(_ANAPHORIC, context=_followup_context())
        authorized = {t.spec.name for t in tools}
        assert set(_tools_sent(engine2)).issubset(authorized)
