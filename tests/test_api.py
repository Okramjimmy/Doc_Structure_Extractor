"""
API integration tests using FastAPI TestClient.
Run with:  pytest tests/ -v
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ── Health ────────────────────────────────────────────────────────────────────


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


# ── Upload validation ─────────────────────────────────────────────────────────


def test_upload_unsupported_type():
    """Should reject .exe files with 415."""
    fake_file = io.BytesIO(b"MZ\x90\x00")
    r = client.post(
        "/api/v1/extract",
        files={"file": ("malware.exe", fake_file, "application/octet-stream")},
    )
    assert r.status_code == 415


def test_upload_too_large(monkeypatch):
    """Should reject files exceeding the size limit."""
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "max_upload_size_mb", 0)
    # Re-import router so the cap is re-evaluated — simplest approach is to
    # patch the private constant directly in the router module.
    import app.routers.extract as ext_router
    monkeypatch.setattr(ext_router, "_MAX_BYTES", 0)

    fake_file = io.BytesIO(b"some content")
    r = client.post(
        "/api/v1/extract",
        files={"file": ("test.txt", fake_file, "text/plain")},
    )
    assert r.status_code == 413


# ── Job lifecycle ─────────────────────────────────────────────────────────────


def test_job_not_found():
    r = client.get("/api/v1/jobs/nonexistent-id")
    assert r.status_code == 404


def test_job_list():
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_export_not_found():
    r = client.get("/api/v1/export/nonexistent-id?format=json")
    assert r.status_code == 404


def test_export_invalid_format():
    r = client.get("/api/v1/export/some-id?format=xml")
    assert r.status_code == 422  # FastAPI enum validation


# ── Semantic Extraction Endpoint ──────────────────────────────────────────────


def test_upload_semantic_unsupported_type():
    """Should reject .exe files with 415 on semantic extraction endpoint."""
    fake_file = io.BytesIO(b"MZ\x90\x00")
    r = client.post(
        "/api/v1/extract-semantic",
        files={"file": ("malware.exe", fake_file, "application/octet-stream")},
    )
    assert r.status_code == 415

