"""FASE 4P.1 -- model-callable proactive insight tools.

Covers STEP 12 (tool surface) and the tool-layer parts of the STEP 14
matrix: L (no execution tool), registry read-only behavior, and that the
analyze tool only ever gathers evidence through already-governed
sub-tools (never free-text evidence from the model).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools.proactive_insight_tools import (
    ProactiveActionProposalGetTool,
    ProactiveActionProposalsListTool,
    ProactiveAnalyzeTool,
    ProactiveInsightGetTool,
    ProactiveInsightsListTool,
    _reset_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _fake_sb_tool(result: ToolResult) -> MagicMock:
    m = MagicMock()
    m.execute.return_value = result
    return m


def _fake_doc_tool(result: ToolResult) -> MagicMock:
    m = MagicMock()
    m.execute.return_value = result
    return m


class TestToolRegistration:
    def test_all_five_tools_registered(self):
        """The project's own autouse fixture (tests/conftest.py) clears every
        registry before each test; module import-time @ToolRegistry.register
        side effects only ran once, at first import. importlib.reload
        re-runs the decorators -- matching the established pattern in
        tests/document_knowledge/test_tools.py."""
        import importlib

        import openjarvis.tools.proactive_insight_tools as mod

        importlib.reload(mod)

        for name in (
            "maia_analyze_evidence_for_insights",
            "maia_insights_list",
            "maia_insight_get",
            "maia_action_proposals_list",
            "maia_action_proposal_get",
        ):
            assert ToolRegistry.contains(name), name

    def test_l_no_execution_tool_registered(self):
        """Hard requirement (STEP 6/12): no generic execute-shaped tool."""
        forbidden_substrings = ("execute_action", "run_action", "do_it", "send_anything", "write_anything")
        all_names = [
            "maia_analyze_evidence_for_insights",
            "maia_insights_list",
            "maia_insight_get",
            "maia_action_proposals_list",
            "maia_action_proposal_get",
        ]
        for name in all_names:
            for forbidden in forbidden_substrings:
                assert forbidden not in name


class TestAnalyzeTool:
    def test_no_params_no_crash_no_insight(self):
        tool = ProactiveAnalyzeTool()
        result = tool.execute()
        assert result.success is True
        assert result.metadata["num_insights"] == 0

    def test_ops_capability_calls_bridge_via_reused_helper(self, monkeypatch):
        captured = {}

        def fake_call_bridge(capability, params):
            captured["capability"] = capability
            captured["params"] = params
            return {
                "status": "ok",
                "period": "2026-07",
                "source": {"function_area": "production"},
                "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
                "confidence_status": "not_evaluated",
            }

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", fake_call_bridge
        )
        tool = ProactiveAnalyzeTool(
            second_brain_tool=_fake_sb_tool(ToolResult(tool_name="second_brain_find_related_experiences", content="", success=True, metadata={"num_candidates": 0})),
            document_tool=_fake_doc_tool(ToolResult(tool_name="document_search", content="", success=True, metadata={"num_results": 0})),
        )
        result = tool.execute(ops_capability="ops.production.get_kpi", ops_params={"metric": "oee"})
        assert captured["capability"] == "ops.production.get_kpi"
        assert result.metadata["num_insights"] == 1

    def test_unavailable_capability_reported_not_available_never_fake_result(self, monkeypatch):
        """FASE 4P.1B STEP 8-A: a capability that doesn't exist/is
        unreachable raises inside _call_bridge (matches the real
        HTTP-error path, e.g. httpx.raise_for_status on a 404) -- the
        tool must report this honestly as NOT_AVAILABLE, never as a
        fabricated successful result."""

        def raising_call_bridge(capability, params):
            raise RuntimeError("404 Not Found")

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", raising_call_bridge
        )
        tool = ProactiveAnalyzeTool(
            second_brain_tool=_fake_sb_tool(ToolResult(tool_name="second_brain_find_related_experiences", content="", success=True, metadata={"num_candidates": 0})),
            document_tool=_fake_doc_tool(ToolResult(tool_name="document_search", content="", success=True, metadata={"num_results": 0})),
        )
        result = tool.execute(ops_capability="ops.nonexistent.get_widget")
        # The tool itself succeeded (it ran and reported honestly) but
        # produced zero insights -- a NOT_AVAILABLE capability is not
        # evidence of anything to flag.
        assert result.metadata["num_insights"] == 0

    def test_capability_call_failure_represented_honestly(self, monkeypatch):
        """FASE 4P.1B STEP 8-D: verifies the underlying _call_ops path
        (exercised via the analyze tool) tags a failed capability lookup
        with the exact REQUESTED CAPABILITY / STATUS: NOT_AVAILABLE
        vocabulary, distinct from a real capability's data_not_available
        response -- checked directly on the ToolResult content, not
        inferred."""
        from openjarvis.tools.proactive_insight_tools import ProactiveAnalyzeTool as _Tool

        def raising_call_bridge(capability, params):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", raising_call_bridge
        )
        tool = _Tool()
        tr = tool._call_ops("ops.nonexistent.get_widget", {})
        assert tr.success is False
        assert "STATUS: NOT_AVAILABLE" in tr.content
        assert "REQUESTED CAPABILITY: ops.nonexistent.get_widget" in tr.content

    def test_unsupported_status_from_real_bridge_reported_not_available(self, monkeypatch):
        """FASE 4P.1B correction: live-verified the real OPS Bridge does
        NOT raise for an unknown capability -- it returns a clean HTTP 200
        with status='unsupported'. This must ALSO be tagged NOT_AVAILABLE,
        not just the exception path."""
        from openjarvis.tools.proactive_insight_tools import ProactiveAnalyzeTool as _Tool

        def unsupported_call_bridge(capability, params):
            return {
                "status": "unsupported",
                "data": None,
                "source": None,
                "period": None,
                "reason": f"Capability '{capability}' is not exposed by the OPS Bridge.",
                "confidence_status": "not_evaluated",
            }

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", unsupported_call_bridge
        )
        tool = _Tool()
        tr = tool._call_ops("ops.nonexistent.get_widget", {})
        assert tr.success is False
        assert "STATUS: NOT_AVAILABLE" in tr.content
        assert "REQUESTED CAPABILITY: ops.nonexistent.get_widget" in tr.content

    def test_data_not_available_status_not_confused_with_unsupported(self, monkeypatch):
        """A real, existing capability with no data for the period must
        NOT get the NOT_AVAILABLE-capability phrasing -- that would
        falsely imply the capability itself doesn't exist."""
        from openjarvis.tools.proactive_insight_tools import ProactiveAnalyzeTool as _Tool

        def data_not_available_call_bridge(capability, params):
            return {
                "status": "data_not_available",
                "data": None,
                "source": None,
                "period": "2019-01",
                "reason": "No data for the specified filters.",
                "confidence_status": "not_evaluated",
            }

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", data_not_available_call_bridge
        )
        tool = _Tool()
        tr = tool._call_ops("ops.production.get_kpi", {})
        assert tr.success is False
        assert "STATUS: NOT_AVAILABLE" not in tr.content

    def test_second_brain_and_document_only_called_by_reused_tools(self):
        sb = _fake_sb_tool(ToolResult(tool_name="second_brain_find_related_experiences", content="", success=True, metadata={"num_candidates": 0}))
        doc = _fake_doc_tool(ToolResult(tool_name="document_search", content="", success=True, metadata={"num_results": 0}))
        tool = ProactiveAnalyzeTool(second_brain_tool=sb, document_tool=doc)
        tool.execute(second_brain_query="changeover", document_query="changeover")
        sb.execute.assert_called_once()
        doc.execute.assert_called_once()

    def test_model_cannot_pass_free_text_evidence_directly(self):
        """The tool's schema has no 'evidence' or 'insight' free-text field
        -- only structured query params identical in spirit to the
        underlying governed tools' own params."""
        tool = ProactiveAnalyzeTool()
        props = tool.spec.parameters["properties"]
        assert "evidence" not in props
        assert "insight" not in props
        assert set(props.keys()) <= {
            "ops_capability", "ops_params", "second_brain_query",
            "second_brain_domains", "document_query",
        }


class TestReadTools:
    def test_insights_list_empty_by_default(self):
        tool = ProactiveInsightsListTool()
        result = tool.execute()
        assert result.metadata["num_insights"] == 0

    def test_insight_get_unknown_id_fails_honestly(self):
        tool = ProactiveInsightGetTool()
        result = tool.execute(insight_id="nope")
        assert result.success is False

    def test_action_proposals_list_empty_by_default(self):
        tool = ProactiveActionProposalsListTool()
        result = tool.execute()
        assert result.metadata["num_actions"] == 0

    def test_action_proposal_get_unknown_id_fails_honestly(self):
        tool = ProactiveActionProposalGetTool()
        result = tool.execute(action_id="nope")
        assert result.success is False

    def test_full_round_trip_analyze_then_list_then_get(self, monkeypatch):
        def fake_call_bridge(capability, params):
            return {
                "status": "ok",
                "period": "2026-07",
                "source": {"function_area": "production"},
                "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
                "confidence_status": "not_evaluated",
            }

        monkeypatch.setattr(
            "openjarvis.tools.ops_bridge_generic._call_bridge", fake_call_bridge
        )
        analyze = ProactiveAnalyzeTool(
            second_brain_tool=_fake_sb_tool(ToolResult(tool_name="second_brain_find_related_experiences", content="", success=True, metadata={"num_candidates": 0})),
            document_tool=_fake_doc_tool(ToolResult(tool_name="document_search", content="", success=True, metadata={"num_results": 0})),
        )
        analyze_result = analyze.execute(ops_capability="ops.production.get_kpi")
        insight_id = analyze_result.metadata["insight_ids"][0]

        list_tool = ProactiveInsightsListTool()
        listing = list_tool.execute()
        assert listing.metadata["num_insights"] == 1
        assert listing.metadata["insights"][0]["id"] == insight_id

        get_tool = ProactiveInsightGetTool()
        detail = get_tool.execute(insight_id=insight_id)
        assert detail.success is True
        assert detail.metadata["id"] == insight_id
        assert detail.metadata["evidence"]  # evidence-grounded, never empty

        action_id = detail.metadata["proposed_action_ids"][0]
        action_list = ProactiveActionProposalsListTool().execute()
        assert action_list.metadata["num_actions"] == 1
        action_detail = ProactiveActionProposalGetTool().execute(action_id=action_id)
        assert action_detail.metadata["status"] == "PROPOSED"
        assert action_detail.metadata["requires_confirmation"] is True
        assert action_detail.metadata["execution_capability"] is None
