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
    without also surfacing ``evidence.citation_label()``."""

    content: str
    score: float
    evidence: DocumentEvidenceReference
    title: str = ""
    doc_type: str = ""


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Source-file-level provenance for one ingested document."""

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
