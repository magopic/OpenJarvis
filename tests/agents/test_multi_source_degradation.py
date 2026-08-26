"""M1.4 -- Multi-Source Degradation Hardening.

Regression coverage for the documented live failure in
docs/MAIA_MULTI_SOURCE_REASONING_V1.md (KNOWN LIMITATIONS): the model
selected the wrong OPS capability, retried Second Brain repeatedly until
LoopGuard stopped it, and its final turn's content was a malformed,
un-parsed tool-call fragment instead of a coherent answer.

These tests drive the REAL OrchestratorAgent._run_function_calling loop
(mocked engine, real ToolExecutor/LoopGuard/build_evidence) -- not just a
prompt string -- so they exercise the actual runtime path the live
failure went through.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Role, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# ---------------------------------------------------------------------------
# Stub tools -- mirror real OPS/Second Brain/Document Knowledge tool names
# and result shapes exactly (operational_evidence.py's own classification
# is name/shape-driven, so these must match for build_evidence() to
# classify them the same way the live failure's real tools did).
# ---------------------------------------------------------------------------


class _OpsWrongCapabilityStub(BaseTool):
    """Models `ops_dynamic_balance_get_kpi` picked instead of `..._production_get_kpi`
    in the documented live failure -- a plausible but insufficient choice,
    returning `data_not_available` (a LIMITATION, not a FACT, not a crash)."""

    tool_id = "ops_dynamic_balance_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Get the balance KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content="n/a",
            success=True,
            metadata={"status": "data_not_available", "reason": "No data for this period."},
        )


class _OpsCorrectCapabilityStub(BaseTool):
    tool_id = "ops_dynamic_production_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Get the production KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content="87.3",
            success=True,
            metadata={
                "status": "ok",
                "data": {"value": 87.3},
                "source": "live",
                "period": "2026-08",
                "period_status": "REAL_DATA",
            },
        )


class _OpsFailingCapabilityStub(BaseTool):
    """A genuine tool FAILURE (e.g. Bridge unreachable) -- distinct from
    `data_not_available`: no recognizable evidence shape at all, so
    build_evidence() silently ignores it rather than misfiling it as a
    certified limitation. Must never poison other sources' evidence."""

    tool_id = "ops_dynamic_waste_get_kpi"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Get the waste KPI.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content="Connection to OPS Bridge timed out.",
            success=False,
            metadata={},
        )


class _SecondBrainSearchStub(BaseTool):
    tool_id = "second_brain_search"

    def __init__(self, entries: list | None = None):
        self._entries = entries if entries is not None else []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Search Second Brain for historical experience.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=f"{len(self._entries)} result(s)",
            success=True,
            metadata={"num_results": len(self._entries), "entries": self._entries},
        )


class _DocumentSearchStub(BaseTool):
    tool_id = "document_search"

    def __init__(self, results: list | None = None):
        self._results = results if results is not None else []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Search Document Knowledge.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=f"{len(self._results)} result(s)",
            success=True,
            metadata={"num_results": len(self._results), "results": self._results},
        )


def _sb_entry(**overrides) -> dict:
    base = {
        "id": "e1",
        "type": "PROBLEM",
        "title": "Line stoppage",
        "summary": "Line 3 stopped unexpectedly.",
        "trust_status": "TRUSTED",
        "domains": ["production"],
    }
    base.update(overrides)
    return base


def _doc_hit(**overrides) -> dict:
    base = {"filename": "sop.pdf", "page": 4, "text": "Changeover standard is 15 minutes."}
    base.update(overrides)
    return base


def _turn(content: str = "", tool_calls: list | None = None, finish_reason: str | None = None) -> dict:
    d = {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
        "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
    }
    if tool_calls:
        d["tool_calls"] = tool_calls
    return d


def _call(name: str, call_id: str = "c1", arguments: str = "{}") -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


def _is_well_formed_answer(content: str) -> bool:
    """A well-formed final answer: non-empty, no leaked tool-call tags,
    not a bare JSON tool-call object."""
    if not content or not content.strip():
        return False
    if "<tool_call>" in content:
        return False
    stripped = content.strip()
    if stripped.startswith("{") and '"arguments"' in stripped and '"name"' in stripped:
        return False
    return True


# ---------------------------------------------------------------------------
# CASE A -- NORMAL: OPS + Second Brain + Document -> answer
# ---------------------------------------------------------------------------


class TestCaseANormalMultiSource:
    def test_three_sources_batched_produce_coherent_answer(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2", '{"query": "production issue"}'),
                    _call("document_search", "c3", '{"query": "production sop"}'),
                ]
            ),
            _turn("Production is at 87.3%, consistent with past experience and the SOP."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[
                _OpsCorrectCapabilityStub(),
                _SecondBrainSearchStub([_sb_entry()]),
                _DocumentSearchStub([_doc_hit()]),
            ],
        )
        result = agent.run("How is production doing and is this a known issue?")
        assert _is_well_formed_answer(result.content)
        assert len(result.tool_results) == 3
        assert all(tr.success for tr in result.tool_results)


# ---------------------------------------------------------------------------
# CASE B -- ONE EMPTY SOURCE: OPS + empty Second Brain + Document -> answer, no loop
# ---------------------------------------------------------------------------


class TestCaseBOneEmptySource:
    def test_empty_second_brain_does_not_loop_and_still_answers(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2", '{"query": "production issue"}'),
                    _call("document_search", "c3", '{"query": "production sop"}'),
                ]
            ),
            _turn("Production is at 87.3%. No historical precedent was found for this."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[
                _OpsCorrectCapabilityStub(),
                _SecondBrainSearchStub([]),  # empty -- a valid outcome, not a failure
                _DocumentSearchStub([_doc_hit()]),
            ],
        )
        result = agent.run("How is production doing and is this a known issue?")
        assert _is_well_formed_answer(result.content)
        assert engine.generate.call_count == 2  # no retry loop triggered
        sb_calls = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_calls) == 1
        assert sb_calls[0].success is True  # empty result != failure


# ---------------------------------------------------------------------------
# CASE C -- WRONG FIRST OPS TOOL: wrong capability -> recovery/degradation -> valid answer
# ---------------------------------------------------------------------------


class TestCaseCWrongFirstOpsTool:
    def test_wrong_capability_then_correct_one_recovers_cleanly(self):
        """The 'reasonable' recovery path: the model notices the wrong
        capability was insufficient and switches to the right one, with
        no loop and no malformed final answer."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_balance_get_kpi", "c1")]),
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c2")]),
            _turn("Production is at 87.3% (balance KPI had no data for this period)."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_OpsWrongCapabilityStub(), _OpsCorrectCapabilityStub()],
        )
        result = agent.run("What's our production rate?")
        assert _is_well_formed_answer(result.content)
        assert len(result.tool_results) == 2
        assert result.tool_results[0].tool_name == "ops_dynamic_balance_get_kpi"
        assert result.tool_results[1].tool_name == "ops_dynamic_production_get_kpi"

    def test_wrong_capability_degrades_gracefully_without_recovery(self):
        """The sanctioned fallback path (STEP 9's second branch): the
        model never finds the right capability and instead retries
        Second Brain with an IDENTICAL query past LoopGuard's default
        budget -- reproducing the documented 1/6 failure pattern (wrong
        OPS capability -> repeated identical Second Brain retries ->
        LoopGuard intervenes -> a malformed fragment attempt). Must not
        loop forever and must not surface a malformed answer.

        Production default is warn_before_block=True (see
        test_orchestrator.py's own TestLoopGuardParityM13 certification):
        calls 1-3 allowed, call 4 warned-but-executed, call 5+ genuinely
        blocked."""
        identical_sb_call = _call("second_brain_search", "sb", '{"query": "balance issue"}')
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_balance_get_kpi", "c1")]),
            _turn(tool_calls=[identical_sb_call]),  # 1: allowed
            _turn(tool_calls=[identical_sb_call]),  # 2: allowed
            _turn(tool_calls=[identical_sb_call]),  # 3: allowed (boundary)
            _turn(tool_calls=[identical_sb_call]),  # 4: warned, still executes
            _turn(tool_calls=[identical_sb_call]),  # 5: genuinely blocked
            # M1.4 final-answer guard: this malformed fragment must not
            # reach the user -- one bounded recovery nudge fires instead.
            _turn(content='{"name": "second_brain_search", "arguments": {"query": "balance issue"}}'),
            _turn("I could not find a matching KPI for this query with the data available."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_OpsWrongCapabilityStub(), _SecondBrainSearchStub([])],
            max_turns=10,
        )
        result = agent.run("What's our balance situation?")
        assert _is_well_formed_answer(result.content)
        assert "<tool_call>" not in result.content
        assert '"arguments"' not in result.content
        sb_results = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_results) == 5
        for tr in sb_results[:4]:  # 3 tolerated + 1 warned-but-executed
            assert tr.success is True
        assert sb_results[4].success is False  # genuinely blocked
        assert sb_results[4].content.startswith("Loop guard:")


# ---------------------------------------------------------------------------
# CASE D -- MULTIPLE EMPTY SOURCES: OPS only -> valid answer with limitations
# ---------------------------------------------------------------------------


class TestCaseDMultipleEmptySources:
    def test_only_ops_has_evidence_others_empty_still_valid_answer(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2", '{"query": "x"}'),
                    _call("document_search", "c3", '{"query": "x"}'),
                ]
            ),
            _turn(
                "Production is at 87.3%. No historical precedent or document "
                "evidence was found for this question."
            ),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[
                _OpsCorrectCapabilityStub(),
                _SecondBrainSearchStub([]),
                _DocumentSearchStub([]),
            ],
        )
        result = agent.run("How is production doing, any related history or docs?")
        assert _is_well_formed_answer(result.content)
        assert engine.generate.call_count == 2


# ---------------------------------------------------------------------------
# CASE E -- ACTUAL TOOL FAILURE: one tool fails -> other evidence preserved -> valid answer
# ---------------------------------------------------------------------------


class TestCaseEActualToolFailure:
    def test_one_tool_failure_does_not_destroy_other_evidence(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("ops_dynamic_waste_get_kpi", "c2"),
                ]
            ),
            _turn("Production is at 87.3%. Waste data is currently unavailable (connection error)."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_OpsCorrectCapabilityStub(), _OpsFailingCapabilityStub()],
        )
        result = agent.run("How is production and waste doing?")
        assert _is_well_formed_answer(result.content)
        prod = next(tr for tr in result.tool_results if tr.tool_name == "ops_dynamic_production_get_kpi")
        waste = next(tr for tr in result.tool_results if tr.tool_name == "ops_dynamic_waste_get_kpi")
        assert prod.success is True
        assert waste.success is False
        assert "timed out" in waste.content


# ---------------------------------------------------------------------------
# Additional targeted coverage (STEP 12 items not already covered above)
# ---------------------------------------------------------------------------


class TestFinalAnswerGuard:
    def test_leaked_tool_call_tag_is_recovered_as_real_call(self):
        """The engine leaked a genuine tool call as <tool_call> text
        instead of structured tool_calls -- must be recovered and
        executed (through the real LoopGuard-gated path), not shown to
        the user as raw text."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                content='<tool_call>{"name": "ops_dynamic_production_get_kpi", "arguments": {}}</tool_call>'
            ),
            _turn("Production is at 87.3%."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsCorrectCapabilityStub()])
        result = agent.run("How is production?")
        assert _is_well_formed_answer(result.content)
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "ops_dynamic_production_get_kpi"
        assert result.tool_results[0].success is True

    def test_empty_final_content_triggers_nudge_then_safe_fallback(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1")]),
            _turn(content=""),  # malformed: empty, not a real answer
            _turn(content=""),  # still malformed after the one nudge
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsCorrectCapabilityStub()])
        result = agent.run("How is production?")
        assert _is_well_formed_answer(result.content)
        assert "operational fact" in result.content

    def test_malformed_json_fragment_never_reaches_user_verbatim(self):
        """Both coverage families (Second Brain / Document Knowledge) are
        attempted in turn 1 so the FASE 4O.6A evidence-coverage nudge
        never fires here -- this isolates the M1.4 malformed-content
        guard specifically."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2", '{"query": "x"}'),
                    _call("document_search", "c3", '{"query": "x"}'),
                ]
            ),
            _turn(content='{"name": "second_brain_search", "arguments": {"query": "x"}}'),
            _turn(content='{"name": "second_brain_search", "arguments": {"query": "x"}}'),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_OpsCorrectCapabilityStub(), _SecondBrainSearchStub([]), _DocumentSearchStub([])],
        )
        result = agent.run("How is production?")
        assert _is_well_formed_answer(result.content)
        assert '"arguments"' not in result.content

    def test_normal_answer_untouched_by_the_guard(self):
        """A genuine natural-language final answer must pass through
        completely unmodified -- the guard must never fire on real
        prose, even prose that happens to mention braces or the word
        'tool'."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1")]),
            _turn("Production is at 87.3% this period, using the {standard} tool-free calculation."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsCorrectCapabilityStub()])
        result = agent.run("How is production?")
        assert result.content == "Production is at 87.3% this period, using the {standard} tool-free calculation."

    def test_max_turns_exceeded_with_trailing_malformed_content_still_safe(self):
        identical_call = _call("second_brain_search", "sb", '{"query": "x"}')
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(
            content='<tool_call>{"name": "second_brain_search", "arguments": {}}</tool_call>',
            tool_calls=[identical_call],
        )
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([])],
            max_turns=3,
        )
        result = agent.run("Tell me about x.")
        assert result.metadata.get("max_turns_exceeded") is True
        assert _is_well_formed_answer(result.content)


class TestLoopGuardStillStopsGenuineLoop:
    """M1.3 is frozen -- this only certifies M1.4 doesn't rely on
    weakening it. Uses the production default (max_identical_calls=3, no
    override), matching tests/agents/test_orchestrator.py's own
    TestLoopGuardParityM13 certification."""

    def test_identical_second_brain_call_still_blocked_after_default_limit(self):
        identical_call = _call("second_brain_search", "sb", '{"query": "same"}')
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.return_value = _turn(tool_calls=[identical_call])
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([])],
            max_turns=6,
        )
        result = agent.run("Search for same repeatedly.")
        sb_results = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_results) == 6
        for tr in sb_results[:4]:  # 3 tolerated + 1 warned-but-executed (production default)
            assert tr.success is True
        for tr in sb_results[4:]:  # genuinely blocked from here
            assert tr.success is False
            assert tr.content.startswith("Loop guard:")


class TestEvidenceSourceLabelsRemainCorrect:
    def test_ops_second_brain_document_labeled_distinctly(self):
        from openjarvis.agents.operational_evidence import (
            SOURCE_CURRENT_OPERATIONAL_FACT,
            SOURCE_DOCUMENT_EVIDENCE,
            SOURCE_HISTORICAL_EXPERIENCE,
            build_evidence,
        )

        results = [
            _OpsCorrectCapabilityStub().execute(),
            _SecondBrainSearchStub([_sb_entry()]).execute(),
            _DocumentSearchStub([_doc_hit()]).execute(),
        ]
        evidence = build_evidence(results)
        assert evidence.facts[0].source_class == SOURCE_CURRENT_OPERATIONAL_FACT
        assert evidence.historical_experience[0].source_class == SOURCE_HISTORICAL_EXPERIENCE
        assert evidence.document_evidence[0].source_class == SOURCE_DOCUMENT_EVIDENCE
