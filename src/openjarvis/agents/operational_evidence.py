"""OperationalEvidence -- a lightweight evidence model (FASE 4M.5B).

Classifies already-certified data the OPS Bridge tools returned this turn
into FACT / KNOWLEDGE / LIMITATION, and renders a compact note the
orchestrator injects into the conversation so the model can judge
sufficiency and comparisons for itself.

This module computes nothing and duplicates no formula. It only reads
fields the Bridge/capabilities already certified (FASE 4K classifyPeriodStatus,
FASE 4I capability governance, FASE 4M.4A/4M.4B trust_status/provenance/
limitations) and reorganizes them for the model to see clearly. It has no
knowledge of any specific business domain, KPI, or threshold -- "facts",
"knowledge", "domain", and "sufficiency" here are structural categories
derived purely from the shape of the Bridge envelope
({status, data, source, period, period_status, reason, confidence_status}),
not from any Maffei-specific rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openjarvis.core.types import ToolResult

# The one structural marker this module relies on: which dynamic tool is the
# Knowledge capability, so its results are classified as KNOWLEDGE rather
# than FACT. Matches ops_bridge_generic.py::_capability_to_tool_id's naming
# convention ('ops.knowledge.get_kpi_definition' -> tool id ending in this).
_KNOWLEDGE_TOOL_SUFFIX = "knowledge_get_kpi_definition"
_DYNAMIC_TOOL_PREFIX = "ops_dynamic_"


@dataclass
class EvidenceItem:
    kind: str  # 'fact' | 'knowledge'
    tool_name: str
    domain: Optional[str]
    period: Optional[str]
    period_status: Optional[str]
    trust_status: Optional[str]
    provenance: Optional[str]
    summary: str


@dataclass
class OperationalEvidence:
    facts: List[EvidenceItem] = field(default_factory=list)
    knowledge: List[EvidenceItem] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    sources: List[Any] = field(default_factory=list)

    def domains_covered(self) -> Set[str]:
        return {item.domain for item in self.facts if item.domain}

    def trusted_domains_covered(self) -> Set[str]:
        return {
            item.domain
            for item in self.facts
            if item.domain and item.trust_status == "TRUSTED"
        }

    def has_any_evidence(self) -> bool:
        return bool(self.facts or self.knowledge)

    def sufficient_for_cross_domain_claim(self, min_domains: int = 2) -> bool:
        """Generic, domain-agnostic coverage check: a claim spanning "the"
        business (e.g. a single root cause) needs TRUSTED evidence from more
        than one distinct domain -- this counts domains, it does not judge
        any content. Not a diagnosis rule; a caller (or the model, via the
        rendered note) still decides what to conclude, if anything."""
        return len(self.trusted_domains_covered()) >= min_domains

    def render_note(self) -> str:
        """Compact, structured recap of everything gathered so far this
        conversation. Purely a reorganization of already-certified fields --
        no new fact, number, or judgment is introduced here."""
        lines: List[str] = ["[OPERATIONAL EVIDENCE COLLECTED THIS TURN]"]

        if self.facts:
            lines.append("FACTS (from operational capabilities):")
            for item in self.facts:
                period_bit = f" period={item.period}" if item.period else ""
                status_bit = f" period_status={item.period_status}" if item.period_status else ""
                lines.append(
                    f"  - [{item.domain or item.tool_name}]{period_bit}{status_bit} "
                    f"trust_status={item.trust_status}: {item.summary}"
                )
        else:
            lines.append("FACTS: none collected yet.")

        if self.knowledge:
            lines.append("KNOWLEDGE (definitions, from the Knowledge capability):")
            for item in self.knowledge:
                lines.append(
                    f"  - [{item.domain}] trust_status={item.trust_status} "
                    f"provenance={item.provenance}: {item.summary}"
                )

        if self.limitations:
            lines.append("LIMITATIONS reported by the sources above:")
            for lim in self.limitations:
                lines.append(f"  - {lim}")

        covered = sorted(self.trusted_domains_covered())
        lines.append(
            f"DOMAIN COVERAGE: {len(covered)} distinct trusted domain(s) so far"
            + (f" ({', '.join(covered)})" if covered else "")
            + ". A claim about a single cause or problem spanning the whole "
            "business is not supported by evidence from only one domain -- "
            "say evidence is insufficient rather than generalizing beyond "
            "what these domains actually show."
        )
        return "\n".join(lines)


def _domain_from_tool_name(tool_name: str) -> Optional[str]:
    """'ops_dynamic_production_get_kpi' -> 'production'. Purely a label for
    grouping/display -- not used for any computation or trust decision."""
    if not tool_name.startswith(_DYNAMIC_TOOL_PREFIX):
        return None
    stripped = tool_name[len(_DYNAMIC_TOOL_PREFIX):]
    for suffix in ("_get_kpi_definition", "_get_kpi", "_get_status", "_get_metrics", "_list", "_list_capabilities"):
        if stripped.endswith(suffix):
            return stripped[: -len(suffix)]
    return stripped


def build_evidence(tool_results: List[ToolResult]) -> OperationalEvidence:
    """Builds an OperationalEvidence snapshot from the ToolResults collected
    so far. Reads only ToolResult.metadata (the full Bridge envelope,
    already set verbatim by ops_bridge_generic.py) -- never re-fetches,
    never recomputes, never reinterprets a value.
    """
    evidence = OperationalEvidence()

    for tr in tool_results:
        envelope = tr.metadata if isinstance(tr.metadata, dict) else None
        if not envelope:
            continue

        status = envelope.get("status")
        source = envelope.get("source")
        if source:
            evidence.sources.append(source)

        is_knowledge = tr.tool_name.endswith(_KNOWLEDGE_TOOL_SUFFIX)
        domain = _domain_from_tool_name(tr.tool_name)

        if status != "ok":
            reason = envelope.get("reason")
            if reason:
                evidence.limitations.append(f"[{domain or tr.tool_name}] {status}: {reason}")
            continue

        data = envelope.get("data") or {}

        if is_knowledge:
            data_domain = data.get("domain") if isinstance(data, dict) else None
            trust_status = data.get("trust_status") if isinstance(data, dict) else None
            provenance = data.get("provenance") if isinstance(data, dict) else None
            item_limitations = data.get("limitations") if isinstance(data, dict) else None
            reason = envelope.get("reason")
            if reason:
                evidence.limitations.append(f"[{data_domain or domain}] {reason}")
            if isinstance(item_limitations, list):
                for lim in item_limitations:
                    evidence.limitations.append(f"[{data_domain or domain}] {lim}")
            evidence.knowledge.append(
                EvidenceItem(
                    kind="knowledge",
                    tool_name=tr.tool_name,
                    domain=data_domain or domain,
                    period=None,
                    period_status=None,
                    trust_status=trust_status,
                    provenance=provenance,
                    summary=f"{data.get('metric_id')}: {data.get('definition')}"
                    if isinstance(data, dict)
                    else str(data),
                )
            )
        else:
            # Every auto-enabled operational capability is TRUSTED at the
            # capability-registration level (FASE 4I governance -- see
            # ops_bridge_generic.py::_passes_governance). This module does
            # not invent a per-metric trust field OPS ONE doesn't return;
            # it relays the capability-level trust the Registry already
            # certified. FASE 4M.5's audit separately found two metric-level
            # aliasing gaps (Production 'resa', Logistics 'saldo_epal') that
            # this cannot see or correct -- that is an OPS ONE-side fix, out
            # of scope here.
            evidence.facts.append(
                EvidenceItem(
                    kind="fact",
                    tool_name=tr.tool_name,
                    domain=domain,
                    period=envelope.get("period"),
                    period_status=envelope.get("period_status"),
                    trust_status="TRUSTED",
                    provenance=None,
                    summary=str(data),
                )
            )

    return evidence
