from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoricalJobStore:
    """Small SQLite-backed store for long historical backtest jobs.

    The path can be pointed at a Render persistent disk with HISTORICAL_JOB_DB_PATH.
    Without a mounted persistent disk, the store still survives ordinary process
    crashes/reloads that retain the filesystem, while the job keepalive reduces
    idle instance recycling during active work.
    """

    def __init__(self, path: str | None = None):
        configured = path or os.getenv("HISTORICAL_JOB_DB_PATH")
        if configured:
            self.path = Path(configured)
        else:
            self.path = Path("data") / "historical_backtest_jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self):
        with self._lock, self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT,
                    updated_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    request_json TEXT NOT NULL,
                    progress_json TEXT,
                    result_json TEXT,
                    checkpoint_json TEXT,
                    error TEXT,
                    persistence_note TEXT
                )
                """
            )
            con.commit()

    @staticmethod
    def _dump(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, separators=(",", ":"), default=str)

    @staticmethod
    def _load(value: str | None, default=None):
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    def upsert(self, job: dict):
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO jobs (
                    job_id,status,created_at,updated_at,started_at,finished_at,
                    request_json,progress_json,result_json,checkpoint_json,error,persistence_note
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    request_json=excluded.request_json,
                    progress_json=excluded.progress_json,
                    result_json=excluded.result_json,
                    checkpoint_json=excluded.checkpoint_json,
                    error=excluded.error,
                    persistence_note=excluded.persistence_note
                """,
                (
                    job["job_id"],
                    job.get("status", "queued"),
                    job.get("created_at") or _utc_now(),
                    job.get("updated_at") or _utc_now(),
                    job.get("started_at"),
                    job.get("finished_at"),
                    self._dump(job.get("request") or {}),
                    self._dump(job.get("progress") or {}),
                    self._dump(job.get("result")),
                    self._dump(job.get("checkpoint")),
                    job.get("error"),
                    job.get("persistence_note"),
                ),
            )
            con.commit()

    def get(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row(r) for r in rows]

    def resumable(self) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created_at ASC"
            ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, job_id: str):
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
            con.commit()

    def _row(self, row: sqlite3.Row) -> dict:
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "request": self._load(row["request_json"], {}),
            "progress": self._load(row["progress_json"], {}),
            "result": self._load(row["result_json"], None),
            "checkpoint": self._load(row["checkpoint_json"], None),
            "error": row["error"],
            "persistence_note": row["persistence_note"],
        }
