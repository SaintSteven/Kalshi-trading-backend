from __future__ import annotations

import base64
import gzip
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


DEFAULT_REPO = "SaintSteven/Kalshi-trading-backend"
DEFAULT_BRANCH = "backtest-checkpoints"
DEFAULT_ROOT = "historical_backtest_checkpoints"
API_ROOT = "https://api.github.com"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitHubCheckpointError(RuntimeError):
    pass


class GitHubCheckpointStore:
    """Durable mirror for historical-backtest jobs using a non-deploy GitHub branch.

    Required Render env var:
      GITHUB_CHECKPOINT_TOKEN  fine-grained PAT with repository Contents: Read and write

    Optional env vars:
      GITHUB_CHECKPOINT_REPO   owner/repo (defaults to the backend repo)
      GITHUB_CHECKPOINT_BRANCH branch used only for checkpoint artifacts
      GITHUB_CHECKPOINT_ROOT   directory inside that branch

    Job payloads are gzip-compressed JSON. A tiny index is maintained separately so
    a fresh Render process can rediscover the latest/resumable job without local disk.
    """

    def __init__(self):
        self.token = (os.getenv("GITHUB_CHECKPOINT_TOKEN") or "").strip()
        self.repo = (os.getenv("GITHUB_CHECKPOINT_REPO") or DEFAULT_REPO).strip()
        self.branch = (os.getenv("GITHUB_CHECKPOINT_BRANCH") or DEFAULT_BRANCH).strip()
        self.root = (os.getenv("GITHUB_CHECKPOINT_ROOT") or DEFAULT_ROOT).strip().strip("/")
        self.timeout = float(os.getenv("GITHUB_CHECKPOINT_TIMEOUT", "30"))
        self._lock = threading.RLock()
        self._branch_ready = False
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repo and "/" in self.repo and self.branch)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": "github",
            "repository": self.repo,
            "branch": self.branch,
            "root": self.root,
            "token_configured": bool(self.token),
            "last_error": self.last_error,
            "durability": "external" if self.enabled else "not_configured",
        }

    def require_enabled(self):
        if not self.enabled:
            raise GitHubCheckpointError(
                "GitHub checkpoint persistence is not configured. Add GITHUB_CHECKPOINT_TOKEN to the Render "
                "backend environment. The token should be a fine-grained GitHub token limited to the backend "
                "repository with Contents read/write permission."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "KalshiTradingPlatform-v2.6.8-checkpoint-store",
        }

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.require_enabled()
        url = f"{API_ROOT}{path}"
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            response = client.request(method, url, **kwargs)
        if response.status_code >= 400:
            detail = response.text[:1200]
            self.last_error = f"GitHub {response.status_code}: {detail}"
            raise GitHubCheckpointError(self.last_error)
        self.last_error = None
        return response

    def _ensure_branch(self):
        if self._branch_ready:
            return
        with self._lock:
            if self._branch_ready:
                return
            owner_repo = self.repo
            ref_path = quote(f"heads/{self.branch}", safe="/")
            # Fast path: checkpoint branch already exists.
            with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                existing = client.get(f"{API_ROOT}/repos/{owner_repo}/git/ref/{ref_path}")
            if existing.status_code == 200:
                self._branch_ready = True
                self.last_error = None
                return
            if existing.status_code not in (404, 422):
                self.last_error = f"GitHub {existing.status_code}: {existing.text[:1200]}"
                raise GitHubCheckpointError(self.last_error)

            repo_meta = self._request("GET", f"/repos/{owner_repo}").json()
            default_branch = repo_meta.get("default_branch") or "main"
            default_ref = self._request(
                "GET", f"/repos/{owner_repo}/git/ref/{quote(f'heads/{default_branch}', safe='/')}"
            ).json()
            sha = ((default_ref.get("object") or {}).get("sha"))
            if not sha:
                raise GitHubCheckpointError("Could not resolve the backend repository default-branch commit SHA.")

            with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                created = client.post(
                    f"{API_ROOT}/repos/{owner_repo}/git/refs",
                    json={"ref": f"refs/heads/{self.branch}", "sha": sha},
                )
            if created.status_code not in (201, 422):
                self.last_error = f"GitHub {created.status_code}: {created.text[:1200]}"
                raise GitHubCheckpointError(self.last_error)
            self._branch_ready = True
            self.last_error = None

    @staticmethod
    def _encode(value: Any) -> str:
        raw = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
        compressed = gzip.compress(raw, compresslevel=6)
        return base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def _decode(content_b64: str) -> Any:
        compressed = base64.b64decode(content_b64.encode("ascii"))
        raw = gzip.decompress(compressed)
        return json.loads(raw.decode("utf-8"))

    def _content_path(self, rel_path: str) -> str:
        full = f"{self.root}/{rel_path.lstrip('/')}"
        return quote(full, safe="/")

    def _get_file(self, rel_path: str) -> tuple[Any | None, str | None]:
        self._ensure_branch()
        owner_repo = self.repo
        encoded = self._content_path(rel_path)
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            response = client.get(
                f"{API_ROOT}/repos/{owner_repo}/contents/{encoded}", params={"ref": self.branch}
            )
        if response.status_code == 404:
            return None, None
        if response.status_code >= 400:
            self.last_error = f"GitHub {response.status_code}: {response.text[:1200]}"
            raise GitHubCheckpointError(self.last_error)
        payload = response.json()
        content = (payload.get("content") or "").replace("\n", "")
        if not content:
            return None, payload.get("sha")
        return self._decode(content), payload.get("sha")

    def _put_file(self, rel_path: str, value: Any, message: str):
        self._ensure_branch()
        owner_repo = self.repo
        encoded = self._content_path(rel_path)
        _, sha = self._get_file(rel_path)
        body: dict[str, Any] = {
            "message": message,
            "content": self._encode(value),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        self._request("PUT", f"/repos/{owner_repo}/contents/{encoded}", json=body)

    @staticmethod
    def _summary(job: dict) -> dict:
        p = job.get("progress") or {}
        return {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at") or _utc_now(),
            "finished_at": job.get("finished_at"),
            "days_processed": p.get("days_processed", 0),
            "days_total": p.get("days_total"),
            "last_completed_date": p.get("last_completed_date"),
        }

    def upsert(self, job: dict):
        with self._lock:
            self.require_enabled()
            job_id = str(job["job_id"])
            self._put_file(
                f"jobs/{job_id}.json.gz",
                job,
                f"checkpoint historical backtest {job_id}: {job.get('status', 'unknown')}",
            )
            index, _ = self._get_file("index.json.gz")
            if not isinstance(index, dict):
                index = {"updated_at": _utc_now(), "jobs": []}
            jobs = [x for x in (index.get("jobs") or []) if x.get("job_id") != job_id]
            jobs.append(self._summary(job))
            jobs.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
            index = {"updated_at": _utc_now(), "jobs": jobs[:50]}
            self._put_file("index.json.gz", index, f"update historical backtest checkpoint index: {job_id}")

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            if not self.enabled:
                return None
            value, _ = self._get_file(f"jobs/{job_id}.json.gz")
            return value if isinstance(value, dict) else None

    def latest_checkpoint_before_completion(self, job_id: str) -> dict | None:
        """Recover the most recent durable per-slate checkpoint from Git history.

        Completed v2.6.8 jobs clear checkpoint from the current job document, but
        every slate checkpoint was committed to the dedicated branch. This walks
        commits touching that job file and returns the newest historical version
        that still contains a checkpoint.
        """
        with self._lock:
            self.require_enabled(); self._ensure_branch()
            rel=f"{self.root}/jobs/{job_id}.json.gz"
            with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                resp=client.get(f"{API_ROOT}/repos/{self.repo}/commits", params={"sha":self.branch,"path":rel,"per_page":40})
            if resp.status_code>=400:
                raise GitHubCheckpointError(f"GitHub {resp.status_code}: {resp.text[:1200]}")
            for commit in resp.json():
                sha=commit.get("sha")
                if not sha: continue
                encoded=self._content_path(f"jobs/{job_id}.json.gz")
                with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                    r=client.get(f"{API_ROOT}/repos/{self.repo}/contents/{encoded}",params={"ref":sha})
                if r.status_code!=200: continue
                payload=r.json(); content=(payload.get("content") or "").replace("\n","")
                if not content: continue
                try: value=self._decode(content)
                except Exception: continue
                if isinstance(value,dict) and isinstance(value.get("checkpoint"),dict):
                    return value["checkpoint"]
            return None

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            if not self.enabled:
                return []
            index, _ = self._get_file("index.json.gz")
            if not isinstance(index, dict):
                return []
            summaries = list(index.get("jobs") or [])[: int(limit)]
            out = []
            for item in summaries:
                jid = item.get("job_id")
                if not jid:
                    continue
                try:
                    job = self.get(jid)
                except Exception:
                    job = None
                out.append(job or item)
            return out

    def resumable(self) -> list[dict]:
        return [j for j in self.list_recent(50) if j.get("status") in {"queued", "running"}]
