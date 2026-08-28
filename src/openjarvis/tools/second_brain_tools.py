"""Model-callable tools over MAIA Second Brain V1 (FASE 4N.2, hardened 4N.2A).

Every tool here is a thin wrapper around ``SecondBrainService`` --
none of them touch SQL, none of them can bypass governance, and there
is no generic "write anything" tool. The two-step capture workflow
(propose -> confirm) is enforced by the service, not re-implemented
here; these tools exist only to expose that contract to a model.

FASE 4N.2A: the authorization-sensitive identity (``actor``/
``created_by``) is no longer a model-supplied argument on ANY of these
tools. Each tool takes an optional ``principal`` constructor argument;
``_build_tools()`` (``cli/ask.py``/``cli/chat_cmd.py``) injects
``identity.resolve_runtime_principal()`` there, mirroring exactly how
memory tools already receive a constructor-injected ``backend``. A
tool constructed without an explicit ``principal`` (direct
instantiation, e.g. in tests) resolves one lazily via the same
function, so the model's action space never includes "supply your own
identity" in the first place -- there is no parameter for it to fill
in, spoofed or otherwise.

Tool id <-> conceptual contract name (see docs/MAIA_SECOND_BRAIN_V1.md):
    second_brain_search                   <-> second_brain.search
    second_brain_get                      <-> second_brain.get
    second_brain_propose_entry            <-> second_brain.propose_entry
    second_brain_confirm_entry            <-> second_brain.confirm_entry
    second_brain_link                     <-> second_brain.link
    second_brain_archive                  <-> second_brain.archive
    second_brain_find_related_experiences <-> second_brain.find_related_experiences (FASE 4N.4)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.identity import resolve_runtime_principal
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import EntryType, EvidenceReference, SecondBrainEntry
from openjarvis.tools._stubs import BaseTool, ToolSpec

_ENTRY_TYPES = [
    "EVENT", "PROBLEM", "OBSERVATION", "HYPOTHESIS", "DECISION",
    "ACTION", "OUTCOME", "LESSON", "PROCEDURE", "MEETING_NOTE",
]
_TRUST_STATUSES = ["OBSERVED", "HYPOTHESIS", "VERIFIED", "DECISION", "OUTCOME", "LEARNED"]
_VISIBILITIES = ["PRIVATE", "TEAM", "COMPANY"]
_RELATIONSHIP_TYPES = [
    "CAUSES", "CORRELATES_WITH", "PRECEDES", "RESULTED_IN", "RESOLVED_BY",
    "DECIDED_IN", "RELATED_TO", "SIMILAR_TO", "AFFECTS", "SUPERSEDES", "DUPLICATES",
]

_EVIDENCE_REFERENCE_SCHEMA = {
    "type": "object",
    "description": (
        "A pointer to a certified OPS ONE capability -- never a copied "
        "KPI value. Only reference a capability/metric/period you actually "
        "called via an OPS tool this conversation; do not invent one."
    ),
    "properties": {
        "capability": {"type": "string", "description": "e.g. 'ops.production.get_kpi'"},
        "domain": {"type": "string"},
        "metric": {"type": "string"},
        "period": {"type": "string"},
        "filters": {"type": "object"},
        "trust_status_at_capture": {
            "type": "string",
            "description": "The OPS Knowledge trust_status the capability reported, verbatim.",
        },
        "fetched_at": {"type": "number"},
    },
    "required": ["capability", "domain"],
}


def _service() -> SecondBrainService:
    return SecondBrainService()


def _parse_evidence_refs(raw: Optional[List[Dict[str, Any]]]) -> List[EvidenceReference]:
    return [EvidenceReference(**ref) for ref in (raw or [])]


def _entry_summary(
    service: SecondBrainService, entry: SecondBrainEntry, *, actor: Optional[str]
) -> Dict[str, Any]:
    """FASE 4N.3 STEP 3: relationships are returned with the actual
    neighbor entry id, not just a status count. Without this, a model
    could see "this PROBLEM has 1 CONFIRMED relationship" but had no
    way to learn *which entry* that pointed to -- meaning it could
    never walk a PROBLEM -> HYPOTHESIS -> DECISION -> ACTION -> OUTCOME
    -> LESSON chain using only second_brain_search/second_brain_get.
    This is a tool-output enrichment only (get_relationships() already
    returned full Relationship objects at the service layer) -- no
    schema change, reusing the frozen relationships table as-is.
    """
    # M2.5C Phase 1: service.get_relationships() is a raw, unfiltered
    # read (no visibility check on either endpoint) -- resolving each
    # neighbor through the SAME principal-scoped access path
    # (service.get_entry(..., actor=actor)) that already gates this
    # entry itself, and omitting the relationship entirely when the
    # neighbor isn't visible, closes the leak this raw read would
    # otherwise create. Mirrors second_brain/projections/obsidian.py's
    # _relationship_line(), which already does exactly this. Full
    # omission, not a placeholder -- a placeholder like "[HIDDEN]"
    # would itself confirm the relationship exists.
    rels = service.get_relationships(entry.id, direction="both")
    relationships = []
    for rel in rels:
        is_outgoing = rel.source_entry_id == entry.id
        related_id = rel.target_entry_id if is_outgoing else rel.source_entry_id
        try:
            neighbor = service.get_entry(related_id, actor=actor)
        except SecondBrainValidationError:
            neighbor = None
        if neighbor is None:
            continue
        relationships.append(
            {
                "relationship_id": rel.id,
                "relation_type": rel.relation_type.value,
                "status": rel.status.value,
                "direction": "outgoing" if is_outgoing else "incoming",
                "related_entry_id": related_id,
            }
        )
    result = {
        "id": entry.id,
        "type": entry.type.value,
        "title": entry.title,
        "summary": entry.summary,
        "trust_status": entry.trust_status.value,
        "provenance": entry.provenance,
        "domains": entry.domains,
        "entities": entry.entities,
        "visibility": entry.visibility.value,
        "relationships": relationships,
        "archived": entry.archived_at is not None,
        "superseded_by": entry.superseded_by,
    }
    if entry.type is EntryType.LESSON or entry.trust_status.value == "LEARNED":
        result["outcome_backed"] = service.is_outcome_backed(entry.id)
    if entry.evidence_references:
        result["evidence_references"] = [_evidence_reference_summary(ref) for ref in entry.evidence_references]
    return result


def _evidence_reference_summary(ref: EvidenceReference) -> Dict[str, Any]:
    """M2.5B Phase 1 -- render-time-only representation of a stored
    EvidenceReference. ``verification_status`` is computed here, never
    stored: this codebase has no mechanism (yet) that re-checks a
    reference against live OPS Bridge/session state, so it is ALWAYS
    "UNVERIFIED" today, regardless of the memory entry's own
    trust_status. See docs/MAIA_SECOND_BRAIN_V1.md, Evidence Reference
    Honest Rendering V1 -- evidence_reference != evidence_verified."""
    return {
        "capability": ref.capability,
        "domain": ref.domain,
        "metric": ref.metric,
        "period": ref.period,
        "filters": ref.filters,
        "trust_status_at_capture": ref.trust_status_at_capture,
        "fetched_at": ref.fetched_at,
        "verification_status": "UNVERIFIED",
    }


def _render_entry_text(s: Dict[str, Any]) -> str:
    """Render an entry summary as text for ToolResult.content.

    FASE 4N.3: this -- not the ``metadata`` dict -- is what actually
    reaches the model. The orchestrator's tool-calling loop only
    threads ``ToolResult.content`` back into the conversation
    (``orchestrator.py``'s ``Message(role=Role.TOOL, content=...)``);
    ``metadata`` never does. Relationship/outcome-backing data placed
    only in ``metadata`` would be structurally invisible to the model,
    silently defeating experience-chain traversal -- so every field a
    model needs to walk PROBLEM -> HYPOTHESIS -> ... -> LESSON, or to
    tell an outcome-backed lesson from an unlinked one, is rendered
    into this string.
    """
    lines = [f"[{s['id']}] ({s['type']}, trust={s['trust_status']}) {s['title']} -- {s['summary']}"]
    if s.get("visibility") in ("TEAM", "COMPANY"):
        # M2.5C Phase 1: honesty only, no new enforcement -- this
        # runtime has no team/org/tenant membership primitive
        # (Phase 0 finding), so TEAM/COMPANY behave identically to
        # each other and to "not PRIVATE" today. PRIVATE is unchanged
        # and gets no such note.
        lines.append(
            f"  visibility={s['visibility']} (stored label only -- this runtime has no "
            "group-membership authorization for TEAM/COMPANY; do not treat as access-controlled)"
        )
    if s.get("domains") or s.get("entities"):
        lines.append(f"  domains={s.get('domains') or []} entities={s.get('entities') or []}")
    if "outcome_backed" in s:
        lines.append(
            f"  outcome_backed={s['outcome_backed']}"
            + ("" if s["outcome_backed"] else " (not linked to any CONFIRMED OUTCOME -- treat as unverified)")
        )
    if s["relationships"]:
        rel_lines = [
            f"    {r['direction']} {r['relation_type']} ({r['status']}) -> {r['related_entry_id']}"
            for r in s["relationships"]
        ]
        lines.append("  relationships:\n" + "\n".join(rel_lines))
    else:
        lines.append("  relationships: none")
    if s.get("archived"):
        lines.append("  [ARCHIVED]")
    if s.get("superseded_by"):
        lines.append(f"  superseded_by={s['superseded_by']} (a newer version exists -- prefer it)")
    if s.get("evidence_references"):
        lines.append("  evidence_references (stored with this memory, NOT independently re-verified):")
        for ref in s["evidence_references"]:
            lines.append(
                f"    - [UNVERIFIED] capability={ref.get('capability')} domain={ref.get('domain')} "
                f"metric={ref.get('metric')} period={ref.get('period')}"
            )
    return "\n".join(lines)


@ToolRegistry.register("second_brain_search")
class SecondBrainSearchTool(BaseTool):
    """Exact/FTS retrieval only -- no semantic similarity, no embeddings."""

    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_search",
            description=(
                "Search MAIA's Second Brain (governed HISTORICAL organizational "
                "memory: past events, problems, hypotheses, decisions, outcomes, "
                "lessons) -- NOT OPS ONE KPI data (use the ops_dynamic_* tools for "
                "current facts) and NOT free-text conversation memory "
                "(memory_search). A result here describes what happened in a PAST "
                "case, never the current situation -- do not restate it as a "
                "current fact. Each result lists its 'relationships' with the "
                "related entry's id; call second_brain_get on that id to walk a "
                "chain (e.g. PROBLEM -> HYPOTHESIS -> DECISION -> ACTION -> "
                "OUTCOME -> LESSON) one hop at a time. A LESSON also reports "
                "'outcome_backed' -- only true if a CONFIRMED relationship links it "
                "to an OUTCOME; an unbacked lesson is unverified, say so. "
                "Combine free-text 'query' with any filters -- shared domain/entity "
                "overlap with the current situation is the explicit basis for "
                "calling two cases 'similar,' never a computed percentage. When "
                "looking for a similar PAST case (not the exact same one), searching "
                "by the current entity alone will correctly find nothing if that "
                "exact entity was never recorded before -- also try a broader search "
                "(by domain, or by entry type, or by a shared term) before concluding "
                "nothing similar exists; report an honest 'not found' only after that "
                "broader search also comes up empty. "
                "SIMILAR_TO/CORRELATES_WITH relationships never mean CAUSES. "
                "Returns confirmed entries only -- proposals awaiting confirmation "
                "never appear here. PRIVATE entries not belonging to the caller "
                "running this tool are silently excluded -- there is no parameter "
                "to search as a different identity. If a result includes "
                "evidence_references, each is marked [UNVERIFIED] -- it is a "
                "pointer the memory's author recorded, never independently "
                "re-checked by MAIA; do not treat it as proof the underlying "
                "evidence still exists, is current, or matches the claim."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search (optional)."},
                    "domain": {"type": "string"},
                    "entity": {"type": "string"},
                    "type": {"type": "string", "enum": _ENTRY_TYPES},
                    "trust_status": {"type": "string", "enum": _TRUST_STATUSES},
                    "since": {"type": "number", "description": "Unix timestamp, entry's event timestamp >= this."},
                    "until": {"type": "number", "description": "Unix timestamp, entry's event timestamp <= this."},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        actor = self._principal
        query = params.get("query")
        limit = int(params.get("limit", 20))

        try:
            if query:
                results = self._service.search_entries(query, actor=actor, limit=max(limit * 4, 50))
            else:
                results = self._service.list_entries(
                    actor=actor,
                    entry_type=params.get("type"),
                    trust_status=params.get("trust_status"),
                    domain=params.get("domain"),
                    entity=params.get("entity"),
                    since=params.get("since"),
                    until=params.get("until"),
                    limit=max(limit * 4, 50),
                )
        except SecondBrainValidationError as exc:
            return ToolResult(tool_name="second_brain_search", success=False, content=str(exc))

        # When a free-text query was combined with filters, apply the
        # remaining filters in Python -- search_entries_fts doesn't take
        # them, and re-deriving FTS+filter SQL here would duplicate logic
        # that already exists correctly in list_entries.
        if query:
            if params.get("type"):
                results = [e for e in results if e.type.value == params["type"]]
            if params.get("trust_status"):
                results = [e for e in results if e.trust_status.value == params["trust_status"]]
            if params.get("domain"):
                results = [e for e in results if params["domain"] in e.domains]
            if params.get("entity"):
                results = [e for e in results if params["entity"] in e.entities]
            if params.get("since") is not None:
                results = [e for e in results if e.timestamp is None or e.timestamp >= params["since"]]
            if params.get("until") is not None:
                results = [e for e in results if e.timestamp is None or e.timestamp <= params["until"]]

        results = results[:limit]

        if not results:
            return ToolResult(
                tool_name="second_brain_search",
                success=True,
                content="No matching Second Brain entries found.",
                metadata={"num_results": 0},
            )

        summaries = [_entry_summary(self._service, e, actor=actor) for e in results]
        return ToolResult(
            tool_name="second_brain_search",
            success=True,
            content="\n".join(_render_entry_text(s) for s in summaries),
            metadata={"num_results": len(summaries), "entries": summaries},
        )


@ToolRegistry.register("second_brain_get")
class SecondBrainGetTool(BaseTool):
    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_get",
            description=(
                "Fetch one Second Brain entry by id -- HISTORICAL organizational "
                "memory, never the current situation. Use this to follow a "
                "'related_entry_id' from a second_brain_search result one hop at "
                "a time (e.g. from a PROBLEM to its linked DECISION or OUTCOME). "
                "Denied if it is PRIVATE to someone other than the caller running "
                "this tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string"},
                },
                "required": ["entry_id"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        entry_id = params.get("entry_id", "")
        actor = self._principal
        try:
            entry = self._service.get_entry(entry_id, actor=actor)
        except SecondBrainValidationError as exc:
            return ToolResult(tool_name="second_brain_get", success=False, content=str(exc))
        if entry is None:
            return ToolResult(
                tool_name="second_brain_get", success=False, content=f"No such entry: {entry_id}"
            )
        summary = _entry_summary(self._service, entry, actor=actor)
        return ToolResult(
            tool_name="second_brain_get",
            success=True,
            content=_render_entry_text(summary),
            metadata=summary,
        )


@ToolRegistry.register("second_brain_propose_entry")
class SecondBrainProposeEntryTool(BaseTool):
    """Step 1 of capture. Never persists a searchable memory by itself."""

    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_propose_entry",
            description=(
                "Propose a new Second Brain memory. This does NOT save anything "
                "permanently and the entry will NOT appear in second_brain_search "
                "yet. You MUST show the user what you propose to remember (title + "
                "summary + trust_status) and ask them to confirm, in these exact "
                "terms or similar -- e.g. 'Vuoi che salvi questa conclusione nel "
                "Second Brain?'. Only call second_brain_confirm_entry after the "
                "user has explicitly said yes in a later message. Never call "
                "confirm_entry from inferred intent, and never treat silence as "
                "consent. The memory is automatically attributed to the current "
                "runtime identity -- there is no field to attribute it to someone "
                "else."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": _ENTRY_TYPES},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "provenance": {
                        "type": "string",
                        "description": "Where this comes from -- required, never leave empty.",
                    },
                    "source": {"type": "string", "description": "e.g. 'conversation'."},
                    "trust_status": {
                        "type": "string",
                        "enum": _TRUST_STATUSES,
                        "description": (
                            "Be conservative: an unverified claim is HYPOTHESIS, "
                            "not VERIFIED or DECISION. Never mark LEARNED unless "
                            "domains/entities/evidence_references ground it."
                        ),
                    },
                    "visibility": {"type": "string", "enum": _VISIBILITIES, "default": "PRIVATE"},
                    "domains": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "timestamp": {"type": "number", "description": "Required for type=DECISION."},
                    "confidence": {"type": "number", "description": "0..1, only if you have a real basis for it."},
                    "evidence_references": {"type": "array", "items": _EVIDENCE_REFERENCE_SCHEMA},
                },
                "required": ["type", "title", "summary", "provenance", "source", "trust_status"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        evidence_refs = _parse_evidence_refs(params.get("evidence_references"))
        try:
            proposal = self._service.propose_entry(
                type=params.get("type"),
                title=params.get("title", ""),
                summary=params.get("summary", ""),
                created_by=self._principal,
                provenance=params.get("provenance", ""),
                source=params.get("source", ""),
                trust_status=params.get("trust_status"),
                visibility=params.get("visibility", "PRIVATE"),
                domains=params.get("domains"),
                entities=params.get("entities"),
                timestamp=params.get("timestamp"),
                confidence=params.get("confidence"),
                evidence_references=evidence_refs,
            )
        except SecondBrainValidationError as exc:
            return ToolResult(
                tool_name="second_brain_propose_entry", success=False, content=str(exc)
            )

        prompt = (
            f"Proposal (not yet saved): [{params.get('type')}] {params.get('title')} -- "
            f"{params.get('summary')} (trust_status={params.get('trust_status')}). "
            f"proposal_id={proposal.id}. Ask the user to confirm before calling "
            f"second_brain_confirm_entry."
        )
        return ToolResult(
            tool_name="second_brain_propose_entry",
            success=True,
            content=prompt,
            metadata={"proposal_id": proposal.id, "status": proposal.status.value},
        )


@ToolRegistry.register("second_brain_confirm_entry")
class SecondBrainConfirmEntryTool(BaseTool):
    """Step 2 of capture -- the only tool call that persists anything."""

    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_confirm_entry",
            description=(
                "Persist a previously proposed memory. Call this ONLY after the "
                "user has explicitly confirmed in their own message (e.g. 'Sì', "
                "'Salvala', 'Yes save it') -- never as a follow-up to your own "
                "proposal without a real user reply in between, and never because "
                "the user changed topic without objecting. If the user is "
                "correcting an existing entry (e.g. 'Correggi: la causa verificata "
                "era il cambio formato'), pass that entry's id as "
                "supersedes_entry_id -- the old entry is preserved and linked, "
                "never overwritten."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "supersedes_entry_id": {
                        "type": "string",
                        "description": "Set only when this confirmation corrects an existing entry.",
                    },
                },
                "required": ["proposal_id"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            entry = self._service.confirm_entry(
                params.get("proposal_id", ""),
                actor=self._principal,
                supersedes_entry_id=params.get("supersedes_entry_id"),
            )
        except SecondBrainValidationError as exc:
            return ToolResult(
                tool_name="second_brain_confirm_entry", success=False, content=str(exc)
            )
        return ToolResult(
            tool_name="second_brain_confirm_entry",
            success=True,
            content=f"Saved: [{entry.id}] {entry.title}",
            metadata={"entry_id": entry.id},
        )


@ToolRegistry.register("second_brain_link")
class SecondBrainLinkTool(BaseTool):
    """Always creates a PROPOSED relationship -- never auto-CONFIRMED."""

    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_link",
            description=(
                "Propose a relationship between two existing Second Brain entries. "
                "Always created as PROPOSED -- a relationship you infer is never "
                "automatically treated as certified; a human must confirm it "
                "separately before other reasoning should rely on it as fact."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_entry_id": {"type": "string"},
                    "target_entry_id": {"type": "string"},
                    "relation_type": {"type": "string", "enum": _RELATIONSHIP_TYPES},
                    "source": {"type": "string", "description": "e.g. 'conversation'."},
                    "confidence": {"type": "number"},
                },
                "required": ["source_entry_id", "target_entry_id", "relation_type", "source"],
            },
            category="memory",
        )

    def _is_readable(self, entry_id: str) -> bool:
        """M2.5C Phase 1: service.create_relationship() only checks that
        both entries EXIST (a raw, unfiltered store read) -- it never
        checks whether the caller is authorized to read them, making it
        an existence oracle / unauthorized-mutation path against a
        PRIVATE entry someone else owns. This reuses the exact same
        principal-scoped access path get/search already enforce
        (service.get_entry(..., actor=...)) entirely at the tool layer
        -- no change to create_relationship() or its existence check,
        which still runs redundantly afterward and is harmless."""
        try:
            entry = self._service.get_entry(entry_id, actor=self._principal)
        except SecondBrainValidationError:
            return False
        return entry is not None

    def execute(self, **params: Any) -> ToolResult:
        source_entry_id = params.get("source_entry_id", "")
        target_entry_id = params.get("target_entry_id", "")

        # Deliberately the SAME denial message for "does not exist" and
        # "exists but not authorized" -- distinguishing the two would
        # itself be an existence oracle against entries the caller
        # cannot read.
        if not self._is_readable(source_entry_id):
            return ToolResult(
                tool_name="second_brain_link",
                success=False,
                content=f"Cannot reference source_entry_id {source_entry_id!r}: not found or not accessible.",
            )
        if not self._is_readable(target_entry_id):
            return ToolResult(
                tool_name="second_brain_link",
                success=False,
                content=f"Cannot reference target_entry_id {target_entry_id!r}: not found or not accessible.",
            )

        try:
            rel = self._service.create_relationship(
                source_entry_id=source_entry_id,
                target_entry_id=target_entry_id,
                relation_type=params.get("relation_type"),
                source=params.get("source", ""),
                created_by=self._principal,
                confidence=params.get("confidence"),
            )
        except SecondBrainValidationError as exc:
            return ToolResult(tool_name="second_brain_link", success=False, content=str(exc))
        return ToolResult(
            tool_name="second_brain_link",
            success=True,
            content=f"Proposed relationship {rel.relation_type.value} ({rel.status.value}): "
            f"{rel.source_entry_id} -> {rel.target_entry_id}",
            metadata={"relationship_id": rel.id, "status": rel.status.value},
        )


@ToolRegistry.register("second_brain_archive")
class SecondBrainArchiveTool(BaseTool):
    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_archive",
            description=(
                "Archive a Second Brain entry (soft-delete -- excluded from default "
                "search/list, but never destroyed; the full history stays in the "
                "audit log). Only works for entries the caller running this tool "
                "owns or is authorized on."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string"},
                },
                "required": ["entry_id"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            entry = self._service.archive_entry(
                params.get("entry_id", ""), actor=self._principal
            )
        except SecondBrainValidationError as exc:
            return ToolResult(tool_name="second_brain_archive", success=False, content=str(exc))
        return ToolResult(
            tool_name="second_brain_archive",
            success=True,
            content=f"Archived: [{entry.id}] {entry.title}",
            metadata={"entry_id": entry.id},
        )


@ToolRegistry.register("second_brain_find_related_experiences")
class SecondBrainFindRelatedExperiencesTool(BaseTool):
    """FASE 4N.4 -- deterministic progressive broadening, in one call.

    second_brain_search stays exactly as it was (frozen, FASE 4N.2) --
    this is a separate, additional tool (STEP 6 chose "add one focused
    tool" over silently changing search's behavior) for the specific
    question "find relevant past experiences for this situation,"
    which needs broadening logic search never had and was never meant
    to grow on its own.
    """

    def __init__(
        self, service: Optional[SecondBrainService] = None, principal: Optional[str] = None
    ) -> None:
        self._service = service or _service()
        self._principal = principal or resolve_runtime_principal()

    def _evidence_refs_for(self, entry_id: str) -> List[Dict[str, Any]]:
        """M2.5B Phase 1: candidates/bundle stages from
        find_related_experiences carry only match-basis fields (see
        second_brain/retrieval.py's RetrievalCandidate/
        ExperienceBundleItem -- neither has an evidence_references
        field, and extending them would mean touching service.py's
        find_related_experiences internals, out of this phase's scope).
        This fetches the full entry via the existing, unmodified
        get_entry() read to surface its evidence_references honestly,
        exactly like second_brain_search/second_brain_get already do --
        bounded by the same _DEFAULT_MAX_CANDIDATES/
        _DEFAULT_MAX_BUNDLE_ENTRIES limits that already cap this tool's
        result set. Never raises -- a resolution failure here is not
        this tool's job to report, and it already couldn't have entered
        `candidates`/`bundle.stages` in the first place if genuinely
        inaccessible."""
        try:
            entry = self._service.get_entry(entry_id, actor=self._principal)
        except SecondBrainValidationError:
            return []
        if entry is None or not entry.evidence_references:
            return []
        return [_evidence_reference_summary(ref) for ref in entry.evidence_references]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="second_brain_find_related_experiences",
            description=(
                "Find historical Second Brain experiences relevant to a CURRENT "
                "situation -- past PROBLEM/HYPOTHESIS/DECISION/ACTION/OUTCOME/LESSON "
                "records, never current facts. Runs a fixed, deterministic "
                "broadening sequence itself (exact entity -> same domain/type -> "
                "shared terms -> direct relationships) so you do NOT need to guess "
                "or retry with progressively broader second_brain_search calls -- "
                "pass everything you know about the current situation (any "
                "combination of query/domains/entities/entry_types) and this tool "
                "does the widening. Every result states its 'level' and exactly "
                "what matched (domain/entity/term/relationship) -- never a "
                "similarity score. A result being similar does NOT mean it shares "
                "the current cause and does NOT mean its past action is "
                "automatically the right one now -- that judgment needs current "
                "evidence, which this tool does not provide. If the top result is "
                "part of a chain (e.g. a PROBLEM with a linked DECISION/ACTION/"
                "OUTCOME/LESSON), that chain is included automatically as a bundle -- "
                "you do not need to call second_brain_get once per stage. An empty "
                "result means honestly nothing relevant was found; do not invent one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text terms from the current situation (optional)."},
                    "domains": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "entry_types": {"type": "array", "items": {"type": "string", "enum": _ENTRY_TYPES}},
                    "since": {"type": "number"},
                    "until": {"type": "number"},
                },
                "required": [],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            candidates = self._service.find_related_experiences(
                actor=self._principal,
                query=params.get("query"),
                domains=params.get("domains"),
                entities=params.get("entities"),
                entry_types=params.get("entry_types"),
                since=params.get("since"),
                until=params.get("until"),
            )
        except SecondBrainValidationError as exc:
            return ToolResult(
                tool_name="second_brain_find_related_experiences", success=False, content=str(exc)
            )

        if not candidates:
            return ToolResult(
                tool_name="second_brain_find_related_experiences",
                success=True,
                content="No relevant historical experiences found.",
                metadata={"num_candidates": 0},
            )

        lines: List[str] = []
        candidate_dicts: List[Dict[str, Any]] = []
        for c in candidates:
            basis_parts = []
            if c.matched_domains:
                basis_parts.append(f"domain={c.matched_domains}")
            if c.matched_entities:
                basis_parts.append(f"entity={c.matched_entities}")
            if c.matched_terms:
                basis_parts.append(f"term={c.matched_terms}")
            if c.relationship_basis:
                basis_parts.append(f"relationship=[{'; '.join(c.relationship_basis)}]")
            lines.append(
                f"[{c.entry_id}] ({c.historical_entry_type}, {c.active_or_superseded}) "
                f"{c.title} -- matched via {c.retrieval_level}: {', '.join(basis_parts) or 'n/a'}"
            )
            candidate_entry_refs = self._evidence_refs_for(c.entry_id)
            if candidate_entry_refs:
                lines.append(
                    "    evidence_references (stored with this memory, NOT independently re-verified): "
                    + "; ".join(f"[UNVERIFIED] {ref['capability']}" for ref in candidate_entry_refs)
                )
            candidate_dicts.append(
                {
                    "entry_id": c.entry_id,
                    "retrieval_level": c.retrieval_level,
                    "matched_domains": c.matched_domains,
                    "matched_entities": c.matched_entities,
                    "matched_terms": c.matched_terms,
                    "relationship_basis": c.relationship_basis,
                    "active_or_superseded": c.active_or_superseded,
                    "type": c.historical_entry_type,
                    **({"evidence_references": candidate_entry_refs} if candidate_entry_refs else {}),
                }
            )

        # STEP 5: bundle the top candidate's experience chain automatically
        # -- bounded, and only for the single strongest match, so this
        # never turns into an unbounded multi-entry bundling operation.
        bundle_text = ""
        top = candidates[0]
        try:
            bundle = self._service.get_experience_bundle(top.entry_id, actor=self._principal)
            if len(bundle.stages) > 1:
                bundle_lines = []
                for s in bundle.stages:
                    line = (
                        f"  [{s.entry_id}] ({s.type}, trust={s.trust_status}) {s.title} -- "
                        f"{s.summary} [{s.relationship_basis}] provenance: {s.provenance}"
                    )
                    stage_refs = self._evidence_refs_for(s.entry_id)
                    if stage_refs:
                        line += "\n    evidence_references (NOT independently re-verified): " + "; ".join(
                            f"[UNVERIFIED] {ref['capability']}" for ref in stage_refs
                        )
                    bundle_lines.append(line)
                bundle_text = (
                    f"\n\nExperience chain for top match [{top.entry_id}]"
                    + (" (truncated -- more stages exist)" if bundle.truncated else "")
                    + ":\n" + "\n".join(bundle_lines)
                )
        except SecondBrainValidationError:
            pass

        return ToolResult(
            tool_name="second_brain_find_related_experiences",
            success=True,
            content="\n".join(lines) + bundle_text,
            metadata={"num_candidates": len(candidate_dicts), "candidates": candidate_dicts},
        )


__all__ = [
    "SecondBrainArchiveTool",
    "SecondBrainConfirmEntryTool",
    "SecondBrainFindRelatedExperiencesTool",
    "SecondBrainGetTool",
    "SecondBrainLinkTool",
    "SecondBrainProposeEntryTool",
    "SecondBrainSearchTool",
]
