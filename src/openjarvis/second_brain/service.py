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

from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.second_brain.types import (
    AuditEventType,
    EntryTrustStatus,
    EntryType,
    EvidenceReference,
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

    def get_entry(self, entry_id: str) -> Optional[SecondBrainEntry]:
        return self._store.get_entry(entry_id)

    def search_entries(self, query: str, *, limit: int = 50) -> List[SecondBrainEntry]:
        """Exact/FTS retrieval only -- no semantic similarity in V1."""
        return self._store.search_entries_fts(query, limit=limit)

    def list_entries(
        self,
        *,
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

    def archive_entry(self, entry_id: str, *, actor: str) -> SecondBrainEntry:
        _require_nonempty_str(actor, "actor")
        entry = self._store.get_entry(entry_id)
        if entry is None:
            raise SecondBrainValidationError(f"No such entry: {entry_id}")
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
