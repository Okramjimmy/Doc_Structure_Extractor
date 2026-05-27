"""
Improved extraction engine — v2.1

Handles THREE document layouts:
  1. Plain-text / paragraph documents  → question detection by pattern
  2. Table-based checklists (most real-world docs) → parse markdown table rows
  3. Mixed documents                   → both passes applied

Table row format produced by Docling:
  |  1 | Description text here? |  |  |
  ↑col0  ↑col1 (description)   ↑col2 ↑col3 (response / remarks — blank)

Strategy:
  Pass A — Heading tracking (ATX headings + ALL-CAPS fallback)
  Pass B — Per-line classification:
     • If line is a markdown table separator  → skip
     • If line is a markdown table data row   → extract description cell
     • Otherwise                              → question detection by pattern
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from docling.document_converter import DocumentConverter

from app.config import settings
from app.schemas import Checklist, Question, ResponseType

logger = logging.getLogger(__name__)

# ── Compiled patterns ─────────────────────────────────────────────────────────

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_Q_CODE      = re.compile(r"^[Qq]\d+[\.\):\s]")
_CHECKBOX    = re.compile(r"^[-*]\s*\[[ xX]\]\s+")
_UNICODE_CB  = re.compile(r"^[☐□✓✗✔●○•]\s*")
_ALL_CAPS_H  = re.compile(r"^[A-Z0-9][A-Z0-9 \-/&:()\[\]]{2,78}$")

# Markdown table patterns
_TABLE_SEP   = re.compile(r"^\|[-| :]+\|$")          # |---|---|
_TABLE_ROW   = re.compile(r"^\|(.+)\|$")              # | cell | cell |

# Header row keywords — rows whose description cell is a header label, not a checklist item
_TABLE_HEADER_CELLS = {"description", "sl. no.", "sl no", "item", "particulars",
                       "parameter", "check point", "s.no", "s. no", "sr. no",
                       "sr no", "no.", "activity"}

# Sl-No column pattern: a cell that is purely a number (the first column in MoRTH tables)
_SL_NO = re.compile(r"^\s*\**\s*\d+\s*\**\s*$")

# Response-type hint column keywords (columns 2+ that indicate a Yes/No table)
_RESPONSE_COL_HINTS = {"yes/no/na", "yes/no", "y/n", "response", "complied"}


# ── Text helpers ──────────────────────────────────────────────────────────────

def _strip_fmt(text: str) -> str:
    """Remove bold/italic markers and HTML entities."""
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _clean_plain(line: str) -> str:
    line = _strip_fmt(line.strip())
    line = _CHECKBOX.sub("", line).strip()
    line = _UNICODE_CB.sub("", line).strip()
    return line


def _parse_table_cells(line: str) -> list[str]:
    """Split a markdown table row into cleaned cells."""
    # Remove leading/trailing pipes then split
    inner = line.strip().strip("|")
    cells = [_strip_fmt(c) for c in inner.split("|")]
    return cells


def _is_heading_atx(line: str) -> tuple[int, str] | None:
    m = _ATX_HEADING.match(line.rstrip())
    if m:
        return len(m.group(1)), m.group(2).strip()
    return None


def _is_heading_allcaps(line: str) -> bool:
    cleaned = re.sub(r"[^A-Z0-9 \-/&:()\[\]]", "", line.strip())
    return bool(_ALL_CAPS_H.match(cleaned)) and len(cleaned) > 3


def _is_question_plain(line: str, keywords: list[str]) -> bool:
    """Multi-strategy question classifier for plain (non-table) lines."""
    stripped = line.strip()
    if not stripped or len(stripped) < 8:
        return False
    if stripped.endswith("?"):
        return True
    if _CHECKBOX.match(stripped) or _UNICODE_CB.match(stripped):
        return True
    if _Q_CODE.match(stripped):
        return True
    lower = stripped.lower()
    for kw in keywords:
        if lower.startswith(kw + " ") or lower.startswith(kw + ","):
            return True
    if len(stripped.split()) >= 8:
        for kw in keywords:
            if f" {kw} " in lower:
                return True
    return False


def _infer_response_type(line: str) -> ResponseType:
    lower = line.lower()
    if any(w in lower for w in ("how many", "number of", "count", "quantity",
                                 "% ", "amount", "value", "≤", "≥", "mm", "mpa")):
        return ResponseType.numeric
    if any(w in lower for w in ("describe", "explain", "provide detail",
                                 "what is", "list ", "specify")):
        return ResponseType.text
    return ResponseType.yes_no_na


# ── Table-mode detector ───────────────────────────────────────────────────────

def _detect_table_description_col(header_cells: list[str]) -> int | None:
    """
    Given the header cells of a table, return the column index most likely
    to hold the checklist item description.

    Returns None if this doesn't look like a checklist table.
    """
    lower_cells = [c.lower() for c in header_cells]

    # Must have at least one response-hint column → confirms this is a checklist
    has_response_col = any(
        any(hint in cell for hint in _RESPONSE_COL_HINTS)
        for cell in lower_cells
    )
    if not has_response_col:
        return None

    # Find the description column — typically the longest text header
    best_col = None
    best_len = 0
    for i, cell in enumerate(lower_cells):
        if cell.strip() in _TABLE_HEADER_CELLS:
            # Skip known-header labels that are *not* the description
            if cell.strip() in {"sl. no.", "sl no", "s.no", "s. no",
                                  "sr. no", "sr no", "no.", "no"}:
                continue
        if len(cell) > best_len:
            best_len = len(cell)
            best_col = i

    return best_col


# ── Public API ────────────────────────────────────────────────────────────────

def extract_checklist(file_path: str | Path) -> Checklist:
    """
    Extract a structured checklist from *file_path*.

    Returns a :class:`Checklist` with all detected questions/items.
    Raises :exc:`ValueError` on unsupported file types or conversion errors.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix not in settings.allowed_extensions:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Allowed: {settings.allowed_extensions}"
        )

    logger.info("Converting document: %s", file_path.name)

    try:
        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        markdown = result.document.export_to_markdown()
    except Exception as exc:
        raise ValueError(f"Docling conversion failed: {exc}") from exc

    logger.debug("Markdown length: %d chars", len(markdown))

    lines = markdown.splitlines()
    keywords = [kw.lower() for kw in settings.question_keywords]

    # ── State ─────────────────────────────────────────────────────────────────
    heading_stack: list[tuple[int, str]] = []
    current_section = "GENERAL"
    questions: list[Question] = []
    counter = 1

    # Table-mode state
    in_table = False
    table_desc_col: int | None = None  # which column holds the description

    def _add_question(text: str, raw: str = "") -> None:
        nonlocal counter
        section_path = [txt for _, txt in heading_stack]
        code = f"Q{counter:04d}"
        questions.append(
            Question(
                question_code=code,
                section=current_section,
                section_path=section_path,
                question=text,
                response_type=_infer_response_type(text),
                raw_line=raw,
            )
        )
        counter += 1

    # ── Main parse loop ───────────────────────────────────────────────────────
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            in_table = False
            table_desc_col = None
            continue

        # ── Heading detection ─────────────────────────────────────────────────
        atx = _is_heading_atx(line)
        if atx:
            in_table = False
            table_desc_col = None
            level, heading_text = atx
            heading_text_clean = _strip_fmt(heading_text).strip("#").strip()
            if not heading_text_clean:
                continue
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, heading_text_clean))
            current_section = heading_text_clean
            continue

        # ALL-CAPS heading fallback (only for non-table lines)
        if not line.startswith("|") and _is_heading_allcaps(line):
            in_table = False
            heading_stack = [(1, line)]
            current_section = line
            continue

        # ── Table separator ───────────────────────────────────────────────────
        if _TABLE_SEP.match(line):
            # Don't change in_table — separator comes after header row
            continue

        # ── Table data row ────────────────────────────────────────────────────
        if _TABLE_ROW.match(line):
            cells = _parse_table_cells(line)

            if not in_table:
                # This is the header row — detect which column is description
                table_desc_col = _detect_table_description_col(cells)
                in_table = True
                logger.debug(
                    "Table detected under '%s', description col=%s, headers=%s",
                    current_section, table_desc_col, cells,
                )
                continue  # header row itself is not a checklist item

            # Data row
            if table_desc_col is None:
                # Not a checklist table — skip
                continue

            if table_desc_col >= len(cells):
                continue

            desc = cells[table_desc_col].strip()

            # Skip header-like repetitions (sometimes tables repeat their header mid-table)
            if desc.lower() in _TABLE_HEADER_CELLS:
                continue

            # Skip serial-number-only cells (e.g. the sl. no. column was mis-detected)
            if _SL_NO.match(desc):
                continue

            # Skip empty or very short descriptions
            if len(desc) < 5:
                continue

            _add_question(desc, raw_line)
            continue

        # Not a table row — reset table mode
        if in_table:
            in_table = False
            table_desc_col = None

        # ── Plain-line question detection ─────────────────────────────────────
        cleaned = _clean_plain(line)
        if cleaned and _is_question_plain(cleaned, keywords):
            _add_question(cleaned, raw_line)

    # ── Finalise ──────────────────────────────────────────────────────────────
    sections = list(dict.fromkeys(q.section for q in questions))

    logger.info(
        "Extraction complete: %d items across %d sections",
        len(questions),
        len(sections),
    )

    return Checklist(
        document_name=file_path.name,
        total_questions=len(questions),
        sections=sections,
        questions=questions,
    )
