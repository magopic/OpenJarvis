"""MAIA Proactive Insight & Action Proposal V1 (FASE 4P.1).

Moves MAIA from "answer questions" toward "recognize something worth
attention, explain why, and propose what the user could do next" --
without ever autonomously executing an external action.

DETECT -> GROUND -> EXPLAIN -> PROPOSE. Never DETECT -> EXECUTE.

Builds directly on the frozen FASE 4O.6/4O.6A evidence model
(``operational_evidence.py``): every ``ProactiveInsight`` is produced from
an already-built ``OperationalEvidence`` (and the raw ``ToolResult``s it
was built from, for the few detectors that need a real number to compare
against a real number -- ``EvidenceItem`` itself intentionally carries no
numeric ``value`` field, only a rendered ``summary`` string). Nothing here
re-fetches, recomputes, or reinterprets a value; nothing here invents a
threshold, an owner, a deadline, or an expected saving.

Detection is deterministic Python, never an LLM judgment call: a detector
either finds the certified numeric/structural condition it looks for, or
it does not fire. The LLM's job (elsewhere, in the model-callable tools
built on top of this module) is to explain an already-grounded insight in
natural language and to decide whether calling a detector is relevant --
never to decide whether a threshold was breached.

Source precedence (reused unmodified from FASE 4O.6, never redefined
here): CURRENT_OPERATIONAL_FACT establishes current state;
KNOWLEDGE_DEFINITION defines meaning/threshold when certified;
HISTORICAL_EXPERIENCE is precedent, never current-cause proof;
DOCUMENT_EVIDENCE is supporting/contextual, never a certified value.
Historical/document evidence may only ENRICH an insight a current fact
already established -- see ``_enrich_with_context`` below. Neither can
establish a current operational issue on its own (STEP 3's evidence-first
rule): a detector that runs with only historical/document evidence and no
current fact simply produces no insight, which is the correct, honest V1
outcome -- not a defect.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjarvis.agents.operational_evidence import (
    SOURCE_CURRENT_OPERATIONAL_FACT,
    EvidenceItem,
    OperationalEvidence,
)
from openjarvis.core.types import ToolResult

# ---------------------------------------------------------------------------
# Contracts (STEP 2, STEP 5)
# ---------------------------------------------------------------------------

SEVERITY_INFO = "INFO"
SEVERITY_ATTENTION = "ATTENTION"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"
_VALID_SEVERITIES = frozenset(
    {SEVERITY_INFO, SEVERITY_ATTENTION, SEVERITY_WARNING, SEVERITY_CRITICAL}
)

# Categorical, never a fabricated numeric probability (STEP 9: "do not
# invent ... probabilities"). Derived purely from how many independent
# detectors/source classes corroborate the same insight -- see
# _confidence_for().
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"

INSIGHT_STATUS_DETECTED = "DETECTED"

ACTION_STATUS_PROPOSED = "PROPOSED"
ACTION_STATUS_APPROVED = "APPROVED"
ACTION_STATUS_REJECTED = "REJECTED"
ACTION_STATUS_EXECUTED = "EXECUTED"
ACTION_STATUS_FAILED = "FAILED"
ACTION_STATUS_CANCELLED = "CANCELLED"
_VALID_ACTION_STATUSES = frozenset(
    {
        ACTION_STATUS_PROPOSED,
        ACTION_STATUS_APPROVED,
        ACTION_STATUS_REJECTED,
        ACTION_STATUS_EXECUTED,
        ACTION_STATUS_FAILED,
        ACTION_STATUS_CANCELLED,
    }
)


@dataclass
class ProposedAction:
    """A recommendation, never an executable command.

    STEP 6 (hard requirement): there is no ``execute()`` method here, no
    ``execution_capability`` wired to anything callable in V1, and this
    class is never passed to any tool executor. ``requires_confirmation``
    is unconditionally True for every action this module generates --
    V1 cannot prove an action has zero possible external side effect, so
    it defaults safe rather than trying to classify which recommendations
    are "safe enough" to skip confirmation.
    """

    id: str
    title: str
    description: str
    action_type: str
    status: str = ACTION_STATUS_PROPOSED
    rationale: str = ""
    supporting_evidence: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    requires_confirmation: bool = True
    # Always None in V1 (STEP 6/STEP 9 AUTHORITY): a future phase may map
    # this to a real, separately-authorized execution capability id; this
    # module never sets it, so "AUTHORITY: recommendation only" is always
    # structurally true, not just documented.
    execution_capability: Optional[str] = None


@dataclass
class ProactiveInsight:
    """Something MAIA believes deserves user attention -- never exists
    without at least one governed EvidenceItem behind it (STEP 3)."""

    id: str
    title: str
    summary: str
    severity: str
    confidence: str
    status: str = INSIGHT_STATUS_DETECTED
    detected_at: float = field(default_factory=time.time)
    evidence: List[EvidenceItem] = field(default_factory=list)
    reasoning_basis: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    proposed_actions: List[ProposedAction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers shared by detectors
# ---------------------------------------------------------------------------


def _stable_id(*parts: str) -> str:
    """Deterministic id from grounding content -- re-analyzing the same
    evidence twice yields the same insight id (STEP 14 scenario O), rather
    than a fresh random id every call."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def _fact_envelopes(
    tool_results: List[ToolResult], evidence: OperationalEvidence
) -> List["tuple[ToolResult, Dict[str, Any]]"]:
    """The raw (tool_result, envelope) pairs behind evidence.facts -- the
    one place this module reads a real number, because EvidenceItem itself
    only carries a rendered summary string, never a structured value.
    Matched back to tool_results by tool_name; only status=='ok' facts
    carry a `data` dict worth reading."""
    fact_tool_names = {item.tool_name for item in evidence.facts}
    out = []
    for tr in tool_results:
        if tr.tool_name not in fact_tool_names:
            continue
        envelope = tr.metadata if isinstance(tr.metadata, dict) else {}
        if envelope.get("status") == "ok" and isinstance(envelope.get("data"), dict):
            out.append((tr, envelope))
    return out


def _fact_evidence_for(evidence: OperationalEvidence, tool_name: str) -> List[EvidenceItem]:
    matches = [item for item in evidence.facts if item.tool_name == tool_name]
    return matches or list(evidence.facts)


def _confidence_for(evidence: OperationalEvidence, *, corroborated: bool) -> str:
    """Deterministic, not invented: MEDIUM for a single current fact,
    HIGH only when a certified numeric comparison genuinely corroborates
    it (e.g. a real threshold breach), LOW when the insight rests on an
    absence (missing data) rather than a positive certified reading."""
    if corroborated:
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


def _enrich_with_context(insight: ProactiveInsight, evidence: OperationalEvidence) -> None:
    """STEP 3/STEP 8: historical/document evidence may enrich an insight a
    current fact already established -- appended as additional, clearly
    labeled evidence and reasoning lines, never merged into the current
    fact's own certainty tier, never used to raise severity."""
    for item in evidence.historical_experience:
        insight.evidence.append(item)
        insight.reasoning_basis.append(
            f"Historical precedent (context only, not proof of current cause): {item.summary}"
        )
    for item in evidence.document_evidence:
        insight.evidence.append(item)
        insight.reasoning_basis.append(
            f"Document context (procedure/reference, not a certified value): {item.summary}"
        )


# ---------------------------------------------------------------------------
# Detectors (STEP 4) -- each operates ONLY when its required certified
# input genuinely exists. None hardcodes a business KPI, domain, or
# threshold value; every threshold/target compared below is read from the
# evidence itself, never invented by this module.
# ---------------------------------------------------------------------------


class Detector(ABC):
    name: str

    @abstractmethod
    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        ...


class ThresholdBreachDetector(Detector):
    """Fires only when a FACT envelope's own `data` dict carries an
    explicit, self-describing threshold contract:
    {"value": <number>, "threshold": <number>, "threshold_type": "max"|"min"}
    (optionally "critical_threshold": <number> for the CRITICAL tier).

    This is a generic contract, not any specific KPI's shape -- as of this
    phase, no OPS ONE capability actually returns a `threshold` field
    (confirmed via audit: ops.knowledge.get_kpi_definition's KpiDefinitionData
    has no threshold/target field at all). That means this detector is
    correctly DORMANT against real production data today -- exactly the
    "operate only when required inputs exist" behavior STEP 4 asks for,
    not a bug. It exists so that once a capability does expose a certified
    threshold, detection requires zero new code.

    `threshold_type` is required, not defaulted: guessing "greater than
    threshold = breach" when direction isn't stated would be inventing a
    business rule this module has no authority to invent.
    """

    name = "threshold_breach"

    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        insights: List[ProactiveInsight] = []
        for tr, envelope in _fact_envelopes(tool_results, evidence):
            data = envelope["data"]
            value = data.get("value")
            threshold = data.get("threshold")
            threshold_type = data.get("threshold_type")
            if not isinstance(value, (int, float)) or not isinstance(threshold, (int, float)):
                continue
            if threshold_type not in ("max", "min"):
                continue

            breached = (threshold_type == "max" and value > threshold) or (
                threshold_type == "min" and value < threshold
            )
            if not breached:
                continue

            severity = SEVERITY_WARNING
            critical_threshold = data.get("critical_threshold")
            if isinstance(critical_threshold, (int, float)):
                critical_breach = (
                    threshold_type == "max" and value > critical_threshold
                ) or (threshold_type == "min" and value < critical_threshold)
                if critical_breach:
                    severity = SEVERITY_CRITICAL

            metric = data.get("metric", envelope.get("source", {}).get("function_area", "value"))
            domain = envelope.get("source", {}).get("function_area")
            period = envelope.get("period")
            insight = ProactiveInsight(
                id=_stable_id(self.name, str(metric), str(value), str(threshold), str(period)),
                title=f"{metric}: certified threshold breached",
                summary=(
                    f"Current value {value} breaches the certified {threshold_type} threshold "
                    f"{threshold}" + (f" for period {period}" if period else "") + "."
                ),
                severity=severity,
                confidence=_confidence_for(evidence, corroborated=True),
                evidence=_fact_evidence_for(evidence, tr.tool_name),
                reasoning_basis=[
                    f"Certified current value ({value}) compared against the certified "
                    f"{threshold_type} threshold ({threshold}) present in the same source's own data.",
                ],
                limitations=list(evidence.limitations),
            )
            insight.proposed_actions.append(
                ProposedAction(
                    id=_stable_id("action", insight.id),
                    title=f"Review {metric}",
                    description=(
                        f"Review why {metric} is at {value}, beyond its certified "
                        f"{threshold_type} threshold of {threshold}."
                    ),
                    action_type="review",
                    rationale=f"A certified threshold breach was detected for {metric}.",
                    supporting_evidence=[insight.summary],
                    limitations=[
                        "No owner, deadline, or expected outcome is known -- MAIA has no "
                        "certified source for any of those.",
                    ],
                )
            )
            _enrich_with_context(insight, evidence)
            insights.append(insight)
        return insights


class MissingDataDetector(Detector):
    """Fires when a current-operational-fact call was attempted but the
    source reported the data does not exist for the requested period
    (`status != 'ok'`, i.e. exactly the case already surfaced as a
    limitation by build_evidence()). Severity is INFO, never
    WARNING/CRITICAL -- an absence is not itself a certified problem."""

    name = "missing_data"

    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        insights: List[ProactiveInsight] = []
        fact_tool_names = {item.tool_name for item in evidence.facts}
        for tr in tool_results:
            if tr.tool_name in fact_tool_names:
                continue  # already a successful fact, not missing
            envelope = tr.metadata if isinstance(tr.metadata, dict) else {}
            status = envelope.get("status")
            if status is None or status == "ok":
                continue
            # Only current-operational-fact-family tools (ops_dynamic_* that
            # are not the Knowledge capability) qualify -- never Second
            # Brain/Document (their own zero-result limitations are already
            # handled distinctly, and are not "missing current data").
            if not tr.tool_name.startswith("ops_dynamic_"):
                continue
            if tr.tool_name.endswith("knowledge_get_kpi_definition"):
                continue
            period = envelope.get("period")
            insight = ProactiveInsight(
                id=_stable_id(self.name, tr.tool_name, str(period), str(status)),
                title=f"{tr.tool_name}: current data not available",
                summary=(
                    f"Current operational data for {tr.tool_name}"
                    + (f" (period {period})" if period else "")
                    + f" is not available (status={status})."
                ),
                severity=SEVERITY_INFO,
                confidence=_confidence_for(evidence, corroborated=False),
                evidence=[],
                reasoning_basis=[
                    f"The source explicitly reported status={status} rather than returning a value.",
                ],
                limitations=[envelope.get("reason") or "No reason given by the source."],
            )
            _enrich_with_context(insight, evidence)
            insights.append(insight)
        return insights


class ConflictDetector(Detector):
    """Fires when two CURRENT_OPERATIONAL_FACT results report a different
    numeric value for the same metric -- a structural, deterministic
    comparison (same `data.metric` key, different `data.value`), never a
    free-text conflict guess against historical/document prose. Surfaces
    the conflict; never picks a winner (STEP 8's precedence order still
    only ever ranks source CLASSES against each other, not two facts of
    the same class against one another -- that is a genuine, unresolved
    disagreement between two certified sources and must be reported as
    such)."""

    name = "conflict"

    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        # FASE 4P.1A STEP 10 live-found bug: grouping by metric name alone
        # (without period) misclassified normal change-over-time (e.g. June
        # OEE vs July OEE, genuinely different periods) as a "certified
        # sources disagree" conflict. Two DIFFERENT periods having
        # DIFFERENT values is expected, not a conflict -- a real conflict
        # is two results for the SAME metric AND SAME period disagreeing.
        insights: List[ProactiveInsight] = []
        by_metric_period: Dict[
            "tuple[str, Any]", List["tuple[ToolResult, Dict[str, Any]]"]
        ] = {}
        for tr, envelope in _fact_envelopes(tool_results, evidence):
            metric = envelope["data"].get("metric")
            value = envelope["data"].get("value")
            if metric is None or not isinstance(value, (int, float)):
                continue
            key = (str(metric), envelope.get("period"))
            by_metric_period.setdefault(key, []).append((tr, envelope))

        for (metric, period), pairs in by_metric_period.items():
            values = {e["data"]["value"] for _, e in pairs}
            if len(values) <= 1:
                continue
            evidence_items: List[EvidenceItem] = []
            for tr, _ in pairs:
                evidence_items.extend(_fact_evidence_for(evidence, tr.tool_name))
            insight = ProactiveInsight(
                id=_stable_id(self.name, metric, str(sorted(values)), str(period)),
                title=f"{metric}: conflicting certified values",
                summary=(
                    f"Certified sources report different current values for {metric}"
                    + (f" in the same period ({period})" if period else "")
                    + f": {sorted(values)}. This is surfaced as a disagreement, not resolved."
                ),
                severity=SEVERITY_ATTENTION,
                confidence=_confidence_for(evidence, corroborated=True),
                evidence=evidence_items or list(evidence.facts),
                reasoning_basis=[
                    "Two CURRENT_OPERATIONAL_FACT results for the same metric AND the "
                    "same period disagree -- neither is presumed correct; both are "
                    "certified sources.",
                ],
                limitations=list(evidence.limitations),
            )
            _enrich_with_context(insight, evidence)
            insights.append(insight)
        return insights


class UnresolvedProposalDetector(Detector):
    """Fires when the caller supplies a history of prior ProposedActions
    (V1 has no persistence layer of its own for that history -- STEP 10/11
    deliberately avoid inventing a new store -- so this detector requires
    an explicit `prior_proposals` argument and is a no-op without one;
    a future phase with real persistence can pass real history through
    the same interface with zero changes here)."""

    name = "unresolved_proposal"

    def __init__(self, prior_proposals: Optional[List[ProposedAction]] = None) -> None:
        self._prior = prior_proposals or []

    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        insights: List[ProactiveInsight] = []
        for action in self._prior:
            if action.status != ACTION_STATUS_PROPOSED:
                continue
            insight = ProactiveInsight(
                id=_stable_id(self.name, action.id),
                title=f"Still open: {action.title}",
                summary=f"A previously proposed action has not been resolved: {action.title}.",
                severity=SEVERITY_INFO,
                confidence=CONFIDENCE_LOW,
                reasoning_basis=[f"Proposal {action.id} remains in status PROPOSED."],
                limitations=["No new evidence was gathered for this proposal in this analysis."],
            )
            insights.append(insight)
        return insights


class CertifiedAlertDetector(Detector):
    """Fires only on a source that explicitly self-identifies as a
    certified alert (a FACT envelope whose `data` carries
    `is_certified_alert: True` and an `alert_severity` in
    INFO/ATTENTION/WARNING/CRITICAL). No such capability exists in OPS ONE
    today (confirmed via audit: the only alert-shaped feature,
    PredictiveAlertEngine, is templated/hardcoded display text with no id,
    no persistence, and is not exposed as an OPS Bridge capability at all
    -- it is not a certified source and this detector must never treat it
    as one). Dormant by design until a real certified alert capability
    exists."""

    name = "certified_alert"

    def detect(
        self, tool_results: List[ToolResult], evidence: OperationalEvidence
    ) -> List[ProactiveInsight]:
        insights: List[ProactiveInsight] = []
        for _tr, envelope in _fact_envelopes(tool_results, evidence):
            data = envelope["data"]
            if data.get("is_certified_alert") is not True:
                continue
            alert_severity = data.get("alert_severity")
            if alert_severity not in _VALID_SEVERITIES:
                continue
            period = envelope.get("period")
            insight = ProactiveInsight(
                id=_stable_id(self.name, str(data.get("metric")), str(period), alert_severity),
                title=f"Certified alert: {data.get('metric', 'unspecified')}",
                summary=str(data.get("alert_message") or "A source-certified alert was raised."),
                severity=alert_severity,
                confidence=_confidence_for(evidence, corroborated=True),
                evidence=list(evidence.facts),
                reasoning_basis=["The source itself certified this as an alert; not inferred."],
                limitations=list(evidence.limitations),
            )
            _enrich_with_context(insight, evidence)
            insights.append(insight)
        return insights


_DEFAULT_DETECTORS: List[Detector] = [
    ThresholdBreachDetector(),
    MissingDataDetector(),
    ConflictDetector(),
    CertifiedAlertDetector(),
]


# ---------------------------------------------------------------------------
# Service (STEP 7)
# ---------------------------------------------------------------------------


class ProactiveReasoningService:
    """The smallest service required to turn already-gathered evidence
    into zero or more grounded ProactiveInsights. Stateless by default;
    detectors are deterministic Python, never an LLM call."""

    def __init__(self, detectors: Optional[List[Detector]] = None) -> None:
        self._detectors = detectors if detectors is not None else list(_DEFAULT_DETECTORS)

    def analyze(
        self,
        tool_results: List[ToolResult],
        evidence: Optional[OperationalEvidence] = None,
        *,
        prior_proposals: Optional[List[ProposedAction]] = None,
    ) -> List[ProactiveInsight]:
        """`evidence` may be passed in (e.g. already built once this turn
        by the orchestrator) or omitted, in which case it is built here
        via the frozen, unmodified `build_evidence()` -- never duplicated
        or reimplemented."""
        if evidence is None:
            from openjarvis.agents.operational_evidence import build_evidence

            evidence = build_evidence(tool_results)

        detectors = list(self._detectors)
        if prior_proposals:
            detectors.append(UnresolvedProposalDetector(prior_proposals))

        insights: List[ProactiveInsight] = []
        for detector in detectors:
            insights.extend(detector.detect(tool_results, evidence))
        return insights


# ---------------------------------------------------------------------------
# FASE 4P.1A: activation rule + runtime-injected context blocks.
#
# STEP 1's audit found no existing "intent"/"planning state" mechanism in
# the orchestrator to reuse (AgentContext.metadata is free-form and
# unpopulated for this purpose) -- these are new, but deliberately small,
# bounded, and structural, per STEP 3's explicit constraint against
# brittle business keyword matching. Nothing below reads a KPI name, a
# domain, or any Maffei-specific term; the intent markers are generic
# English/Italian words about the TASK TYPE (asking for analysis/risk/
# attention), not business content.
# ---------------------------------------------------------------------------

# Generic (never business-specific) signal that the user is asking for
# analysis/risk/attention, not just a fact. Small and fixed on purpose --
# a false negative here just means the orchestrator falls back to the
# model's own choice (call maia_analyze_evidence_for_insights, or don't);
# it never blocks anything the model could otherwise do.
_PROACTIVE_INTENT_MARKERS = frozenset(
    {
        "insight",
        "insights",
        "proactive",
        "flag",
        "flagging",
        "flagged",
        "risk",
        "risks",
        "risky",
        "issue",
        "issues",
        "concern",
        "concerns",
        "concerning",
        "alert",
        "alerts",
        "anomaly",
        "anomalies",
        "anomalous",
        "attention",
        "worrying",
        "worrisome",
        "worth attention",
        "watch out",
        "rischio",
        "rischi",
        "problema",
        "problemi",
        "attenzione",
        "segnala",
        "segnalare",
        "allarme",
        "allerta",
        "anomalia",
        "anomalie",
        "preoccup",
    }
)


def has_proactive_intent(user_input: str) -> bool:
    """STEP 3 activation signal: 'an explicit user request to identify
    issues / risks / things worth attention.' A plain factual question
    ('What is current OEE?') contains none of these generic markers and
    correctly does not activate anything."""
    if not user_input:
        return False
    lowered = user_input.lower()
    return any(marker in lowered for marker in _PROACTIVE_INTENT_MARKERS)


def has_certified_alert_source(
    tool_results: List[ToolResult], evidence: OperationalEvidence
) -> bool:
    """STEP 3 activation signal: 'a certified alert returned by a source.'
    Structural, reusing CertifiedAlertDetector's own trigger condition --
    never inferred from prose."""
    for _tr, envelope in _fact_envelopes(tool_results, evidence):
        data = envelope["data"]
        if data.get("is_certified_alert") is True and data.get("alert_severity") in _VALID_SEVERITIES:
            return True
    return False


def should_activate_proactive_analysis(
    user_input: str,
    tool_results: List[ToolResult],
    evidence: OperationalEvidence,
    *,
    tool_names_called_this_turn: Optional[List[str]] = None,
) -> bool:
    """STEP 2/3: the orchestrator calls this, not the model. Activation
    never depends on which specific tool the model chose to call for
    evidence-gathering -- only on (a) the ORIGINAL user request signaling
    proactive intent, (b) an explicit call to the analysis tool this turn,
    or (c) a source self-certifying as an alert -- AND on at least one
    tool having actually been called this run (an ordinary question that
    never called any tool, or that never signaled proactive intent,
    correctly does not activate).

    Gates on `tool_results`, NOT `evidence.has_any_evidence()`: a
    data_not_available / empty-result tool call is exactly the input
    MissingDataDetector needs, but such a call never populates
    evidence.facts/knowledge/historical_experience/document_evidence
    (those lists hold only positive results) -- gating on
    has_any_evidence() would silently exclude the one detector most in
    need of a "nothing was found" result. Live-verified in FASE 4P.1A
    STEP 10: a proactive request against a real data_not_available period
    failed to activate under the has_any_evidence() gate before this fix.
    """
    if not tool_results:
        return False
    if has_proactive_intent(user_input):
        return True
    if tool_names_called_this_turn and "maia_analyze_evidence_for_insights" in tool_names_called_this_turn:
        return True
    if has_certified_alert_source(tool_results, evidence):
        return True
    return False


def render_governed_proactive_block(insights: List[ProactiveInsight]) -> str:
    """STEP 4: the structured result the orchestrator injects. Generated
    entirely from already-computed ProactiveInsight objects -- the model
    never generates this text, only explains it. Explicitly instructs the
    model not to alter any of the governed fields."""
    lines = ["[GOVERNED_PROACTIVE_ANALYSIS]"]
    if not insights:
        lines.append(
            "No insight met the evidence-grounding bar (e.g. no certified "
            "threshold was breached, no conflict between certified sources, "
            "no missing-data gap). This is a normal, successful outcome -- "
            "tell the user current evidence does not support a proactive "
            "alert rather than inventing one."
        )
        return "\n".join(lines)

    for insight in insights:
        lines.append(
            f"- id={insight.id} severity={insight.severity} confidence={insight.confidence} "
            f"status={insight.status}"
        )
        lines.append(f"  title: {insight.title}")
        lines.append(f"  summary: {insight.summary}")
        if insight.reasoning_basis:
            lines.append("  reasoning_basis:")
            for line in insight.reasoning_basis:
                lines.append(f"    - {line}")
        if insight.limitations:
            lines.append("  limitations:")
            for line in insight.limitations:
                lines.append(f"    - {line}")
        for action in insight.proposed_actions:
            lines.append(
                f"  proposed_action id={action.id} status={action.status} "
                f"requires_confirmation={action.requires_confirmation}: {action.title} -- {action.description}"
            )
    lines.append(
        "This block was generated deterministically by ProactiveReasoningService, "
        "not by you. Explain it naturally, but do not change any id/severity/"
        "confidence/status/requires_confirmation value, and do not add a "
        "proposed action that is not listed here."
    )
    return "\n".join(lines)


def render_tool_execution_integrity(tool_results: List[ToolResult]) -> str:
    """STEP 5: tool-claim integrity. Ground truth of what was actually
    executed, reusing the orchestrator's own all_tool_results list --
    no new tracking, no new persistence (STEP 6). Present from the very
    first turn (before any tool call happens) so the exact observed
    failure mode -- fabricating a tool call and its result in prose on a
    turn where nothing was actually executed -- has a concrete, always-
    present ground truth to contradict it."""
    lines = ["[ACTUALLY_EXECUTED_TOOLS]"]
    if not tool_results:
        lines.append("No tools have been executed yet this conversation.")
    else:
        for tr in tool_results:
            lines.append(f"- {tr.tool_name}: success={tr.success}")
    lines.append(
        "Only report a tool as executed, or a result as returned, if it "
        "appears in this list. Never fabricate a tool call, a tool result, "
        "or a turn transcript in your own text -- including as a code "
        "block or JSON snippet that merely looks like a call. If a tool "
        "you wanted to use was not executed, say so honestly instead of "
        "describing what it might have returned."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FASE 4P.1B: closed-world tool manifest + claim-boundary notice.
#
# STEP 1's audit found no existing structured-output/citation/claim-
# validation mechanism to reuse anywhere in OpenJarvis. Per STEP 7's
# explicit ordering (structured schema > governed-section/narrative
# separation > existing validator > minimal post-generation check for
# structurally identifiable claims, never a general NLP fact-checker),
# this stays at level 2: a runtime-rendered governed section the model
# cannot author, plus an explicit narrative/claims boundary. It is a
# strong deterrent, not a hard guarantee -- see docs/
# MAIA_PROACTIVE_INSIGHT_V1.md's KNOWN LIMITATIONS for the honest
# enforcement-level statement this corresponds to.
# ---------------------------------------------------------------------------


def render_available_tools_manifest(tool_names: List[str]) -> str:
    """STEP 5: a closed-world list of every tool actually registered this
    session (reuses the orchestrator's own `self._tools`, no new
    registry). Lets the model answer 'is X available' by lookup instead
    of inference or guesswork -- if a name the user mentions is not on
    this list, it is not a callable tool in this session, full stop."""
    lines = ["[AVAILABLE_TOOLS_THIS_SESSION]"]
    if not tool_names:
        lines.append("No tools are available this session.")
    else:
        for name in sorted(set(tool_names)):
            lines.append(f"- {name}")
    lines.append(
        "This is the complete list of tools you can actually call this "
        "session. A capability or tool name that is not on this list "
        "cannot be executed -- if asked about one, say it is not "
        "available rather than describing what it might return. For an "
        "OPS Bridge capability (e.g. 'ops.production.get_kpi'), the only "
        "way to check whether it truly exists is to call it via a tool "
        "on this list (e.g. maia_analyze_evidence_for_insights) and read "
        "the real result -- never assume it exists or invent its output."
    )
    return "\n".join(lines)


def render_claim_boundary_notice() -> str:
    """STEP 2/4: establishes, once per conversation (turn 0, before any
    tool call), the boundary between MODEL NARRATIVE (reasoning,
    summaries, suggestions -- always allowed) and GOVERNED CLAIMS
    (anything represented as a certified/current value, a validated
    fact, a tool-returned result, a governed insight, or a governed
    proposed action -- which must come from [ACTUALLY_EXECUTED_TOOLS],
    an [OPERATIONAL EVIDENCE COLLECTED THIS TURN] note, or a
    [GOVERNED_PROACTIVE_ANALYSIS] block, never invented). The model is
    never the authority for assigning trust/certification status to
    anything."""
    # Deliberately does NOT reproduce the other blocks' exact bracketed
    # headers verbatim (e.g. writing the literal string
    # "[ACTUALLY_EXECUTED_TOOLS]") -- doing so previously caused a real
    # substring-collision bug where lookups for that header matched this
    # notice instead of the actual block. Refers to them by plain
    # description instead; each block's own header remains the one and
    # only place its exact bracketed tag appears.
    return (
        "[CLAIM BOUNDARY]\n"
        "Two kinds of statement exist in this conversation:\n"
        "1. MODEL NARRATIVE -- your own reasoning, summaries, and "
        "suggestions. Always allowed, and expected.\n"
        "2. GOVERNED CLAIMS -- anything you present as a certified or "
        "current operational value, a validated fact, a tool-returned "
        "result, a governed insight, or a governed proposed action. "
        "These may ONLY come from a later message in this conversation "
        "that lists actually-executed tools, certified operational "
        "evidence, or a governed proactive-analysis result -- never from "
        "this notice itself, and never invented. You are never the "
        "authority for assigning a status like 'validated', 'certified', "
        "or 'current operational fact' to anything -- only a real source "
        "can, and only when it actually said so. If no such message "
        "supports a claim, it is narrative, not a governed claim -- say "
        "so plainly rather than presenting it as one."
    )


__all__ = [
    "SEVERITY_INFO",
    "SEVERITY_ATTENTION",
    "SEVERITY_WARNING",
    "SEVERITY_CRITICAL",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_HIGH",
    "INSIGHT_STATUS_DETECTED",
    "ACTION_STATUS_PROPOSED",
    "ACTION_STATUS_APPROVED",
    "ACTION_STATUS_REJECTED",
    "ACTION_STATUS_EXECUTED",
    "ACTION_STATUS_FAILED",
    "ACTION_STATUS_CANCELLED",
    "ProposedAction",
    "ProactiveInsight",
    "Detector",
    "ThresholdBreachDetector",
    "MissingDataDetector",
    "ConflictDetector",
    "UnresolvedProposalDetector",
    "CertifiedAlertDetector",
    "ProactiveReasoningService",
    "has_proactive_intent",
    "has_certified_alert_source",
    "should_activate_proactive_analysis",
    "render_governed_proactive_block",
    "render_tool_execution_integrity",
    "render_available_tools_manifest",
    "render_claim_boundary_notice",
]
