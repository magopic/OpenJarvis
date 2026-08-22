"""MAIA Second Brain V1 -- storage/domain foundation.

Model-independent, governed memory for MAIA. Distinct from every other
memory subsystem in OpenJarvis (``memory.db``, ``knowledge.db``,
``sessions.db``, ``traces.db``): entries here carry explicit type,
provenance, trust lifecycle, and evidence *references* (never copied
values) into OPS ONE's certified capabilities.

No model-callable tools are registered by this module (FASE 4N.1
scope) -- see ``docs/MAIA_SECOND_BRAIN_V1.md`` for the documented
future tool-contract boundary.
"""

from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import (
    AuditEvent,
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

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "EntryTrustStatus",
    "EntryType",
    "EvidenceReference",
    "Proposal",
    "ProposalStatus",
    "Relationship",
    "RelationshipStatus",
    "RelationshipType",
    "SecondBrainEntry",
    "SecondBrainService",
    "SecondBrainValidationError",
    "Visibility",
]
