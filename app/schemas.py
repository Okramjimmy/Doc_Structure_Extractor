"""
Pydantic schemas for the API request/response models.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class ResponseType(str, Enum):
    yes_no_na = "yes_no_na"
    yes_no = "yes_no"
    text = "text"
    numeric = "numeric"


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ExportFormat(str, Enum):
    json = "json"
    csv = "csv"
    xlsx = "xlsx"
    md = "md"


# ── Domain models ─────────────────────────────────────────────────────────────


class Question(BaseModel):
    question_code: str = Field(..., examples=["Q0001"])
    section: str = Field(..., examples=["GENERAL"])
    section_path: list[str] = Field(
        default_factory=list,
        description="Ordered list of heading ancestors, e.g. ['Chapter 1', 'Section 1.2']",
    )
    question: str
    response_type: ResponseType = ResponseType.yes_no_na
    raw_line: str = Field("", description="Original line from the document")


class Checklist(BaseModel):
    document_name: str
    total_questions: int
    sections: list[str]
    questions: list[Question]
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


# ── Job models ────────────────────────────────────────────────────────────────


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    filename: str
    created_at: datetime


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    filename: str
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    total_questions: int | None = None


class JobResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    checklist: Checklist | None = None
    error: str | None = None


# ── Internal job record ───────────────────────────────────────────────────────


class JobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.pending
    filename: str
    file_path: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    error: str | None = None
    checklist: Checklist | None = None

    def to_status_response(self) -> JobStatusResponse:
        return JobStatusResponse(
            job_id=self.job_id,
            status=self.status,
            filename=self.filename,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error=self.error,
            total_questions=self.checklist.total_questions if self.checklist else None,
        )
