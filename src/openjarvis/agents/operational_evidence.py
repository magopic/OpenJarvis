"""OperationalEvidence -- a lightweight, multi-source evidence model.

Originally FASE 4M.5B: classified already-certified OPS Bridge data into
FACT / KNOWLEDGE / LIMITATION, and rendered a compact note the
orchestrator injects into the conversation so the model can judge
sufficiency and comparisons for itself.

FASE 4O.6 (Multi-Source Reasoning / Evidence Composition V1) extends this
to also classify Second Brain (historical experience) and Document
Knowledge (file-sourced) tool results into the same kind of structural,
cross-turn ledger OPS evidence already got -- so a question spanning
multiple governed sources gets ONE coherent, re-injected recap instead of
OPS being the only source with persistent framing. This is additive: the
OPS classification logic below (FACT/KNOWLEDGE from the Bridge envelope
shape) is UNCHANGED, byte-for-byte, from before this phase. Nothing here
computes a new fact, duplicates a formula, or lets one source's data
masquerade as another's -- every item still traces back to exactly the
tool result it came from, tagged with which of five source classes it is:

    CURRENT_OPERATIONAL_FACT  -- certified OPS Bridge operational value (was 'fact')
    KNOWLEDGE_DEFINITION      -- certified OPS Knowledge-capability definition (was 'knowledge')
    HISTORICAL_EXPERIENCE     -- a Second Brain entry -- a PAST case, never current
    DOCUMENT_EVIDENCE         -- a Document Knowledge chunk -- contextual, never certified
    LIMITATION                -- a source explicitly reported a gap (still tagged by origin)

This module has no knowledge of any specific business domain, KPI, or
threshold -- these are structural categories derived purely from the
SHAPE of each tool family's own result (the Bridge envelope for OPS;
Second Brain's entry-summary shape; Document Knowledge's citation shape),
not from any business-specific rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openjarvis.core.types import ToolResult
from openjarvis.tools.ops_bridge_generic import _compact_for_context

# The one structural marker this module relies on for OPS: which dynamic
# tool is the Knowledge capability, so its results are classified as
# KNOWLEDGE_DEFINITION rather than CURRENT_OPERATIONAL_FACT. Matches
# ops_bridge_generic.py::_capability_to_tool_id's naming convention
# ('ops.knowledge.get_kpi_definition' -> tool id ending in this).
_KNOWLEDGE_TOOL_SUFFIX = "knowledge_get_kpi_definition"
_DYNAMIC_TOOL_PREFIX = "ops_dynamic_"

# FASE 4O.6: the structural markers for the other two source families --
# tool NAMES, not content shapes, since (unlike OPS) Second Brain and
# Document Knowledge tools don't share one common envelope; each tool's
# own metadata shape (asserted, not guessed) is read directly per name.
_SECOND_BRAIN_TOOL_NAMES = frozenset(
    {
        "second_brain_search",
        "second_brain_get",
        "second_brain_find_related_experiences",
    }
)
_DOCUMENT_KNOWLEDGE_TOOL_NAMES = frozenset({"document_search"})

SOURCE_CURRENT_OPERATIONAL_FACT = "CURRENT_OPERATIONAL_FACT"
SOURCE_KNOWLEDGE_DEFINITION = "KNOWLEDGE_DEFINITION"
SOURCE_HISTORICAL_EXPERIENCE = "HISTORICAL_EXPERIENCE"
SOURCE_DOCUMENT_EVIDENCE = "DOCUMENT_EVIDENCE"


@dataclass
class EvidenceItem:
    kind: str  # 'fact' | 'knowledge' -- kept for internal/back-compat readability
    source_class: str  # one of the five SOURCE_* constants above -- the authoritative tag
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
    # FASE 4O.6: two new, separately-tracked lists -- deliberately NOT
    # merged into `facts`/`knowledge`, so a caller can never accidentally
    # sum "all evidence" and get a domain-coverage count that silently
    # includes historical or document-sourced items alongside certified
    # current facts (`trusted_domains_covered()` below stays scoped to
    # `facts` exactly as it always was).
    historical_experience: List[EvidenceItem] = field(default_factory=list)
    document_evidence: List[EvidenceItem] = field(default_factory=list)
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
        return bool(self.facts or self.knowledge or self.historical_experience or self.document_evidence)

    def sufficient_for_cross_domain_claim(self, min_domains: int = 2) -> bool:
        """Generic, domain-agnostic coverage check: a claim spanning "the"
        business (e.g. a single root cause) needs TRUSTED evidence from more
        than one distinct domain -- this counts domains, it does not judge
        any content. Not a diagnosis rule; a caller (or the model, via the
        rendered note) still decides what to conclude, if anything."""
        return len(self.trusted_domains_covered()) >= min_domains

    def render_note(self) -> str:
        """Compact, structured recap of everything gathered so far this
        conversation, across every governed source class. Purely a
        reorganization of already-certified/already-retrieved fields --
        no new fact, number, citation, or judgment is introduced here.

        FASE 4O.6: extended with two new sections (HISTORICAL EXPERIENCE,
        DOCUMENT EVIDENCE) alongside the original FACTS/KNOWLEDGE -- each
        under its own header so the model never has to infer which
        source class an item belongs to, plus one PRECEDENCE line stating
        the generic (never business-specific) rule for when sources
        compose or conflict.
        """
        lines: List[str] = ["[OPERATIONAL EVIDENCE COLLECTED THIS TURN]"]

        if self.facts:
            lines.append("FACTS -- CURRENT_OPERATIONAL_FACT (certified, from operational capabilities):")
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
            lines.append("KNOWLEDGE -- KNOWLEDGE_DEFINITION (certified, from the Knowledge capability):")
            for item in self.knowledge:
                lines.append(
                    f"  - [{item.domain}] trust_status={item.trust_status} "
                    f"provenance={item.provenance}: {item.summary}"
                )

        if self.historical_experience:
            lines.append(
                "HISTORICAL EXPERIENCE -- HISTORICAL_EXPERIENCE (Second Brain, PAST cases -- "
                "precedent/context only, never the current situation):"
            )
            for item in self.historical_experience:
                lines.append(f"  - [{item.tool_name}] {item.summary}")

        if self.document_evidence:
            lines.append(
                "DOCUMENT EVIDENCE -- DOCUMENT_EVIDENCE (contextual/supporting source, "
                "NEVER a certified operational value even if it contains a number):"
            )
            for item in self.document_evidence:
                lines.append(f"  - [{item.provenance}] {item.summary}")

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

        if self.knowledge or self.historical_experience or self.document_evidence:
            lines.append(
                "PRECEDENCE (generic, not business-specific): a certified FACT above always "
                "outranks a number merely mentioned in DOCUMENT EVIDENCE or recalled from "
                "HISTORICAL EXPERIENCE -- if they differ, say so explicitly rather than "
                "silently picking one. A certified KNOWLEDGE definition always outranks any "
                "definition implied by document text or general knowledge. HISTORICAL "
                "EXPERIENCE is precedent, not proof of current cause -- do not state a past "
                "case caused the current situation without current evidence saying so. "
                "DOCUMENT EVIDENCE is supporting/contextual, not a substitute for a FACT."
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


def _evidence_references_bit(refs: Any) -> str:
    """M2.5B Phase 1: renders the same [UNVERIFIED] semantic
    second_brain_tools.py already computes at read time (each ref
    dict's own "verification_status" key, never re-derived here) into
    the structured evidence recap -- closing the same propagation-gap
    class M2.5A.1 already fixed once for Document Knowledge. Returns
    "" when there is nothing to disclose (entry carries no references)."""
    if not isinstance(refs, list) or not refs:
        return ""
    labels = [f"{r.get('capability')} [{r.get('verification_status', 'UNVERIFIED')}]" for r in refs if isinstance(r, dict)]
    if not labels:
        return ""
    return "[evidence_references (stored, NOT independently re-verified): " + "; ".join(labels) + "]"


def _render_second_brain_entry_summary(entry: Dict[str, Any]) -> str:
    """One line per entry, preserving type/lifecycle/match-basis -- never
    collapsed into free prose that would lose the PROBLEM/DECISION/
    ACTION/OUTCOME/LESSON distinction (FASE 4O.6 STEP 5)."""
    bits = [f"[{entry.get('id')}] ({entry.get('type')}) {entry.get('title')} -- {entry.get('summary')}"]
    if entry.get("archived"):
        bits.append("[ARCHIVED]")
    if entry.get("superseded_by"):
        bits.append(f"[superseded_by={entry.get('superseded_by')} -- a newer version exists]")
    if "outcome_backed" in entry:
        bits.append(f"[outcome_backed={entry.get('outcome_backed')}]")
    evidence_bit = _evidence_references_bit(entry.get("evidence_references"))
    if evidence_bit:
        bits.append(evidence_bit)
    return " ".join(bits)


def _render_second_brain_candidate_summary(cand: Dict[str, Any]) -> str:
    basis_parts = []
    if cand.get("matched_domains"):
        basis_parts.append(f"domain={cand['matched_domains']}")
    if cand.get("matched_entities"):
        basis_parts.append(f"entity={cand['matched_entities']}")
    if cand.get("matched_terms"):
        basis_parts.append(f"term={cand['matched_terms']}")
    if cand.get("relationship_basis"):
        basis_parts.append(f"relationship=[{'; '.join(cand['relationship_basis'])}]")
    text = (
        f"[{cand.get('entry_id')}] ({cand.get('type')}, {cand.get('active_or_superseded')}) "
        f"matched via {cand.get('retrieval_level')}: {', '.join(basis_parts) or 'n/a'}"
    )
    evidence_bit = _evidence_references_bit(cand.get("evidence_references"))
    if evidence_bit:
        text += " " + evidence_bit
    return text


def _classify_second_brain_result(evidence: OperationalEvidence, tr: ToolResult) -> None:
    """FASE 4O.6: Second Brain results carry no OPS Bridge envelope --
    each tool's own metadata shape (asserted from second_brain_tools.py,
    not guessed) is read directly. Every item is tagged
    HISTORICAL_EXPERIENCE and rendered with its stored trust_status/
    provenance -- Second Brain's own EntryTrustStatus vocabulary, never
    conflated with OPS ONE's (see second_brain/types.py's own warning
    against comparing the two namespaces)."""
    meta = tr.metadata if isinstance(tr.metadata, dict) else {}

    entries: List[Dict[str, Any]] = []
    if isinstance(meta.get("entries"), list):  # second_brain_search
        entries = meta["entries"]
    elif "id" in meta and "type" in meta:  # second_brain_get -- the summary dict itself, unwrapped
        entries = [meta]

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        evidence.historical_experience.append(
            EvidenceItem(
                kind="historical",
                source_class=SOURCE_HISTORICAL_EXPERIENCE,
                tool_name=tr.tool_name,
                domain=(entry.get("domains") or [None])[0] if isinstance(entry.get("domains"), list) else None,
                period=None,
                period_status=None,
                trust_status=entry.get("trust_status"),
                provenance=entry.get("provenance"),
                summary=_render_second_brain_entry_summary(entry),
            )
        )

    candidates = meta.get("candidates") if isinstance(meta.get("candidates"), list) else []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        evidence.historical_experience.append(
            EvidenceItem(
                kind="historical",
                source_class=SOURCE_HISTORICAL_EXPERIENCE,
                tool_name=tr.tool_name,
                domain=None,
                period=None,
                period_status=None,
                trust_status=None,
                provenance=None,
                summary=_render_second_brain_candidate_summary(cand),
            )
        )

    # STEP 11: an explicit zero-results call is a reportable gap, not
    # silence -- the model must be able to say "no historical precedent
    # was found" rather than never mentioning it looked.
    if not entries and not candidates:
        num = meta.get("num_results", meta.get("num_candidates"))
        if num == 0:
            evidence.limitations.append(
                f"[{tr.tool_name}] Second Brain queried -- no historical precedent found."
            )


def _render_document_result_summary(r: Dict[str, Any]) -> str:
    """M2.5A.1: mirrors _render_second_brain_entry_summary's bracket-
    append pattern -- Second Brain's own superseded_by signal already
    survived into its evidence summary that way; Document Knowledge's
    M2.5A status/supersession fields did not (the exact propagation gap
    this phase closes). Appends at most one version-state bracket, using
    only fields the document_search tool already computed -- no new
    fact, comparison, or diff is made here.

    same_content_as_successor semantics (never a guess):
      True  -> state plainly the stored content is identical
      False -> state only that the stored content hash differs --
               never characterize WHAT differs
      None  -> no content-equality claim at all

    M2.5A orphaned-supersession repair: when successor_missing is True,
    a distinct broken-reference bracket is used instead -- the recorded
    successor no longer resolves (e.g. it was removed from the
    workspace). This is a broken lifecycle reference, never evidence
    the predecessor is CURRENT again and never evidence the
    supersession decision itself should be reversed -- only a
    deliberate human repair (DocumentKnowledgeService.clear_supersession,
    via `jarvis document unsupersede`) changes status.
    """
    content = str(r.get("content", ""))
    if r.get("status") != "SUPERSEDED":
        return content

    if r.get("successor_missing"):
        return f"{content} [SUPERSEDED -- recorded successor is missing from the workspace; this is a broken reference, not evidence this document is current]"

    successor = r.get("superseded_by_filename") or r.get("superseded_by_doc_id")
    same_content = r.get("same_content_as_successor")
    if same_content is True:
        identity_bit = "; stored content is identical to the successor (same content hash) -- no content difference can be established"
    elif same_content is False:
        identity_bit = "; stored content hash differs from the successor -- the nature of the difference is NOT established by this alone"
    else:
        identity_bit = ""
    return f"{content} [SUPERSEDED -- a newer version exists: {successor}{identity_bit}]"


def _classify_document_result(evidence: OperationalEvidence, tr: ToolResult) -> None:
    """FASE 4O.6: Document Knowledge results carry no OPS Bridge envelope
    either. Every item is tagged DOCUMENT_EVIDENCE with its real citation
    (filename/page/section) as `provenance` -- exactly what lets the model
    say "According to X, page Y..." instead of presenting the text as its
    own knowledge, and exactly what STEP 12 needs to detect a document
    number disagreeing with a certified FACT.

    M2.5A.1: also preserves each result's version-state metadata
    (status/superseded_by_filename/same_content_as_successor) into the
    rendered summary -- previously dropped here, which meant the
    structured, per-turn evidence recap (the model's freshest, most
    repeated context) carried none of it even though the raw tool
    output did."""
    meta = tr.metadata if isinstance(tr.metadata, dict) else {}
    results = meta.get("results") if isinstance(meta.get("results"), list) else []

    for r in results:
        if not isinstance(r, dict):
            continue
        evidence.document_evidence.append(
            EvidenceItem(
                kind="document",
                source_class=SOURCE_DOCUMENT_EVIDENCE,
                tool_name=tr.tool_name,
                domain=None,
                period=None,
                period_status=None,
                trust_status=None,
                provenance=r.get("citation"),
                summary=_render_document_result_summary(r),
            )
        )

    if not results and meta.get("num_results") == 0:
        evidence.limitations.append(
            f"[{tr.tool_name}] Document Knowledge queried -- no matching source found."
        )


def build_evidence(tool_results: List[ToolResult]) -> OperationalEvidence:
    """Builds an OperationalEvidence snapshot from the ToolResults collected
    so far, across every governed source family this turn.

    OPS Bridge results: read only ToolResult.metadata (the full Bridge
    envelope, already set verbatim by ops_bridge_generic.py) -- never
    re-fetches, never recomputes, never reinterprets a value. Second
    Brain / Document Knowledge results: read only their own tool's
    already-asserted metadata shape (FASE 4O.6) -- same non-invasive
    contract, different tool family.
    """
    evidence = OperationalEvidence()

    for tr in tool_results:
        if tr.tool_name in _SECOND_BRAIN_TOOL_NAMES:
            _classify_second_brain_result(evidence, tr)
            continue
        if tr.tool_name in _DOCUMENT_KNOWLEDGE_TOOL_NAMES:
            _classify_document_result(evidence, tr)
            continue

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

        raw_data = envelope.get("data") or {}
        # Apply the same generic, capability-agnostic truncation the tool
        # result text already went through (ops_bridge_generic._summarize).
        # Without this, a list field trimmed for the model in the tool-call
        # turn would reappear here in full via the untruncated envelope --
        # silently leaking the omitted items back into context and letting
        # the model "see" (and name) records it was told were not returned.
        data, was_truncated = _compact_for_context(raw_data)
        if was_truncated:
            evidence.limitations.append(
                f"[{domain or tr.tool_name}] One or more list fields in this "
                "result were truncated for size; the omitted items are "
                "UNKNOWN -- do not name, guess, or characterize them, only "
                "the items actually present above."
            )

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
                    source_class=SOURCE_KNOWLEDGE_DEFINITION,
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
                    source_class=SOURCE_CURRENT_OPERATIONAL_FACT,
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
