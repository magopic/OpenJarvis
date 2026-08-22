"""Ingestion orchestration for the MAIA document workspace (FASE 4O.5).

Deliberately does NOT reuse ``connectors/pipeline.py::IngestionPipeline``:
that pipeline dedupes by ``doc_id`` *permanently* (loaded once from the
store at construction, never re-checked against content) -- a file
modified after its first ingest would silently keep serving its stale,
original chunks forever, which violates this phase's explicit requirement
("renaming, modifying and re-ingesting a document must have deterministic,
documented behavior... never silently lose provenance").

This module instead reuses ``KnowledgeStore`` and ``SemanticChunker``
directly (both stateless/general-purpose, no dedup assumptions baked in)
and adds its own file-level diff against ``FileStateStore`` (SHA-256 +
mtime), giving explicit, deterministic behavior for every case:

- new file            -> chunk + store, record file state
- unchanged file (same sha256) -> no-op (skipped, not re-chunked)
- modified file (different sha256) -> delete old chunks for this doc_id,
  then chunk + store fresh ones (clean replace, never old+new mixed)
- file no longer present on disk -> delete its chunks, remove file state
  (STEP 9: deterministic deleted-document behavior, not silently stale)
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.connectors.chunker import SemanticChunker
from openjarvis.connectors.store import KnowledgeStore
from openjarvis.document_knowledge.connector import LocalDocumentsConnector
from openjarvis.document_knowledge.file_state import FileRecord, FileStateStore, make_doc_id, sha256_file
from openjarvis.document_knowledge.parsers import file_type_for
from openjarvis.document_knowledge.types import IngestOutcome

_PARSER_VERSION = "openjarvis.document_knowledge/v1"


def _chunk_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_workspace(
    *,
    store: KnowledgeStore,
    file_state: FileStateStore,
    root: Path,
    workspace_id: str,
    max_tokens: int = 512,
) -> IngestOutcome:
    """Run one full ingestion sweep of the authorized workspace.

    Bounded and deterministic: every file is visited at most once, and
    the outcome fully accounts for every file the walk saw (added,
    updated, unchanged, or one of the skip/error buckets) plus every
    previously-known file that's now missing (removed).
    """
    connector = LocalDocumentsConnector(workspace_root=root)
    chunker = SemanticChunker(max_tokens=max_tokens)
    outcome = IngestOutcome()
    seen_paths: set[str] = set()

    for doc in connector.sync():
        rel_path = doc.metadata["relative_path"]
        seen_paths.add(rel_path)
        abs_path = root / rel_path

        try:
            sha = sha256_file(abs_path)
            mtime = abs_path.stat().st_mtime
        except OSError as exc:
            outcome.errors[rel_path] = f"Could not hash file: {exc}"
            continue

        prior = file_state.get(rel_path)
        if prior is not None and prior.sha256 == sha:
            outcome.unchanged += 1
            continue

        doc_id = make_doc_id(workspace_id, rel_path)
        is_update = prior is not None
        if is_update:
            # Clean replace -- never leaves old and new chunks coexisting
            # under the same doc_id.
            store.delete(doc_id)

        try:
            file_type = file_type_for(abs_path)
        except Exception as exc:  # already filtered by the connector, defensive only
            outcome.errors[rel_path] = str(exc)
            continue

        chunk_count = _chunk_and_store(
            store=store,
            chunker=chunker,
            doc=doc,
            doc_id=doc_id,
            rel_path=rel_path,
            filename=abs_path.name,
            workspace_id=workspace_id,
        )

        file_state.upsert(
            FileRecord(
                relative_path=rel_path,
                doc_id=doc_id,
                sha256=sha,
                mtime=mtime,
                file_type=file_type,
                ingested_at=time.time(),
                parser_version=_PARSER_VERSION,
            )
        )

        if is_update:
            outcome.updated.append(rel_path)
        else:
            outcome.added.append(rel_path)
        outcome.chunks_written += chunk_count

    outcome.skipped_unsupported = connector.skipped_unsupported
    outcome.skipped_sensitive = connector.skipped_sensitive
    outcome.errors.update(connector.errors)

    # Deleted-file handling: anything file_state remembers that the walk
    # didn't see this time is gone from disk -- remove its chunks and its
    # file-state record rather than leaving stale, unreachable-by-user
    # content permanently searchable.
    for rel_path, record in file_state.all().items():
        if rel_path not in seen_paths:
            store.delete(record.doc_id)
            file_state.remove(rel_path)
            outcome.removed.append(rel_path)

    return outcome


def _chunk_and_store(
    *,
    store: KnowledgeStore,
    chunker: SemanticChunker,
    doc: Any,
    doc_id: str,
    rel_path: str,
    filename: str,
    workspace_id: str,
) -> int:
    pages: Optional[List[Dict[str, Any]]] = doc.metadata.get("pages")
    ingest_epoch = time.time()
    written = 0

    if pages:
        # PDF: chunk per page so every chunk points back to a real page
        # number (STEP: "preserve page references whenever technically
        # available") -- never joined into one blob that loses page
        # boundaries.
        for page_info in pages:
            page_num = page_info["page"]
            page_text = page_info["text"]
            if not page_text.strip():
                continue
            chunks = chunker.chunk(
                page_text,
                doc_type="document",
                metadata={"workspace_id": workspace_id, "relative_path": rel_path, "filename": filename, "page": page_num},
            )
            source_id = f"{rel_path}#p{page_num}"
            for chunk in chunks:
                store.store(
                    content=chunk.content,
                    source="maia_documents",
                    source_id=source_id,
                    doc_type="document",
                    doc_id=doc_id,
                    title=doc.title,
                    timestamp=doc.timestamp,
                    url=doc.url,
                    metadata=chunk.metadata,
                    chunk_index=chunk.index,
                    content_hash=_chunk_content_hash(chunk.content),
                    last_synced=ingest_epoch,
                )
                written += 1
    else:
        chunks = chunker.chunk(
            doc.content,
            doc_type="document",
            metadata={"workspace_id": workspace_id, "relative_path": rel_path, "filename": filename},
        )
        for chunk in chunks:
            store.store(
                content=chunk.content,
                source="maia_documents",
                source_id=rel_path,
                doc_type="document",
                doc_id=doc_id,
                title=doc.title,
                timestamp=doc.timestamp,
                url=doc.url,
                metadata=chunk.metadata,
                chunk_index=chunk.index,
                content_hash=_chunk_content_hash(chunk.content),
                last_synced=ingest_epoch,
            )
            written += 1

    return written


__all__ = ["sync_workspace"]
