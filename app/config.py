"""
Application configuration loaded from environment variables / .env file.
All values have sensible defaults so the app works out-of-the-box.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of the app/ package
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_title: str = "Doc Structure Extractor"
    app_version: str = "2.0.0"
    debug: bool = False

    # ── Paths ─────────────────────────────────────────────────────────────────
    # Absolute paths so they work regardless of uvicorn's cwd
    upload_dir: Path = _PROJECT_ROOT / "uploads"
    output_dir: Path = _PROJECT_ROOT / "output"

    # ── File validation ───────────────────────────────────────────────────────
    allowed_extensions: list[str] = [".pdf", ".docx", ".pptx", ".xlsx", ".txt"]
    max_upload_size_mb: int = 50

    # ── Extraction tuning ─────────────────────────────────────────────────────
    # Extra keywords: any line containing these (case-insensitive) is a question
    question_keywords: list[str] = [
        "does", "is there", "has the", "have the", "are there",
        "can the", "will the", "should", "verify", "confirm",
    ]

    # ── Job store ─────────────────────────────────────────────────────────────
    # How long (seconds) to keep completed job results in memory
    job_ttl_seconds: int = 3600


settings = Settings()

# Ensure directories exist on import
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
