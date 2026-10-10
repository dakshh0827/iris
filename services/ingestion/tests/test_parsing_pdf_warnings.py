"""Tests for blank-page and no-text-layer warnings in app/parsing/pdf.py (Phase 3, Step 4).

The scanned and blank PDFs are made by the tests themselves with PyMuPDF, in
pytest's temporary folder, so we never need real scanned documents.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.config import get_settings
from app.parsing.pdf import extract_pdf


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a small PDF. Each page is "text:<words>", "image" or "blank"."""
    pdf = pymupdf.open()
    for content in pages:
        page = pdf.new_page()  # A4 size
        if content.startswith("text:"):
            page.insert_text((72, 72), content.removeprefix("text:"))
        elif content == "image":
            # A grey 100 x 100 pixel picture, standing in for a scanned page.
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
            pixmap.clear_with(200)
            page.insert_image(pymupdf.Rect(72, 72, 472, 472), pixmap=pixmap)
    pdf.save(path)
    pdf.close()
    return path


def test_emir_v1_blank_second_page_is_reported() -> None:
    document = extract_pdf(get_settings().regulations_dir / "EMIR_2021_v1.pdf")
    assert document.page_count == 2
    assert document.warnings == ["Page 2 has no text (blank page)."]
    assert document.blocks_on_page(2) == []


def test_other_supplied_pdfs_have_no_warnings() -> None:
    for path in get_settings().regulations_dir.glob("*.pdf"):
        if path.name != "EMIR_2021_v1.pdf":
            assert extract_pdf(path).warnings == [], path.name


def test_text_pdf_has_no_warnings(tmp_path: Path) -> None:
    document = extract_pdf(make_pdf(tmp_path / "text.pdf", ["text:Paragraph 1. Hello"]))
    assert [block.text for block in document.blocks] == ["Paragraph 1. Hello"]
    assert document.warnings == []


def test_scanned_pdf_says_ocr_is_needed(tmp_path: Path) -> None:
    document = extract_pdf(make_pdf(tmp_path / "scanned.pdf", ["image", "image"]))
    assert document.blocks == []
    assert document.warnings == [
        "Page 1 has images but no text; it may be scanned (OCR needed).",
        "Page 2 has images but no text; it may be scanned (OCR needed).",
        "No text layer found: this PDF looks scanned. OCR is needed before it can be searched.",
    ]


def test_only_blank_pages(tmp_path: Path) -> None:
    document = extract_pdf(make_pdf(tmp_path / "blank.pdf", ["blank"]))
    assert document.page_count == 1
    assert document.warnings == ["Page 1 has no text (blank page).", "No text found on any page."]


def test_mixed_pdf_warns_only_about_the_scanned_page(tmp_path: Path) -> None:
    document = extract_pdf(make_pdf(tmp_path / "mixed.pdf", ["text:Page one text", "image", "text:Page three text"]))
    assert [block.page for block in document.blocks] == [1, 3]
    assert document.warnings == ["Page 2 has images but no text; it may be scanned (OCR needed)."]