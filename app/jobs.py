"""
In-memory job store with SQLite write-through persistence.

On startup the store reloads all existing jobs from SQLite so nothing
is lost when the server restarts.

For horizontal scale, swap the dict + SQLite for a Redis-backed store —
the public interface (create / get / update / list_all) stays identical.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings
from app.database import (
    db_delete_job,
    db_insert_job,
    db_load_all_jobs,
    db_load_checklist,
    db_save_questions,
    db_update_job,
)
from app.extractor import extract_checklist
from app.schemas import JobRecord, JobStatus

logger = logging.getLogger(__name__)


class JobStore:
    """
    Thread-safe in-memory job cache backed by SQLite.

    • Reads: served from the in-memory dict (fast).
    • Writes: written to both the dict and SQLite (durable).
    • On init: all jobs are reloaded from SQLite into memory.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._load_from_db()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        """Populate in-memory cache from the SQLite database at startup."""
        try:
            records = db_load_all_jobs()
            with self._lock:
                for rec in records:
                    self._jobs[rec.job_id] = rec
            logger.info("JobStore: reloaded %d jobs from SQLite", len(records))
        except Exception as exc:
            logger.warning("JobStore: could not load from DB on startup: %s", exc)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock:
            self._jobs[record.job_id] = record
        try:
            db_insert_job(record)
        except Exception as exc:
            logger.warning("DB insert failed for job %s: %s", record.job_id, exc)
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_with_questions(self, job_id: str) -> Optional[JobRecord]:
        """
        Return the job record with the full questions list populated.
        The in-memory stub has questions=[] for jobs loaded at startup;
        this method fetches them from SQLite on demand.
        """
        record = self.get(job_id)
        if record is None:
            return None

        # Already has questions loaded (e.g. freshly extracted this session)
        if record.checklist and record.checklist.questions:
            return record

        # Load questions from DB
        if record.checklist and not record.checklist.questions:
            try:
                full = db_load_checklist(job_id)
                if full:
                    record.checklist = full
                    with self._lock:
                        self._jobs[job_id] = record
            except Exception as exc:
                logger.warning("Could not load questions from DB for %s: %s", job_id, exc)

        return record

    def update(self, record: JobRecord) -> None:
        with self._lock:
            self._jobs[record.job_id] = record
        try:
            db_update_job(record)
        except Exception as exc:
            logger.warning("DB update failed for job %s: %s", record.job_id, exc)

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        try:
            db_delete_job(job_id)
        except Exception as exc:
            logger.warning("DB delete failed for job %s: %s", job_id, exc)

    def list_all(self) -> list[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    def purge_expired(self) -> int:
        """Remove jobs older than ``settings.job_ttl_seconds``."""
        cutoff = datetime.utcnow() - timedelta(seconds=settings.job_ttl_seconds)
        removed = 0
        with self._lock:
            to_delete = [
                jid
                for jid, rec in self._jobs.items()
                if rec.created_at < cutoff
            ]
        for jid in to_delete:
            self.delete(jid)
            removed += 1
        if removed:
            logger.info("Purged %d expired jobs", removed)
        return removed


# Singleton store — imported everywhere
job_store = JobStore()


# ── Background worker ─────────────────────────────────────────────────────────


def run_extraction(job_id: str, use_semantic: bool = False) -> None:
    """
    Background task: run extraction then persist everything to SQLite.
    Called by FastAPI ``BackgroundTasks`` after upload returns.
    """
    record = job_store.get(job_id)
    if not record:
        logger.error("Job %s not found in store", job_id)
        return

    logger.info("Starting extraction for job %s (%s) [semantic=%s]", job_id, record.filename, use_semantic)
    record.status = JobStatus.processing
    job_store.update(record)

    try:
        if use_semantic:
            from app.extractor_semantic import extract_checklist_semantic
            checklist = extract_checklist_semantic(record.file_path)
        else:
            checklist = extract_checklist(record.file_path)
        record.checklist = checklist
        record.status = JobStatus.completed
        logger.info(
            "Job %s completed — %d questions extracted",
            job_id,
            checklist.total_questions,
        )

        # ── Persist questions to SQLite ───────────────────────────────────────
        try:
            db_save_questions(job_id, checklist)
            logger.info("Questions persisted to SQLite for job %s", job_id)
        except Exception as db_exc:
            logger.warning("Could not persist questions to DB: %s", db_exc)

        # ── Auto-save JSON + Markdown to output/ ──────────────────────────────
        try:
            from app.exporter import to_json, to_markdown
            out_dir = settings.output_dir / job_id
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = record.filename.rsplit(".", 1)[0]

            json_path = out_dir / f"{stem}_checklist.json"
            json_path.write_bytes(to_json(checklist))
            logger.info("Auto-saved JSON → %s", json_path.resolve())

            md_path = out_dir / f"{stem}_checklist.md"
            md_path.write_bytes(to_markdown(checklist))
            logger.info("Auto-saved Markdown → %s", md_path.resolve())
        except Exception as save_exc:
            logger.warning("Could not auto-save output files: %s", save_exc)

    except Exception as exc:
        record.status = JobStatus.failed
        record.error = str(exc)
        logger.exception("Job %s failed: %s", job_id, exc)
    finally:
        record.completed_at = datetime.utcnow()
        job_store.update(record)
