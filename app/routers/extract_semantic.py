"""
POST /api/v1/extract-semantic
  - Similar validation & uploads handling to standard extract endpoint
  - Queues semantic search extraction (Sentence Transformers) in the background
  - Returns job_id immediately
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status

from app.config import settings
from app.jobs import job_store, run_extraction
from app.schemas import JobCreateResponse, JobRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["extraction"])

_MAX_BYTES = settings.max_upload_size_mb * 1024 * 1024


@router.post(
    "/extract-semantic",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document and start async semantic extraction",
    description=(
        "Upload a PDF, DOCX, PPTX, XLSX, or TXT file. "
        "Extraction runs in the background using Sentence Transformers (Few-Shot Semantic Search). "
        "Poll `GET /api/v1/jobs/{job_id}` for status."
    ),
)
async def upload_and_extract_semantic(
    file: UploadFile,
    background_tasks: BackgroundTasks,
) -> JobCreateResponse:
    # ── Validate extension ────────────────────────────────────────────────────
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"File type '{suffix}' is not supported. "
                f"Allowed: {settings.allowed_extensions}"
            ),
        )

    # ── Read & validate size ──────────────────────────────────────────────────
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb} MB.",
        )

    # ── Save to disk ──────────────────────────────────────────────────────────
    save_path = settings.upload_dir / (file.filename or "upload")
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # Avoid collisions: prefix with job_id after creation
    record = JobRecord(filename=file.filename or "upload", file_path="")
    unique_name = f"{record.job_id}_{file.filename}"
    save_path = settings.upload_dir / unique_name
    save_path.write_bytes(content)

    record.file_path = str(save_path)
    job_store.create(record)

    logger.info("Semantic Job %s created for file '%s'", record.job_id, file.filename)

    # ── Queue background semantic extraction ──────────────────────────────────
    background_tasks.add_task(run_extraction, record.job_id, use_semantic=True)

    return JobCreateResponse(
        job_id=record.job_id,
        status=record.status,
        filename=record.filename,
        created_at=record.created_at,
    )
