# Doc Structure Extractor

A production-quality FastAPI service that converts compliance / audit documents
(PDF, DOCX, PPTX, XLSX, TXT) into structured checklists and exports them as
**JSON**, **Markdown**, **CSV**, or **styled Excel**. Jobs are persisted to **SQLite**
so results survive server restarts.

---

## Features

| Feature | Detail |
|---------|--------|
| 📄 Multi-format input | PDF, DOCX, PPTX, XLSX, TXT via [Docling](https://github.com/DS4SD/docling) |
| 🔍 Smart extraction | Table-aware + plain-text: ATX headings, ALL-CAPS, `?` endings, checkbox chars, Q-codes, keywords |
| 🗂 Section hierarchy | Full breadcrumb path (`Chapter > Section > Sub-section`) |
| 🔁 Response type inference | `yes_no_na` / `yes_no` / `text` / `numeric` — auto-detected per item |
| ⚡ Async job queue | Upload returns instantly with a `job_id`; poll for completion |
| 💾 SQLite persistence | Jobs + questions stored in `jobs.db` — survive server restarts |
| 📊 Multi-format export | JSON · Markdown · CSV (BOM UTF-8) · Excel (styled, dropdowns, summary sheet) |
| 💾 Auto-save | JSON + Markdown auto-saved to `output/<job_id>/` on extraction completion |
| ✅ Validation | File type, file size (configurable), job state guards |
| 🧪 Tests | `pytest` unit + integration suite |
| ⚙️ Config | `.env` via `pydantic-settings` |

---

## Quick Start

```bash
# 1 — Enter directory
cd /Users/okrammeitei/Projects/doc_structure

# 2 — Activate virtual environment
source venv/bin/activate

# 3 — Install dependencies (first time only)
pip install -r requirements.txt

# 4 — Copy environment config (optional — defaults work fine)
cp .env.example .env

# 5 — Start the server (exclude dynamic files from auto-reloading)
uvicorn app.main:app --reload --reload-exclude "uploads" --reload-exclude "output" --reload-exclude "jobs.db*" --host 0.0.0.0 --port 8000
```

**Interactive API docs:** http://localhost:8000/docs  
**ReDoc docs:** http://localhost:8000/redoc  
**Health check:** http://localhost:8000/health

---

## API Endpoints

### System

#### `GET /health`
Returns server status and total job count.

```bash
curl http://localhost:8000/health
```

**Response `200`:**
```json
{
  "status": "ok",
  "version": "2.0.0",
  "total_jobs": 5
}
```

---

### Extraction

#### `POST /api/v1/extract`
Upload a document and start async extraction. Returns immediately with a `job_id`.

**Supported formats:** `.pdf` `.docx` `.pptx` `.xlsx` `.txt`  
**Max size:** 50 MB (configurable)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -F "file=@Quality_checklist.docx"
```

**Response `202 Accepted`:**
```json
{
  "job_id": "ffd2d645-8946-48f7-b24f-85590aa6663c",
  "status": "pending",
  "filename": "Quality checklist_civil works.docx",
  "created_at": "2026-05-27T09:24:00.388327"
}
```

**Error responses:**

| Code | Reason |
|------|--------|
| `415` | Unsupported file type |
| `413` | File exceeds size limit |

---

#### `POST /api/v1/extract-semantic`
Upload a document and start async **semantic extraction** using **Sentence Transformers (Few-Shot Semantic Search)**. Returns immediately with a `job_id`.

**Supported formats:** `.pdf` `.docx` `.pptx` `.xlsx` `.txt`  
**Max size:** 50 MB (configurable)

```bash
curl -X POST http://localhost:8000/api/v1/extract-semantic \
  -F "file=@Quality_checklist.docx"
```

**Response `202 Accepted`:**
```json
{
  "job_id": "8c2579df-ea93-4a11-bba6-0158ea9c84e1",
  "status": "pending",
  "filename": "Quality checklist_civil works.docx",
  "created_at": "2026-05-27T16:22:00.388327"
}
```

---

### Jobs

#### `GET /api/v1/jobs`
List all jobs, newest first. Supports pagination.

```bash
# Default (50 results)
curl http://localhost:8000/api/v1/jobs

# With pagination
curl "http://localhost:8000/api/v1/jobs?limit=10&offset=0"
```

**Query parameters:**

| Param | Default | Description |
|-------|---------|-------------|
| `limit` | `50` | Max results (1–200) |
| `offset` | `0` | Skip this many results |

**Response `200`:**
```json
[
  {
    "job_id": "ffd2d645-8946-48f7-b24f-85590aa6663c",
    "status": "completed",
    "filename": "Quality checklist_civil works.docx",
    "created_at": "2026-05-27T09:24:00.388327",
    "completed_at": "2026-05-27T09:24:01.525016",
    "error": null,
    "total_questions": 238
  }
]
```

**Job statuses:** `pending` → `processing` → `completed` | `failed`

---

#### `GET /api/v1/jobs/{job_id}`
Poll the status of a single job.

```bash
curl http://localhost:8000/api/v1/jobs/ffd2d645-8946-48f7-b24f-85590aa6663c
```

**Response `200`:** *(same shape as one item from the list above)*

**Error responses:**

| Code | Reason |
|------|--------|
| `404` | Job not found |

---

#### `GET /api/v1/jobs/{job_id}/result`
Get the full checklist result. Supports **4 output formats** via the `?format=` parameter.

| `?format=` | Returns | Use case |
|------------|---------|----------|
| `json` *(default)* | Inline JSON body | View in browser / API client / Swagger |
| `md` | Markdown file download | Documentation, GitHub, Notion |
| `csv` | CSV file download | Spreadsheet apps |
| `xlsx` | Excel workbook download | Styled with dropdowns + summary sheet |

```bash
# View structured JSON (default)
curl http://localhost:8000/api/v1/jobs/<job_id>/result

# Download as Markdown
curl -o checklist.md \
  "http://localhost:8000/api/v1/jobs/<job_id>/result?format=md"

# Download as CSV
curl -o checklist.csv \
  "http://localhost:8000/api/v1/jobs/<job_id>/result?format=csv"

# Download as Excel
curl -o checklist.xlsx \
  "http://localhost:8000/api/v1/jobs/<job_id>/result?format=xlsx"
```

**JSON Response `200`:**
```json
{
  "job_id": "ffd2d645-8946-48f7-b24f-85590aa6663c",
  "status": "completed",
  "checklist": {
    "document_name": "Quality checklist_civil works.docx",
    "total_questions": 238,
    "sections": ["PRE-CONSTRUCTION STAGE", "CEMENT", "..."],
    "extracted_at": "2026-05-27T09:24:01.525016",
    "questions": [
      {
        "question_code": "Q0001",
        "section": "PRE-CONSTRUCTION STAGE",
        "section_path": ["CONCRETE ROAD- QUALITY CHECKLISTS", "PRE-CONSTRUCTION STAGE"],
        "question": "Approved GFC (Good for Construction) drawings available?",
        "response_type": "yes_no_na",
        "raw_line": "| 1 | Approved GFC ..."
      }
    ]
  },
  "error": null
}
```

**Error responses:**

| Code | Reason |
|------|--------|
| `202` | Job still processing — retry shortly |
| `404` | Job not found |
| `422` | Extraction failed — see `error` field |

---

#### `DELETE /api/v1/jobs/{job_id}`
Remove a job from memory and the SQLite database.

```bash
curl -X DELETE http://localhost:8000/api/v1/jobs/<job_id>
```

**Response:** `204 No Content`

**Error responses:**

| Code | Reason |
|------|--------|
| `404` | Job not found |
| `409` | Job is currently processing — wait for it to finish |

---

### Export (standalone download endpoint)

#### `GET /api/v1/export/{job_id}`
Alternative download endpoint. Identical format support to `/result` but always triggers a file download.

```bash
# Excel (default)
curl -o checklist.xlsx \
  "http://localhost:8000/api/v1/export/<job_id>"

# Specify format
curl -o checklist.md \
  "http://localhost:8000/api/v1/export/<job_id>?format=md"
```

**Query parameters:**

| Param | Default | Options |
|-------|---------|---------|
| `format` | `xlsx` | `json` · `md` · `csv` · `xlsx` |

---

## Complete Workflow Example

```bash
# 1. Upload document
JOB=$(curl -s -X POST http://localhost:8000/api/v1/extract \
  -F "file=@my_checklist.docx" | python3 -m json.tool)
JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"

# 2. Poll until completed
curl -s "http://localhost:8000/api/v1/jobs/$JOB_ID" | python3 -m json.tool

# 3. View result as JSON
curl -s "http://localhost:8000/api/v1/jobs/$JOB_ID/result" | python3 -m json.tool

# 4. Download all formats
curl -o checklist.md   "http://localhost:8000/api/v1/jobs/$JOB_ID/result?format=md"
curl -o checklist.csv  "http://localhost:8000/api/v1/jobs/$JOB_ID/result?format=csv"
curl -o checklist.xlsx "http://localhost:8000/api/v1/jobs/$JOB_ID/result?format=xlsx"
```

---

## Project Structure

```
doc_structure/
├── app/
│   ├── main.py          # FastAPI app, lifespan, CORS, health check
│   ├── config.py        # pydantic-settings config (.env support)
│   ├── schemas.py       # Pydantic models (Question, Checklist, Job, ExportFormat)
│   ├── extractor.py     # Multi-strategy extraction engine (table-aware)
│   ├── exporter.py      # JSON / Markdown / CSV / Excel exporters
│   ├── jobs.py          # Thread-safe job store (SQLite write-through cache)
│   ├── database.py      # SQLite schema, CRUD, lazy checklist loading
│   └── routers/
│       ├── extract.py   # POST /api/v1/extract
│       ├── jobs.py      # GET/DELETE /api/v1/jobs/...
│       └── export.py    # GET /api/v1/export/...
├── uploads/             # Uploaded files (auto-created)
├── output/              # Exported files (auto-created per job_id)
│   └── <job_id>/
│       ├── filename_checklist.json   ← auto-saved on extraction
│       ├── filename_checklist.md     ← auto-saved on extraction
│       ├── filename_checklist.xlsx   ← saved on download
│       └── filename_checklist.csv    ← saved on download
├── jobs.db              # SQLite database (auto-created at project root)
├── tests/
│   ├── test_extractor.py
│   └── test_api.py
├── .env.example
├── requirements.txt
└── README.md
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Configuration (`.env`)

Copy `.env.example` → `.env` and adjust:

| Key | Default | Description |
|-----|---------|-------------|
| `DEBUG` | `false` | Verbose logging |
| `UPLOAD_DIR` | `uploads/` | Where uploaded files are saved (absolute) |
| `OUTPUT_DIR` | `output/` | Where exported files are saved (absolute) |
| `MAX_UPLOAD_SIZE_MB` | `50` | Max file size in MB |
| `ALLOWED_EXTENSIONS` | `[".pdf",".docx",...]` | Accepted input file types |
| `QUESTION_KEYWORDS` | `["does","is there",...]` | Extra question trigger words |
| `JOB_TTL_SECONDS` | `3600` | How long completed jobs are kept in memory |

---

## Scaling Up

The current setup is **single-instance** (in-memory cache + SQLite).

To scale horizontally:
- Replace `JobStore` in `app/jobs.py` with a **Redis-backed** implementation — the `create / get / update / list_all / delete` interface stays identical.
- Replace SQLite in `app/database.py` with **PostgreSQL** (e.g. via `asyncpg` or `SQLAlchemy`).
- Add a proper **task queue** (Celery, ARQ, or FastAPI background workers with Redis).
