"""
Semantic extraction engine using Sentence Transformers (Few-Shot Semantic Search).
This module uses 'all-MiniLM-L6-v2' to semantically classify document lines 
into checklist items, section headings, or instructions using vectorized few-shot exemplars.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

import torch
from docling.document_converter import DocumentConverter
from sentence_transformers import SentenceTransformer, util

from app.config import settings
from app.schemas import Checklist, Question, ResponseType

logger = logging.getLogger(__name__)

# ── Lazy load Sentence Transformers ───────────────────────────────────────────

_MODEL: SentenceTransformer | None = None
_EXEMPLAR_EMBEDDINGS: Dict[str, torch.Tensor] | None = None

# Few-Shot Exemplars for different line categories
EXEMPLARS = {
    "heading": [
        "WELDING – PROCEDURES, EXECUTION & INSPECTION",
        "PRE-CONSTRUCTION STAGE",
        "CEMENT AND REINFORCEMENT",
        "QUALITY CONTROL STANDARDS",
        "Chapter 3: Electrical Systems",
        "GENERAL REQUIREMENTS",
        "SITE PREPARATION",
        "MATERIALS AND EQUIPMENT",
        "CONCRETE PAVEMENT STAGE",
        "SAFETY GUIDELINES AND COMPLIANCE",
    ],
    "question": [
        "Verify Welding Procedure Specification (WPS) and PQR are approved",
        "Check welder qualification certificates (WQT) per IS 817 / IS 7310",
        "Verify pre-heat temperature before welding",
        "Inspect fit-up and root gap before welding",
        "Is the emergency shutoff switch accessible?",
        "Are all safety guards in place?",
        "Confirm background check is completed before hiring.",
        "Does the audit log capture all events?",
        "Ensure all welds are visually inspected 100%.",
        "Conduct Dye Penetrant Test (DPT/LPT) on fillet welds.",
        "Check that testing tools are calibrated and certified.",
        "Has the concrete cube strength test been completed?",
    ],
    "instruction": [
        "Note: All tests must be conducted in the presence of an authorized representative.",
        "Guideline: Use only approved consumables for steel welding.",
        "This section applies to primary load-bearing members only.",
        "The contractor shall submit reports weekly.",
        "Important: Always disconnect power before servicing.",
        "Refer to drawing number concrete-section-A.",
        "Warning: High voltage area. Wear protective gear.",
        "Disclaimer: Values are indicative and subject to change.",
    ]
}


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        logger.info("Initializing SentenceTransformer('all-MiniLM-L6-v2') on device...")
        # Device fallback (CPU/MPS/CUDA)
        device = "cpu"
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2", device=device)
    return _MODEL


def _get_exemplar_embeddings() -> Dict[str, torch.Tensor]:
    global _EXEMPLAR_EMBEDDINGS
    if _EXEMPLAR_EMBEDDINGS is None:
        model = _get_model()
        logger.info("Vectorizing Few-Shot Exemplars...")
        _EXEMPLAR_EMBEDDINGS = {}
        for category, texts in EXEMPLARS.items():
            # Encoded as [num_exemplars, embedding_dim]
            _EXEMPLAR_EMBEDDINGS[category] = model.encode(
                texts, convert_to_tensor=True, show_progress_bar=False
            )
    return _EXEMPLAR_EMBEDDINGS


# ── Text helpers ──────────────────────────────────────────────────────────────

def _strip_fmt(text: str) -> str:
    """Remove bold/italic markers and HTML entities."""
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _clean_plain(line: str) -> str:
    line = _strip_fmt(line.strip())
    # Strip standard list markers or checkboxes
    line = re.sub(r"^[-*]\s*\[[ xX]\]\s+", "", line).strip()
    line = re.sub(r"^[☐□✓✗✔●○•]\s*", "", line).strip()
    return line


def _infer_response_type(line: str) -> ResponseType:
    lower = line.lower()
    if any(w in lower for w in ("how many", "number of", "count", "quantity",
                                 "% ", "amount", "value", "≤", "≥", "mm", "mpa")):
        return ResponseType.numeric
    if any(w in lower for w in ("describe", "explain", "provide detail",
                                 "what is", "list ", "specify")):
        return ResponseType.text
    return ResponseType.yes_no_na


# ── Semantic Line Classifier ──────────────────────────────────────────────────

def classify_lines_semantic(lines: List[str], threshold: float = 0.42) -> List[Tuple[str, float]]:
    """
    Semantically classify a batch of clean text lines.
    Returns a list of (label, confidence_score) for each line.
    Labels: 'heading', 'question', 'instruction'
    """
    if not lines:
        return []

    model = _get_model()
    exemplars = _get_exemplar_embeddings()

    logger.info("Semantic Classifying %d text lines...", len(lines))
    # Batch encode all lines to get a tensor of shape [num_lines, embedding_dim]
    line_embeddings = model.encode(lines, convert_to_tensor=True, show_progress_bar=False)

    results = []
    for i, line_emb in enumerate(line_embeddings):
        best_cat = "instruction"  # default fallback
        best_score = -1.0

        for category, ex_embs in exemplars.items():
            # ex_embs is [num_exemplars, embedding_dim]
            # util.cos_sim calculates pairwise cosine similarity between line_emb and all exemplars
            similarities = util.cos_sim(line_emb, ex_embs)[0]
            max_score = float(similarities.max().item())

            if max_score > best_score:
                best_score = max_score
                best_cat = category

        # If similarity is too low, treat as instruction/description fallback
        if best_score < threshold:
            best_cat = "instruction"

        results.append((best_cat, best_score))

    return results


# ── Public API ────────────────────────────────────────────────────────────────

def extract_checklist_semantic(file_path: str | Path) -> Checklist:
    """
    Extract structured checklist using Sentence Transformers few-shot semantic classification.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix not in settings.allowed_extensions:
        raise ValueError(
            f"Unsupported file type '{suffix}'. Allowed: {settings.allowed_extensions}"
        )

    logger.info("Semantic Converter: converting document: %s", file_path.name)

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        # Force CPU device for Docling to avoid float64/MPS compatibility issues on macOS
        accelerator_options = AcceleratorOptions(device="cpu")
        pipeline_options = PdfPipelineOptions(accelerator_options=accelerator_options)

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(str(file_path))
        markdown = result.document.export_to_markdown()
    except Exception as exc:
        raise ValueError(f"Docling conversion failed: {exc}") from exc

    raw_lines = markdown.splitlines()

    # ── Table Parsing Pre-Pass (highly structured, same as regex mode) ───────────
    # We still use table row extraction because table structures are extremely precise.
    # But for any plain text lines, we apply Few-Shot Semantic Search!

    # State
    heading_stack: List[Tuple[int, str]] = []
    current_section = "GENERAL"
    questions: List[Question] = []
    counter = 1

    # Track plain lines that need semantic classification, and map their index
    plain_lines_to_classify: List[str] = []
    plain_lines_mapping: List[Tuple[int, str]] = []  # tuple of (original_line_idx, raw_line)

    # ── First Pass: Parse Tables and Collect Plain Lines ──────────────────────────
    in_table = False
    table_desc_col: int | None = None
    _RESPONSE_COL_HINTS = {
        "yes/no/na", "yes/no", "y/n", "response", "complied", "status",
        "✓", "✗", "✔", "✘", "yes", "no", "na", "compliance", "conformance"
    }
    _TABLE_HEADER_CELLS = {
        "description", "sl. no.", "sl no", "item", "particulars",
        "parameter", "check point", "s.no", "s. no", "sr. no",
        "sr no", "no.", "activity"
    }

    def _parse_table_cells(line: str) -> List[str]:
        inner = line.strip().strip("|")
        return [_strip_fmt(c) for c in inner.split("|")]

    # Regex table pattern matches
    _TABLE_SEP = re.compile(r"^\|[-| :]+\|$")
    _TABLE_ROW = re.compile(r"^\|(.+)\|$")
    _ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
    _SL_NO = re.compile(r"^\s*\**\s*\d+\s*\**\s*$")

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
                field_type="Long Text",
                raw_line=raw,
            )
        )
        counter += 1

    for line_idx, raw_line in enumerate(raw_lines):
        line = raw_line.strip()
        if not line:
            in_table = False
            table_desc_col = None
            continue

        # ATX Headings (explicit markdown structure is preserved)
        atx = _ATX_HEADING.match(line.rstrip())
        if atx:
            in_table = False
            table_desc_col = None
            level = len(atx.group(1))
            heading_text = _strip_fmt(atx.group(2)).strip("#").strip()
            if not heading_text:
                continue
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, heading_text))
            current_section = heading_text
            continue

        # Table separators
        if _TABLE_SEP.match(line):
            continue

        # Table rows
        if _TABLE_ROW.match(line):
            cells = _parse_table_cells(line)
            if not in_table:
                # Detect description column based on header cell hints
                lower_cells = [c.lower() for c in cells]
                has_response_col = any(
                    any(hint in cell for hint in _RESPONSE_COL_HINTS)
                    for cell in lower_cells
                )
                if has_response_col:
                    best_col = None
                    best_len = 0
                    for i, cell in enumerate(lower_cells):
                        if cell.strip() in _TABLE_HEADER_CELLS:
                            if cell.strip() in {"sl. no.", "sl no", "s.no", "s. no", "sr. no", "sr no", "no.", "no"}:
                                continue
                        if len(cell) > best_len:
                            best_len = len(cell)
                            best_col = i
                    table_desc_col = best_col
                in_table = True
                continue

            if table_desc_col is not None and table_desc_col < len(cells):
                desc = cells[table_desc_col].strip()
                if desc.lower() not in _TABLE_HEADER_CELLS and not _SL_NO.match(desc) and len(desc) >= 5:
                    _add_question(desc, raw_line)
            continue

        # Reset table mode
        if in_table:
            in_table = False
            table_desc_col = None

        # Clean non-empty plain lines for semantic classification
        cleaned = _clean_plain(line)
        if cleaned and len(cleaned) >= 8:
            plain_lines_to_classify.append(cleaned)
            plain_lines_mapping.append((line_idx, raw_line))

    # ── Second Pass: Batch Semantic Classification of Plain Lines ─────────────────
    if plain_lines_to_classify:
        classifications = classify_lines_semantic(plain_lines_to_classify)
        
        for idx, (label, score) in enumerate(classifications):
            original_idx, raw_line = plain_lines_mapping[idx]
            cleaned_text = plain_lines_to_classify[idx]

            if label == "heading":
                # Semantically classified heading is treated as a new section
                heading_stack = [(1, cleaned_text)]
                current_section = cleaned_text
                logger.info("Semantically detected heading: '%s' (conf: %.2f)", cleaned_text, score)
            elif label == "question":
                # Semantically classified checkpoint is added as a checklist question
                _add_question(cleaned_text, raw_line)
                logger.info("Semantically detected question: '%s' (conf: %.2f)", cleaned_text, score)

    sections = list(dict.fromkeys(q.section for q in questions))
    logger.info(
        "Semantic extraction complete: %d items across %d sections",
        len(questions),
        len(sections),
    )

    return Checklist(
        document_name=file_path.name,
        total_questions=len(questions),
        sections=sections,
        questions=questions,
    )
