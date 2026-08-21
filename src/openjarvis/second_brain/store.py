"""SQLite storage for MAIA Second Brain V1.

Dedicated database (``second_brain.db``, distinct from ``memory.db``
and ``knowledge.db``) with three tables: ``entries``, ``relationships``,
``audit_log``. FTS5 search on entries mirrors the auto-syncing
content-linked pattern already used by ``connectors/store.py``'s
``KnowledgeStore`` (trigger-maintained, not manually re-indexed like
``tools/storage/sqlite.py``'s ``SQLiteMemory``).

The audit hash chain mirrors ``security/audit.py``'s ``AuditLogger``
(SHA-256 over ``prev_hash | fields``) rather than inventing a new
mechanism.

This module has no governance logic and no knowledge of business
rules -- see ``service.py`` for the enforcement layer. Nothing here
should be imported directly by a tool or agent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.second_brain.types import (
    AuditEvent,
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

DEFAULT_SECOND_BRAIN_DB_PATH = DEFAULT_CONFIG_DIR / "second_brain.db"

_CREATE_ENTRIES = """
CREATE TABLE IF NOT EXISTS entries (
    id                   TEXT PRIMARY KEY,
    type                 TEXT NOT NULL,
    title                TEXT NOT NULL,
    summary              TEXT NOT NULL DEFAULT '',
    domains              TEXT NOT NULL DEFAULT '[]',
    entities             TEXT NOT NULL DEFAULT '[]',
    timestamp            REAL,
    source               TEXT NOT NULL,
    created_by           TEXT NOT NULL,
    provenance           TEXT NOT NULL,
    trust_status         TEXT NOT NULL,
    confidence           REAL,
    evidence_references  TEXT NOT NULL DEFAULT '[]',
    visibility           TEXT NOT NULL DEFAULT 'PRIVATE',
    superseded_by        TEXT REFERENCES entries(id),
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    archived_at          REAL
);
"""

_CREATE_RELATIONSHIPS = """
CREATE TABLE IF NOT EXISTS relationships (
    id               TEXT PRIMARY KEY,
    source_entry_id  TEXT NOT NULL REFERENCES entries(id),
    target_entry_id  TEXT NOT NULL REFERENCES entries(id),
    relation_type    TEXT NOT NULL,
    source           TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    confidence       REAL,
    status           TEXT NOT NULL DEFAULT 'PROPOSED',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
"""

_CREATE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',
    row_hash    TEXT NOT NULL,
    prev_hash   TEXT NOT NULL
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, summary, domains, entities,
    content='entries', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""

_CREATE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, summary, domains, entities)
    VALUES (new.rowid, new.title, new.summary, new.domains, new.entities);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, summary, domains, entities)
    VALUES ('delete', old.rowid, old.title, old.summary, old.domains, old.entities);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, summary, domains, entities)
    VALUES ('delete', old.rowid, old.title, old.summary, old.domains, old.entities);
    INSERT INTO entries_fts(rowid, title, summary, domains, entities)
    VALUES (new.rowid, new.title, new.summary, new.domains, new.entities);
END;
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_entries_type          ON entries(type);
CREATE INDEX IF NOT EXISTS idx_entries_trust_status   ON entries(trust_status);
CREATE INDEX IF NOT EXISTS idx_entries_visibility     ON entries(visibility);
CREATE INDEX IF NOT EXISTS idx_entries_created_by     ON entries(created_by);
CREATE INDEX IF NOT EXISTS idx_entries_archived_at    ON entries(archived_at);
CREATE INDEX IF NOT EXISTS idx_entries_superseded_by  ON entries(superseded_by);
CREATE INDEX IF NOT EXISTS idx_rel_source             ON relationships(source_entry_id);
CREATE INDEX IF NOT EXISTS idx_rel_target             ON relationships(target_entry_id);
CREATE INDEX IF NOT EXISTS idx_rel_type               ON relationships(relation_type);
CREATE INDEX IF NOT EXISTS idx_rel_status             ON relationships(status);
CREATE INDEX IF NOT EXISTS idx_audit_target           ON audit_log(target_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type       ON audit_log(event_type);
"""


def _entry_to_row(e: SecondBrainEntry) -> tuple:
    return (
        e.id,
        e.type.value,
        e.title,
        e.summary,
        json.dumps(e.domains),
        json.dumps(e.entities),
        e.timestamp,
        e.source,
        e.created_by,
        e.provenance,
        e.trust_status.value,
        e.confidence,
        json.dumps(
            [
                {
                    "capability": ref.capability,
                    "domain": ref.domain,
                    "metric": ref.metric,
                    "period": ref.period,
                    "filters": ref.filters,
                    "trust_status_at_capture": ref.trust_status_at_capture,
                    "fetched_at": ref.fetched_at,
                }
                for ref in e.evidence_references
            ]
        ),
        e.visibility.value,
        e.superseded_by,
        e.created_at,
        e.updated_at,
        e.archived_at,
    )


def _row_to_entry(row: sqlite3.Row) -> SecondBrainEntry:
    evidence_raw = json.loads(row["evidence_references"] or "[]")
    return SecondBrainEntry(
        id=row["id"],
        type=EntryType(row["type"]),
        title=row["title"],
        summary=row["summary"],
        domains=json.loads(row["domains"] or "[]"),
        entities=json.loads(row["entities"] or "[]"),
        timestamp=row["timestamp"],
        source=row["source"],
        created_by=row["created_by"],
        provenance=row["provenance"],
        trust_status=EntryTrustStatus(row["trust_status"]),
        confidence=row["confidence"],
        evidence_references=[
            EvidenceReference(
                capability=ref["capability"],
                domain=ref["domain"],
                metric=ref["metric"],
                period=ref["period"],
                filters=ref.get("filters", {}),
                trust_status_at_capture=ref.get("trust_status_at_capture", ""),
                fetched_at=ref.get("fetched_at", 0.0),
            )
            for ref in evidence_raw
        ],
        visibility=Visibility(row["visibility"]),
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
    )


def _relationship_to_row(r: Relationship) -> tuple:
    return (
        r.id,
        r.source_entry_id,
        r.target_entry_id,
        r.relation_type.value,
        r.source,
        r.created_by,
        r.confidence,
        r.status.value,
        r.created_at,
        r.updated_at,
    )


def _row_to_relationship(row: sqlite3.Row) -> Relationship:
    return Relationship(
        id=row["id"],
        source_entry_id=row["source_entry_id"],
        target_entry_id=row["target_entry_id"],
        relation_type=RelationshipType(row["relation_type"]),
        source=row["source"],
        created_by=row["created_by"],
        confidence=row["confidence"],
        status=RelationshipStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_audit_event(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        event_type=AuditEventType(row["event_type"]),
        actor=row["actor"],
        target_id=row["target_id"],
        action=row["action"],
        timestamp=row["timestamp"],
        details=json.loads(row["details"] or "{}"),
        row_hash=row["row_hash"],
        prev_hash=row["prev_hash"],
    )


class SecondBrainStore:
    """Low-level SQLite persistence. No validation, no governance."""

    def __init__(
        self, db_path: Union[str, Path] = DEFAULT_SECOND_BRAIN_DB_PATH
    ) -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            from openjarvis.security.file_utils import secure_create

            secure_create(self._db_path)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            _CREATE_ENTRIES
            + _CREATE_RELATIONSHIPS
            + _CREATE_AUDIT_LOG
            + _CREATE_FTS
            + _CREATE_TRIGGERS
            + _CREATE_INDEXES
        )
        self._conn.commit()

    # -- entries --------------------------------------------------------

    def insert_entry(self, entry: SecondBrainEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO entries (
                id, type, title, summary, domains, entities, timestamp,
                source, created_by, provenance, trust_status, confidence,
                evidence_references, visibility, superseded_by,
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _entry_to_row(entry),
        )
        self._conn.commit()

    def get_entry(self, entry_id: str) -> Optional[SecondBrainEntry]:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def set_entry_superseded(
        self, entry_id: str, superseded_by: str, updated_at: float
    ) -> None:
        self._conn.execute(
            "UPDATE entries SET superseded_by = ?, updated_at = ? WHERE id = ?",
            (superseded_by, updated_at, entry_id),
        )
        self._conn.commit()

    def set_entry_archived(
        self, entry_id: str, archived_at: float, updated_at: float
    ) -> None:
        self._conn.execute(
            "UPDATE entries SET archived_at = ?, updated_at = ? WHERE id = ?",
            (archived_at, updated_at, entry_id),
        )
        self._conn.commit()

    def list_entries(
        self,
        *,
        entry_type: Optional[EntryType] = None,
        trust_status: Optional[EntryTrustStatus] = None,
        domain: Optional[str] = None,
        entity: Optional[str] = None,
        include_archived: bool = False,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 50,
    ) -> List[SecondBrainEntry]:
        sql = "SELECT * FROM entries WHERE 1=1"
        params: List[Any] = []
        if entry_type is not None:
            sql += " AND type = ?"
            params.append(entry_type.value)
        if trust_status is not None:
            sql += " AND trust_status = ?"
            params.append(trust_status.value)
        if domain is not None:
            sql += " AND domains LIKE ?"
            params.append(f'%"{domain}"%')
        if entity is not None:
            sql += " AND entities LIKE ?"
            params.append(f'%"{entity}"%')
        if not include_archived:
            sql += " AND archived_at IS NULL"
        if since is not None:
            sql += " AND (timestamp IS NULL OR timestamp >= ?)"
            params.append(since)
        if until is not None:
            sql += " AND (timestamp IS NULL OR timestamp <= ?)"
            params.append(until)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def search_entries_fts(self, query: str, *, limit: int = 50) -> List[SecondBrainEntry]:
        if not query.strip():
            return []
        rows = self._conn.execute(
            """
            SELECT entries.* FROM entries_fts
            JOIN entries ON entries.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    # -- relationships ----------------------------------------------------

    def insert_relationship(self, rel: Relationship) -> None:
        self._conn.execute(
            """
            INSERT INTO relationships (
                id, source_entry_id, target_entry_id, relation_type,
                source, created_by, confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _relationship_to_row(rel),
        )
        self._conn.commit()

    def get_relationship(self, relationship_id: str) -> Optional[Relationship]:
        row = self._conn.execute(
            "SELECT * FROM relationships WHERE id = ?", (relationship_id,)
        ).fetchone()
        return _row_to_relationship(row) if row else None

    def get_relationships(
        self,
        entry_id: str,
        *,
        direction: str = "both",
        relation_type: Optional[RelationshipType] = None,
        status: Optional[RelationshipStatus] = None,
    ) -> List[Relationship]:
        """Direct neighbors only -- no multi-hop traversal in V1."""
        clauses = []
        params: List[Any] = []
        if direction in ("out", "both"):
            clauses.append("source_entry_id = ?")
            params.append(entry_id)
        if direction in ("in", "both"):
            clauses.append("target_entry_id = ?")
            params.append(entry_id)
        if not clauses:
            return []
        sql = f"SELECT * FROM relationships WHERE ({' OR '.join(clauses)})"
        if relation_type is not None:
            sql += " AND relation_type = ?"
            params.append(relation_type.value)
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_relationship(r) for r in rows]

    def set_relationship_status(
        self, relationship_id: str, status: RelationshipStatus, updated_at: float
    ) -> None:
        self._conn.execute(
            "UPDATE relationships SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, updated_at, relationship_id),
        )
        self._conn.commit()

    # -- audit hash chain (mirrors security/audit.py::AuditLogger) --------

    def tail_hash(self) -> str:
        row = self._conn.execute(
            "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row and row[0] else ""

    def append_audit_event(
        self,
        event_type: AuditEventType,
        *,
        actor: str,
        target_id: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> AuditEvent:
        ts = timestamp if timestamp is not None else time.time()
        details_json = json.dumps(details or {})
        prev_hash = self.tail_hash()
        hash_input = (
            f"{prev_hash}|{ts}|{event_type.value}|{actor}|{target_id}"
            f"|{action}|{details_json}"
        )
        row_hash = hashlib.sha256(hash_input.encode()).hexdigest()

        cur = self._conn.execute(
            """
            INSERT INTO audit_log
                (event_type, actor, target_id, action, timestamp,
                 details, row_hash, prev_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type.value, actor, target_id, action, ts, details_json, row_hash, prev_hash),
        )
        self._conn.commit()
        return AuditEvent(
            id=cur.lastrowid,
            event_type=event_type,
            actor=actor,
            target_id=target_id,
            action=action,
            timestamp=ts,
            details=details or {},
            row_hash=row_hash,
            prev_hash=prev_hash,
        )

    def list_audit_events(
        self, *, target_id: Optional[str] = None, limit: int = 100
    ) -> List[AuditEvent]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: List[Any] = []
        if target_id is not None:
            sql += " AND target_id = ?"
            params.append(target_id)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_audit_event(r) for r in rows]

    def verify_audit_chain(self) -> "tuple[bool, Optional[int]]":
        """Verify the hash chain. Returns (True, None) or (False, first_broken_id)."""
        rows = self._conn.execute(
            "SELECT id, event_type, actor, target_id, action, timestamp,"
            " details, row_hash, prev_hash FROM audit_log ORDER BY id"
        ).fetchall()
        expected_prev = ""
        for row in rows:
            rid, etype, actor, target_id, action, ts, details, stored_hash, stored_prev = row
            if stored_prev != expected_prev:
                return False, rid
            hash_input = f"{stored_prev}|{ts}|{etype}|{actor}|{target_id}|{action}|{details}"
            computed = hashlib.sha256(hash_input.encode()).hexdigest()
            if computed != stored_hash:
                return False, rid
            expected_prev = stored_hash
        return True, None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_SECOND_BRAIN_DB_PATH", "SecondBrainStore"]
