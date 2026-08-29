"""Baseline behaviour of the OPS-tool router (FASE 4L.2 invariants).

This file did not exist before M3.2C, which is part of why the follow-up
routing defect went unnoticed: the function that decides what the model can
see each turn had no tests of its own.

These pin the router's ORIGINAL contract, unchanged by M3.2C. The
conversational-continuity behaviour lives in the Orchestrator instead (see
test_conversational_tool_availability.py) precisely so the router stays a
pure, stateless narrowing function.
"""

from __future__ import annotations

from openjarvis.agents.tool_router import select_relevant_tools
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _tool(name: str, description: str = "") -> BaseTool:
    class _Stub(BaseTool):
        tool_id = name

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=name,
                description=description or f"{name} tool.",
                parameters={"type": "object", "properties": {}},
            )

        def execute(self, **params) -> ToolResult:
            return ToolResult(tool_name=name, content="", success=True)

    return _Stub()


def _names(tools) -> list[str]:
    return [t.spec.name for t in tools]


class TestRouterBaseline:
    def test_1_relevant_keyword_selects_the_matching_ops_tool(self) -> None:
        tools = [
            _tool("ops_dynamic_production_get_kpi", "Get a production KPI such as OEE."),
            _tool("ops_dynamic_warehouse_get_status", "Get warehouse inventory status."),
        ]
        selected = _names(select_relevant_tools(tools, "Come va la produzione e l'OEE?"))
        assert "ops_dynamic_production_get_kpi" in selected

    def test_2_zero_scoring_ops_tool_is_not_routed_in(self) -> None:
        """The narrowing itself is intact: an unrelated query does not pull
        in an OPS tool just because it exists."""
        tools = [
            _tool("ops_dynamic_production_get_kpi", "Get a production KPI such as OEE."),
            _tool("ops_dynamic_warehouse_get_status", "Get warehouse inventory status."),
        ]
        selected = _names(select_relevant_tools(tools, "raccontami una barzelletta"))
        assert "ops_dynamic_production_get_kpi" not in selected
        assert "ops_dynamic_warehouse_get_status" not in selected

    def test_3_non_ops_tools_are_always_kept_and_top_n_bounds_only_ops(self) -> None:
        static = [_tool("calculator", "Math."), _tool("web_search", "Search the web.")]
        ops = [
            _tool(f"ops_dynamic_kpi_{i}", "Get a production KPI such as OEE.")
            for i in range(10)
        ]
        selected = _names(select_relevant_tools(static + ops, "production KPI OEE", top_n=3))
        assert "calculator" in selected and "web_search" in selected
        assert len([n for n in selected if n.startswith("ops_dynamic_")]) == 3

    def test_3b_nothing_to_route_returns_the_input_unchanged(self) -> None:
        static = [_tool("calculator"), _tool("web_search")]
        assert select_relevant_tools(static, "anything") == static

    def test_4_registry_fallback_is_offered_when_nothing_scores(self) -> None:
        """Unchanged FASE 4I behaviour: with no relevant OPS tool, the
        infrastructural discovery tool is offered for that turn only.

        Registered directly in ToolRegistry because the fallback is resolved
        from there, not from the passed-in tool list -- which is also why it
        can never become sticky (see the security tests).
        """
        from openjarvis.core.registry import ToolRegistry

        tool_id = "ops_dynamic_registry_list_capabilities"
        if not ToolRegistry.contains(tool_id):
            ToolRegistry.register_value(tool_id, type(_tool(tool_id)))

        tools = [_tool("ops_dynamic_production_get_kpi", "Get a production KPI such as OEE.")]
        selected = _names(select_relevant_tools(tools, "zzz qqq xxx"))
        assert selected == [tool_id]
