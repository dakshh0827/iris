"""Tests for app/parsing/pdf.py (Phase 3, Steps 2 and 3), on the real dataset PDFs."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.parsing.pdf import extract_pdf


def regulation(file_name: str):
    return extract_pdf(get_settings().regulations_dir / file_name)


@pytest.mark.parametrize(
    ("file_name", "blocks_per_page"),
    [
        ("Dodd_Frank_2022.pdf", [12, 2]),
        ("EMIR_2021_v1.pdf", [12, 0]),        # page 2 is blank
        ("EMIR_2021_v2.pdf", [12, 13, 5]),
        ("ESMA_TRADE_REPORTING_2022.pdf", [15, 3]),
        ("MiFID_II_2023_v1.pdf", [11, 1]),
        ("MiFID_II_2023_v2.pdf", [11, 3]),
        ("SEC_GUIDANCE_2023_v1.pdf", [11, 3]),
        ("SEC_GUIDANCE_2023_v2.pdf", [11, 5]),
    ],
)
def test_every_supplied_pdf(file_name: str, blocks_per_page: list[int]) -> None:
    document = regulation(file_name)
    assert document.file_type == "pdf"
    assert document.source_name == file_name
    assert document.page_count == len(blocks_per_page)
    assert [len(document.blocks_on_page(n)) for n in range(1, document.page_count + 1)] == blocks_per_page


def test_mifid_v1_first_and_last_blocks() -> None:
    document = regulation("MiFID_II_2023_v1.pdf")
    assert document.blocks[0].text == "Paragraph 1. Scope and Objective"
    assert document.blocks[0].page == 1
    assert document.blocks[-1].page == 2
    assert document.blocks[-1].text.startswith("Each reporting submission shall undergo")


def test_clause_across_a_page_break_keeps_both_pages() -> None:
    # MiFID II v1: the heading of Paragraph 6 is on page 1, its text on page 2.
    blocks = regulation("MiFID_II_2023_v1.pdf").blocks
    heading = next(b for b in blocks if b.text.startswith("Paragraph 6."))
    following = blocks[heading.order + 1]
    assert heading.page == 1
    assert following.page == 2


def test_no_empty_or_padded_blocks() -> None:
    for path in get_settings().regulations_dir.glob("*.pdf"):
        for block in extract_pdf(path).blocks:
            assert block.text == block.text.strip() != ""


def test_text_is_kept_as_extracted() -> None:
    # The PDFs use a narrow no-break space (U+202F) in "MiFID II". Cleaning it is Phase 4's job.
    blocks = regulation("MiFID_II_2023_v1.pdf").blocks
    assert any("MiFID\u202fII" in block.text for block in blocks)


def test_emir_v2_has_the_new_paragraph_and_the_appended_dodd_frank_text() -> None:
    # A known dataset problem: EMIR v2 has the Dodd-Frank text pasted after its own.
    # Extraction reports what is in the file; Phase 4 flags the foreign content.
    document = regulation("EMIR_2021_v2.pdf")
    page_2 = [block.text for block in document.blocks_on_page(2)]
    assert page_2[0].startswith("Paragraph 7. New Requirement")
    assert any(text.startswith("Dodd-Frank Act") for text in page_2)