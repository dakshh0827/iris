"""Tests for app/segmentation/cleaning.py (Phase 4), using the special characters found in the dataset."""

from __future__ import annotations

import pytest

from app.segmentation.cleaning import clean_text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("1\u202fJanuary\u202f2021", "1 January 2021"),             # narrow no-break space (all files)
        ("fine of\u00a0up to", "fine of up to"),                     # no-break space
        ("Dodd\u2011Frank", "Dodd-Frank"),                           # non-breaking hyphen (CFTC, FCA)
        ("Equities \u2013 end of day", "Equities - end of day"),    # en dash
        ("\u201cAmendment=Y\u201d", '"Amendment=Y"'),                # curly double quotes
        ("firm\u2019s report", "firm's report"),                     # curly apostrophe
        ("the field `Clearing_House` is new", "the field Clearing_House is new"),
        ("the \ufb01eld", "the field"),                              # "fi" ligature
        ("a  lot \t of   spaces", "a lot of spaces"),
    ],
)
def test_characters_are_normalised(raw: str, clean: str) -> None:
    assert clean_text(raw) == clean


@pytest.mark.parametrize("kept", ["Price \u2265 0", "\u00a310,000", "Counterparty_LEI", "2023-06-01", "USD 5 million"])
def test_important_characters_are_kept(kept: str) -> None:
    assert clean_text(kept) == kept


def test_wrapped_pdf_lines_are_joined() -> None:
    raw = "must be reported to the ETR within \none business day of execution."
    assert clean_text(raw) == "must be reported to the ETR within one business day of execution."


def test_hyphen_at_line_end_joins_without_space() -> None:
    assert clean_text("a non-\nnegative value") == "a non-negative value"


def test_real_line_breaks_are_kept() -> None:
    raw = "Validation Rules:\n* Price must be positive.\n* Quantity too.\nQ1: Why?\nA1: Because."
    assert clean_text(raw) == raw


def test_empty_and_whitespace_only() -> None:
    assert clean_text("") == ""
    assert clean_text(" \n\u202f\n\t") == ""


def test_cleaning_twice_changes_nothing() -> None:
    raw = "Dodd\u2011Frank \u201cX\u201d within \none `Field_Name` day."
    assert clean_text(clean_text(raw)) == clean_text(raw)