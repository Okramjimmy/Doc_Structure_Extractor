"""
FastAPI application — entry point.
"""

from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.jobs import job_store
from app.routers import export, extract, jobs

# ── Logging ───────────────────────────────────────────────────────────────────

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            }
        },
        "root": {"level": "DEBUG" if settings.debug else "INFO", "handlers": ["console"]},
    }
)

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀  Doc Structure Extractor v%s starting up", settings.app_version)
    logger.info("   Upload dir : %s", settings.upload_dir.resolve())
    logger.info("   Output dir : %s", settings.output_dir.resolve())
    yield
    # Purge expired jobs on shutdown
    removed = job_store.purge_expired()
    logger.info("🛑  Shutdown — purged %d expired jobs", removed)


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        description=(
            "Upload compliance / audit documents and extract structured checklists "
            "as JSON, CSV, or Excel. Extraction runs asynchronously via a job queue."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(extract.router)
    app.include_router(jobs.router)
    app.include_router(export.router)

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["system"], summary="Health check")
    def health() -> JSONResponse:
        total = len(job_store.list_all())
        return JSONResponse(
            {
                "status": "ok",
                "version": settings.app_version,
                "total_jobs": total,
            }
        )

    return app


app = create_app()
