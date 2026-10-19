import hmac
import hashlib
import time
import uuid
import threading
from typing import Any, Callable
from pydantic import BaseModel, Field
import requests
from app.core.config import settings
from app.core.logging import logger

class Job(BaseModel):
    job_id: str
    status: str = "queued"  # queued, running, succeeded, failed
    progress: int = 0  # 0 to 100
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    result_links: dict[str, str] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    callback_url: str | None = None
    idempotency_key: str | None = None

class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.idempotency_cache: dict[str, str] = {}  # key -> job_id
        self._lock = threading.Lock()

    def get_job_by_idempotency_key(self, key: str) -> Job | None:
        with self._lock:
            job_id = self.idempotency_cache.get(key)
            if job_id and job_id in self.jobs:
                return self.jobs[job_id]
        return None

    def create_job(self, callback_url: str | None = None, idempotency_key: str | None = None) -> Job:
        with self._lock:
            if idempotency_key and idempotency_key in self.idempotency_cache:
                existing_id = self.idempotency_cache[idempotency_key]
                if existing_id in self.jobs:
                    return self.jobs[existing_id]

            job_id = f"job_{uuid.uuid4().hex[:12]}"
            job = Job(job_id=job_id, callback_url=callback_url, idempotency_key=idempotency_key)
            self.jobs[job_id] = job
            if idempotency_key:
                self.idempotency_cache[idempotency_key] = job_id
            return job

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        progress: int | None = None,
        result_links: dict[str, str] | None = None,
        error: dict[str, Any] | None = None
    ) -> Job | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            if status:
                job.status = status
            if progress is not None:
                job.progress = max(0, min(100, progress))
            if result_links:
                job.result_links.update(result_links)
            if error:
                job.error = error
            job.updated_at = time.time()
            return job

    def run_in_background(self, job_id: str, task_fn: Callable[[Job], None]) -> None:
        def worker():
            job = self.jobs.get(job_id)
            if not job:
                return
            self.update_job(job_id, status="running", progress=10)
            try:
                task_fn(job)
                self.update_job(job_id, status="succeeded", progress=100)
            except Exception as e:
                logger.error(f"Background task failed for job {job_id}: {e}", exc_info=True)
                self.update_job(job_id, status="failed", error={"code": "INTERNAL_SERVER_ERROR", "message": str(e), "retryable": True})
            finally:
                # Trigger callback if configured
                self._dispatch_callback(job_id)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _dispatch_callback(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or not job.callback_url:
            return

        payload = job.model_dump_json().encode("utf-8")
        signature = hmac.new(settings.WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-IncuBrix-Signature": signature
        }
        try:
            requests.post(job.callback_url, data=payload, headers=headers, timeout=5)
        except Exception as e:
            logger.warning(f"Failed to post HMAC callback to {job.callback_url}: {e}")

job_manager = JobManager()
