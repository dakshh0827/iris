"""Tests for Phase 4 segmentation: clause chunks for all 10 files, the EMIR v2
integrity check, the fallback chunker and the Chunk model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.parsing.base import extract
from app.parsing.models import ExtractedDocument, TextBlock
from app.segmentation.clause_splitter import text_hash
from app.segmentation.fallback_chunker import MAX_WORDS, chunk_blocks
from app.segmentation.models import Chunk
from app.segmentation.segmenter import describe, segment

REGULATIONS = get_settings().regulations_dir
TYPES = {".pdf": "pdf", ".html": "html", ".txt": "txt"}

P = "Paragraph"
# file name: the expected clause refs with their locations
EXPECTED = {
    "CFTC_2022.txt": [f"Q{n} lines {3 * n}-{3 * n + 1}" for n in range(1, 11)],
    "Dodd_Frank_2022.pdf": [f"{P} {n} p.1" for n in range(1, 7)] + [f"{P} 7 p.2"],
    "EMIR_2021_v1.pdf": [f"{P} {n} p.1" for n in range(1, 7)],
    "EMIR_2021_v2.pdf": [f"{P} {n} p.1" for n in range(1, 7)] + [f"{P} 7 p.2"]
    + [f"{P} {n} p.2" for n in range(1, 5)] + [f"{P} 5 p.2-3", f"{P} 6 p.3", f"{P} 7 p.3"],
    "ESMA_TRADE_REPORTING_2022.pdf": [f"{P} {n} p.1" for n in range(1, 5)] + [f"{P} 5 p.1-2", f"{P} 6 p.2"],
    "FCA_Guidance_2023.html": [f"Section {n} #p{n}" for n in range(1, 8)],
    "MiFID_II_2023_v1.pdf": [f"{P} {n} p.1" for n in range(1, 6)] + [f"{P} 6 p.1-2"],
    "MiFID_II_2023_v2.pdf": [f"{P} {n} p.1" for n in range(1, 6)] + [f"{P} 6 p.1-2", f"{P} 7 p.2"],
    "SEC_GUIDANCE_2023_v1.pdf": [f"{P} {n} p.1" for n in range(1, 6)] + [f"{P} 6 p.1-2", f"{P} 7 p.2"],
    "SEC_GUIDANCE_2023_v2.pdf": [f"{P} {n} p.1" for n in range(1, 6)] + [f"{P} 6 p.1-2", f"{P} 7 p.2", f"{P} 8 p.2"],
}


def segment_file(name: str):
    return segment(extract(REGULATIONS / name, TYPES[name[name.rindex("."):]]))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_file_gives_the_expected_clauses(name: str) -> None:
    result = segment_file(name)
    assert result.method == "clauses"
    assert [f"{chunk.clause_ref} {describe(chunk)}" for chunk in result.chunks] == EXPECTED[name]
    for chunk in result.chunks:
        assert chunk.text.strip()
        assert chunk.text_hash == text_hash(chunk.text)
        assert "\u202f" not in chunk.text and "`" not in chunk.text


def test_output_is_the_same_on_every_run() -> None:
    first = segment_file("MiFID_II_2023_v2.pdf")
    second = segment_file("MiFID_II_2023_v2.pdf")
    assert first == second


def test_unchanged_clauses_have_the_same_hash_in_both_versions() -> None:
    v1 = {c.clause_ref: c.text_hash for c in segment_file("MiFID_II_2023_v1.pdf").chunks}
    v2 = {c.clause_ref: c.text_hash for c in segment_file("MiFID_II_2023_v2.pdf").chunks}
    assert set(v2) - set(v1) == {"Paragraph 7"}  # the new clause in v2
    assert sum(v1[ref] == v2[ref] for ref in v1) >= 1


def test_emir_v2_flags_the_pasted_dodd_frank_text() -> None:
    result = segment_file("EMIR_2021_v2.pdf")
    own = [c for c in result.chunks if not c.foreign_content]
    foreign = [c for c in result.chunks if c.foreign_content]
    assert [c.clause_ref for c in own] == [f"Paragraph {n}" for n in range(1, 8)]
    assert len(foreign) == 7
    assert {c.embedded_source for c in foreign} == {"Dodd_Frank_2022.pdf"}
    assert "Dodd-Frank Act" not in own[-1].text  # the banner is not glued to EMIR Paragraph 7
    assert own[-1].heading == "New Requirement - Clearing House Identifier (v1.1)"
    assert result.warnings == [
        "Another document is pasted in on page 2: 'Dodd-Frank Act - 2022 (Single version) / "
        "File name: Dodd_Frank_2022.pdf'. Its text is marked as foreign content from Dodd_Frank_2022.pdf."
    ]


def test_emir_v1_and_v2_share_their_first_six_clauses() -> None:
    v1 = segment_file("EMIR_2021_v1.pdf").chunks
    v2 = segment_file("EMIR_2021_v2.pdf").chunks
    assert [c.text_hash for c in v1] == [c.text_hash for c in v2[:6]]


def test_bullets_stay_with_their_lead_in() -> None:
    esma_3 = segment_file("ESMA_TRADE_REPORTING_2022.pdf").chunks[2]
    assert esma_3.text.startswith("Paragraph 3. Validation Rules\n* Price must be \u2265 0.\n")
    assert esma_3.text.count("\n* ") == 4
    fca_4 = segment_file("FCA_Guidance_2023.html").chunks[3]
    assert fca_4.heading == "Timing of Submission"
    assert fca_4.text.count("\n- ") == 3 and fca_4.text.endswith("per breach.")


def test_paragraph_word_inside_a_sentence_does_not_start_a_clause() -> None:
    blocks = [
        TextBlock(order=0, page=1, text="Paragraph 1. Scope"),
        TextBlock(order=1, page=1, text="Paragraph 2. applies, as described in Paragraph 3. below."),
        TextBlock(order=2, page=1, text="See Paragraph 3. for details."),
    ]
    result = segment(ExtractedDocument(source_name="x.pdf", file_type="pdf", page_count=1, blocks=blocks))
    assert [c.clause_ref for c in result.chunks] == ["Paragraph 1", "Paragraph 2"]


def test_text_before_the_first_clause_is_kept_as_preamble() -> None:
    blocks = [
        TextBlock(order=0, page=1, text="This guidance explains the rules."),
        TextBlock(order=1, page=1, text="Paragraph 1. Scope"),
    ]
    result = segment(ExtractedDocument(source_name="x.pdf", file_type="pdf", page_count=1, blocks=blocks))
    assert [c.clause_ref for c in result.chunks] == ["Preamble", "Paragraph 1"]


# --- Fallback chunker ---------------------------------------------------------

def unstructured(sentences: int) -> ExtractedDocument:
    """A document with no clause headings: 10 words per sentence, 5 sentences per block (page)."""
    blocks = []
    for page in range(1, sentences // 5 + 1):
        text = " ".join(f"Sentence {page}.{n} has exactly ten words in it for testing." for n in range(5))
        blocks.append(TextBlock(order=page - 1, page=page, text=text))
    return ExtractedDocument(source_name="notes.pdf", file_type="pdf", page_count=len(blocks), blocks=blocks)


def test_unstructured_text_uses_the_fallback() -> None:
    result = segment(unstructured(100))  # 1000 words
    assert result.method == "fallback"
    assert [c.clause_ref for c in result.chunks][:2] == ["Part 1", "Part 2"]
    for chunk in result.chunks:
        assert len(chunk.text.split()) <= MAX_WORDS


def test_fallback_windows_overlap_and_cover_everything() -> None:
    document = unstructured(100)
    chunks = chunk_blocks(document.blocks)
    for before, after in zip(chunks, chunks[1:]):
        last_sentence = before.text.split(". ")[-1]
        assert last_sentence in after.text  # the end of one window starts the next
    every_sentence = [s for b in document.blocks for s in b.text.split(". ")]
    joined = " ".join(c.text for c in chunks)
    assert all(sentence.rstrip(".") in joined for sentence in every_sentence)
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 20


def test_one_huge_sentence_is_cut_by_words() -> None:
    block = TextBlock(order=0, page=1, text=" ".join(["word"] * 400))
    chunks = chunk_blocks([block])
    assert [len(c.text.split()) for c in chunks] == [180, 180, 40]


def test_empty_document_gives_no_chunks_and_a_warning() -> None:
    result = segment(ExtractedDocument(source_name="blank.txt", file_type="txt", blocks=[]))
    assert result.chunks == [] and result.method == "none"
    assert result.warnings == ["No chunks: the document has no text."]


# --- Chunk model --------------------------------------------------------------

def test_chunk_needs_a_location_and_matching_foreign_fields() -> None:
    good = {"ordinal": 0, "clause_ref": "Q1", "text": "Q1: x", "text_hash": text_hash("Q1: x")}
    Chunk(**good, line_start=3, line_end=4)
    with pytest.raises(ValidationError, match="needs a location"):
        Chunk(**good)
    with pytest.raises(ValidationError, match="go together"):
        Chunk(**good, page_start=1, page_end=1, foreign_content=True)