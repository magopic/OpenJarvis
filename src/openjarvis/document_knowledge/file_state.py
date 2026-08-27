"""Per-file provenance tracking for the document workspace (FASE 4O.5).

A small, dedicated SQLite table -- deliberately NOT bolted onto
``KnowledgeStore``'s own schema (that store tracks chunk-level
``content_hash``, not file-level ``path``/``mtime``/``sha256``; see the
package README/docs for why). This is the source of truth ``ingest.py``
consults to decide whether a file is new, unchanged, modified, or has
disappeared since the last sweep.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# M2.5A: additive migration for document-level supersession metadata --
# same idempotent ALTER TABLE technique as connectors/store.py's own
# _V1_COLUMNS (PRAGMA table_info check + ALTER TABLE ADD COLUMN only for
# columns missing on the existing on-disk table). Both columns are
# nullable: every pre-existing row (including the real M2.3 PDF) defaults
# to superseded_by_doc_id=NULL -> status CURRENT, with zero behavior
# change until a human explicitly marks a supersession.
_V1A_COLUMNS: List[Tuple[str, str]] = [
    ("superseded_by_doc_id", "TEXT"),
    ("superseded_at", "REAL"),
]


@dataclass(frozen=True, slots=True)
class FileRecord:
    relative_path: str
    doc_id: str
    sha256: str
    mtime: float
    file_type: str
    ingested_at: float
    parser_version: str
    superseded_by_doc_id: Optional[str] = None
    superseded_at: Optional[float] = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class FileStateStore:
    """One SQLite file per workspace, tracking exactly one row per
    ingested source file. Never stores file content -- only identity."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # FASE 4O.6A: DocumentKnowledgeService (which owns this store) is
        # constructed once per tool instance and then invoked by whichever
        # thread the orchestrator dispatches a tool call on -- the
        # orchestrator's tool-execution loop is not guaranteed single-
        # threaded. `check_same_thread=False` matches the same pattern
        # already used by every other SQLite-backed store in this codebase
        # (connectors/store.py::KnowledgeStore, second_brain/store.py::
        # SecondBrainStore) -- this file was the one place that pattern
        # was missed, causing a live-reproduced "SQLite objects created in
        # a thread can only be used in that same thread" failure on
        # document_list_sources/document_search when called alongside
        # other tools in one turn.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                relative_path   TEXT PRIMARY KEY,
                doc_id          TEXT NOT NULL,
                sha256          TEXT NOT NULL,
                mtime           REAL NOT NULL,
                file_type       TEXT NOT NULL,
                ingested_at     REAL NOT NULL,
                parser_version  TEXT NOT NULL
            )
            """
        )
        self._migrate_v1a_columns()
        self._conn.commit()

    def _migrate_v1a_columns(self) -> None:
        """Add M2.5A supersession columns to pre-existing tables
        (idempotent -- safe to run against a fresh table or one already
        migrated)."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(files)").fetchall()}
        for col_name, col_def in _V1A_COLUMNS:
            if col_name not in existing:
                self._conn.execute(f"ALTER TABLE files ADD COLUMN {col_name} {col_def}")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FileRecord:
        keys = row.keys()
        return FileRecord(
            relative_path=row["relative_path"],
            doc_id=row["doc_id"],
            sha256=row["sha256"],
            mtime=row["mtime"],
            file_type=row["file_type"],
            ingested_at=row["ingested_at"],
            parser_version=row["parser_version"],
            superseded_by_doc_id=row["superseded_by_doc_id"] if "superseded_by_doc_id" in keys else None,
            superseded_at=row["superseded_at"] if "superseded_at" in keys else None,
        )

    def get(self, relative_path: str) -> Optional[FileRecord]:
        row = self._conn.execute(
            "SELECT * FROM files WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_doc_id(self, doc_id: str) -> Optional[FileRecord]:
        """Linear scan (files table has no doc_id index) -- deliberately
        matching the existing lookup-by-doc_id pattern already used by
        DocumentKnowledgeService.get_document(); acceptable at current
        and 10x-100x corpus scale per the M2.5 Phase 0 recon finding that
        this specific access pattern degrades linearly, not
        combinatorially."""
        row = self._conn.execute("SELECT * FROM files WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def all(self) -> Dict[str, FileRecord]:
        rows = self._conn.execute("SELECT * FROM files").fetchall()
        return {r["relative_path"]: self._row_to_record(r) for r in rows}

    def set_superseded(self, relative_path: str, *, superseded_by_doc_id: str, superseded_at: float) -> None:
        """Mark the document at ``relative_path`` as superseded. A single
        UPDATE statement -- atomic by construction, never touches
        knowledge_chunks (supersession never deletes evidence)."""
        self._conn.execute(
            "UPDATE files SET superseded_by_doc_id = ?, superseded_at = ? WHERE relative_path = ?",
            (superseded_by_doc_id, superseded_at, relative_path),
        )
        self._conn.commit()

    def clear_superseded(self, relative_path: str) -> None:
        """Orphaned-supersession repair: clear a document's supersession
        link, restoring it to CURRENT. A single UPDATE statement --
        atomic by construction, never touches knowledge_chunks, never
        touches any other row (the successor document, if it still
        exists, is completely unaffected). This is the only code path
        in this module that clears superseded_by_doc_id/superseded_at
        -- always a deliberate human action via
        DocumentKnowledgeService.clear_supersession(), never automatic."""
        self._conn.execute(
            "UPDATE files SET superseded_by_doc_id = NULL, superseded_at = NULL WHERE relative_path = ?",
            (relative_path,),
        )
        self._conn.commit()

    def upsert(self, record: FileRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO files (relative_path, doc_id, sha256, mtime, file_type, ingested_at, parser_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                doc_id=excluded.doc_id, sha256=excluded.sha256, mtime=excluded.mtime,
                file_type=excluded.file_type, ingested_at=excluded.ingested_at,
                parser_version=excluded.parser_version
            """,
            (
                record.relative_path, record.doc_id, record.sha256, record.mtime,
                record.file_type, record.ingested_at, record.parser_version,
            ),
        )
        self._conn.commit()

    def remove(self, relative_path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE relative_path = ?", (relative_path,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def make_doc_id(workspace_id: str, relative_path: str) -> str:
    """Stable, deterministic doc_id -- same file always maps to the same
    id across renames-of-nothing / repeat ingests, so an unmodified file
    is idempotently a no-op rather than accumulating duplicates."""
    return f"maia_documents:{workspace_id}:{relative_path}"


__all__ = ["FileRecord", "FileStateStore", "sha256_file", "make_doc_id"]
