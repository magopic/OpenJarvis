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
    second_brain_search         <-> second_brain.search
    second_brain_get            <-> second_brain.get
    second_brain_propose_entry  <-> second_brain.propose_entry
    second_brain_confirm_entry  <-> second_brain.confirm_entry
    second_brain_link           <-> second_brain.link
    second_brain_archive        <-> second_brain.archive
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.identity import resolve_runtime_principal
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import EvidenceReference, SecondBrainEntry
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
    rels = service.get_relationships(entry.id, direction="both")
    rel_summary = {}
    for rel in rels:
        rel_summary[rel.status.value] = rel_summary.get(rel.status.value, 0) + 1
    return {
        "id": entry.id,
        "type": entry.type.value,
        "title": entry.title,
        "summary": entry.summary,
        "trust_status": entry.trust_status.value,
        "provenance": entry.provenance,
        "domains": entry.domains,
        "entities": entry.entities,
        "visibility": entry.visibility.value,
        "relationships_summary": (
            ", ".join(f"{count} {status}" for status, count in rel_summary.items())
            if rel_summary
            else "none"
        ),
        "archived": entry.archived_at is not None,
        "superseded_by": entry.superseded_by,
    }


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
                "Search MAIA's Second Brain (governed business memory: past events, "
                "problems, hypotheses, decisions, outcomes, lessons) -- NOT OPS ONE "
                "KPI data (use the ops_dynamic_* tools for that) and NOT free-text "
                "conversation memory (memory_search). Combine free-text 'query' with "
                "any filters. Returns confirmed entries only -- proposals awaiting "
                "confirmation never appear here. PRIVATE entries not belonging to the "
                "caller running this tool are silently excluded -- there is no "
                "parameter to search as a different identity."
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
            content="\n".join(
                f"[{s['id']}] ({s['type']}, trust={s['trust_status']}) {s['title']} -- {s['summary']}"
                for s in summaries
            ),
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
                "Fetch one Second Brain entry by id. Denied if it is PRIVATE to "
                "someone other than the caller running this tool."
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
            content=f"[{summary['id']}] ({summary['type']}) {summary['title']}: {summary['summary']}",
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

    def execute(self, **params: Any) -> ToolResult:
        try:
            rel = self._service.create_relationship(
                source_entry_id=params.get("source_entry_id", ""),
                target_entry_id=params.get("target_entry_id", ""),
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


__all__ = [
    "SecondBrainArchiveTool",
    "SecondBrainConfirmEntryTool",
    "SecondBrainGetTool",
    "SecondBrainLinkTool",
    "SecondBrainProposeEntryTool",
    "SecondBrainSearchTool",
]
