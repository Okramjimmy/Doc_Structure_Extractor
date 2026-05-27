"""
SQLite persistence layer.

Schema:
  jobs      — one row per extraction job
  questions — one row per checklist item (FK → jobs.job_id)

Uses Python's built-in sqlite3 — no extra dependencies.
Connection is opened once and reused (check_same_thread=False for FastAPI threads).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings
from app.schemas import Checklist, JobRecord, JobStatus, Question, ResponseType

logger = logging.getLogger(__name__)

# ── DB path ───────────────────────────────────────────────────────────────────

DB_PATH: Path = settings.output_dir.parent / "jobs.db"


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    error           TEXT,
    document_name   TEXT,
    total_questions INTEGER,
    sections        TEXT       -- JSON array
);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    question_code   TEXT NOT NULL,
    section         TEXT NOT NULL,
    section_path    TEXT NOT NULL,   -- JSON array
    question        TEXT NOT NULL,
    response_type   TEXT NOT NULL,
    field_type      TEXT,
    raw_line        TEXT
);

CREATE INDEX IF NOT EXISTS idx_questions_job ON questions(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created  ON jobs(created_at DESC);
"""


# ── Connection ────────────────────────────────────────────────────────────────

def _get_connection() -> sqlite3.Connection:
    """Return a module-level singleton connection (thread-safe with WAL mode)."""
    if not hasattr(_get_connection, "_conn") or _get_connection._conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + writes
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_DDL)
        conn.commit()
        # Graceful migration for existing DB
        try:
            conn.execute("ALTER TABLE questions ADD COLUMN field_type TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        _get_connection._conn = conn
        logger.info("SQLite DB opened: %s", DB_PATH.resolve())
    return _get_connection._conn


def get_db() -> sqlite3.Connection:
    return _get_connection()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dt_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _dt_parse(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


# ── Job CRUD ──────────────────────────────────────────────────────────────────


def db_insert_job(record: JobRecord) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO jobs
            (job_id, status, filename, file_path, created_at,
             completed_at, error, document_name, total_questions, sections)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record.job_id,
            record.status.value,
            record.filename,
            record.file_path,
            _dt_str(record.created_at),
            _dt_str(record.completed_at),
            record.error,
            record.checklist.document_name if record.checklist else None,
            record.checklist.total_questions if record.checklist else None,
            json.dumps(record.checklist.sections) if record.checklist else None,
        ),
    )
    db.commit()


def db_update_job(record: JobRecord) -> None:
    """Update all mutable fields of an existing job row."""
    db = get_db()
    db.execute(
        """
        UPDATE jobs SET
            status          = ?,
            completed_at    = ?,
            error           = ?,
            document_name   = ?,
            total_questions = ?,
            sections        = ?
        WHERE job_id = ?
        """,
        (
            record.status.value,
            _dt_str(record.completed_at),
            record.error,
            record.checklist.document_name if record.checklist else None,
            record.checklist.total_questions if record.checklist else None,
            json.dumps(record.checklist.sections) if record.checklist else None,
            record.job_id,
        ),
    )
    db.commit()


def db_save_questions(job_id: str, checklist: Checklist) -> None:
    """Delete old questions for this job and insert fresh ones."""
    db = get_db()
    db.execute("DELETE FROM questions WHERE job_id = ?", (job_id,))
    db.executemany(
        """
        INSERT INTO questions
            (job_id, question_code, section, section_path, question, response_type, field_type, raw_line)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        [
            (
                job_id,
                q.question_code,
                q.section,
                json.dumps(q.section_path),
                q.question,
                q.response_type.value,
                q.field_type,
                q.raw_line,
            )
            for q in checklist.questions
        ],
    )
    db.commit()
    logger.debug("Saved %d questions for job %s", len(checklist.questions), job_id)


def db_load_all_jobs() -> list[JobRecord]:
    """Load all job records (without question details) from the DB."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC"
    ).fetchall()
    records = []
    for row in rows:
        record = JobRecord(
            job_id=row["job_id"],
            status=JobStatus(row["status"]),
            filename=row["filename"],
            file_path=row["file_path"],
            created_at=_dt_parse(row["created_at"]),
            completed_at=_dt_parse(row["completed_at"]),
            error=row["error"],
        )
        # Attach a lightweight checklist stub (no questions — loaded on demand)
        if row["document_name"]:
            record.checklist = Checklist(
                document_name=row["document_name"],
                total_questions=row["total_questions"] or 0,
                sections=json.loads(row["sections"] or "[]"),
                questions=[],  # questions loaded separately via db_load_checklist()
            )
        records.append(record)
    logger.info("Loaded %d jobs from SQLite", len(records))
    return records


def db_load_checklist(job_id: str) -> Optional[Checklist]:
    """Load the full checklist (with all questions) for a job."""
    db = get_db()

    job_row = db.execute(
        "SELECT document_name, total_questions, sections FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()

    if not job_row or not job_row["document_name"]:
        return None

    q_rows = db.execute(
        "SELECT * FROM questions WHERE job_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()

    questions = [
        Question(
            question_code=r["question_code"],
            section=r["section"],
            section_path=json.loads(r["section_path"]),
            question=r["question"],
            response_type=ResponseType(r["response_type"]),
            field_type=r["field_type"] if "field_type" in r.keys() and r["field_type"] is not None else "Long Text",
            raw_line=r["raw_line"] or "",
        )
        for r in q_rows
    ]

    return Checklist(
        document_name=job_row["document_name"],
        total_questions=job_row["total_questions"] or len(questions),
        sections=json.loads(job_row["sections"] or "[]"),
        questions=questions,
    )


def db_delete_job(job_id: str) -> None:
    db = get_db()
    db.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    db.commit()
