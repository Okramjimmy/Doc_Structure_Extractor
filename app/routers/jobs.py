"""
Job status & result endpoints.

GET /api/v1/jobs                      — list all jobs (paginated)
GET /api/v1/jobs/{job_id}             — poll job status
GET /api/v1/jobs/{job_id}/result      — full checklist result
  ?format=json   → structured JSON response  (default)
  ?format=md     → Markdown document download
  ?format=csv    → CSV file download
  ?format=xlsx   → Excel file download
DELETE /api/v1/jobs/{job_id}          — remove a job from store + DB
"""

from __future__ import annotations

import io
import logging
from typing import Union

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.exporter import to_csv, to_excel, to_json, to_markdown
from app.jobs import job_store
from app.schemas import ExportFormat, JobResultResponse, JobStatus, JobStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# ── Format metadata ───────────────────────────────────────────────────────────

_MEDIA_TYPES = {
    ExportFormat.json: "application/json",
    ExportFormat.csv:  "text/csv; charset=utf-8-sig",
    ExportFormat.xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ExportFormat.md:   "text/markdown; charset=utf-8",
}

_EXTENSIONS = {
    ExportFormat.json: "json",
    ExportFormat.csv:  "csv",
    ExportFormat.xlsx: "xlsx",
    ExportFormat.md:   "md",
}


# ── List jobs ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[JobStatusResponse],
    summary="List all extraction jobs",
)
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[JobStatusResponse]:
    all_jobs = job_store.list_all()
    all_jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [j.to_status_response() for j in all_jobs[offset : offset + limit]]


# ── Job status ────────────────────────────────────────────────────────────────

@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll job status",
)
def get_job_status(job_id: str) -> JobStatusResponse:
    record = job_store.get(job_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return record.to_status_response()


# ── Result (multi-format) ─────────────────────────────────────────────────────

@router.get(
    "/{job_id}/result",
    summary="Get the full checklist result — view as JSON or download as MD / CSV / Excel",
    responses={
        200: {
            "description": (
                "JSON body (format=json) or file download "
                "(format=md | csv | xlsx)"
            )
        },
        202: {"description": "Job still processing"},
        404: {"description": "Job not found"},
    },
    # We can't declare a single response_model because the return type varies
    response_model=None,
)
def get_job_result(
    job_id: str,
    format: ExportFormat = Query(
        ExportFormat.json,
        description=(
            "Output format:\n"
            "- **json** — structured JSON (view in browser / API client)\n"
            "- **md**   — Markdown document (download)\n"
            "- **csv**  — CSV spreadsheet (download)\n"
            "- **xlsx** — Excel workbook with dropdowns (download)"
        ),
    ),
) -> Union[JSONResponse, StreamingResponse]:
    # ── Load record ───────────────────────────────────────────────────────────
    record = job_store.get_with_questions(job_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    if record.status in (JobStatus.pending, JobStatus.processing):
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail=f"Job is still {record.status.value}. Try again shortly.",
        )
    if record.status == JobStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extraction failed: {record.error}",
        )
    if not record.checklist:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Job completed but checklist data is missing.",
        )

    checklist = record.checklist

    # ── JSON: return as inline response body (no download) ───────────────────
    if format == ExportFormat.json:
        result = JobResultResponse(
            job_id=record.job_id,
            status=record.status,
            checklist=checklist,
            error=record.error,
        )
        return JSONResponse(content=result.model_dump(mode="json"))

    # ── File formats: stream as download ──────────────────────────────────────
    stem = record.filename.rsplit(".", 1)[0]
    ext  = _EXTENSIONS[format]
    filename = f"{stem}_checklist.{ext}"

    if format == ExportFormat.md:
        data = to_markdown(checklist)
    elif format == ExportFormat.csv:
        data = to_csv(checklist)
    else:  # xlsx
        data = to_excel(checklist)

    logger.info(
        "Result export: job=%s format=%s filename=%s size=%d bytes",
        job_id, format.value, filename, len(data),
    )

    return StreamingResponse(
        io.BytesIO(data),
        media_type=_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a job from the store and database",
)
def delete_job(job_id: str) -> None:
    record = job_store.get(job_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    if record.status == JobStatus.processing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a job that is currently processing.",
        )
    job_store.delete(job_id)
