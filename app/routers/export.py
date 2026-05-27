"""
Export endpoint.

GET /api/v1/export/{job_id}?format=json|csv|xlsx&save=true

Returns the checklist as a downloadable file in the requested format.
Also saves a copy to output/<job_id>/ on disk automatically.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.config import settings
from app.exporter import to_csv, to_excel, to_json, to_markdown
from app.jobs import job_store
from app.schemas import ExportFormat, JobStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/export", tags=["export"])

_MEDIA_TYPES = {
    ExportFormat.json: "application/json",
    ExportFormat.csv: "text/csv; charset=utf-8-sig",
    ExportFormat.xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ExportFormat.md: "text/markdown; charset=utf-8",
}

_EXTENSIONS = {
    ExportFormat.json: "json",
    ExportFormat.csv: "csv",
    ExportFormat.xlsx: "xlsx",
    ExportFormat.md: "md",
}


def _save_to_output(job_id: str, filename: str, data: bytes) -> Path:
    """
    Persist *data* to  output/<job_id>/<filename>.
    Returns the full path that was written.
    """
    out_dir = settings.output_dir / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_bytes(data)
    logger.info("Saved output → %s", out_path)
    return out_path


@router.get(
    "/{job_id}",
    summary="Download checklist in JSON, CSV, or Excel format",
    responses={
        200: {"description": "File download"},
        202: {"description": "Job still processing"},
        404: {"description": "Job not found"},
    },
)
def export_checklist(
    job_id: str,
    format: ExportFormat = Query(ExportFormat.xlsx, description="Output format"),
) -> StreamingResponse:
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
            detail=f"Job failed: {record.error}",
        )
    if not record.checklist:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Job completed but checklist data is missing.",
        )

    checklist = record.checklist

    # Build a clean filename (strip the UUID prefix docling added)
    original_stem = record.filename.rsplit(".", 1)[0]
    ext = _EXTENSIONS[format]
    filename = f"{original_stem}_checklist.{ext}"

    # ── Generate export bytes ──────────────────────────────────────────────────────
    if format == ExportFormat.json:
        data = to_json(checklist)
    elif format == ExportFormat.csv:
        data = to_csv(checklist)
    elif format == ExportFormat.md:
        data = to_markdown(checklist)
    else:
        data = to_excel(checklist)

    logger.info("Exporting job %s as %s (%d bytes)", job_id, format.value, len(data))

    # ── Persist to output/ ──────────────────────────────────────────────────────
    saved_path = _save_to_output(job_id, filename, data)
    logger.info("Output saved → %s", saved_path.resolve())

    # ── Stream to client ──────────────────────────────────────────────────────
    return StreamingResponse(
        io.BytesIO(data),
        media_type=_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
