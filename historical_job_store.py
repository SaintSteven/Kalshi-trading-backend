from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from github_checkpoint_store import GitHubCheckpointError, GitHubCheckpointStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoricalJobStore:
    """Local SQLite job store with a GitHub-backed durable mirror.

    SQLite remains the fast process-local cache. GitHub is the authoritative
    recovery layer for month-long jobs because Render's free filesystem is
    ephemeral across instance replacement/redeploys.
    """

    def __init__(self, path: str | None = None):
        configured = path or os.getenv("HISTORICAL_JOB_DB_PATH")
        self.path = Path(configured) if configured else Path("data") / "historical_backtest_jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        self.github = GitHubCheckpointStore()

    @property
    def external_enabled(self) -> bool:
        return self.github.enabled

    def persistence_status(self) -> dict:
        status = self.github.status()
        status["local_sqlite_path"] = str(self.path)
        return status

    def require_external(self):
        self.github.require_enabled()

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

    def upsert(self, job: dict, *, sync_external: bool = False):
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
        if sync_external:
            self.github.upsert(job)

    def _get_local(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def get(self, job_id: str) -> dict | None:
        local = self._get_local(job_id)
        if local:
            return local
        try:
            remote = self.github.get(job_id)
        except Exception:
            remote = None
        if remote:
            self.upsert(remote, sync_external=False)
            return remote
        return None

    def list_recent(self, limit: int = 20) -> list[dict]:
        local: list[dict]
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?", (int(limit),)
            ).fetchall()
        local = [self._row(r) for r in rows]
        remote: list[dict] = []
        try:
            remote = self.github.list_recent(limit)
        except Exception:
            pass
        merged: dict[str, dict] = {j["job_id"]: j for j in local if j.get("job_id")}
        for j in remote:
            jid = j.get("job_id")
            if not jid:
                continue
            prior = merged.get(jid)
            if not prior or (j.get("updated_at") or "") > (prior.get("updated_at") or ""):
                merged[jid] = j
                if "request" in j:
                    self.upsert(j, sync_external=False)
        out = sorted(merged.values(), key=lambda j: j.get("updated_at") or j.get("created_at") or "", reverse=True)
        return out[: int(limit)]

    def recover_latest_checkpoint(self, job_id: str) -> dict | None:
        local=self._get_local(job_id)
        if local and isinstance(local.get("checkpoint"),dict):
            return local["checkpoint"]
        return self.github.latest_checkpoint_before_completion(job_id)

    def resumable(self) -> list[dict]:
        return [j for j in self.list_recent(50) if j.get("status") in ("queued", "running") and "request" in j]

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
