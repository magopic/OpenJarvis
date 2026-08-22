"""Governance-enforcing service API for MAIA Second Brain V1.

This is the *only* supported way to read or write Second Brain data.
``store.py`` has no validation of its own -- every rule from FASE 4N's
governance model (STEP 5) is enforced here, once, so a caller can never
bypass it by reaching for the storage layer directly.

No tool wraps this service yet (see ``docs/MAIA_SECOND_BRAIN_V1.md``,
INTEGRATION BOUNDARY) -- that is deliberately out of scope for this
phase.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Union

from openjarvis.second_brain.errors import (
    SecondBrainAuthorizationError,
    SecondBrainValidationError,
)
from openjarvis.second_brain.retrieval import (
    LEVEL_EXACT,
    LEVEL_RELATIONSHIP,
    LEVEL_STRUCTURED,
    LEVEL_TERM,
    _DEFAULT_MAX_BUNDLE_ENTRIES,
    _DEFAULT_MAX_BUNDLE_HOPS,
    _DEFAULT_MAX_CANDIDATES,
    _LEVEL_PRIORITY,
    _MAX_RELATIONSHIP_SEEDS,
    _PER_LEVEL_LIMIT,
    ExperienceBundle,
    ExperienceBundleItem,
    RetrievalCandidate,
    _merge_into,
    _to_candidate,
)
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.second_brain.types import (
    AuditEventType,
    EntryTrustStatus,
    EntryType,
    EvidenceReference,
    Proposal,
    ProposalStatus,
    Relationship,
    RelationshipStatus,
    RelationshipType,
    SecondBrainEntry,
    Visibility,
)


def _coerce_enum(value: Any, enum_cls: type, field_name: str):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = ", ".join(m.value for m in enum_cls)
        raise SecondBrainValidationError(
            f"Invalid {field_name}: {value!r}. Valid values: {valid}"
        ) from exc


def _require_nonempty_str(value: Optional[str], field_name: str) -> str:
    if not value or not str(value).strip():
        raise SecondBrainValidationError(f"{field_name} is required and cannot be empty")
    return value


def _validate_confidence(confidence: Optional[float]) -> None:
    if confidence is None:
        return
    if not (0.0 <= confidence <= 1.0):
        raise SecondBrainValidationError(
            f"confidence must be within 0..1, got {confidence!r}"
        )


def _validate_entry_kwargs(
    *,
    type: Union[EntryType, str],
    title: str,
    summary: str,
    created_by: str,
    provenance: str,
    source: str,
    trust_status: Union[EntryTrustStatus, str],
    visibility: Union[Visibility, str],
    domains: Optional[List[str]],
    entities: Optional[List[str]],
    timestamp: Optional[float],
    confidence: Optional[float],
    evidence_references: Optional[List[EvidenceReference]],
) -> "tuple[EntryType, EntryTrustStatus, Visibility]":
    """Shared governance check for both ``create_entry`` and
    ``propose_entry`` -- a proposal must fail the exact same rules a
    direct create would, just before it ever reaches ``entries``."""
    entry_type = _coerce_enum(type, EntryType, "type")
    entry_trust = _coerce_enum(trust_status, EntryTrustStatus, "trust_status")
    entry_visibility = _coerce_enum(visibility, Visibility, "visibility")

    _require_nonempty_str(created_by, "created_by")
    _require_nonempty_str(provenance, "provenance")
    _require_nonempty_str(source, "source")
    _require_nonempty_str(title, "title")
    _validate_confidence(confidence)

    # DECISION must carry created_by (already required above),
    # timestamp, and context provenance (already required above).
    if entry_type is EntryType.DECISION and timestamp is None:
        raise SecondBrainValidationError(
            "DECISION entries require a timestamp (when the decision was made)"
        )

    # A LESSON (or any entry reaching LEARNED) must declare which
    # prior experience/outcome it derives from -- provenance alone
    # is not enough evidence of grounding; it must also point at
    # something concrete (a domain/entity it concerns, or an
    # evidence reference). We cannot require the OUTCOME
    # relationship to already exist here (chicken-and-egg: the
    # relationship needs this entry's id, which doesn't exist yet)
    # -- that link is created separately via create_relationship().
    if entry_type is EntryType.LESSON or entry_trust is EntryTrustStatus.LEARNED:
        if not (domains or entities or evidence_references):
            raise SecondBrainValidationError(
                "LESSON/LEARNED entries must be grounded in something concrete: "
                "provide at least one of domains, entities, or evidence_references "
                "describing the prior experience/outcome this lesson derives from"
            )

    return entry_type, entry_trust, entry_visibility


class SecondBrainService:
    """Model-independent Second Brain API. Never expose raw SQL to a caller."""

    def __init__(self, store: Optional[SecondBrainStore] = None) -> None:
        self._store = store if store is not None else SecondBrainStore()

    # -- entries ----------------------------------------------------------

    def create_entry(
        self,
        *,
        type: Union[EntryType, str],
        title: str,
        summary: str,
        created_by: str,
        provenance: str,
        source: str,
        trust_status: Union[EntryTrustStatus, str],
        visibility: Union[Visibility, str] = Visibility.PRIVATE,
        domains: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        timestamp: Optional[float] = None,
        confidence: Optional[float] = None,
        evidence_references: Optional[List[EvidenceReference]] = None,
    ) -> SecondBrainEntry:
        """Persist an entry directly. For AI-initiated conversational
        capture, use ``propose_entry`` + ``confirm_entry`` instead --
        this method is for callers (supersede_entry, tests, a human
        directly using the service) that don't need the two-step gate.
        """
        entry_type, entry_trust, entry_visibility = _validate_entry_kwargs(
            type=type,
            title=title,
            summary=summary,
            created_by=created_by,
            provenance=provenance,
            source=source,
            trust_status=trust_status,
            visibility=visibility,
            domains=domains,
            entities=entities,
            timestamp=timestamp,
            confidence=confidence,
            evidence_references=evidence_references,
        )

        now = time.time()
        entry = SecondBrainEntry(
            id=str(uuid.uuid4()),
            type=entry_type,
            title=title,
            summary=summary,
            domains=list(domains or []),
            entities=list(entities or []),
            timestamp=timestamp,
            source=source,
            created_by=created_by,
            provenance=provenance,
            trust_status=entry_trust,
            confidence=confidence,
            evidence_references=list(evidence_references or []),
            visibility=entry_visibility,
            superseded_by=None,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self._store.insert_entry(entry)
        self._store.append_audit_event(
            AuditEventType.ENTRY_CREATED,
            actor=created_by,
            target_id=entry.id,
            action=f"create_entry(type={entry_type.value}, trust_status={entry_trust.value})",
            details={"title": title, "type": entry_type.value},
            timestamp=now,
        )
        return entry

    def propose_entry(
        self,
        *,
        type: Union[EntryType, str],
        title: str,
        summary: str,
        created_by: str,
        provenance: str,
        source: str,
        trust_status: Union[EntryTrustStatus, str],
        visibility: Union[Visibility, str] = Visibility.PRIVATE,
        domains: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        timestamp: Optional[float] = None,
        confidence: Optional[float] = None,
        evidence_references: Optional[List[EvidenceReference]] = None,
    ) -> Proposal:
        """Step 1 of the mandatory two-step capture workflow.

        Validates exactly as ``create_entry`` would, but does **not**
        write to ``entries`` -- nothing here is searchable, retrievable,
        or a "memory" yet. Returns a ``Proposal`` the caller (the model,
        via a tool) must show the user and get explicit confirmation on
        before ``confirm_entry`` can turn it into a real entry. Silence
        is never confirmation -- only a separate, later call to
        ``confirm_entry`` with this proposal's id persists anything.
        """
        _validate_entry_kwargs(
            type=type,
            title=title,
            summary=summary,
            created_by=created_by,
            provenance=provenance,
            source=source,
            trust_status=trust_status,
            visibility=visibility,
            domains=domains,
            entities=entities,
            timestamp=timestamp,
            confidence=confidence,
            evidence_references=evidence_references,
        )

        payload: Dict[str, Any] = {
            "type": _coerce_enum(type, EntryType, "type").value,
            "title": title,
            "summary": summary,
            "created_by": created_by,
            "provenance": provenance,
            "source": source,
            "trust_status": _coerce_enum(trust_status, EntryTrustStatus, "trust_status").value,
            "visibility": _coerce_enum(visibility, Visibility, "visibility").value,
            "domains": list(domains or []),
            "entities": list(entities or []),
            "timestamp": timestamp,
            "confidence": confidence,
            "evidence_references": [
                {
                    "capability": ref.capability,
                    "domain": ref.domain,
                    "metric": ref.metric,
                    "period": ref.period,
                    "filters": ref.filters,
                    "trust_status_at_capture": ref.trust_status_at_capture,
                    "fetched_at": ref.fetched_at,
                }
                for ref in (evidence_references or [])
            ],
        }

        now = time.time()
        proposal = Proposal(
            id=str(uuid.uuid4()),
            payload=payload,
            proposed_by=created_by,
            status=ProposalStatus.PENDING,
            created_at=now,
        )
        self._store.insert_proposal(proposal)
        self._store.append_audit_event(
            AuditEventType.ENTRY_PROPOSED,
            actor=created_by,
            target_id=proposal.id,
            action=f"propose_entry(type={payload['type']})",
            details={"title": title, "type": payload["type"]},
            timestamp=now,
        )
        return proposal

    def confirm_entry(
        self,
        proposal_id: str,
        *,
        actor: str,
        supersedes_entry_id: Optional[str] = None,
    ) -> SecondBrainEntry:
        """Step 2 of the capture workflow -- the only call that persists.

        Requires an explicit, separate invocation with the actor who is
        confirming (typically the user, via whatever surface relayed
        their "yes"/"salvala"). There is no implicit-confirmation path:
        this method is never called as a side effect of anything else.

        Pass ``supersedes_entry_id`` when the user's confirmation is a
        *correction* of an existing entry (STEP 6: "Correggi: ..."):
        the new entry is created exactly as usual, but via
        ``supersede_entry`` rather than a bare ``create_entry`` -- the
        old entry is never overwritten, only linked via a CONFIRMED
        SUPERSEDES relationship and its ``superseded_by`` pointer.
        """
        _require_nonempty_str(actor, "actor")
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise SecondBrainValidationError(f"No such proposal: {proposal_id}")
        if proposal.status is not ProposalStatus.PENDING:
            raise SecondBrainValidationError(
                f"Proposal {proposal_id} is already {proposal.status.value}, not PENDING"
            )

        payload = dict(proposal.payload)
        evidence_refs = [
            EvidenceReference(**ref) for ref in payload.pop("evidence_references", [])
        ]
        payload["evidence_references"] = evidence_refs

        if supersedes_entry_id is not None:
            _, relationship = self.supersede_entry(
                supersedes_entry_id, actor=actor, new_entry_kwargs=payload
            )
            entry = self._store.get_entry(relationship.source_entry_id)
            assert entry is not None
        else:
            entry = self.create_entry(**payload)

        now = time.time()
        self._store.set_proposal_resolved(
            proposal_id, resolved_entry_id=entry.id, resolved_at=now
        )
        return entry

    @staticmethod
    def _check_visibility(entry: SecondBrainEntry, actor: Optional[str]) -> None:
        """Fail closed: a missing actor never satisfies a PRIVATE check."""
        if entry.visibility is Visibility.PRIVATE and entry.created_by != actor:
            raise SecondBrainAuthorizationError(
                f"Entry {entry.id} is PRIVATE to its creator; access denied"
            )

    def get_entry(
        self, entry_id: str, *, actor: Optional[str] = None
    ) -> Optional[SecondBrainEntry]:
        entry = self._store.get_entry(entry_id)
        if entry is None:
            return None
        self._check_visibility(entry, actor)
        return entry

    def search_entries(
        self, query: str, *, actor: Optional[str] = None, limit: int = 50
    ) -> List[SecondBrainEntry]:
        """Exact/FTS retrieval only -- no semantic similarity in V1.

        PRIVATE entries not owned by ``actor`` are silently excluded
        (not raised) -- a search result is a filtered list, not a
        single-resource access decision like ``get_entry``.
        """
        return self._store.search_entries_fts(query, actor=actor, limit=limit)

    def list_entries(
        self,
        *,
        actor: Optional[str] = None,
        entry_type: Optional[Union[EntryType, str]] = None,
        trust_status: Optional[Union[EntryTrustStatus, str]] = None,
        domain: Optional[str] = None,
        entity: Optional[str] = None,
        include_archived: bool = False,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 50,
    ) -> List[SecondBrainEntry]:
        return self._store.list_entries(
            actor=actor,
            entry_type=_coerce_enum(entry_type, EntryType, "entry_type") if entry_type else None,
            trust_status=(
                _coerce_enum(trust_status, EntryTrustStatus, "trust_status")
                if trust_status
                else None
            ),
            domain=domain,
            entity=entity,
            include_archived=include_archived,
            since=since,
            until=until,
            limit=limit,
        )

    def _resolve_active(self, entry: SecondBrainEntry, *, actor: Optional[str]) -> SecondBrainEntry:
        """Follow ``superseded_by`` forward to the newest version (STEP 4).

        Bounded by construction: each hop must point at a strictly
        different id, and entries are never superseded by themselves,
        so this terminates in at most ``len(entries)`` hops -- in
        practice a handful at most, since supersession chains are short.
        """
        seen = {entry.id}
        current = entry
        while current.superseded_by and current.superseded_by not in seen:
            newer = self._store.get_entry(current.superseded_by)
            if newer is None:
                break
            seen.add(newer.id)
            current = newer
        return current

    def find_related_experiences(
        self,
        *,
        actor: Optional[str] = None,
        query: Optional[str] = None,
        domains: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        entry_types: Optional[List[Union[EntryType, str]]] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        max_candidates: int = _DEFAULT_MAX_CANDIDATES,
    ) -> List[RetrievalCandidate]:
        """Retrieval Intelligence V1 (FASE 4N.4) -- deterministic
        progressive broadening. See ``second_brain/retrieval.py`` for
        the full rationale.

        Runs LEVEL_EXACT -> LEVEL_STRUCTURED -> LEVEL_TERM ->
        LEVEL_RELATIONSHIP in that fixed order (only the levels whose
        required input was actually supplied), merging repeat matches
        rather than duplicating them, and returns a bounded,
        deterministically-ordered list: SUPERSEDED entries are resolved
        to their active replacement (STEP 4) and sorted after ACTIVE
        ones, ties broken by retrieval level then recency. This method
        performs no new database work beyond what ``list_entries``/
        ``search_entries``/``get_relationships`` already did -- it only
        fixes the *order and completeness* of calling them.
        """
        by_id: Dict[str, RetrievalCandidate] = {}
        entry_types_norm = (
            [_coerce_enum(t, EntryType, "entry_types") for t in entry_types]
            if entry_types
            else [None]
        )

        def _add(entry: SecondBrainEntry, level: str, **matched: List[str]) -> None:
            if entry.id in by_id:
                _merge_into(by_id[entry.id], **matched)
                return
            candidate = _to_candidate(entry, level=level)
            _merge_into(candidate, **matched)
            by_id[entry.id] = candidate

        # LEVEL 1 -- EXACT: explicit entity identifiers only.
        for entity in entities or []:
            for entry in self._store.list_entries(
                actor=actor, entity=entity, limit=_PER_LEVEL_LIMIT
            ):
                _add(entry, LEVEL_EXACT, matched_entities=[entity])

        # LEVEL 2 -- STRUCTURED: domain, optionally narrowed by type.
        for domain in domains or []:
            for etype in entry_types_norm:
                for entry in self._store.list_entries(
                    actor=actor, domain=domain, entry_type=etype, limit=_PER_LEVEL_LIMIT
                ):
                    _add(entry, LEVEL_STRUCTURED, matched_domains=[domain])

        # LEVEL 3 -- TERM: free-text FTS, OR-joined (search_entries_fts_broad,
        # not the AND-joined search_entries_fts second_brain_search uses) --
        # a broadening search must not let one unmatched word (e.g. a
        # brand-new entity name typed alongside real descriptive terms)
        # zero out an otherwise-good match. See _fts5_safe_query_or.
        if query:
            for entry in self._store.search_entries_fts_broad(
                query, actor=actor, limit=_PER_LEVEL_LIMIT
            ):
                _add(entry, LEVEL_TERM, matched_terms=[query])

        # LEVEL 4 -- RELATIONSHIP: direct neighbors of the strongest
        # candidates found so far, not of every candidate -- bounded.
        seed_ids = sorted(
            by_id, key=lambda eid: _LEVEL_PRIORITY[by_id[eid].retrieval_level]
        )[:_MAX_RELATIONSHIP_SEEDS]
        for seed_id in seed_ids:
            for rel in self._store.get_relationships(
                seed_id, direction="both", status=RelationshipStatus.CONFIRMED
            ):
                other_id = (
                    rel.target_entry_id if rel.source_entry_id == seed_id else rel.source_entry_id
                )
                other = self._store.get_entry(other_id)
                if other is None:
                    continue
                if other.visibility is Visibility.PRIVATE and other.created_by != actor:
                    continue  # fail closed, same rule as every other read path
                _add(
                    other,
                    LEVEL_RELATIONSHIP,
                    relationship_basis=[f"{rel.relation_type.value} ({rel.status.value}) via {seed_id}"],
                )

        # Active-version policy (STEP 4): a SUPERSEDED candidate is
        # replaced by its active successor in the result set, carrying
        # its match reasons forward, unless the successor is already
        # present (then the reasons are merged instead of duplicated).
        resolved: Dict[str, RetrievalCandidate] = {}
        for candidate in by_id.values():
            if candidate.active_or_superseded == "ACTIVE":
                resolved.setdefault(candidate.entry_id, candidate)
                continue
            entry = self._store.get_entry(candidate.entry_id)
            active_entry = self._resolve_active(entry, actor=actor) if entry else None
            if active_entry is None or active_entry.id == candidate.entry_id:
                resolved.setdefault(candidate.entry_id, candidate)
                continue
            if active_entry.id in resolved:
                _merge_into(
                    resolved[active_entry.id],
                    matched_domains=candidate.matched_domains,
                    matched_entities=candidate.matched_entities,
                    matched_terms=candidate.matched_terms,
                    relationship_basis=candidate.relationship_basis
                    + [f"supersedes {candidate.entry_id}"],
                )
            else:
                active_candidate = _to_candidate(active_entry, level=candidate.retrieval_level)
                _merge_into(
                    active_candidate,
                    matched_domains=candidate.matched_domains,
                    matched_entities=candidate.matched_entities,
                    matched_terms=candidate.matched_terms,
                    relationship_basis=candidate.relationship_basis
                    + [f"supersedes {candidate.entry_id}"],
                )
                resolved[active_entry.id] = active_candidate

        ordered = sorted(
            resolved.values(),
            key=lambda c: (
                0 if c.active_or_superseded == "ACTIVE" else 1,
                _LEVEL_PRIORITY[c.retrieval_level],
                c.entry_id,
            ),
        )
        return ordered[:max_candidates]

    def get_experience_bundle(
        self,
        anchor_entry_id: str,
        *,
        actor: Optional[str] = None,
        max_hops: int = _DEFAULT_MAX_BUNDLE_HOPS,
        max_entries: int = _DEFAULT_MAX_BUNDLE_ENTRIES,
    ) -> ExperienceBundle:
        """STEP 5: retrieve a coherent historical experience -- PROBLEM,
        HYPOTHESIS, DECISION, ACTION, OUTCOME, LESSON where linked -- as
        one bounded bundle, instead of requiring a model to make one
        ``second_brain_get`` call per hop.

        A bounded breadth-first walk over CONFIRMED relationships only
        (a PROPOSED relationship is a model's unverified inference, not
        yet part of certified structure -- including it here would let
        an unconfirmed guess masquerade as an experience chain). Each
        stage keeps its own id/type/summary/trust_status/provenance --
        never collapsed into one generated summary that would lose
        exactly the distinctions FASE 4N.3 built LESSON governance and
        trust-lifecycle tracking to preserve.
        """
        anchor = self.get_entry(anchor_entry_id, actor=actor)
        if anchor is None:
            raise SecondBrainValidationError(f"No such entry: {anchor_entry_id}")

        bundle = ExperienceBundle(anchor_entry_id=anchor_entry_id)
        visited = {anchor.id}
        bundle.stages.append(
            ExperienceBundleItem(
                entry_id=anchor.id,
                type=anchor.type.value,
                title=anchor.title,
                summary=anchor.summary,
                trust_status=anchor.trust_status.value,
                provenance=anchor.provenance,
                relationship_basis="anchor",
            )
        )

        frontier = [anchor.id]
        hops = 0
        while frontier and hops < max_hops and len(bundle.stages) < max_entries:
            hops += 1
            next_frontier: List[str] = []
            for current_id in frontier:
                for rel in self._store.get_relationships(
                    current_id, direction="both", status=RelationshipStatus.CONFIRMED
                ):
                    other_id = (
                        rel.target_entry_id
                        if rel.source_entry_id == current_id
                        else rel.source_entry_id
                    )
                    if other_id in visited:
                        continue
                    other = self._store.get_entry(other_id)
                    if other is None:
                        continue
                    if other.visibility is Visibility.PRIVATE and other.created_by != actor:
                        continue
                    visited.add(other_id)
                    bundle.stages.append(
                        ExperienceBundleItem(
                            entry_id=other.id,
                            type=other.type.value,
                            title=other.title,
                            summary=other.summary,
                            trust_status=other.trust_status.value,
                            provenance=other.provenance,
                            relationship_basis=f"{rel.relation_type.value} ({rel.status.value}) via {current_id}",
                        )
                    )
                    next_frontier.append(other_id)
                    if len(bundle.stages) >= max_entries:
                        break
                if len(bundle.stages) >= max_entries:
                    break
            frontier = next_frontier

        bundle.truncated = bool(frontier) and (hops >= max_hops or len(bundle.stages) >= max_entries)
        return bundle

    def archive_entry(self, entry_id: str, *, actor: str) -> SecondBrainEntry:
        _require_nonempty_str(actor, "actor")
        entry = self._store.get_entry(entry_id)
        if entry is None:
            raise SecondBrainValidationError(f"No such entry: {entry_id}")
        self._check_visibility(entry, actor)
        now = time.time()
        self._store.set_entry_archived(entry_id, archived_at=now, updated_at=now)
        self._store.append_audit_event(
            AuditEventType.ENTRY_ARCHIVED,
            actor=actor,
            target_id=entry_id,
            action="archive_entry",
            timestamp=now,
        )
        return self._store.get_entry(entry_id)  # type: ignore[return-value]

    def supersede_entry(
        self,
        old_entry_id: str,
        *,
        actor: str,
        new_entry: Optional[SecondBrainEntry] = None,
        new_entry_kwargs: Optional[Dict[str, Any]] = None,
    ) -> "tuple[SecondBrainEntry, Relationship]":
        """Correct an entry without destroying its history.

        Either pass an already-created ``new_entry`` (e.g. from a prior
        ``create_entry()`` call) or ``new_entry_kwargs`` to have one
        created here. The old entry is never mutated except for its
        ``superseded_by`` pointer and ``updated_at`` -- its original
        content is preserved permanently for audit.
        """
        _require_nonempty_str(actor, "actor")
        old_entry = self._store.get_entry(old_entry_id)
        if old_entry is None:
            raise SecondBrainValidationError(f"No such entry: {old_entry_id}")
        self._check_visibility(old_entry, actor)

        if new_entry is None:
            if not new_entry_kwargs:
                raise SecondBrainValidationError(
                    "supersede_entry requires either new_entry or new_entry_kwargs"
                )
            new_entry = self.create_entry(**new_entry_kwargs)

        now = time.time()
        self._store.set_entry_superseded(
            old_entry_id, superseded_by=new_entry.id, updated_at=now
        )

        # This relationship is CONFIRMED, not PROPOSED: unlike
        # create_relationship() (which records a model's *inference*
        # that two entries relate), supersede_entry() is an explicit,
        # deliberate structural action the caller invoked on purpose --
        # there is nothing left for a human to confirm.
        relationship = Relationship(
            id=str(uuid.uuid4()),
            source_entry_id=new_entry.id,
            target_entry_id=old_entry_id,
            relation_type=RelationshipType.SUPERSEDES,
            source="supersede_entry",
            created_by=actor,
            status=RelationshipStatus.CONFIRMED,
            created_at=now,
            updated_at=now,
        )
        self._store.insert_relationship(relationship)
        self._store.append_audit_event(
            AuditEventType.ENTRY_SUPERSEDED,
            actor=actor,
            target_id=old_entry_id,
            action=f"supersede_entry(new_entry_id={new_entry.id})",
            details={"new_entry_id": new_entry.id},
            timestamp=now,
        )
        self._store.append_audit_event(
            AuditEventType.RELATIONSHIP_CREATED,
            actor=actor,
            target_id=relationship.id,
            action="supersede_entry:SUPERSEDES",
            details={"source_entry_id": new_entry.id, "target_entry_id": old_entry_id},
            timestamp=now,
        )
        old_entry = self._store.get_entry(old_entry_id)
        assert old_entry is not None
        return old_entry, relationship

    # -- relationships ------------------------------------------------------

    def create_relationship(
        self,
        *,
        source_entry_id: str,
        target_entry_id: str,
        relation_type: Union[RelationshipType, str],
        source: str,
        created_by: str,
        confidence: Optional[float] = None,
    ) -> Relationship:
        rel_type = _coerce_enum(relation_type, RelationshipType, "relation_type")
        _require_nonempty_str(source, "source")
        _require_nonempty_str(created_by, "created_by")
        _validate_confidence(confidence)

        if self._store.get_entry(source_entry_id) is None:
            raise SecondBrainValidationError(f"No such source entry: {source_entry_id}")
        if self._store.get_entry(target_entry_id) is None:
            raise SecondBrainValidationError(f"No such target entry: {target_entry_id}")

        now = time.time()
        relationship = Relationship(
            id=str(uuid.uuid4()),
            source_entry_id=source_entry_id,
            target_entry_id=target_entry_id,
            relation_type=rel_type,
            source=source,
            created_by=created_by,
            # Every relationship is born PROPOSED -- there is no
            # parameter to override this. A relationship a model
            # infers must never become CONFIRMED by construction; only
            # update_relationship_status() (an explicit, separate call)
            # can move it, and that call is where a human-in-the-loop
            # gate belongs.
            status=RelationshipStatus.PROPOSED,
            confidence=confidence,
            created_at=now,
            updated_at=now,
        )
        self._store.insert_relationship(relationship)
        self._store.append_audit_event(
            AuditEventType.RELATIONSHIP_CREATED,
            actor=created_by,
            target_id=relationship.id,
            action=f"create_relationship(type={rel_type.value})",
            details={
                "source_entry_id": source_entry_id,
                "target_entry_id": target_entry_id,
            },
            timestamp=now,
        )
        return relationship

    def get_relationships(
        self,
        entry_id: str,
        *,
        direction: str = "both",
        relation_type: Optional[Union[RelationshipType, str]] = None,
        status: Optional[Union[RelationshipStatus, str]] = None,
    ) -> List[Relationship]:
        """Direct neighbors only -- no multi-hop graph traversal in V1."""
        if direction not in ("in", "out", "both"):
            raise SecondBrainValidationError(f"Invalid direction: {direction!r}")
        return self._store.get_relationships(
            entry_id,
            direction=direction,
            relation_type=(
                _coerce_enum(relation_type, RelationshipType, "relation_type")
                if relation_type
                else None
            ),
            status=_coerce_enum(status, RelationshipStatus, "status") if status else None,
        )

    def is_outcome_backed(self, entry_id: str) -> bool:
        """FASE 4N.3 STEP 2: "no outcome, no certified LEARNED lesson."

        Entry-creation validation (frozen, FASE 4N.1) already requires a
        LESSON/LEARNED entry to be grounded in *something* concrete at
        write time (domains/entities/evidence_references), since the
        entry doesn't have an id yet to link an OUTCOME relationship to.
        This is the second half of that governance, checked at
        *retrieval* time instead: does this entry now have at least one
        CONFIRMED relationship connecting it to an entry of type
        OUTCOME? A LESSON lacking this is not "wrong" -- it may simply
        not have been linked yet -- but callers (tools, retrieval
        composition) should present it as unverified-by-outcome rather
        than silently treating it as equally certain as one that is.
        Computed, not stored -- becomes true the moment a CONFIRMED link
        to an OUTCOME exists, with no separate migration or mutation.
        """
        for rel in self._store.get_relationships(entry_id, direction="both"):
            if rel.status is not RelationshipStatus.CONFIRMED:
                continue
            other_id = (
                rel.target_entry_id
                if rel.source_entry_id == entry_id
                else rel.source_entry_id
            )
            other = self._store.get_entry(other_id)
            if other is not None and other.type is EntryType.OUTCOME:
                return True
        return False

    def update_relationship_status(
        self,
        relationship_id: str,
        new_status: Union[RelationshipStatus, str],
        *,
        actor: str,
    ) -> Relationship:
        _require_nonempty_str(actor, "actor")
        status = _coerce_enum(new_status, RelationshipStatus, "new_status")
        relationship = self._store.get_relationship(relationship_id)
        if relationship is None:
            raise SecondBrainValidationError(f"No such relationship: {relationship_id}")

        now = time.time()
        self._store.set_relationship_status(relationship_id, status, updated_at=now)
        self._store.append_audit_event(
            AuditEventType.RELATIONSHIP_STATUS_CHANGED,
            actor=actor,
            target_id=relationship_id,
            action=f"update_relationship_status({relationship.status.value} -> {status.value})",
            details={"from": relationship.status.value, "to": status.value},
            timestamp=now,
        )
        updated = self._store.get_relationship(relationship_id)
        assert updated is not None
        return updated

    # -- audit ----------------------------------------------------------

    def verify_audit_chain(self) -> "tuple[bool, Optional[int]]":
        return self._store.verify_audit_chain()

    def close(self) -> None:
        self._store.close()


__all__ = ["SecondBrainService"]
