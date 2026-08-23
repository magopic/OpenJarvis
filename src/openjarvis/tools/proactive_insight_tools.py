"""Model-callable tools over MAIA Proactive Insight & Action Proposal V1
(FASE 4P.1).

STEP 12 (hard requirement): read/proposal tools only. There is
deliberately no ``execute_action``/``run_action``/``do_it``-style tool
anywhere in this module, and ``maia_analyze_evidence_for_insights``
cannot accept free-text "evidence" from the model -- it only accepts the
same kind of structured query parameters the underlying OPS/Second
Brain/Document Knowledge tools already accept (a capability name, a
domain, a search query), then calls those ALREADY-GOVERNED tools/services
itself. This keeps evidence coming from real, already-authorized calls --
never from text the model could fabricate and pass in as if it were
gathered evidence.

Reuses, never re-implements:
  - ``ops_bridge_generic._call_bridge``/``_summarize``/``_capability_to_tool_id``
    for the OPS Bridge call (same envelope, same tool-id naming
    ``build_evidence()`` already expects).
  - ``SecondBrainFindRelatedExperiencesTool`` for historical evidence
    (already enforces Second Brain's own visibility/authorization).
  - ``DocumentSearchTool`` for document evidence (already enforces the
    authorized-workspace boundary).
  - ``build_evidence()`` (FASE 4O.6, frozen) for evidence composition.
  - ``ProactiveReasoningService`` (this phase) for deterministic detection.

The insight/proposal registry below is a plain in-memory, per-process
dict -- explicitly NOT Second Brain (STEP 10: no silent Second Brain
writes; if the user wants to keep an insight, that goes through the
existing, unmodified ``second_brain_propose_entry``/``confirm_entry``
tools) and explicitly NOT the OPS ONE Action Book (STEP 11: kept
separate for V1; Action Book's own authorization is never touched by
this module, which never calls it). It resets on process restart --
this phase does not add a new persistent store.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjarvis.agents.operational_evidence import build_evidence
from openjarvis.agents.proactive_insight import (
    ProactiveInsight,
    ProactiveReasoningService,
    ProposedAction,
)
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.document_knowledge_tools import DocumentSearchTool
from openjarvis.tools.second_brain_tools import SecondBrainFindRelatedExperiencesTool

# ---------------------------------------------------------------------------
# In-memory registry -- see module docstring for why this is intentionally
# not a persistent store.
# ---------------------------------------------------------------------------

_insights: Dict[str, ProactiveInsight] = {}
_actions: Dict[str, ProposedAction] = {}
_MAX_REGISTRY_SIZE = 500  # bounded, oldest-evicted-first; V1 scope, not a database


def _remember(insights: List[ProactiveInsight]) -> None:
    for insight in insights:
        _insights[insight.id] = insight
        for action in insight.proposed_actions:
            _actions[action.id] = action
    while len(_insights) > _MAX_REGISTRY_SIZE:
        _insights.pop(next(iter(_insights)))
    while len(_actions) > _MAX_REGISTRY_SIZE:
        _actions.pop(next(iter(_actions)))


def _reset_registry_for_tests() -> None:
    """Test-only helper -- keeps the module-level dict from leaking state
    between isolated test cases."""
    _insights.clear()
    _actions.clear()


def _insight_brief(insight: ProactiveInsight) -> Dict[str, Any]:
    return {
        "id": insight.id,
        "title": insight.title,
        "severity": insight.severity,
        "confidence": insight.confidence,
        "status": insight.status,
        "summary": insight.summary,
        "proposed_action_ids": [a.id for a in insight.proposed_actions],
    }


def _insight_full(insight: ProactiveInsight) -> Dict[str, Any]:
    brief = _insight_brief(insight)
    brief["detected_at"] = insight.detected_at
    brief["reasoning_basis"] = list(insight.reasoning_basis)
    brief["limitations"] = list(insight.limitations)
    brief["evidence"] = [
        {
            "source_class": item.source_class,
            "tool_name": item.tool_name,
            "domain": item.domain,
            "provenance": item.provenance,
            "trust_status": item.trust_status,
            "summary": item.summary,
        }
        for item in insight.evidence
    ]
    return brief


def _action_full(action: ProposedAction) -> Dict[str, Any]:
    return {
        "id": action.id,
        "title": action.title,
        "description": action.description,
        "action_type": action.action_type,
        "status": action.status,
        "rationale": action.rationale,
        "supporting_evidence": list(action.supporting_evidence),
        "limitations": list(action.limitations),
        "requires_confirmation": action.requires_confirmation,
        "execution_capability": action.execution_capability,
    }


# ---------------------------------------------------------------------------
# Detection tool -- gathers evidence itself from already-governed sources,
# runs the deterministic service, stores the result.
# ---------------------------------------------------------------------------


@ToolRegistry.register("maia_analyze_evidence_for_insights")
class ProactiveAnalyzeTool(BaseTool):
    tool_id = "maia_analyze_evidence_for_insights"

    def __init__(
        self,
        service: Optional[ProactiveReasoningService] = None,
        second_brain_tool: Optional[SecondBrainFindRelatedExperiencesTool] = None,
        document_tool: Optional[DocumentSearchTool] = None,
    ) -> None:
        self._service = service or ProactiveReasoningService()
        self._sb_tool = second_brain_tool or SecondBrainFindRelatedExperiencesTool()
        self._doc_tool = document_tool or DocumentSearchTool()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_analyze_evidence_for_insights",
            description=(
                "Check governed sources for something that may deserve attention, "
                "and produce zero or more grounded insights with proposed next "
                "steps. This does NOT execute anything and does NOT take free-text "
                "'evidence' from you -- it calls the same certified OPS Bridge/"
                "Second Brain/Document Knowledge sources you already use, then runs "
                "deterministic detectors (never an LLM guess) over the results. If "
                "you don't have a specific ops_capability/domain/query in mind, "
                "don't call this speculatively -- an empty result ('no insight') is "
                "a normal, successful outcome, not a failure. Insights and any "
                "proposed actions are always recommendations only; nothing is ever "
                "executed automatically."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "ops_capability": {
                        "type": "string",
                        "description": (
                            "Optional OPS Bridge capability name to check as a "
                            "current fact, e.g. 'ops.production.get_kpi'."
                        ),
                    },
                    "ops_params": {
                        "type": "object",
                        "description": "Optional params for the OPS capability call.",
                    },
                    "second_brain_query": {"type": "string"},
                    "second_brain_domains": {"type": "array", "items": {"type": "string"}},
                    "document_query": {"type": "string"},
                },
                "required": [],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        tool_results: List[ToolResult] = []

        ops_capability = params.get("ops_capability")
        if isinstance(ops_capability, str) and ops_capability.strip():
            tool_results.append(self._call_ops(ops_capability.strip(), params.get("ops_params") or {}))

        sb_query = params.get("second_brain_query")
        sb_domains = params.get("second_brain_domains")
        if sb_query or sb_domains:
            tool_results.append(self._sb_tool.execute(query=sb_query, domains=sb_domains))

        doc_query = params.get("document_query")
        if isinstance(doc_query, str) and doc_query.strip():
            tool_results.append(self._doc_tool.execute(query=doc_query.strip()))

        if not tool_results:
            return ToolResult(
                tool_name="maia_analyze_evidence_for_insights",
                success=True,
                content=(
                    "No evidence source was specified, so nothing was checked and "
                    "no insight was produced. This is a normal outcome -- pass "
                    "ops_capability/second_brain_query/document_query to check a "
                    "specific source."
                ),
                metadata={"num_insights": 0},
            )

        evidence = build_evidence(tool_results)
        insights = self._service.analyze(tool_results, evidence)
        _remember(insights)

        if not insights:
            return ToolResult(
                tool_name="maia_analyze_evidence_for_insights",
                success=True,
                content=(
                    "Evidence was checked; no detector found a condition worth "
                    "surfacing (e.g. no certified threshold was breached, no "
                    "conflict, no missing-data gap). Not enough evidence to raise "
                    "an operational alert is a normal, successful outcome."
                ),
                metadata={"num_insights": 0},
            )

        lines = [
            f"[{i.severity}] {i.id}: {i.title} (confidence={i.confidence}, "
            f"{len(i.proposed_actions)} proposed action(s))"
            for i in insights
        ]
        return ToolResult(
            tool_name="maia_analyze_evidence_for_insights",
            success=True,
            content=(
                f"{len(insights)} insight(s) detected:\n"
                + "\n".join(lines)
                + "\nUse maia_insight_get(insight_id) for full detail before explaining "
                "any of these to the user."
            ),
            metadata={"num_insights": len(insights), "insight_ids": [i.id for i in insights]},
        )

    def _call_ops(self, capability: str, ops_params: Dict[str, Any]) -> ToolResult:
        from openjarvis.tools.ops_bridge_generic import (
            _call_bridge,
            _capability_to_tool_id,
            _summarize,
        )

        tool_id = _capability_to_tool_id(capability)
        try:
            envelope = _call_bridge(capability, ops_params)
        except Exception as exc:  # same fail-honest pattern as _DynamicOpsBridgeTool
            # FASE 4P.1B STEP 5: an exception here (HTTP error/timeout/
            # unreachable) is the capability-does-not-exist-or-is-
            # unreachable case, distinct from a real capability returning
            # status=data_not_available for an empty period. Phrased with
            # the exact REQUESTED CAPABILITY / STATUS vocabulary so it is
            # unambiguous in [ACTUALLY_EXECUTED_TOOLS] -- the model never
            # has to infer whether the capability exists.
            return ToolResult(
                tool_name=tool_id,
                content=(
                    f"REQUESTED CAPABILITY: {capability}\n"
                    f"STATUS: NOT_AVAILABLE\n"
                    f"Reason: OPS Bridge call failed: {exc}"
                ),
                success=False,
            )

        # FASE 4P.1B STEP 5 (correction): a nonexistent/unexposed
        # capability does NOT raise -- OPS Bridge answers with a clean
        # HTTP 200 and status='unsupported' (live-verified against the
        # real bridge: 'Capability X is not exposed by the OPS Bridge.').
        # This is the actual "capability does not exist" signal, distinct
        # from 'data_not_available' (capability exists, no data for the
        # queried period) and 'forbidden' (exists, not authorized) --
        # only 'unsupported' gets the NOT_AVAILABLE vocabulary; the other
        # non-ok statuses keep their existing honest _summarize() handling.
        if envelope.get("status") == "unsupported":
            return ToolResult(
                tool_name=tool_id,
                content=(
                    f"REQUESTED CAPABILITY: {capability}\n"
                    f"STATUS: NOT_AVAILABLE\n"
                    f"Reason: {envelope.get('reason') or 'Not exposed by the OPS Bridge.'}"
                ),
                success=False,
                metadata=envelope,
            )

        return ToolResult(
            tool_name=tool_id,
            content=_summarize(envelope),
            success=envelope.get("status") == "ok",
            metadata=envelope,
        )


# ---------------------------------------------------------------------------
# Read-only tools -- STEP 12: no create/execute path.
# ---------------------------------------------------------------------------


@ToolRegistry.register("maia_insights_list")
class ProactiveInsightsListTool(BaseTool):
    tool_id = "maia_insights_list"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_insights_list",
            description=(
                "List proactive insights already detected this session via "
                "maia_analyze_evidence_for_insights, most recent first. "
                "Read-only -- does not run new detection."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "min_severity": {
                        "type": "string",
                        "enum": ["INFO", "ATTENTION", "WARNING", "CRITICAL"],
                    },
                },
                "required": [],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        order = ["INFO", "ATTENTION", "WARNING", "CRITICAL"]
        min_severity = params.get("min_severity")
        min_rank = order.index(min_severity) if min_severity in order else 0

        items = [
            _insight_brief(i)
            for i in reversed(list(_insights.values()))
            if order.index(i.severity) >= min_rank
        ]
        if not items:
            return ToolResult(
                tool_name="maia_insights_list",
                success=True,
                content="No insights recorded this session.",
                metadata={"num_insights": 0, "insights": []},
            )
        return ToolResult(
            tool_name="maia_insights_list",
            success=True,
            content=f"{len(items)} insight(s).",
            metadata={"num_insights": len(items), "insights": items},
        )


@ToolRegistry.register("maia_insight_get")
class ProactiveInsightGetTool(BaseTool):
    tool_id = "maia_insight_get"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_insight_get",
            description="Full detail for one previously detected proactive insight, by id.",
            parameters={
                "type": "object",
                "properties": {"insight_id": {"type": "string"}},
                "required": ["insight_id"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        insight_id = str(params.get("insight_id", ""))
        insight = _insights.get(insight_id)
        if insight is None:
            return ToolResult(
                tool_name="maia_insight_get",
                success=False,
                content=f"No insight found with id {insight_id!r}.",
            )
        return ToolResult(
            tool_name="maia_insight_get",
            success=True,
            content=insight.summary,
            metadata=_insight_full(insight),
        )


@ToolRegistry.register("maia_action_proposals_list")
class ProactiveActionProposalsListTool(BaseTool):
    tool_id = "maia_action_proposals_list"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_proposals_list",
            description=(
                "List proposed actions attached to insights detected this session. "
                "Every item is a recommendation only (status is always PROPOSED in "
                "this phase) -- nothing here has been or can be executed."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        items = [_action_full(a) for a in reversed(list(_actions.values()))]
        if not items:
            return ToolResult(
                tool_name="maia_action_proposals_list",
                success=True,
                content="No proposed actions recorded this session.",
                metadata={"num_actions": 0, "actions": []},
            )
        return ToolResult(
            tool_name="maia_action_proposals_list",
            success=True,
            content=f"{len(items)} proposed action(s), all status=PROPOSED.",
            metadata={"num_actions": len(items), "actions": items},
        )


@ToolRegistry.register("maia_action_proposal_get")
class ProactiveActionProposalGetTool(BaseTool):
    tool_id = "maia_action_proposal_get"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_proposal_get",
            description="Full detail for one proposed action, by id.",
            parameters={
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        action_id = str(params.get("action_id", ""))
        action = _actions.get(action_id)
        if action is None:
            return ToolResult(
                tool_name="maia_action_proposal_get",
                success=False,
                content=f"No proposed action found with id {action_id!r}.",
            )
        return ToolResult(
            tool_name="maia_action_proposal_get",
            success=True,
            content=action.description,
            metadata=_action_full(action),
        )


__all__ = [
    "ProactiveAnalyzeTool",
    "ProactiveInsightsListTool",
    "ProactiveInsightGetTool",
    "ProactiveActionProposalsListTool",
    "ProactiveActionProposalGetTool",
]
