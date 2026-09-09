"""Small in-process job queue used to expose real planning progress."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict
from uuid import uuid4

from planning_models import PlanningError, ProgressCallback


logger = logging.getLogger(__name__)


class JobNotFound(LookupError):
    """Raised when a requested background job is unknown or expired."""


class PlanningJobStore:
    """Run planning callables in a small thread pool and expose their status."""

    def __init__(self, workers: int = 2, retention_seconds: int = 3600):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="route-plan")
        self._retention_seconds = retention_seconds
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        work: Callable[[ProgressCallback], Dict[str, Any]],
        start_message: str = "Rozpoczynam wyznaczanie planu.",
    ) -> str:
        """Queue work and return its opaque job identifier."""

        job_id = uuid4().hex
        with self._lock:
            self._remove_expired()
            self._jobs[job_id] = {
                "status": "queued",
                "progress": 0,
                "message": "Zadanie oczekuje na rozpoczęcie.",
                "result": None,
                "error": None,
                "updated_at": time.monotonic(),
            }
        self._executor.submit(self._run, job_id, work, start_message)
        return job_id

    def get(self, job_id: str) -> Dict[str, Any]:
        """Return a serializable snapshot of current job state."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            return {
                "id": job_id,
                "status": job["status"],
                "progress": job["progress"],
                "message": job["message"],
                "result": job["result"],
                "error": job["error"],
            }

    def _run(
        self,
        job_id: str,
        work: Callable[[ProgressCallback], Dict[str, Any]],
        start_message: str,
    ) -> None:
        self._update(job_id, status="running", progress=1, message=start_message)

        def progress(value: int, message: str) -> None:
            self._update(job_id, progress=value, message=message)

        try:
            result = work(progress)
            self._update(
                job_id,
                status="completed",
                progress=100,
                message="Plan jest gotowy.",
                result=result,
            )
        except PlanningError as exc:
            self._update(
                job_id,
                status="failed",
                message="Nie udało się wyznaczyć planu.",
                error=str(exc),
            )
        except Exception:
            logger.exception("Unexpected planning job error")
            self._update(
                job_id,
                status="failed",
                message="Nie udało się wyznaczyć planu.",
                error="Wystąpił wewnętrzny błąd podczas wyznaczania trasy.",
            )

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if "progress" in values:
                values["progress"] = max(job["progress"], min(100, int(values["progress"])))
            job.update(values)
            job["updated_at"] = time.monotonic()

    def _remove_expired(self) -> None:
        cutoff = time.monotonic() - self._retention_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job["updated_at"] < cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]
