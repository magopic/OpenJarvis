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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.document_knowledge.file_state import FileRecord, FileStateStore
from openjarvis.document_knowledge.types import (
    DocumentChunkResult,
    DocumentEvidenceReference,
    DocumentRecord,
    IngestOutcome,
)
from openjarvis.document_knowledge.workspace import default_workspace_root, ensure_workspace_root


class DocumentSupersessionError(Exception):
    """Raised when a requested document->document supersession is invalid
    (missing target, self-supersession, or would create a cycle). Never
    raised after any mutation has occurred -- validation runs entirely
    before the single atomic write in FileStateStore.set_superseded."""


_MAX_SUPERSESSION_WALK = 100


@dataclass(frozen=True, slots=True)
class _SupersessionInfo:
    """Internal helper -- the per-document version-state facts computed
    by _supersession_info(), unpacked into DocumentChunkResult/
    DocumentRecord by their respective callers. Not part of the public
    API of this module."""

    status: str
    successor_doc_id: Optional[str]
    successor_filename: Optional[str]
    superseded_at: Optional[float]
    same_content_as_successor: Optional[bool]
    # Orphaned-supersession repair (live-found: the successor file was
    # removed via `jarvis document ingest`, leaving superseded_by_doc_id
    # pointing at a doc_id no longer in FileStateStore). True only when
    # superseded_by_doc_id is set but does not resolve. Deliberately
    # does NOT imply status should become CURRENT -- a missing successor
    # is a broken lifecycle reference, not evidence the supersession
    # decision itself was wrong; only clear_supersession() (a deliberate
    # human action) can restore CURRENT.
    successor_missing: bool


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

        M2.5A: also attaches document-level authority status (CURRENT/
        SUPERSEDED) to every result -- superseded evidence is still
        returned (never silently erased), only annotated. Query
        construction, ranking, and FTS are untouched by this addition.
        """
        if not query.strip():
            return []
        safe_query = _fts5_safe_query(query)
        raw = self._store.retrieve(safe_query, top_k=max(1, top_k), source="maia_documents")

        by_doc_id = self._file_records_by_doc_id()

        results: List[DocumentChunkResult] = []
        for r in raw:
            meta = r.metadata
            rel_path = meta.get("relative_path", "")
            if filename is not None and Path(rel_path).name != filename:
                continue
            doc_id = meta.get("doc_id", "")
            evidence = DocumentEvidenceReference(
                doc_id=doc_id,
                chunk_id=meta.get("chunk_id", ""),
                workspace_id=meta.get("workspace_id", self._config.workspace_id),
                relative_path=rel_path,
                filename=meta.get("filename") or Path(rel_path).name,
                content_hash=meta.get("content_hash", ""),
                chunk_index=int(meta.get("chunk_index", 0)),
                page=meta.get("page"),
                section=meta.get("section"),
            )
            info = self._supersession_info(doc_id, by_doc_id)
            results.append(
                DocumentChunkResult(
                    content=r.content,
                    score=r.score,
                    evidence=evidence,
                    title=meta.get("title", ""),
                    doc_type=meta.get("doc_type", ""),
                    status=info.status,
                    superseded_by_doc_id=info.successor_doc_id,
                    superseded_by_filename=info.successor_filename,
                    superseded_at=info.superseded_at,
                    same_content_as_successor=info.same_content_as_successor,
                    successor_missing=info.successor_missing,
                )
            )
        return results

    # -- M2.5A: document authority / supersession ---------------------------

    def _file_records_by_doc_id(self) -> Dict[str, FileRecord]:
        """One scan of the (small) file_state table, reused across every
        result in a single search_documents/list_documents call -- avoids
        an O(n) doc_id lookup per result."""
        return {record.doc_id: record for record in self._file_state.all().values()}

    def _supersession_info(
        self, doc_id: str, by_doc_id: Optional[Dict[str, FileRecord]] = None
    ) -> "_SupersessionInfo":
        index = by_doc_id if by_doc_id is not None else self._file_records_by_doc_id()
        record = index.get(doc_id)
        if record is None or not record.superseded_by_doc_id:
            return _SupersessionInfo("CURRENT", None, None, None, None, False)
        successor = index.get(record.superseded_by_doc_id)
        successor_filename = Path(successor.relative_path).name if successor else None
        # M2.5A.1: a content-identity signal derived entirely from the
        # whole-file sha256 values already stored in `files` (no new
        # hash computed, no DB column added -- pure read-time equality
        # check). None when the successor record can't be resolved
        # (comparison cannot be established), never a guess.
        same_content = record.sha256 == successor.sha256 if successor is not None else None
        # M2.5A orphaned-supersession repair: successor_missing=True
        # only when superseded_by_doc_id is set (already guaranteed by
        # this branch) but the referenced doc_id no longer resolves.
        # status stays SUPERSEDED regardless -- a missing successor file
        # is never itself treated as reversing the supersession
        # decision (see clear_supersession() for the only path that does).
        return _SupersessionInfo(
            "SUPERSEDED",
            record.superseded_by_doc_id,
            successor_filename,
            record.superseded_at,
            same_content,
            successor is None,
        )

    def resolve_doc_id(self, identifier: str) -> Optional[str]:
        """Resolve a CLI-supplied identifier to a doc_id -- accepts
        either a relative_path (looked up directly) or a doc_id (checked
        for existence). Returns None if neither resolves to a known
        document."""
        by_path = self._file_state.get(identifier)
        if by_path is not None:
            return by_path.doc_id
        by_id = self._file_state.get_by_doc_id(identifier)
        if by_id is not None:
            return by_id.doc_id
        return None

    def supersede_document(self, old_doc_id: str, new_doc_id: str) -> None:
        """Mark ``old_doc_id`` as superseded by ``new_doc_id``.

        Validates entirely before any write (old exists, new exists,
        old != new, no cycle) -- a rejected call leaves the database
        completely unchanged. The single UPDATE this performs (via
        FileStateStore.set_superseded) never touches knowledge_chunks:
        no evidence is ever deleted by a supersession.
        """
        if old_doc_id == new_doc_id:
            raise DocumentSupersessionError("a document cannot supersede itself")

        old_record = self._file_state.get_by_doc_id(old_doc_id)
        if old_record is None:
            raise DocumentSupersessionError(f"old document not found: {old_doc_id!r}")

        new_record = self._file_state.get_by_doc_id(new_doc_id)
        if new_record is None:
            raise DocumentSupersessionError(f"new document not found: {new_doc_id!r}")

        # Cycle check: walk forward through new_doc_id's OWN existing
        # supersession chain. If old_doc_id is reachable, this write
        # would close a loop (covers both the direct 2-cycle -- new is
        # already superseded by old -- and any longer indirect chain).
        cursor = new_record.superseded_by_doc_id
        seen: set[str] = set()
        hops = 0
        while cursor is not None:
            if cursor == old_doc_id:
                raise DocumentSupersessionError(
                    f"supersession would create a cycle: {old_doc_id!r} -> {new_doc_id!r} -> ... -> {old_doc_id!r}"
                )
            if cursor in seen or hops >= _MAX_SUPERSESSION_WALK:
                break  # defensive stop; existing chain already bounded by this same guard
            seen.add(cursor)
            hops += 1
            next_record = self._file_state.get_by_doc_id(cursor)
            cursor = next_record.superseded_by_doc_id if next_record else None

        self._file_state.set_superseded(
            old_record.relative_path,
            superseded_by_doc_id=new_doc_id,
            superseded_at=time.time(),
        )

    def clear_supersession(self, doc_id: str) -> None:
        """Orphaned-supersession repair: clear ``doc_id``'s supersession
        link, restoring it to CURRENT. A deliberate, human-invoked
        authority action (CLI-only, see cli/document_cmd.py's
        ``unsupersede`` command) -- never triggered automatically, in
        particular never as a side effect of the referenced successor
        being removed from the workspace. A missing successor is a
        broken lifecycle reference, not evidence the original
        supersession decision was wrong (see _supersession_info's
        successor_missing).

        Validates the document exists before any write -- a rejected
        call leaves the database completely unchanged. Never touches
        knowledge_chunks (no chunk is ever deleted) and never modifies
        any other document's row, including the (possibly still-
        existing) successor.
        """
        record = self._file_state.get_by_doc_id(doc_id)
        if record is None:
            raise DocumentSupersessionError(f"document not found: {doc_id!r}")
        self._file_state.clear_superseded(record.relative_path)

    def get_document(self, doc_id: str) -> Optional[DocumentRecord]:
        """File-level provenance for one ingested document."""
        by_doc_id = self._file_records_by_doc_id()
        record = by_doc_id.get(doc_id)
        if record is None:
            return None
        row = self._store._conn.execute(
            "SELECT COUNT(*) AS c FROM knowledge_chunks WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        chunk_count = int(row["c"]) if row else 0
        info = self._supersession_info(doc_id, by_doc_id)
        return DocumentRecord(
            doc_id=doc_id,
            workspace_id=self._config.workspace_id,
            relative_path=record.relative_path,
            filename=Path(record.relative_path).name,
            file_type=record.file_type,
            content_hash=record.sha256,
            mtime=record.mtime,
            ingested_at=record.ingested_at,
            parser_version=record.parser_version,
            chunk_count=chunk_count,
            superseded_by_doc_id=info.successor_doc_id,
            superseded_by_filename=info.successor_filename,
            superseded_at=info.superseded_at,
            same_content_as_successor=info.same_content_as_successor,
            successor_missing=info.successor_missing,
        )

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


__all__ = [
    "DocumentKnowledgeService",
    "DocumentKnowledgeConfig",
    "DocumentSupersessionError",
    "default_config",
]
