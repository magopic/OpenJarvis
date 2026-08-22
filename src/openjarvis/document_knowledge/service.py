"""Governed document retrieval contract for MAIA (FASE 4O.5).

Every result carries a full ``DocumentEvidenceReference`` -- callers
(tools, CLI) can always say "According to <filename>, page N..." rather
than presenting retrieved text as unattributed model knowledge. This is
the ONLY module in this package other tools/agents should import from;
``ingest.py``/``connector.py``/``file_state.py`` are implementation
details of how the index gets built, not how it gets read.

Explicitly NOT a Second Brain API: nothing here creates, proposes, or
touches a ``SecondBrainEntry``. Explicitly NOT an OPS API: nothing here
can be mistaken for a certified KPI value -- nothing here returns a
number without the surrounding document text it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.document_knowledge.file_state import FileStateStore
from openjarvis.document_knowledge.types import (
    DocumentChunkResult,
    DocumentEvidenceReference,
    DocumentRecord,
    IngestOutcome,
)
from openjarvis.document_knowledge.workspace import default_workspace_root, ensure_workspace_root


def _fts5_safe_query(text: str) -> str:
    """Same technique as ``second_brain/store.py::_fts5_safe_query`` --
    wrap every whitespace token in double quotes so FTS5 treats each as
    a literal phrase, not query-syntax (bareword hyphens/colons/parens
    otherwise misparse). ``KnowledgeStore.retrieve()`` itself has no
    such sanitizer (a gap noted during this phase's own audit); applying
    it here, at the one governed entry point, closes it without touching
    the shared store's code."""
    tokens = text.split()
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


@dataclass(frozen=True, slots=True)
class DocumentKnowledgeConfig:
    workspace_id: str
    workspace_root: Path
    knowledge_db_path: Path
    file_state_db_path: Path


def default_config(workspace_root: Optional[Path] = None) -> DocumentKnowledgeConfig:
    root = workspace_root or default_workspace_root()
    workspace_id = root.name or "default"
    return DocumentKnowledgeConfig(
        workspace_id=workspace_id,
        workspace_root=root,
        knowledge_db_path=root.parent / "maia_documents_index.db",
        file_state_db_path=root.parent / "maia_documents_files.db",
    )


class DocumentKnowledgeService:
    """Governed read (and, via ``ingest_now``, controlled write-through-
    ingestion) interface. This is the type tools/CLI/agents construct --
    never ``KnowledgeStore``/``FileStateStore`` directly."""

    def __init__(self, config: Optional[DocumentKnowledgeConfig] = None) -> None:
        self._config = config or default_config()
        self._store = KnowledgeStore(db_path=str(self._config.knowledge_db_path))
        self._file_state = FileStateStore(self._config.file_state_db_path)

    @property
    def config(self) -> DocumentKnowledgeConfig:
        return self._config

    def ingest_now(self) -> IngestOutcome:
        # Imported lazily (function-local) purely to keep `ingest.py`'s
        # own imports from ever needing to import this module back --
        # there is no real cycle today, this just keeps the dependency
        # direction obviously one-way (service depends on ingest, never
        # the reverse) as the package grows.
        from openjarvis.document_knowledge.ingest import sync_workspace

        ensure_workspace_root(self._config.workspace_root)
        return sync_workspace(
            store=self._store,
            file_state=self._file_state,
            root=self._config.workspace_root,
            workspace_id=self._config.workspace_id,
        )

    # -- retrieval ---------------------------------------------------------

    def search_documents(
        self,
        query: str,
        *,
        top_k: int = 10,
        filename: Optional[str] = None,
    ) -> List[DocumentChunkResult]:
        """Lexical (FTS5/BM25) search over ingested document chunks.

        V1 is lexical-only by design (STEP: "first establish whether
        lexical/full-text retrieval is sufficient... V1 must work without
        downloading a large ML model") -- no embeddings are computed or
        required. Every result includes its ``DocumentEvidenceReference``.
        """
        if not query.strip():
            return []
        safe_query = _fts5_safe_query(query)
        raw = self._store.retrieve(safe_query, top_k=max(1, top_k), source="maia_documents")

        results: List[DocumentChunkResult] = []
        for r in raw:
            meta = r.metadata
            rel_path = meta.get("relative_path", "")
            if filename is not None and Path(rel_path).name != filename:
                continue
            evidence = DocumentEvidenceReference(
                doc_id=meta.get("doc_id", ""),
                chunk_id=meta.get("chunk_id", ""),
                workspace_id=meta.get("workspace_id", self._config.workspace_id),
                relative_path=rel_path,
                filename=meta.get("filename") or Path(rel_path).name,
                content_hash=meta.get("content_hash", ""),
                chunk_index=int(meta.get("chunk_index", 0)),
                page=meta.get("page"),
                section=meta.get("section"),
            )
            results.append(
                DocumentChunkResult(
                    content=r.content,
                    score=r.score,
                    evidence=evidence,
                    title=meta.get("title", ""),
                    doc_type=meta.get("doc_type", ""),
                )
            )
        return results

    def get_document(self, doc_id: str) -> Optional[DocumentRecord]:
        """File-level provenance for one ingested document."""
        for rel_path, record in self._file_state.all().items():
            if record.doc_id == doc_id:
                row = self._store._conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_chunks WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                chunk_count = int(row["c"]) if row else 0
                return DocumentRecord(
                    doc_id=doc_id,
                    workspace_id=self._config.workspace_id,
                    relative_path=rel_path,
                    filename=Path(rel_path).name,
                    file_type=record.file_type,
                    content_hash=record.sha256,
                    mtime=record.mtime,
                    ingested_at=record.ingested_at,
                    parser_version=record.parser_version,
                    chunk_count=chunk_count,
                )
        return None

    def get_document_chunk(self, chunk_id: str) -> Optional[DocumentChunkResult]:
        """A single chunk by id, with full provenance."""
        row = self._store._conn.execute(
            "SELECT content, metadata FROM knowledge_chunks WHERE id = ? AND deleted_at IS NULL",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        rel_path = meta.get("relative_path", "")
        evidence = DocumentEvidenceReference(
            doc_id=meta.get("doc_id", ""),
            chunk_id=meta.get("chunk_id", chunk_id),
            workspace_id=meta.get("workspace_id", self._config.workspace_id),
            relative_path=rel_path,
            filename=meta.get("filename") or Path(rel_path).name,
            content_hash=meta.get("content_hash", ""),
            chunk_index=int(meta.get("chunk_index", 0)),
            page=meta.get("page"),
            section=meta.get("section"),
        )
        return DocumentChunkResult(
            content=row["content"],
            score=0.0,
            evidence=evidence,
            title=meta.get("title", ""),
            doc_type=meta.get("doc_type", ""),
        )

    def list_documents(self) -> List[DocumentRecord]:
        out: List[DocumentRecord] = []
        for rel_path, record in sorted(self._file_state.all().items()):
            doc = self.get_document(record.doc_id)
            if doc is not None:
                out.append(doc)
        return out

    def close(self) -> None:
        self._store.close()
        self._file_state.close()


__all__ = ["DocumentKnowledgeService", "DocumentKnowledgeConfig", "default_config"]
