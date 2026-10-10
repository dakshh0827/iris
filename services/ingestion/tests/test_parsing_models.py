"""Tests for app/parsing/models.py (Phase 3, Step 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.parsing.models import ExtractedDocument, TextBlock


def test_pdf_block() -> None:
    block = TextBlock(order=0, kind="heading", page=1, text="Paragraph 1. Scope and Objective")
    assert block.page == 1
    assert block.anchor is None


def test_html_block() -> None:
    block = TextBlock(order=3, kind="list_item", anchor="p4", heading="4. Timing of Submission",
                      text="Derivatives - within 30 minutes of execution.")
    assert block.kind == "list_item"


def test_txt_block() -> None:
    block = TextBlock(order=1, line_start=3, line_end=4, text="Q1: ...\nA1: ...")
    assert (block.line_start, block.line_end) == (3, 4)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"text": "x"}, "a block needs a location"),
        ({"text": "   \n ", "page": 1}, "more than whitespace"),
        ({"text": "x", "page": 0}, "greater than or equal to 1"),
        ({"text": "x", "line_start": 3}, "must be given together"),
        ({"text": "x", "line_start": 5, "line_end": 4}, "must not be before"),
        ({"text": "x", "page": 1, "kind": "table"}, "Input should be 'heading', 'paragraph' or 'list_item'"),
        ({"text": "x", "pages": 1}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_blocks_are_rejected(fields: dict, message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        TextBlock(order=0, **fields)
    assert message in str(caught.value)


def test_blocks_cannot_be_changed() -> None:
    block = TextBlock(order=0, page=1, text="x")
    with pytest.raises(ValidationError):
        block.page = 2


def make_document(**fields) -> ExtractedDocument:
    blocks = [
        TextBlock(order=0, page=1, text="Paragraph 1. Scope"),
        TextBlock(order=1, page=1, text="First text."),
        TextBlock(order=2, page=2, text="Second page text."),
    ]
    return ExtractedDocument(source_name="doc.pdf", file_type="pdf", page_count=2, blocks=blocks, **fields)


def test_document_helpers() -> None:
    document = make_document()
    assert [block.order for block in document.blocks_on_page(1)] == [0, 1]
    assert document.text == "Paragraph 1. Scope\n\nFirst text.\n\nSecond page text."
    assert document.warnings == []


def test_block_order_must_have_no_gaps() -> None:
    with pytest.raises(ValidationError, match="no gaps"):
        ExtractedDocument(source_name="doc.pdf", file_type="pdf", blocks=[
            TextBlock(order=0, page=1, text="a"),
            TextBlock(order=2, page=1, text="b"),
        ])


def test_page_cannot_be_beyond_page_count() -> None:
    with pytest.raises(ValidationError, match="has 1 pages"):
        ExtractedDocument(source_name="doc.pdf", file_type="pdf", page_count=1, blocks=[
            TextBlock(order=0, page=2, text="a"),
        ])


def test_unknown_file_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedDocument(source_name="doc.docx", file_type="docx")


def test_document_with_no_blocks_is_allowed() -> None:
    # A PDF with only blank pages is a valid result; the extractor adds a warning (Step 4).
    document = ExtractedDocument(source_name="blank.pdf", file_type="pdf", page_count=1,
                                 warnings=["Page 1 has no text."])
    assert document.blocks == []