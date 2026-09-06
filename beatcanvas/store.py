"""SQLite job store.

Jobs live in a database rather than memory so the queue survives a restart. State
machine, with a deliberate approval gate between the cheap preview and the full render:

    queued -> previewing -> awaiting_approval -> rendering -> done
                   |                |               |
                   +----------------+---------------+--> failed / cancelled
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config

STATUS_QUEUED = "queued"
STATUS_PREVIEWING = "previewing"
STATUS_AWAITING = "awaiting_approval"
STATUS_RENDERING = "rendering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_PREVIEWING, STATUS_RENDERING)

DB_PATH = config.ROOT / "jobs" / "beatcanvas.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    template      TEXT NOT NULL,
    status        TEXT NOT NULL,
    stage         TEXT NOT NULL DEFAULT '',
    progress      REAL NOT NULL DEFAULT 0.0,
    frame_current INTEGER NOT NULL DEFAULT 0,
    frame_total   INTEGER NOT NULL DEFAULT 0,
    options       TEXT NOT NULL DEFAULT '{}',
    result        TEXT NOT NULL DEFAULT '{}',
    error         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
"""


@dataclass
class Job:
    id: int
    name: str
    template: str
    status: str
    stage: str
    progress: float
    frame_current: int
    frame_total: int
    options: dict
    result: dict
    error: str
    created_at: float
    updated_at: float

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["is_active"] = self.is_active
        return data


class JobStore:
    """Thread-safe access to the job table."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._recover()

    def _recover(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, options FROM jobs WHERE status IN (?, ?, ?)",
                (STATUS_QUEUED, STATUS_PREVIEWING, STATUS_RENDERING)
            ).fetchall()
            for row in rows:
                opts = json.loads(row["options"] or "{}")
                retries = opts.get("_retries", 0)
                if retries >= 3:
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, stage = 'failed after 3 retries' WHERE id = ?",
                        (STATUS_FAILED, row["id"])
                    )
                else:
                    opts["_retries"] = retries + 1
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, stage = 'requeued after restart', options = ? WHERE id = ?",
                        (STATUS_QUEUED, json.dumps(opts), row["id"])
                    )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes ----------------------------------------------------------

    def create(self, name: str, template: str, options: dict | None = None) -> Job:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO jobs (name, template, status, options, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?)",
                (name, template, STATUS_QUEUED, json.dumps(options or {}), now, now),
            )
            self._conn.commit()
            job_id = int(cursor.lastrowid)
        return self.get(job_id)  # type: ignore[return-value]

    def update(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        for key in ("options", "result"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*fields.values(), job_id),
            )
            self._conn.commit()

    def delete(self, job_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()

    # -- reads -----------------------------------------------------------

    def _row(self, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["options"] = json.loads(data.get("options") or "{}")
        data["result"] = json.loads(data.get("result") or "{}")
        return Job(**data)

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 60) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def next_queued(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC LIMIT 1",
                (STATUS_QUEUED,)).fetchone()
        return self._row(row) if row else None
