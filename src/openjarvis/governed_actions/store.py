"""SQLite-backed persistence for governed actions and their audit log.
Mirrors monitoring/store.py's exact pattern (`check_same_thread=False`
from day one -- the FASE 4O.6A lesson, applied proactively to every new
store since)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_CREATE_ACTIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS governed_actions (
    id                  TEXT PRIMARY KEY,
    principal           TEXT NOT NULL,
    capability          TEXT NOT NULL,
    arguments           TEXT NOT NULL DEFAULT '{}',
    arguments_hash      TEXT NOT NULL,
    rationale           TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    proposal_id         TEXT,
    supporting_evidence TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    expires_at          TEXT,
    approved_at         TEXT,
    approved_by         TEXT,
    executed_at         TEXT,
    execution_result    TEXT,
    failure             TEXT
);
"""

_CREATE_AUDIT_TABLE = """\
CREATE TABLE IF NOT EXISTS governed_action_audit (
    id                  TEXT PRIMARY KEY,
    action_id           TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    previous_status     TEXT,
    new_status          TEXT NOT NULL,
    principal           TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    capability          TEXT NOT NULL,
    arguments_hash      TEXT NOT NULL DEFAULT ''
);
"""


class GovernedActionStore:
    """SQLite CRUD store for governed actions and their immutable audit log."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_ACTIONS_TABLE)
        self._conn.execute(_CREATE_AUDIT_TABLE)
        self._conn.commit()

    # -- Actions -------------------------------------------------------------

    def save_action(self, a: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO governed_actions
               (id, principal, capability, arguments, arguments_hash, rationale,
                status, proposal_id, supporting_evidence, created_at, expires_at,
                approved_at, approved_by, executed_at, execution_result, failure)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                a["id"],
                a["principal"],
                a["capability"],
                json.dumps(a.get("arguments") or {}),
                a["arguments_hash"],
                a.get("rationale", ""),
                a["status"],
                a.get("proposal_id"),
                json.dumps(a.get("supporting_evidence") or []),
                a.get("created_at", ""),
                a.get("expires_at"),
                a.get("approved_at"),
                a.get("approved_by"),
                a.get("executed_at"),
                json.dumps(a["execution_result"]) if a.get("execution_result") is not None else None,
                a.get("failure"),
            ),
        )
        self._conn.commit()

    def get_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM governed_actions WHERE id = ?", (action_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_actions(
        self, *, principal: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM governed_actions WHERE 1=1"
        params: List[Any] = []
        if principal is not None:
            query += " AND principal = ?"
            params.append(principal)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # -- Audit log (STEP 17, append-only) -------------------------------------

    def append_audit(self, entry: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO governed_action_audit
               (id, action_id, timestamp, previous_status, new_status,
                principal, reason, capability, arguments_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.get("id") or uuid.uuid4().hex[:16],
                entry["action_id"],
                entry["timestamp"],
                entry.get("previous_status"),
                entry["new_status"],
                entry["principal"],
                entry.get("reason", ""),
                entry["capability"],
                entry.get("arguments_hash", ""),
            ),
        )
        self._conn.commit()

    def list_audit(self, action_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM governed_action_audit WHERE action_id = ? ORDER BY timestamp", (action_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["arguments"] = json.loads(d.get("arguments") or "{}")
        d["supporting_evidence"] = json.loads(d.get("supporting_evidence") or "[]")
        d["execution_result"] = json.loads(d["execution_result"]) if d.get("execution_result") else None
        return d


__all__ = ["GovernedActionStore"]
