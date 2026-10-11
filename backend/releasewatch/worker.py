"""Background worker for post-deployment checks.

Jobs live in the `jobs` table and are claimed with `FOR UPDATE SKIP LOCKED`,
so any number of worker processes can run side by side without a separate
message broker. Network calls happen outside of database transactions.

Run with: python -m releasewatch.worker
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from releasewatch import lifecycle
from releasewatch.config import get_settings
from releasewatch.db import get_sessionmaker
from releasewatch.lifecycle import CheckResult, utcnow
from releasewatch.models import Job, JobStatus, Release

log = logging.getLogger("releasewatch.worker")


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.settings = get_settings()
        self.session_factory = session_factory or get_sessionmaker()
        self.http = http or httpx.Client(
            timeout=self.settings.health_check_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ReleaseWatch/health-check"},
        )
        self._stopping = False

    # ------------------------------------------------------------------ queue

    def claim(self) -> Job | None:
        with self.session_factory() as session, session.begin():
            job = session.scalar(
                select(Job)
                .where(Job.status == JobStatus.QUEUED, Job.run_at <= utcnow())
                .order_by(Job.run_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.locked_at = utcnow()
            job.attempts += 1
            return job

    def finish(self, job_id: int, error: str | None = None) -> None:
        with self.session_factory() as session, session.begin():
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return
            job.locked_at = None
            if error is None:
                job.status = JobStatus.DONE
                job.finished_at = utcnow()
            elif job.attempts >= self.settings.job_max_attempts:
                job.status = JobStatus.FAILED
                job.last_error = error
                job.finished_at = utcnow()
            else:
                job.status = JobStatus.QUEUED
                job.last_error = error
                job.run_at = utcnow() + timedelta(seconds=2**job.attempts)

    def requeue_stale(self) -> int:
        """Return jobs whose worker died mid-run to the queue."""
        cutoff = utcnow() - timedelta(seconds=self.settings.worker_stale_job_seconds)
        with self.session_factory() as session, session.begin():
            result = session.execute(
                update(Job)
                .where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
                .values(status=JobStatus.QUEUED, locked_at=None)
            )
            return result.rowcount or 0

    def run_once(self) -> bool:
        """Process a single due job. Returns False when the queue is empty."""
        job = self.claim()
        if job is None:
            return False
        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise ValueError(f"unknown job kind {job.kind!r}")
            handler(self, job.payload)
        except Exception as exc:
            log.exception("job %s (%s) failed", job.id, job.kind)
            self.finish(job.id, error=f"{type(exc).__name__}: {exc}")
        else:
            self.finish(job.id)
        return True

    def drain(self, max_jobs: int = 1000) -> int:
        """Run due jobs until the queue is empty; used by tests and one-off runs."""
        processed = 0
        while processed < max_jobs and self.run_once():
            processed += 1
        return processed

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        log.info("worker started")
        last_reap = 0.0
        while not self._stopping:
            if time.monotonic() - last_reap > 60:
                if requeued := self.requeue_stale():
                    log.warning("requeued %d stale jobs", requeued)
                last_reap = time.monotonic()
            try:
                busy = self.run_once()
            except Exception:
                log.exception("worker loop error")
                busy = False
            if not busy:
                time.sleep(self.settings.worker_poll_interval_seconds)
        log.info("worker stopped")

    def stop(self) -> None:
        self._stopping = True

    # ------------------------------------------------------------------ handlers

    def probe(self, url: str) -> CheckResult:
        started = time.perf_counter()
        try:
            response = self.http.get(url)
        except httpx.HTTPError as exc:
            return CheckResult(
                passed=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        passed = response.is_success
        return CheckResult(
            passed=passed,
            http_status=response.status_code,
            latency_ms=latency_ms,
            error=None if passed else f"HTTP {response.status_code}",
        )

    def handle_health_check(self, payload: dict[str, Any]) -> None:
        release_id, sequence = payload["release_id"], payload["sequence"]
        with self.session_factory() as session:
            release = session.get(Release, release_id)
            if release is None or not release.health_url:
                return
            url = release.health_url

        result = self.probe(url)

        with self.session_factory() as session, session.begin():
            release = session.get(Release, release_id, with_for_update=True)
            if release is not None:
                lifecycle.record_health_check(session, release, sequence, result)

    def handle_rollback_notify(self, payload: dict[str, Any]) -> None:
        with self.session_factory() as session:
            release = session.get(Release, payload["release_id"])
            if release is None or not release.service.rollback_webhook_url:
                return
            target = release.rollback_target
            hook_url = release.service.rollback_webhook_url
            body = {
                "event": "rollback.requested",
                "service": release.service.name,
                "environment": release.environment,
                "release_id": release.id,
                "version": release.version,
                "target_version": target.version if target else None,
                "target_commit_sha": target.commit_sha if target else None,
                "reason": release.rollback_reason,
            }
        # Raising here lets the queue retry with backoff.
        self.http.post(hook_url, json=body).raise_for_status()


HANDLERS = {
    lifecycle.JOB_HEALTH_CHECK: Worker.handle_health_check,
    lifecycle.JOB_ROLLBACK_NOTIFY: Worker.handle_rollback_notify,
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Worker().run_forever()


if __name__ == "__main__":
    main()
