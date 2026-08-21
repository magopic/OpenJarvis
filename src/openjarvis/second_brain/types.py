"""Typed domain model for MAIA Second Brain V1.

Pure data definitions -- no I/O, no SQL, no tool wiring. See
``store.py`` for persistence and ``service.py`` for the
governance-enforcing API that agents/tools will eventually call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EntryType(str, Enum):
    """What kind of thing a SecondBrainEntry records."""

    EVENT = "EVENT"
    PROBLEM = "PROBLEM"
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    DECISION = "DECISION"
    ACTION = "ACTION"
    OUTCOME = "OUTCOME"
    LESSON = "LESSON"
    PROCEDURE = "PROCEDURE"
    MEETING_NOTE = "MEETING_NOTE"


class EntryTrustStatus(str, Enum):
    """Second Brain memory-maturity lifecycle.

    IMPORTANT: this is a completely separate namespace from OPS ONE's
    Knowledge V1 trust statuses (``TRUSTED``, ``BUSINESS_LOGIC_IN_REVISION``,
    ``DATA_NOT_AVAILABLE``, ...). A Second Brain ``DECISION`` never
    certifies OPS ONE business logic, and a ``TRUSTED`` OPS metric never
    auto-promotes a Second Brain entry. Do not compare or conflate the
    two enums.

    States are reachable, not mandatory waypoints on a fixed chain -- a
    MEETING_NOTE or PROCEDURE dictated directly by a user may start
    life as VERIFIED/DECISION without ever passing through OBSERVED or
    HYPOTHESIS. See ``docs/MAIA_SECOND_BRAIN_V1.md`` (TRUST MODEL).

    V1 does not expose any operation that mutates ``trust_status`` on
    an existing entry (see ``service.py``) -- "promotion" always means
    creating a new, separately-created entry and linking it to the
    original via a relationship, never silently rewriting history.
    """

    OBSERVED = "OBSERVED"
    HYPOTHESIS = "HYPOTHESIS"
    VERIFIED = "VERIFIED"
    DECISION = "DECISION"
    OUTCOME = "OUTCOME"
    LEARNED = "LEARNED"


class Visibility(str, Enum):
    PRIVATE = "PRIVATE"
    TEAM = "TEAM"
    COMPANY = "COMPANY"


class RelationshipType(str, Enum):
    CAUSES = "CAUSES"
    CORRELATES_WITH = "CORRELATES_WITH"
    PRECEDES = "PRECEDES"
    RESULTED_IN = "RESULTED_IN"
    RESOLVED_BY = "RESOLVED_BY"
    DECIDED_IN = "DECIDED_IN"
    RELATED_TO = "RELATED_TO"
    SIMILAR_TO = "SIMILAR_TO"
    AFFECTS = "AFFECTS"
    SUPERSEDES = "SUPERSEDES"
    DUPLICATES = "DUPLICATES"


class RelationshipStatus(str, Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class AuditEventType(str, Enum):
    ENTRY_CREATED = "ENTRY_CREATED"
    ENTRY_ARCHIVED = "ENTRY_ARCHIVED"
    ENTRY_SUPERSEDED = "ENTRY_SUPERSEDED"
    RELATIONSHIP_CREATED = "RELATIONSHIP_CREATED"
    RELATIONSHIP_STATUS_CHANGED = "RELATIONSHIP_STATUS_CHANGED"


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A pointer into a certified OPS ONE capability -- never a copied value.

    The Second Brain must never become a second source of truth for a
    KPI: it stores *how to ask again* (capability + params), not the
    answer at capture time. ``trust_status_at_capture`` records what
    Knowledge V1 said about the metric's trust when this reference was
    created (a snapshot of the label, not the number).
    """

    capability: str
    domain: str
    metric: str
    period: str
    filters: Dict[str, Any] = field(default_factory=dict)
    trust_status_at_capture: str = ""
    fetched_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.capability:
            raise ValueError("EvidenceReference.capability is required")
        if not self.domain:
            raise ValueError("EvidenceReference.domain is required")


@dataclass(slots=True)
class SecondBrainEntry:
    id: str
    type: EntryType
    title: str
    summary: str
    domains: List[str]
    entities: List[str]
    source: str
    created_by: str
    provenance: str
    trust_status: EntryTrustStatus
    visibility: Visibility
    created_at: float
    updated_at: float
    timestamp: Optional[float] = None
    confidence: Optional[float] = None
    evidence_references: List[EvidenceReference] = field(default_factory=list)
    superseded_by: Optional[str] = None
    archived_at: Optional[float] = None


@dataclass(slots=True)
class Relationship:
    id: str
    source_entry_id: str
    target_entry_id: str
    relation_type: RelationshipType
    source: str
    created_by: str
    status: RelationshipStatus
    created_at: float
    updated_at: float
    confidence: Optional[float] = None


@dataclass(slots=True)
class AuditEvent:
    id: int
    event_type: AuditEventType
    actor: str
    target_id: str
    action: str
    timestamp: float
    details: Dict[str, Any]
    row_hash: str
    prev_hash: str


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "EntryTrustStatus",
    "EntryType",
    "EvidenceReference",
    "Relationship",
    "RelationshipStatus",
    "RelationshipType",
    "SecondBrainEntry",
    "Visibility",
]
