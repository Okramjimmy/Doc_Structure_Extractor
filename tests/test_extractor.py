"""
Unit tests for the extraction engine.
Run with:  pytest tests/ -v
"""

import textwrap

import pytest

from app.extractor import (
    _clean_line,
    _infer_response_type,
    _is_heading_allcaps,
    _is_heading_atx,
    _is_question,
)
from app.schemas import ResponseType


# ── Heading detection ─────────────────────────────────────────────────────────


class TestATXHeadings:
    def test_h1(self):
        assert _is_heading_atx("# Introduction") == (1, "Introduction")

    def test_h2(self):
        assert _is_heading_atx("## Section 1.1") == (2, "Section 1.1")

    def test_h3_with_bold(self):
        assert _is_heading_atx("### **Risk Assessment**") == (3, "**Risk Assessment**")

    def test_not_heading(self):
        assert _is_heading_atx("This is a normal sentence.") is None

    def test_empty(self):
        assert _is_heading_atx("") is None


class TestAllCapsHeadings:
    def test_all_caps(self):
        assert _is_heading_allcaps("GENERAL REQUIREMENTS") is True

    def test_mixed_case_not_heading(self):
        assert _is_heading_allcaps("General Requirements") is False

    def test_too_short(self):
        assert _is_heading_allcaps("AI") is False

    def test_with_digits(self):
        assert _is_heading_allcaps("SECTION 2 REQUIREMENTS") is True


# ── Question detection ────────────────────────────────────────────────────────

_KEYWORDS = [
    "does", "is there", "has the", "have the", "are there",
    "can the", "will the", "should", "verify", "confirm",
]


class TestIsQuestion:
    def test_ends_with_question_mark(self):
        assert _is_question("Has the system been tested?", _KEYWORDS) is True

    def test_checkbox_unicode(self):
        assert _is_question("☐ Verify backup procedures", _KEYWORDS) is True

    def test_markdown_checkbox(self):
        assert _is_question("- [ ] Confirm audit trail enabled", _KEYWORDS) is True

    def test_q_code_prefix(self):
        assert _is_question("Q01. Does the policy cover all staff?", _KEYWORDS) is True

    def test_starts_with_keyword(self):
        assert _is_question("Does the system log all transactions?", _KEYWORDS) is True

    def test_short_line_skipped(self):
        assert _is_question("Yes", _KEYWORDS) is False

    def test_plain_statement(self):
        assert _is_question("The system is operational.", _KEYWORDS) is False


# ── Response type inference ───────────────────────────────────────────────────


class TestInferResponseType:
    def test_numeric_how_many(self):
        assert _infer_response_type("How many users are registered?") == ResponseType.numeric

    def test_numeric_percent(self):
        assert _infer_response_type("What % of tests passed?") == ResponseType.numeric

    def test_text_describe(self):
        assert _infer_response_type("Describe the backup process.") == ResponseType.text

    def test_default_yes_no_na(self):
        assert _infer_response_type("Is there a firewall in place?") == ResponseType.yes_no_na


# ── Line cleaning ─────────────────────────────────────────────────────────────


class TestCleanLine:
    def test_strips_checkbox(self):
        assert _clean_line("☐ Verify controls") == "Verify controls"

    def test_strips_markdown_checkbox(self):
        assert _clean_line("- [ ] Confirm audit") == "Confirm audit"

    def test_strips_bold(self):
        assert _clean_line("**Important** question?") == "Important question?"

    def test_strips_italic(self):
        assert _clean_line("_Note_ this item") == "Note this item"
