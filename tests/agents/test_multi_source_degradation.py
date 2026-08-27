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
            # M2.4 Stage 2: Second Brain returned num_results=0 -> one
            # bounded zero-result nudge fires here before finalizing.
            _turn("Production is at 87.3%. No historical precedent was found for this."),
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
        assert engine.generate.call_count == 3  # tool turn + Stage-2 zero-result nudge + final
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
            # M2.4 Stage 2: both Second Brain and Document Knowledge
            # returned num_results=0 -> one bounded nudge covering both
            # (single combined message, still one `continue`) before
            # finalizing.
            _turn(
                "Production is at 87.3%. No historical precedent or document "
                "evidence was found for this question."
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
        assert engine.generate.call_count == 3


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
        attempted in turn 1, WITH non-empty results (M2.4 Stage 2 only
        fires when a family's search came back empty -- non-empty
        results here keep Stage 2 from firing) so the FASE 4O.6A
        evidence-coverage nudge never fires either -- this isolates the
        M1.4 malformed-content guard specifically."""
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
            tools=[
                _OpsCorrectCapabilityStub(),
                _SecondBrainSearchStub([_sb_entry()]),
                _DocumentSearchStub([_doc_hit()]),
            ],
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


class TestClaimCoverageM24:
    """M2.4 -- Cross-Source Claim Coverage. Live-reproduced failure: a
    compound question ("who cleans area X, and who moves pallets from
    the cell to the plant?") got a document_search-only answer that
    silently dropped the Second Brain half, because the old coverage
    nudge's wording ("if not, it is fine to finalize without it") let
    the model decline an unattempted family without checking whether
    the ORIGINAL question still had an unaddressed part that family
    could cover. The fix changes only the nudge's wording (self-certify
    completeness against the original question) -- the trigger
    condition (`unattempted_families`) and the once-only latch
    (`coverage_nudge_used`) are unchanged."""

    def test_1_single_source_question_unchanged(self):
        """A single-source question must behave exactly as before: no
        extra nudge turn beyond the one already-certified coverage
        nudge, and the final answer is untouched."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("ops_dynamic_production_get_kpi", "c1")]),
            _turn("Production is at 87.3% this period."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_OpsCorrectCapabilityStub()])
        result = agent.run("How is production doing?")
        assert result.content == "Production is at 87.3% this period."
        assert engine.generate.call_count == 2  # no extra nudge turn

    def test_2_document_covers_b_second_brain_unattempted_a_unresolved(self):
        """A+B: Document Knowledge covers B (pallet movement), Second
        Brain is never attempted, A (cleaning responsibility) is
        unresolved in the first draft -- the nudge must fire once, the
        model must then check Second Brain, and the final answer must
        cover both parts."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane"}')]),
            # First draft only answers B (document part) -- A is still
            # unresolved, and Second Brain was never attempted.
            _turn("Fastlog movimenta le pedane secondo la procedura."),
            _turn(tool_calls=[_call("second_brain_search", "c2", '{"query": "responsabilita pulizia"}')]),
            _turn(
                "Manutenzione pulisce le proprie aree e Fastlog le restanti; "
                "Fastlog movimenta le pedane secondo la procedura."
            ),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_DocumentSearchStub([_doc_hit()]), _SecondBrainSearchStub([_sb_entry()])],
        )
        result = agent.run(
            "Chi è responsabile della pulizia delle aree di manutenzione e chi movimenta le pedane?"
        )
        called = [tr.tool_name for tr in result.tool_results]
        assert "document_search" in called
        assert "second_brain_search" in called, "the nudge must have prompted the missing family"
        assert "Manutenzione" in result.content and "Fastlog" in result.content

    def test_3_second_brain_covers_a_document_unattempted_b_unresolved(self):
        """Mirror of test 2 with the families swapped."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "responsabilita pulizia"}')]),
            _turn("Manutenzione pulisce le proprie aree e Fastlog le restanti."),
            _turn(tool_calls=[_call("document_search", "c2", '{"query": "movimentazione pedane"}')]),
            _turn(
                "Manutenzione pulisce le proprie aree e Fastlog le restanti; "
                "Fastlog movimenta le pedane secondo la procedura."
            ),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([_sb_entry()]), _DocumentSearchStub([_doc_hit()])],
        )
        result = agent.run(
            "Chi è responsabile della pulizia delle aree di manutenzione e chi movimenta le pedane?"
        )
        called = [tr.tool_name for tr in result.tool_results]
        assert "second_brain_search" in called
        assert "document_search" in called, "the nudge must have prompted the missing family"

    def test_4_both_families_already_covered_no_extra_nudge(self):
        """When both families were already attempted in one turn, the
        coverage nudge must never fire -- confirms the fix didn't make
        the nudge fire more often, only changed its wording when it
        does fire."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("second_brain_search", "c1", '{"query": "responsabilita pulizia"}'),
                    _call("document_search", "c2", '{"query": "movimentazione pedane"}'),
                ]
            ),
            _turn("Full answer covering both parts."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([_sb_entry()]), _DocumentSearchStub([_doc_hit()])],
        )
        result = agent.run("Chi pulisce e chi movimenta?")
        assert result.content == "Full answer covering both parts."
        assert engine.generate.call_count == 2  # no nudge turn injected

    def test_5_unavailable_family_never_triggers_retry(self):
        """A family whose tools aren't even offered this session (e.g.
        only Document Knowledge is wired in) must never appear in
        `unattempted_families` -- no infinite retry, no nudge for a
        source that was never an option."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane"}')]),
            _turn("Fastlog movimenta le pedane secondo la procedura."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_DocumentSearchStub([_doc_hit()])])
        result = agent.run("Chi movimenta le pedane?")
        assert result.content == "Fastlog movimenta le pedane secondo la procedura."
        assert engine.generate.call_count == 2  # no nudge -- Second Brain was never available

    def test_6_nudge_fires_at_most_once(self):
        """Even if the model's post-nudge answer is STILL incomplete
        (never checks the suggested family), the nudge must not fire a
        second time -- bounded, matching the original FASE 4O.6A
        guarantee exactly."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane"}')]),
            _turn("Fastlog movimenta le pedane secondo la procedura."),  # triggers the nudge
            _turn("Fastlog movimenta le pedane secondo la procedura."),  # ignores it again -- finalizes anyway
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_DocumentSearchStub([_doc_hit()]), _SecondBrainSearchStub([_sb_entry()])],
        )
        result = agent.run("Chi è responsabile della pulizia e chi movimenta le pedane?")
        # Exactly 3 generate calls: initial tool call, nudge turn, final
        # answer -- never a 4th (no second nudge).
        assert engine.generate.call_count == 3
        called = [tr.tool_name for tr in result.tool_results]
        assert called.count("document_search") == 1
        assert "second_brain_search" not in called
        assert result.content == "Fastlog movimenta le pedane secondo la procedura."


class _StatefulSecondBrainStub(BaseTool):
    """Returns a different result set on each successive call -- models a
    narrow first query returning nothing, then a broader retry finding
    the entry. The LAST entry in `results_sequence` repeats for any call
    beyond its length."""

    tool_id = "second_brain_search"

    def __init__(self, results_sequence: list[list]):
        self._sequence = list(results_sequence)
        self._call_index = 0

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
        idx = min(self._call_index, len(self._sequence) - 1)
        entries = self._sequence[idx]
        self._call_index += 1
        return ToolResult(
            tool_name=self.tool_id,
            content=f"{len(entries)} result(s)",
            success=True,
            metadata={"num_results": len(entries), "entries": entries},
        )


class TestZeroResultRecoveryM24Stage2:
    """M2.4 Stage 2 -- Zero-Result Retrieval Recovery. Live-reproduced:
    Stage 1 (TestClaimCoverageM24) correctly gets the model to ATTEMPT a
    previously-untried family, but a narrow/over-specific query can
    still return num_results=0 while the corresponding sub-claim stays
    unresolved -- Stage 1's own bookkeeping (`attempted_names`) already
    marks that family "done" and can never re-nudge for it. Stage 2 adds
    one further bounded nudge specifically for this "attempted but
    empty" case."""

    def test_1_zero_result_then_broader_retry_finds_evidence(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "pulizia magazzino XYZ 2026"}')]),
            _turn("Non ho trovato precedenti su questo."),  # unresolved -- triggers Stage 2
            _turn(tool_calls=[_call("second_brain_search", "c2", '{"domain": "Operations"}')]),
            _turn("Manutenzione pulisce le proprie aree; Fastlog le restanti."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_StatefulSecondBrainStub([[], [_sb_entry()]])],
        )
        result = agent.run("Chi è responsabile della pulizia delle aree di manutenzione?")
        assert engine.generate.call_count == 4
        sb_calls = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_calls) == 2
        assert sb_calls[0].metadata["num_results"] == 0
        assert sb_calls[1].metadata["num_results"] == 1
        assert result.content == "Manutenzione pulisce le proprie aree; Fastlog le restanti."

    def test_2_zero_result_retry_also_empty_honest_finalization(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "argomento inesistente"}')]),
            _turn("Nessun precedente trovato."),  # triggers Stage 2
            _turn(tool_calls=[_call("second_brain_search", "c2", '{"domain": "unrelated"}')]),  # still empty
            _turn("Nessun precedente trovato su questo argomento."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([])],  # every call returns empty
        )
        result = agent.run("C'è un precedente su un argomento che non esiste?")
        # Bounded: exactly 4 generate calls, never a 5th (no second Stage-2 retry).
        assert engine.generate.call_count == 4
        sb_calls = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_calls) == 2
        assert all(tr.metadata["num_results"] == 0 for tr in sb_calls)
        assert result.content == "Nessun precedente trovato su questo argomento."

    def test_3_nonzero_result_stage_2_never_fires(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "pulizia"}')]),
            _turn("Manutenzione pulisce le proprie aree; Fastlog le restanti."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_SecondBrainSearchStub([_sb_entry()])])
        result = agent.run("Chi è responsabile della pulizia?")
        assert engine.generate.call_count == 2  # no Stage-2 nudge -- real evidence was found

    def test_4_stage_1_then_stage_2_both_fire_in_order(self):
        """Stage 1 (family never attempted) fires first, gets the model
        to attempt it; that attempt returns zero; Stage 2 then fires for
        the same family, gets a broader retry that finds evidence."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane"}')]),
            _turn("Fastlog movimenta le pedane."),  # Second Brain never attempted -- Stage 1 fires
            _turn(tool_calls=[_call("second_brain_search", "c2", '{"query": "pulizia magazzino XYZ 2026"}')]),
            _turn("Fastlog movimenta le pedane. Non ho trovato precedenti sulla pulizia."),  # Stage 2 fires
            _turn(tool_calls=[_call("second_brain_search", "c3", '{"domain": "Operations"}')]),
            _turn(
                "Manutenzione pulisce le proprie aree; Fastlog le restanti. "
                "Fastlog movimenta le pedane."
            ),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_DocumentSearchStub([_doc_hit()]), _StatefulSecondBrainStub([[], [_sb_entry()]])],
        )
        result = agent.run(
            "Chi è responsabile della pulizia delle aree di manutenzione e chi movimenta le pedane?"
        )
        assert engine.generate.call_count == 6
        sb_calls = [tr for tr in result.tool_results if tr.tool_name == "second_brain_search"]
        assert len(sb_calls) == 2
        assert sb_calls[0].metadata["num_results"] == 0
        assert sb_calls[1].metadata["num_results"] == 1
        assert "Manutenzione" in result.content and "Fastlog" in result.content

    def test_5_multiple_empty_families_bounded_single_combined_nudge(self):
        """Two families both come back empty in the same turn -- Stage 2
        must fire exactly ONCE (a single combined nudge, not one retry
        per family), proving there is no pathological per-family retry
        loop."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("ops_dynamic_production_get_kpi", "c1"),
                    _call("second_brain_search", "c2", '{"query": "x"}'),
                ]
            ),
            _turn("Production is at 87.3%."),  # Document Knowledge never attempted -- Stage 1 fires
            _turn(tool_calls=[_call("document_search", "c3", '{"query": "x"}')]),
            _turn("Production is at 87.3%. No other evidence found."),  # both SB and DK empty -- Stage 2 fires once
            _turn("Production is at 87.3%. No other evidence found."),
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
        assert engine.generate.call_count == 5
        assert result.content == "Production is at 87.3%. No other evidence found."


class _SecondBrainFindRelatedStub(BaseTool):
    """second_brain_find_related_experiences -- reports `num_candidates`,
    NOT `num_results` (matching the real tool exactly), a deliberate
    metadata-shape difference from second_brain_search/document_search."""

    tool_id = "second_brain_find_related_experiences"

    def __init__(self, candidates: list | None = None):
        self._candidates = candidates if candidates is not None else []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Find historical Second Brain experiences via progressive broadening.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "domains": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                },
                "required": [],
            },
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=f"{len(self._candidates)} candidate(s)",
            success=True,
            metadata={"num_candidates": len(self._candidates), "candidates": self._candidates},
        )


class TestZeroResultEscalationM24Stage2B:
    """M2.4 Stage 2B -- Second Brain Escalation to find_related_experiences.
    Live-reproduced: after Stage 2 (generic zero-result nudge) fired, the
    model retried Second Brain with the SAME strict second_brain_search
    tool (implicit-AND FTS, frozen since FASE 4N.2A) instead of escalating
    to second_brain_find_related_experiences -- the tool actually built
    for "find this under different wording" (deterministic EXACT/
    STRUCTURED/TERM(OR)/RELATIONSHIP broadening, certified in M2.2). The
    fix only changes the Stage-2 nudge's wording to name that tool
    explicitly when it hasn't been tried yet -- no new tool, no FTS
    change."""

    def test_1_search_empty_find_related_not_tried_nudge_names_it_explicitly(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "pulizia magazzino XYZ 2026"}')]),
            _turn("Non ho trovato precedenti su questo."),  # SB empty, find_related untried -> Stage 2B
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c2", '{"domains": ["Operations"]}')]),
            _turn("Manutenzione pulisce le proprie aree; Fastlog le restanti."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([]), _SecondBrainFindRelatedStub([_sb_entry()])],
        )
        result = agent.run("Chi è responsabile della pulizia delle aree di manutenzione?")
        assert engine.generate.call_count == 4
        called = [tr.tool_name for tr in result.tool_results]
        assert called == ["second_brain_search", "second_brain_find_related_experiences"]
        assert result.content == "Manutenzione pulisce le proprie aree; Fastlog le restanti."
        # The nudge sent for the 3rd generate() call (right after
        # second_brain_search's empty result) must name the escalation
        # tool explicitly, not just generically say "try broader."
        third_call_messages = engine.generate.call_args_list[2][0][0]
        nudge_texts = [m.content for m in third_call_messages if m.role == Role.USER]
        assert any("second_brain_find_related_experiences" in (t or "") for t in nudge_texts)

    def test_2_search_and_find_related_both_empty_honest_finalization(self):
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "argomento inesistente"}')]),
            _turn("Nessun precedente trovato."),  # SB empty, find_related untried -> Stage 2B fires
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c2", '{"query": "argomento inesistente"}')]),
            _turn("Nessun precedente trovato su questo argomento."),
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([]), _SecondBrainFindRelatedStub([])],
        )
        result = agent.run("C'è un precedente su un argomento che non esiste?")
        # Bounded: exactly 4 generate calls, no second Stage-2 nudge even
        # though find_related_experiences also came back empty.
        assert engine.generate.call_count == 4
        assert result.content == "Nessun precedente trovato su questo argomento."

    def test_3_find_related_tried_first_and_empty_no_further_suggestion(self):
        """find_related_experiences is the only Second Brain call from
        the start and returns nothing -- there is no further tool to
        escalate to, so Stage 2 must not keep suggesting it (or anything
        else) a second time."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c1", '{"query": "x"}')]),
            _turn("Nessun precedente trovato."),
        ]
        agent = OrchestratorAgent(engine, "test-model", tools=[_SecondBrainFindRelatedStub([])])
        result = agent.run("C'è un precedente su x?")
        assert engine.generate.call_count == 2  # no Stage-2 nudge at all
        assert result.content == "Nessun precedente trovato."

    def test_4_document_knowledge_empty_generic_wording_unchanged(self):
        """Document Knowledge has no equivalent broadening tool -- its
        Stage-2 nudge must stay the existing generic wording, never
        naming second_brain_find_related_experiences (that tool is
        irrelevant to a Document Knowledge gap)."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane XYZ"}')]),
            _turn("Nessun documento trovato."),  # DK empty -> generic Stage-2 nudge
            _turn(tool_calls=[_call("document_search", "c2", '{"query": "movimentazione"}')]),
            _turn("Fastlog movimenta le pedane secondo la procedura."),
        ]
        # This scenario needs per-call statefulness (empty first, then a
        # real hit) -- a tiny local stateful variant of the fixed-result
        # stub used elsewhere.
        class _StatefulDocStub(BaseTool):
            tool_id = "document_search"

            def __init__(self, sequence):
                self._sequence = list(sequence)
                self._i = 0

            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name=self.tool_id,
                    description="Search Document Knowledge.",
                    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                )

            def execute(self, **params) -> ToolResult:
                idx = min(self._i, len(self._sequence) - 1)
                results = self._sequence[idx]
                self._i += 1
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"{len(results)} result(s)",
                    success=True,
                    metadata={"num_results": len(results), "results": results},
                )

        agent = OrchestratorAgent(engine, "test-model", tools=[_StatefulDocStub([[], [_doc_hit()]])])
        result = agent.run("Chi movimenta le pedane?")
        assert engine.generate.call_count == 4
        assert result.content == "Fastlog movimenta le pedane secondo la procedura."


class TestPerFamilyZeroResultRecoveryM24Stage2C:
    """M2.4 Stage 2C -- Per-Family Zero-Result Recovery. Live-reproduced:
    a single conversation-global `zero_result_retry_used` bool let Second
    Brain's own recovery (Stage 2B) permanently consume the "fires once"
    budget -- Document Knowledge's completely independent, later
    zero-result situation in the SAME conversation never got its own
    nudge, and the final answer incorrectly declared no documented
    source existed even though the real PDF contained the answer. The
    fix replaces the single bool with a per-family set
    (`zero_result_retried_families`) -- each family may still receive at
    most ONE recovery nudge ever, but independently of the other."""

    def test_a_second_brain_recovers_then_document_knowledge_independently_empty(self):
        """Second Brain goes through its full Stage 2B recovery cycle
        FIRST (fires, escalates to find_related_experiences, resolves)
        while Document Knowledge is never even touched -- then, later,
        the model checks Document Knowledge on its own and finds
        nothing. Document Knowledge must still get its own nudge,
        unblocked by Second Brain's earlier, unrelated recovery."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "pulizia magazzino XYZ 2026"}')]),
            _turn("Non ho trovato precedenti."),  # DK unattempted -> Stage 1 fires
            _turn(tool_calls=[_call("second_brain_search", "c2", '{"query": "responsabilita pulizia manutenzione XYZ"}')]),
            # model reformulates SB instead of following Stage 1's DK
            # suggestion -- both SB calls empty, DK still untouched.
            _turn("Non ho trovato precedenti."),  # Stage 2B fires for SB alone (DK never attempted, not flagged)
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c3", '{"domains": ["Operations"]}')]),
            # SB resolved. Model now checks documents on its own initiative.
            _turn(tool_calls=[_call("document_search", "c4", '{"query": "movimentazione pedane XYZ 2026"}')]),
            _turn(
                "Manutenzione pulisce le proprie aree; Fastlog le restanti. "
                "Non ho trovato documenti sulla movimentazione."
            ),  # Stage 2 fires for DK alone (SB already retried, skipped)
            _turn(tool_calls=[_call("document_search", "c5", '{"query": "movimentazione pedane"}')]),
            _turn(
                "Manutenzione pulisce le proprie aree; Fastlog le restanti. "
                "Fastlog movimenta le pedane secondo la procedura."
            ),
        ]
        class _StatefulDocStub(BaseTool):
            tool_id = "document_search"

            def __init__(self, sequence):
                self._sequence = list(sequence)
                self._i = 0

            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name=self.tool_id,
                    description="Search Document Knowledge.",
                    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                )

            def execute(self, **params) -> ToolResult:
                idx = min(self._i, len(self._sequence) - 1)
                results = self._sequence[idx]
                self._i += 1
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"{len(results)} result(s)",
                    success=True,
                    metadata={"num_results": len(results), "results": results},
                )

        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[
                _SecondBrainSearchStub([]),
                _SecondBrainFindRelatedStub([_sb_entry()]),
                _StatefulDocStub([[], [_doc_hit()]]),
            ],
        )
        result = agent.run(
            "Chi è responsabile della pulizia delle aree di manutenzione e chi movimenta le pedane?"
        )
        assert engine.generate.call_count == 9
        called = [tr.tool_name for tr in result.tool_results]
        assert called.count("second_brain_search") == 2
        assert called.count("second_brain_find_related_experiences") == 1
        assert called.count("document_search") == 2
        assert "Manutenzione" in result.content and "Fastlog" in result.content

    def test_b_document_knowledge_recovers_then_second_brain_independently_empty(self):
        """Mirror of test A with the families swapped: Document Knowledge
        recovers first via the generic nudge; Second Brain's own later,
        independent zero-result situation still gets its Stage 2B
        escalation, unblocked by DK's earlier recovery."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("document_search", "c1", '{"query": "movimentazione pedane XYZ 2026"}')]),
            _turn("Nessun documento trovato."),  # SB unattempted -> Stage 1 fires
            _turn(tool_calls=[_call("document_search", "c2", '{"query": "trasferimento pedane cella XYZ"}')]),
            _turn("Nessun documento trovato."),  # Stage 2 fires for DK alone (SB never attempted, not flagged)
            _turn(tool_calls=[_call("document_search", "c3", '{"query": "movimentazione pedane"}')]),
            # DK resolved. Model now checks Second Brain on its own initiative.
            _turn(tool_calls=[_call("second_brain_search", "c4", '{"query": "pulizia magazzino XYZ 2026"}')]),
            _turn(
                "Fastlog movimenta le pedane secondo la procedura. "
                "Non ho trovato precedenti sulla pulizia."
            ),  # Stage 2B fires for SB alone (DK already retried, skipped)
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c5", '{"domains": ["Operations"]}')]),
            _turn(
                "Fastlog movimenta le pedane secondo la procedura. "
                "Manutenzione pulisce le proprie aree; Fastlog le restanti."
            ),
        ]
        class _StatefulDocStub(BaseTool):
            tool_id = "document_search"

            def __init__(self, sequence):
                self._sequence = list(sequence)
                self._i = 0

            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name=self.tool_id,
                    description="Search Document Knowledge.",
                    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                )

            def execute(self, **params) -> ToolResult:
                idx = min(self._i, len(self._sequence) - 1)
                results = self._sequence[idx]
                self._i += 1
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"{len(results)} result(s)",
                    success=True,
                    metadata={"num_results": len(results), "results": results},
                )

        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[
                _StatefulDocStub([[], [], [_doc_hit()]]),
                _SecondBrainSearchStub([]),
                _SecondBrainFindRelatedStub([_sb_entry()]),
            ],
        )
        result = agent.run(
            "Chi movimenta le pedane e chi è responsabile della pulizia delle aree di manutenzione?"
        )
        assert engine.generate.call_count == 9
        called = [tr.tool_name for tr in result.tool_results]
        assert called.count("document_search") == 3
        assert called.count("second_brain_search") == 1
        assert called.count("second_brain_find_related_experiences") == 1
        assert "Manutenzione" in result.content and "Fastlog" in result.content

    def test_c_both_families_empty_simultaneously_one_combined_nudge_both_marked(self):
        """When both families are discovered empty at the SAME
        evaluation point (unchanged from Stage 2's original behavior),
        exactly one combined nudge fires and BOTH are marked retried
        together -- neither gets a second, separate nudge later."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(
                tool_calls=[
                    _call("second_brain_search", "c1", '{"query": "x"}'),
                    _call("document_search", "c2", '{"query": "x"}'),
                ]
            ),
            _turn("Nessuna evidenza trovata."),  # both empty simultaneously -> one combined nudge
            _turn("Nessuna evidenza trovata."),  # neither retries -> honest finalization, no second nudge
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([]), _DocumentSearchStub([])],
        )
        result = agent.run("C'è qualcosa su x?")
        assert engine.generate.call_count == 3
        assert result.content == "Nessuna evidenza trovata."

    def test_d_family_remains_empty_after_its_own_retry_no_second_nudge_for_it(self):
        """A family that gets its one recovery nudge, retries, and is
        STILL empty must never receive a second nudge for itself --
        matches the original Stage 2 bounded guarantee, now scoped per
        family instead of globally."""
        engine = MagicMock()
        engine.engine_id = "mock"
        engine.generate.side_effect = [
            _turn(tool_calls=[_call("second_brain_search", "c1", '{"query": "argomento inesistente"}')]),
            _turn("Nessun precedente trovato."),  # Stage 2B fires for SB
            _turn(tool_calls=[_call("second_brain_find_related_experiences", "c2", '{"query": "argomento inesistente"}')]),
            _turn("Nessun precedente trovato su questo argomento."),  # still empty -- no second nudge
        ]
        agent = OrchestratorAgent(
            engine,
            "test-model",
            tools=[_SecondBrainSearchStub([]), _SecondBrainFindRelatedStub([])],
        )
        result = agent.run("C'è un precedente su un argomento che non esiste?")
        assert engine.generate.call_count == 4
        assert result.content == "Nessun precedente trovato su questo argomento."
