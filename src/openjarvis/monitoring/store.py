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

# FASE 4Q.2 STEP 11 -- concurrency guard. A separate table (not a column
# on `monitors`) so it never needs a schema migration on an existing real
# monitoring.db: the PRIMARY KEY on monitor_id makes claiming atomic (an
# INSERT either succeeds or raises IntegrityError -- no read-then-write
# race), and it survives a process crash mid-run (unlike an in-memory
# lock), which is exactly the case that needs a *stale*-lock expiry
# rather than a lock that's held forever.
_CREATE_RUN_LOCKS_TABLE = """\
CREATE TABLE IF NOT EXISTS monitor_run_locks (
    monitor_id  TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    claimed_at  TEXT NOT NULL
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
        self._conn.execute(_CREATE_RUN_LOCKS_TABLE)
        self._conn.commit()

        # FASE 4Q.3 -- additive migrations for the notification attention
        # layer (principal isolation, promoted presentation fields, the
        # UNREAD/READ distinction). Mirrors the exact pattern already
        # established in agents/manager.py: try each ADD COLUMN, swallow
        # "duplicate column" on every db that already has it. Real
        # monitoring.db has zero notification rows today (the cert
        # monitor has never run a cycle), so there is nothing to backfill.
        _MIGRATIONS = [
            "ALTER TABLE monitor_notifications ADD COLUMN principal TEXT",
            "ALTER TABLE monitor_notifications ADD COLUMN source_type TEXT DEFAULT 'monitor'",
            "ALTER TABLE monitor_notifications ADD COLUMN source_id TEXT",
            "ALTER TABLE monitor_notifications ADD COLUMN severity TEXT",
            "ALTER TABLE monitor_notifications ADD COLUMN title TEXT",
            "ALTER TABLE monitor_notifications ADD COLUMN summary TEXT",
            "ALTER TABLE monitor_notifications ADD COLUMN read_at TEXT",
        ]
        for migration in _MIGRATIONS:
            try:
                self._conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    # -- Run concurrency guard (FASE 4Q.2 STEP 11) ------------------------

    def try_claim_run(self, monitor_id: str, run_id: str, stale_after_seconds: float) -> bool:
        """Atomically claim the right to run *monitor_id* as *run_id*.

        Expires and steals any lock older than *stale_after_seconds*
        first -- this is what makes a crash/restart mid-run recover on
        its own rather than permanently wedging the monitor (STEP 9/11's
        "restart during run"). Returns True if the claim succeeded
        (caller must run_cycle then release_run), False if another run
        is genuinely still active."""
        import time

        cutoff = str(time.time() - stale_after_seconds)
        self._conn.execute(
            "DELETE FROM monitor_run_locks WHERE monitor_id = ? AND claimed_at < ?",
            (monitor_id, cutoff),
        )
        try:
            self._conn.execute(
                "INSERT INTO monitor_run_locks (monitor_id, run_id, claimed_at) VALUES (?, ?, ?)",
                (monitor_id, run_id, str(time.time())),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.commit()  # commit the stale-lock DELETE above either way
            return False

    def release_run(self, monitor_id: str, run_id: str) -> None:
        """Release a claim -- only if *run_id* still holds it, so a stale
        claim this process already lost to someone else's fresh claim is
        never accidentally cleared out from under them."""
        self._conn.execute(
            "DELETE FROM monitor_run_locks WHERE monitor_id = ? AND run_id = ?",
            (monitor_id, run_id),
        )
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
                created_at, acknowledged, acknowledged_at, principal,
                source_type, source_id, severity, title, summary, read_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                n["id"],
                n["monitor_id"],
                n["fingerprint"],
                n["transition"],
                json.dumps(n.get("insight_snapshot") or {}),
                n["created_at"],
                int(bool(n.get("acknowledged", False))),
                n.get("acknowledged_at"),
                n.get("principal"),
                n.get("source_type", "monitor"),
                n.get("source_id") or n["monitor_id"],
                n.get("severity"),
                n.get("title"),
                n.get("summary"),
                n.get("read_at"),
            ),
        )
        self._conn.commit()

    def get_notification(self, notification_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM monitor_notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        return self._notification_row_to_dict(row) if row else None

    def mark_notification_read(self, notification_id: str, read_at: str) -> None:
        """Idempotent -- only sets read_at if it isn't already set, so a
        repeated mark-read never overwrites the original read timestamp."""
        self._conn.execute(
            "UPDATE monitor_notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (read_at, notification_id),
        )
        self._conn.commit()

    def list_notifications(
        self,
        *,
        principal: Optional[str] = None,
        monitor_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        unread_only: bool = False,
        severity: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM monitor_notifications WHERE 1=1"
        params: List[Any] = []
        if principal is not None:
            query += " AND principal = ?"
            params.append(principal)
        if monitor_id is not None:
            query += " AND monitor_id = ?"
            params.append(monitor_id)
        if acknowledged is not None:
            query += " AND acknowledged = ?"
            params.append(int(acknowledged))
        if unread_only:
            query += " AND read_at IS NULL"
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        rows = self._conn.execute(query, params).fetchall()
        return [self._notification_row_to_dict(r) for r in rows]

    def count_unread_notifications(self, principal: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM monitor_notifications WHERE principal = ? AND read_at IS NULL",
            (principal,),
        ).fetchone()
        return int(row["n"]) if row else 0

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
