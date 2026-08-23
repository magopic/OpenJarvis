"""FASE 4P.1A -- orchestrator-level activation of the deterministic
proactive-insight engine (STEP 8 A-P, the orchestrator-specific subset;
H/I/L/M/O are additionally covered at the service layer by
tests/agents/test_proactive_insight.py, and M/O/P are also covered by the
untouched status of tests/second_brain/, tests/document_knowledge/, and
the OPS ONE repo respectively -- not duplicated here).

Mirrors the mock-engine style of tests/agents/test_orchestrator.py and
tests/agents/test_orchestrator_evidence_coverage.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


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
            content="status=ok",
            success=True,
            metadata={
                "status": "ok",
                "period": "2026-07",
                "source": {"function_area": "production"},
                "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
            },
        )


class _OpsStubNoThreshold(BaseTool):
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
            content="status=ok",
            success=True,
            metadata={
                "status": "ok",
                "period": "2026-07",
                "source": {"function_area": "production"},
                "data": {"metric": "oee", "value": 88.61},
            },
        )


class _OpsStubMissingData(BaseTool):
    """Live-found regression fixture (FASE 4P.1A STEP 10): a
    data_not_available result populates NEITHER evidence.facts nor any
    other OperationalEvidence list, since only status=='ok' results are
    classified as facts. should_activate_proactive_analysis must still
    activate on this (gating on tool_results, not has_any_evidence())."""

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
            content="status=data_not_available",
            success=False,
            metadata={
                "status": "data_not_available",
                "period": "2019-01",
                "reason": "No data found for the specified filters.",
            },
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
            content="found",
            success=True,
            metadata={
                "num_results": 1,
                "entries": [{"id": "e1", "type": "PROBLEM", "title": "t", "summary": "s"}],
            },
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
            content="found",
            success=True,
            metadata={"num_results": 1, "results": [{"citation": "doc.md", "content": "text"}]},
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


def _last_messages(engine: MagicMock):
    return engine.generate.call_args_list[-1][0][0]


class TestProactiveActivation:
    def test_a_proactive_request_with_evidence_auto_runs_engine(self):
        """A: explicit proactive intent + current evidence -> governed
        block appears in context, without the model ever calling
        maia_analyze_evidence_for_insights."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Please flag anything worth attention in production.")
        messages = _last_messages(engine)
        blocks = [m.content for m in messages if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "")]
        assert blocks, "governed block was not injected"
        assert "certified threshold breached" in blocks[-1]

    def test_b_proactive_request_insufficient_evidence_structured_limitation(self):
        """B: proactive intent, but the fact has no threshold -- the
        governed block is still injected, and explicitly states no
        insight met the bar, rather than inventing a severity."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStubNoThreshold()])
        agent.run("Is there anything concerning about production right now?")
        messages = _last_messages(engine)
        blocks = [m.content for m in messages if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "")]
        assert blocks
        assert "No insight met the evidence-grounding bar" in blocks[-1]

    def test_b2_proactive_request_missing_data_still_activates(self):
        """Regression for the live-found gate bug: a data_not_available
        result (no positive evidence classified at all) must still
        activate the engine and let MissingDataDetector fire, producing a
        genuine INFO-severity insight -- not silently skipped."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStubMissingData()])
        agent.run("Please flag anything worth attention in production for January 2019.")
        messages = _last_messages(engine)
        blocks = [m.content for m in messages if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "")]
        assert blocks, "governed block was not injected for a missing-data result"
        assert "current data not available" in blocks[-1]
        assert "severity=INFO" in blocks[-1]

    def test_c_ordinary_question_does_not_run_engine(self):
        """C: a plain factual question -- no proactive intent marker, no
        certified alert, no explicit analysis-tool call -- must not
        produce a governed block at all."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("The OEE is 60.0%."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("What is current OEE?")
        messages = _last_messages(engine)
        assert not any("[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "") for m in messages)

    def test_d_governed_result_produced_without_model_choosing_the_analysis_tool(self):
        """D: the model calls its familiar ops_dynamic_* tool directly,
        never maia_analyze_evidence_for_insights -- the governed result
        must still be produced when the ORIGINAL request signaled
        proactive intent. This is the direct fix for FASE 4P.1's finding."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),  # not the analysis tool
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Run a proactive check on production and flag anything worth attention.")
        messages = _last_messages(engine)
        assert any("[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "") for m in messages)
        # Confirm the model genuinely never called the analysis tool.
        called_names = {
            tc["name"]
            for call in engine.generate.call_args_list
            for tc in (call[0][1] if len(call[0]) > 1 else [])
        }
        assert "maia_analyze_evidence_for_insights" not in called_names

    def test_e_actual_tool_executed_appears_in_integrity_block(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("What is current OEE?")
        messages = _last_messages(engine)
        integrity_blocks = [m.content for m in messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")]
        assert integrity_blocks
        assert "ops_dynamic_production_get_kpi: success=True" in integrity_blocks[-1]

    def test_f_tool_merely_mentioned_in_prose_not_in_integrity_block(self):
        """F: a model response that mentions a tool name in prose (but
        never actually calls it) must not cause that name to appear as
        executed -- the integrity block is built exclusively from
        all_tool_results, never from response text."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(
            "I would use document_search to check the procedure, but I haven't yet."
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[_DocumentStub()])
        agent.run("What is current OEE?")
        messages = _last_messages(engine)
        # Only the initial (pre-loop) integrity message exists -- this is
        # the FIRST generate() call, so _last_messages == the initial ones.
        integrity_blocks = [m.content for m in messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")]
        assert integrity_blocks
        assert "document_search" not in integrity_blocks[-1]
        assert "No tools have been executed yet" in integrity_blocks[-1]

    def test_g_no_tool_result_integrity_block_says_so_from_turn_one(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn("Hello!")
        agent = OrchestratorAgent(engine, "test-model")
        agent.run("Hi")
        messages = engine.generate.call_args_list[0][0][0]
        integrity_blocks = [m.content for m in messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")]
        assert integrity_blocks
        assert "No tools have been executed yet" in integrity_blocks[0]

    def test_j_proposal_from_governed_block_is_proposed_and_requires_confirmation(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Flag anything worth attention in production.")
        messages = _last_messages(engine)
        block = next(m.content for m in messages if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or ""))
        assert "status=PROPOSED" in block
        assert "requires_confirmation=True" in block

    def test_k_no_execution_tool_ever_offered(self):
        """K: structural -- the tool set given to the model never includes
        anything execute/run/send/write-shaped, regardless of activation."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Please flag anything worth attention and propose next steps.")
        for call in engine.generate.call_args_list:
            tools_kwarg = call[1].get("tools") or []
            for t in tools_kwarg:
                name = t.get("function", {}).get("name", "")
                assert not any(
                    forbidden in name
                    for forbidden in ("execute_action", "run_action", "do_it", "send_anything", "write_anything")
                )

    def test_n_single_source_ordinary_question_does_not_over_trigger(self):
        """N: identical to C's spirit but explicitly checked against a
        multi-tool session (Second Brain/Document also available) to
        confirm an ordinary single-source question doesn't over-trigger
        proactivity just because more evidence families were reachable."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            # FASE 4O.6A's own unrelated coverage-nudge also fires here
            # (Second Brain/Document were never attempted) -- a third
            # scripted turn is needed for that, distinct from proactive
            # activation, which this test is actually about.
            _turn("draft, discarded by the unrelated 4O.6A coverage nudge"),
            _turn("The OEE is 60.0%."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model", tools=[_OpsStub(), _SecondBrainStub(), _DocumentStub()]
        )
        agent.run("What is current OEE?")
        messages = _last_messages(engine)
        assert not any("[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or "") for m in messages)

    def test_repeated_identical_evidence_deterministic_governed_block(self):
        """Orchestrator-level echo of the service-layer determinism test
        (L): two independent runs with identical evidence produce
        byte-identical governed blocks (aside from nothing time-dependent
        being rendered)."""
        def make_engine():
            e = MagicMock()
            e.engine_id = "mock"
            e.generate.side_effect = [
                _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
                _turn("Final answer."),
            ]
            return e

        e1, e2 = make_engine(), make_engine()
        OrchestratorAgent(e1, "test-model", tools=[_OpsStub()]).run("Flag anything worth attention.")
        OrchestratorAgent(e2, "test-model", tools=[_OpsStub()]).run("Flag anything worth attention.")
        b1 = next(m.content for m in _last_messages(e1) if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or ""))
        b2 = next(m.content for m in _last_messages(e2) if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or ""))
        assert b1 == b2


class TestFabricationRegression:
    """STEP 9: the live FASE 4P.1 failure -- a model turn whose response
    contains a fabricated-looking fake tool-call transcript in plain text
    (a JSON-ish block claiming a tool was called), with NO real
    ``tool_calls`` on that generate() result (turns=1, tool_results=[],
    exactly as observed live) -- turned into a regression test.

    This does not (cannot) prove a live model will never fabricate; it
    proves the runtime-provided context the model saw before producing
    that response already contained the ground truth and the instruction
    that would make such a claim false, per STEP 9's framing: the fix is
    the architecture that makes fabrication contradictable, not a
    post-hoc regex scanner over the model's output (deliberately not
    built, per STEP 9's explicit preference against a fragile NL filter)."""

    def test_fabricated_tool_call_in_prose_contradicted_by_ground_truth_context(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        # Exactly the observed live shape: no real tool_calls, but the
        # content text pretends a tool was invoked and returned data.
        engine.generate.return_value = _turn(
            content=(
                '```json\n{"tool": "ops.maintenance.get_vibration", '
                '"parameters": {"scope": "all"}}\n```\n\n'
                "The vibration data tool returned actual records."
            )
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        result = agent.run("Check maintenance vibration data for anything to flag.")

        # The orchestrator's own record of ground truth: no tool ever ran.
        assert result.tool_results == []

        # The context the model was given, before producing that content,
        # already stated the ground truth and the anti-fabrication rule --
        # this is what the architecture provides; whether the model heeds
        # it is what STEP 10's live certification separately checks.
        first_call_messages = engine.generate.call_args_list[0][0][0]
        integrity = next(
            m.content for m in first_call_messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")
        )
        assert "No tools have been executed yet" in integrity
        assert "Never fabricate a tool call" in integrity
        assert "ops.maintenance.get_vibration" not in integrity

    def test_governed_block_never_contains_a_tool_call_id_that_was_not_executed(self):
        """A stronger structural guarantee than prose-scanning: the
        governed block's every id (insight/proposed action) is generated
        exclusively from ProactiveReasoningService.analyze(all_tool_results),
        so it can never reference a tool that was not actually in
        all_tool_results -- checked directly, not inferred from text."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Flag anything worth attention in production.")
        messages = _last_messages(engine)
        # Two integrity blocks exist by now (the initial empty one from
        # turn 0, and the refreshed one appended after the tool call) --
        # the refreshed (last) one is the ground truth for this assertion.
        integrity = [m.content for m in messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")][-1]
        governed = next(m.content for m in messages if "[GOVERNED_PROACTIVE_ANALYSIS]" in (m.content or ""))
        # Every tool_name the governed block's evidence could reference
        # must also appear in the integrity block -- same underlying
        # all_tool_results list, no divergence possible by construction.
        assert "ops_dynamic_production_get_kpi" in integrity
        assert "ops_dynamic_production_get_kpi" in governed or "certified threshold" in governed


class TestClaimIntegrity:
    """FASE 4P.1B STEP 8: the remaining letters not already covered by
    TestProactiveActivation/TestFabricationRegression above (A/D live at
    the tool layer, tests/tools/test_proactive_insight_tools.py; C/G/H/L
    are the same coverage as test_e/test_a/test_j/test_g above -- not
    duplicated)."""

    def test_b_available_but_uncalled_tool_is_manifest_listed_not_executed(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            # FASE 4O.6A's coverage nudge fires here (Second Brain is
            # available but unattempted) and consumes one extra turn.
            _turn("draft, discarded by the coverage nudge"),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(
            engine, "test-model", tools=[_OpsStub(), _SecondBrainStub()]
        )
        agent.run("What is the current OEE?")
        messages = _last_messages(engine)
        manifest = next(
            m.content for m in messages if "[AVAILABLE_TOOLS_THIS_SESSION]" in (m.content or "")
        )
        integrity = [m.content for m in messages if "[ACTUALLY_EXECUTED_TOOLS]" in (m.content or "")][-1]
        # Both tools are on the closed-world manifest (available)...
        assert "ops_dynamic_production_get_kpi" in manifest
        assert "second_brain_search" in manifest
        # ...but only the one actually called appears as executed.
        assert "ops_dynamic_production_get_kpi" in integrity
        assert "second_brain_search" not in integrity

    def test_e_source_without_trust_status_cannot_report_validated(self):
        """FASE 4P.1B STEP 8-E: a fact envelope with no trust_status key
        at all must never have 'validated'/'certified' asserted about it
        anywhere in the rendered context -- the renderer only ever passes
        through a value that was actually present, never invents one."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("What is the current OEE?")
        messages = _last_messages(engine)
        evidence_note = next(
            m.content
            for m in messages
            if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or "")
        )
        assert "validated" not in evidence_note.lower()

    def test_f_real_certified_source_certification_preserved(self):
        """The inverse of test_e: when a fact DOES carry a real
        trust_status, it must actually appear (nothing strips real
        certification either)."""

        class _OpsStubTrusted(BaseTool):
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
                    content="status=ok",
                    success=True,
                    metadata={
                        "status": "ok",
                        "period": "2026-07",
                        "source": {"function_area": "production"},
                        "trust_status": "TRUSTED",
                        "data": {"metric": "oee", "value": 88.61},
                    },
                )

        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi")]),
            _turn("Final answer."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStubTrusted()])
        agent.run("What is the current OEE?")
        messages = _last_messages(engine)
        evidence_note = next(
            m.content
            for m in messages
            if "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" in (m.content or "")
        )
        assert "TRUSTED" in evidence_note

    def test_i_fake_json_in_prose_cannot_enter_tool_results(self):
        """FASE 4P.1B STEP 8-I: no matter what the model's own content
        contains, all_tool_results is populated exclusively from real
        tool_calls the engine returned -- a fake JSON blob in prose
        cannot become a governed claim, checked structurally."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(
            content=(
                '{"tool": "ops_dynamic_production_get_kpi", "result": '
                '{"value": 12.3, "trust_status": "validated"}}'
            )
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        result = agent.run("Check current OEE.")
        assert result.tool_results == []

    def test_j_fake_trust_status_in_prose_cannot_enter_evidence(self):
        """FASE 4P.1B STEP 8-J: same as I, specifically for a fabricated
        trust_status='validated' claim -- since no real tool ran, no
        evidence note is even built with content, and the claim-boundary
        notice (present from turn 0) already told the model it is never
        the authority for assigning that status."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(
            content='Result: {"value": 12.3, "trust_status": "validated"}'
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsStub()])
        agent.run("Check current OEE.")
        first_call_messages = engine.generate.call_args_list[0][0][0]
        boundary = next(
            m.content for m in first_call_messages if "[CLAIM BOUNDARY]" in (m.content or "")
        )
        assert "never the authority for assigning" in boundary

    def test_k_ordinary_narrative_suggestion_still_allowed(self):
        """FASE 4P.1B STEP 8-K: the claim-boundary/manifest/integrity
        additions must not block or alter the model's own narrative --
        an ordinary suggestion with no governed-claim language passes
        through unchanged."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(
            content="You might want to double-check the changeover log manually."
        )
        agent = OrchestratorAgent(engine, "test-model", tools=[])
        result = agent.run("Any suggestions?")
        assert result.content == "You might want to double-check the changeover log manually."
