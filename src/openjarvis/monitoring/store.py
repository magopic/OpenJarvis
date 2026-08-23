"""SQLite-backed persistence for monitors, runs, issue state, and
notifications. Mirrors scheduler/store.py's exact pattern (including
``check_same_thread=False`` from the start -- FASE 4O.6A found this
missing on a different store caused a genuine live cross-thread crash,
so every store in this codebase gets it from day one now)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

_CREATE_MONITORS_TABLE = """\
CREATE TABLE IF NOT EXISTS monitors (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    source_requirements     TEXT NOT NULL DEFAULT '{}',
    enabled                 INTEGER NOT NULL DEFAULT 1,
    cadence                 TEXT NOT NULL DEFAULT 'MANUAL',
    detector_scope          TEXT,
    principal               TEXT NOT NULL DEFAULT 'monitor:default',
    created_at              TEXT NOT NULL,
    last_run_at             TEXT,
    last_success_at         TEXT,
    status                  TEXT NOT NULL DEFAULT 'active',
    bounds                  TEXT NOT NULL DEFAULT '{}',
    scheduler_task_id       TEXT,
    consecutive_failures    INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_RUNS_TABLE = """\
CREATE TABLE IF NOT EXISTS monitor_runs (
    id                  TEXT PRIMARY KEY,
    monitor_id          TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    evidence_collected  INTEGER NOT NULL DEFAULT 0,
    insights_generated  INTEGER NOT NULL DEFAULT 0,
    errors              TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'success'
);
"""

_CREATE_ISSUE_STATE_TABLE = """\
CREATE TABLE IF NOT EXISTS monitor_issue_state (
    monitor_id      TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    status          TEXT NOT NULL,
    severity        TEXT,
    confidence      TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    resolved_at     TEXT,
    PRIMARY KEY (monitor_id, fingerprint)
);
"""

_CREATE_NOTIFICATIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS monitor_notifications (
    id                  TEXT PRIMARY KEY,
    monitor_id          TEXT NOT NULL,
    fingerprint         TEXT NOT NULL,
    transition          TEXT NOT NULL,
    insight_snapshot    TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    acknowledged        INTEGER NOT NULL DEFAULT 0,
    acknowledged_at     TEXT
);
"""


class MonitorStore:
    """SQLite CRUD store for monitors, runs, issue state, notifications."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_MONITORS_TABLE)
        self._conn.execute(_CREATE_RUNS_TABLE)
        self._conn.execute(_CREATE_ISSUE_STATE_TABLE)
        self._conn.execute(_CREATE_NOTIFICATIONS_TABLE)
        self._conn.commit()

    # -- Monitor CRUD ----------------------------------------------------

    def save_monitor(self, m: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO monitors
               (id, name, source_requirements, enabled, cadence, detector_scope,
                principal, created_at, last_run_at, last_success_at, status,
                bounds, scheduler_task_id, consecutive_failures)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m["id"],
                m["name"],
                json.dumps(m.get("source_requirements") or {}),
                int(bool(m.get("enabled", True))),
                m.get("cadence", "MANUAL"),
                json.dumps(m.get("detector_scope")) if m.get("detector_scope") is not None else None,
                m.get("principal", "monitor:default"),
                m.get("created_at", ""),
                m.get("last_run_at"),
                m.get("last_success_at"),
                m.get("status", "active"),
                json.dumps(m.get("bounds") or {}),
                m.get("scheduler_task_id"),
                int(m.get("consecutive_failures", 0)),
            ),
        )
        self._conn.commit()

    def get_monitor(self, monitor_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM monitors WHERE id = ?", (monitor_id,)
        ).fetchone()
        return self._monitor_row_to_dict(row) if row else None

    def list_monitors(self, *, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        if enabled is None:
            rows = self._conn.execute("SELECT * FROM monitors").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM monitors WHERE enabled = ?", (int(enabled),)
            ).fetchall()
        return [self._monitor_row_to_dict(r) for r in rows]

    def delete_monitor(self, monitor_id: str) -> None:
        self._conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        self._conn.commit()

    # -- Runs --------------------------------------------------------------

    def save_run(self, r: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO monitor_runs
               (id, monitor_id, started_at, completed_at, evidence_collected,
                insights_generated, errors, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["id"],
                r["monitor_id"],
                r["started_at"],
                r.get("completed_at"),
                int(r.get("evidence_collected", 0)),
                int(r.get("insights_generated", 0)),
                json.dumps(r.get("errors") or []),
                r.get("status", "success"),
            ),
        )
        self._conn.commit()

    def list_runs(self, monitor_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM monitor_runs WHERE monitor_id = ? ORDER BY started_at DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
        return [self._run_row_to_dict(r) for r in rows]

    # -- Issue state (dedup) -------------------------------------------------

    def get_issue_state(self, monitor_id: str) -> Dict[str, Dict[str, Any]]:
        """All persisted issue-state rows for a monitor, keyed by fingerprint."""
        rows = self._conn.execute(
            "SELECT * FROM monitor_issue_state WHERE monitor_id = ?", (monitor_id,)
        ).fetchall()
        return {r["fingerprint"]: dict(r) for r in rows}

    def upsert_issue_state(
        self,
        monitor_id: str,
        fingerprint: str,
        *,
        status: str,
        severity: Optional[str],
        confidence: Optional[str],
        first_seen: str,
        last_seen: str,
        resolved_at: Optional[str],
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO monitor_issue_state
               (monitor_id, fingerprint, status, severity, confidence,
                first_seen, last_seen, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (monitor_id, fingerprint, status, severity, confidence, first_seen, last_seen, resolved_at),
        )
        self._conn.commit()

    # -- Notifications -------------------------------------------------------

    def save_notification(self, n: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO monitor_notifications
               (id, monitor_id, fingerprint, transition, insight_snapshot,
                created_at, acknowledged, acknowledged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                n["id"],
                n["monitor_id"],
                n["fingerprint"],
                n["transition"],
                json.dumps(n.get("insight_snapshot") or {}),
                n["created_at"],
                int(bool(n.get("acknowledged", False))),
                n.get("acknowledged_at"),
            ),
        )
        self._conn.commit()

    def get_notification(self, notification_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM monitor_notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        return self._notification_row_to_dict(row) if row else None

    def list_notifications(
        self, *, monitor_id: Optional[str] = None, acknowledged: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM monitor_notifications WHERE 1=1"
        params: List[Any] = []
        if monitor_id is not None:
            query += " AND monitor_id = ?"
            params.append(monitor_id)
        if acknowledged is not None:
            query += " AND acknowledged = ?"
            params.append(int(acknowledged))
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._notification_row_to_dict(r) for r in rows]

    # -- Lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _monitor_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["source_requirements"] = json.loads(d.get("source_requirements") or "{}")
        d["detector_scope"] = json.loads(d["detector_scope"]) if d.get("detector_scope") else None
        d["bounds"] = json.loads(d.get("bounds") or "{}")
        d["enabled"] = bool(d.get("enabled"))
        return d

    @staticmethod
    def _run_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["errors"] = json.loads(d.get("errors") or "[]")
        return d

    @staticmethod
    def _notification_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["insight_snapshot"] = json.loads(d.get("insight_snapshot") or "{}")
        d["acknowledged"] = bool(d.get("acknowledged"))
        return d


__all__ = ["MonitorStore"]
