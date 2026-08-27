"""Typed contracts for MAIA Document Knowledge Ingestion V1.

``DocumentEvidenceReference`` is deliberately a SEPARATE dataclass from
``second_brain.types.EvidenceReference`` -- that type is a pointer into a
certified OPS ONE capability query ("how to ask again", never a copied
value); this one is a pointer into a file on disk. Both dataclasses'
docstrings explicitly warn against conflating trust/evidence namespaces
across systems (Second Brain vs OPS ONE); the same discipline applies
here -- a document citation is never a certified KPI reference, and vice
versa. Reusing ``EvidenceReference`` for this would violate that warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True, slots=True)
class DocumentEvidenceReference:
    """A pointer into a specific chunk of an ingested document.

    Every field here is independently re-derivable from the authorized
    workspace + the knowledge index -- this is a citation, not a copy of
    the underlying content.
    """

    doc_id: str
    chunk_id: str
    workspace_id: str
    relative_path: str
    filename: str
    content_hash: str
    chunk_index: int
    page: Optional[int] = None
    section: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.doc_id:
            raise ValueError("DocumentEvidenceReference.doc_id is required")
        if not self.chunk_id:
            raise ValueError("DocumentEvidenceReference.chunk_id is required")
        if not self.relative_path:
            raise ValueError("DocumentEvidenceReference.relative_path is required")

    def citation_label(self) -> str:
        """Human-readable citation, e.g. 'procedure.pdf, page 4' or
        'notes.md, chunk 2'. Never invents a page number that wasn't
        actually tracked."""
        if self.page is not None:
            return f"{self.filename}, page {self.page}"
        if self.section:
            return f"{self.filename}, section \"{self.section}\""
        return f"{self.filename}, chunk {self.chunk_index}"


@dataclass(frozen=True, slots=True)
class DocumentChunkResult:
    """One retrieved chunk, always carrying its evidence reference --
    never bare text. Callers (tools, CLI) should never present ``content``
    without also surfacing ``evidence.citation_label()``.

    M2.5A: also carries document-level authority status. ``status`` is
    CURRENT unless ``superseded_by_doc_id`` is set, in which case it is
    SUPERSEDED -- callers must surface this rather than presenting
    superseded evidence as if it were current (see
    docs/MAIA_DOCUMENT_KNOWLEDGE_V1.md, Document Authority & Supersession
    V1). This never removes the chunk from results -- superseded evidence
    remains retrievable, only its authority status changes."""

    content: str
    score: float
    evidence: DocumentEvidenceReference
    title: str = ""
    doc_type: str = ""
    status: str = "CURRENT"
    superseded_by_doc_id: Optional[str] = None
    superseded_by_filename: Optional[str] = None
    superseded_at: Optional[float] = None
    # M2.5A.1: derived purely from comparing the two documents' already-
    # stored whole-file sha256 values (FileRecord.sha256) -- no new hash
    # computed, no diff engine. True/False only when status is
    # SUPERSEDED and the successor record resolves; None whenever the
    # comparison cannot be established (CURRENT with no successor, or a
    # successor record that can't be resolved) -- never guessed.
    same_content_as_successor: Optional[bool] = None
    # M2.5A orphaned-supersession repair: True only when status is
    # SUPERSEDED and the recorded successor doc_id no longer resolves
    # (e.g. removed via `jarvis document ingest`). Never implies status
    # should become CURRENT -- only clear_supersession() (a deliberate
    # human action) can do that.
    successor_missing: bool = False


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Source-file-level provenance for one ingested document.

    M2.5A: ``superseded_by_doc_id``/``superseded_at`` are None for every
    document by default (CURRENT). ``ingested_at`` (below) is when this
    record was indexed -- it is NEVER a business-effective date, and must
    never be presented as one; see docs/MAIA_DOCUMENT_KNOWLEDGE_V1.md."""

    doc_id: str
    workspace_id: str
    relative_path: str
    filename: str
    file_type: str
    content_hash: str
    mtime: float
    ingested_at: float
    parser_version: str
    chunk_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    superseded_by_doc_id: Optional[str] = None
    superseded_by_filename: Optional[str] = None
    superseded_at: Optional[float] = None
    same_content_as_successor: Optional[bool] = None
    successor_missing: bool = False

    @property
    def status(self) -> str:
        return "SUPERSEDED" if self.superseded_by_doc_id else "CURRENT"


@dataclass(slots=True)
class IngestOutcome:
    """Result of one ingestion sweep of a workspace."""

    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    unchanged: int = 0
    skipped_unsupported: List[str] = field(default_factory=list)
    skipped_sensitive: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)
    chunks_written: int = 0


__all__ = [
    "DocumentEvidenceReference",
    "DocumentChunkResult",
    "DocumentRecord",
    "IngestOutcome",
]
