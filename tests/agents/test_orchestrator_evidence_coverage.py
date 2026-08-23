"""FASE 4O.6A -- bounded evidence-coverage nudge in OrchestratorAgent.

10-scenario matrix (A-J) for the one addition this phase makes to
``_run_function_calling``: before accepting a final answer, if an
always-on evidence family (Second Brain / Document Knowledge) is
available this session but was never attempted in any turn, the model
gets exactly one non-forcing nudge to consider it. Covers single/dual/
triple-source intents, the one-shot bound, and a selectivity check
(the nudge must never cause tool-spam on a genuinely single-source
question). Mirrors tests/agents/test_orchestrator.py's stub/mock style.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# ---------------------------------------------------------------------------
# Stub tools -- one per evidence family, named exactly like the real tools
# so they fall into operational_evidence.py's own frozensets.
# ---------------------------------------------------------------------------


class _OpsStub(BaseTool):
    tool_id = "ops_dynamic_production_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ops_dynamic_production_get_kpi",
            description="Get a production KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="ops_dynamic_production_get_kpi",
            content="status=ok data={'metric': 'oee', 'value': 90.0}",
            success=True,
        )


class _SecondBrainStub(BaseTool):
    tool_id = "second_brain_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_search",
            description="Search Second Brain.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="second_brain_search",
            content="No matching Second Brain entries found.",
            success=True,
        )


class _DocumentStub(BaseTool):
    tool_id = "document_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_search",
            description="Search Document Knowledge.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="document_search",
            content="No matching documents found in the authorized workspace.",
            success=True,
        )


def _turn(content: str = "", tool_calls: list | None = None, finish: str = "stop") -> dict:
    d = {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
        "finish_reason": finish,
    }
    if tool_calls:
        d["tool_calls"] = tool_calls
        d["finish_reason"] = "tool_calls"
    return d


def _call(name: str, call_id: str = "c1", arguments: str = "{}") -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


# ---------------------------------------------------------------------------
# A-J
# ---------------------------------------------------------------------------


class TestEvidenceCoverageMatrix:
    def test_a_single_source_ops_only_nudge_then_decline(self):
        """A: OPS-only question. Nudge fires once (SB/Doc unattempted), model
        declines and finalizes -- no extra tool calls beyond the one OPS call."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("draft answer, discarded because the coverage check fires"),
            _turn("The OEE is 90.0%."),  # sees nudge, declines, finalizes
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        result = agent.run("What is the current OEE?")
        assert result.content == "The OEE is 90.0%."
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "ops_dynamic_production_get_kpi"
        # One nudge message was injected before the final answer.
        third_call_messages = engine.generate.call_args_list[2][0][0]
        assert any(
            "[EVIDENCE COVERAGE CHECK]" in (m.content or "") for m in third_call_messages
        )

    def test_b_single_source_second_brain_only_nudges_document_only(self):
        """B: Second Brain attempted, OPS+Document never offered/attempted --
        nudge (if any) must name only the family actually available and unattempted."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search")]),
            _turn("draft, discarded"),
            _turn("No precedent found."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_SecondBrainStub(), _DocumentStub()],
        )
        agent.run("Has this happened before?")
        third_call_messages = engine.generate.call_args_list[2][0][0]
        nudge = next(
            (m.content for m in third_call_messages if "[EVIDENCE COVERAGE CHECK]" in (m.content or "")),
            None,
        )
        assert nudge is not None
        assert "document evidence" in nudge
        assert "historical experience" not in nudge

    def test_c_dual_source_ops_plus_second_brain_nudges_document_only(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[
                _call("ops_dynamic_production_get_kpi", "c1"),
                _call("second_brain_search", "c2"),
            ]),
            _turn("draft, discarded"),
            _turn("Current value and no precedent."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        agent.run("Current status and precedent?")
        third_call_messages = engine.generate.call_args_list[2][0][0]
        nudge = next(
            (m.content for m in third_call_messages if "[EVIDENCE COVERAGE CHECK]" in (m.content or "")),
            None,
        )
        assert nudge is not None
        assert "document evidence" in nudge
        assert "historical experience" not in nudge

    def test_d_dual_source_ops_plus_document_nudges_history_only(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[
                _call("ops_dynamic_production_get_kpi", "c1"),
                _call("document_search", "c2"),
            ]),
            _turn("draft, discarded"),
            _turn("Current value and procedure text."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        agent.run("Current status and what does the procedure say?")
        third_call_messages = engine.generate.call_args_list[2][0][0]
        nudge = next(
            (m.content for m in third_call_messages if "[EVIDENCE COVERAGE CHECK]" in (m.content or "")),
            None,
        )
        assert nudge is not None
        assert "historical experience" in nudge
        assert "document evidence" not in nudge

    def test_e_triple_source_in_one_turn_no_nudge_needed(self):
        """E: all three families attempted in the SAME turn before finalizing --
        no coverage gap exists, so no nudge is injected and the answer is
        immediate (matches the common live-tested pattern)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[
                _call("ops_dynamic_production_get_kpi", "c1"),
                _call("second_brain_search", "c2"),
                _call("document_search", "c3"),
            ]),
            _turn("Full three-source answer."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        result = agent.run("Current, precedent, and procedure?")
        assert result.turns == 2
        assert len(result.tool_results) == 3
        second_call_messages = engine.generate.call_args_list[1][0][0]
        assert not any(
            "[EVIDENCE COVERAGE CHECK]" in (m.content or "") for m in second_call_messages
        )

    def test_f_triple_source_completed_after_one_nudge(self):
        """F: model attempts OPS first, gets nudged, THEN attempts SB+Doc,
        then finalizes -- coverage achieved across turns via the one bounded pass."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn(tool_calls=[
                _call("second_brain_search", "c2"),
                _call("document_search", "c3"),
            ]),
            _turn("Now-complete three-source answer."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        result = agent.run("Current, precedent, and procedure?")
        assert result.content == "Now-complete three-source answer."
        assert len(result.tool_results) == 3
        names = {tr.tool_name for tr in result.tool_results}
        assert names == {
            "ops_dynamic_production_get_kpi",
            "second_brain_search",
            "document_search",
        }

    def test_g_nudge_fires_exactly_once_bounded(self):
        """G: model ignores the nudge and keeps not calling the missing
        tools -- the nudge must not repeat (no infinite loop)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("draft, discarded by the coverage check"),
            _turn("Declining to check further, here is the answer anyway."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        result = agent.run("What is the current OEE?")
        assert result.content == "Declining to check further, here is the answer anyway."
        # Exactly 3 generate() calls: the tool-call turn, the turn whose
        # no-tool-call response triggers (and consumes) the one bounded
        # nudge, and the post-nudge final turn -- proves the nudge fired
        # exactly once, not repeatedly (no infinite loop).
        assert engine.generate.call_count == 3

    def test_h_families_not_offered_never_nudged(self):
        """H: session has ONLY an OPS tool registered -- Second Brain /
        Document families are not available at all, so the coverage check
        must never mention them (nothing to nudge about)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("The OEE is 90.0%."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        result = agent.run("What is the current OEE?")
        assert result.content == "The OEE is 90.0%."
        assert engine.generate.call_count == 2  # no nudge turn injected

    def test_i_no_tools_at_all_no_crash_no_nudge(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Hello!")
        agent = OrchestratorAgent(engine, "test-model", tools=[])
        result = agent.run("Hi")
        assert result.content == "Hello!"
        assert result.turns == 1

    def test_j_selectivity_no_tool_spam_after_decline(self):
        """J: selective composition, not tool spam -- after declining the
        nudge, the model's final tool_results must contain ONLY the source
        it actually needed, never a speculative call to the others."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("draft, discarded"),
            _turn("The OEE is 90.0%, no need to check history or documents."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model",
            tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()],
        )
        result = agent.run("What is the current OEE?")
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "ops_dynamic_production_get_kpi"
