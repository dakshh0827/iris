"""Tests for app/parsing/txt.py (Phase 3, Steps 8 and 9).

The first tests use the real CFTC_2022.txt. The others write tiny text files
(as bytes, so we control the encoding and line endings exactly) into
pytest's temporary folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.parsing.models import ExtractedDocument
from app.parsing.txt import decode_text, extract_txt, split_lines

CFTC = get_settings().regulations_dir / "CFTC_2022.txt"
CFTC_TITLE = "CFTC (2022) \u2013 Commodity Futures Reporting FAQ"


def txt_file(tmp_path: Path, data: bytes) -> ExtractedDocument:
    """Write bytes to a .txt file and extract it."""
    path = tmp_path / "notes.txt"
    path.write_bytes(data)
    return extract_txt(path)


# --- The real CFTC file ------------------------------------------------------

def test_cftc_title_and_ten_question_answer_pairs() -> None:
    document = extract_txt(CFTC)
    assert document.file_type == "txt"
    assert document.page_count is None  # text files have no pages
    assert document.warnings == []
    assert len(document.blocks) == 11
    title = document.blocks[0]
    assert (title.kind, title.text, title.line_start, title.line_end) == ("heading", CFTC_TITLE, 1, 2)
    pairs = document.blocks[1:]
    assert [(block.line_start, block.line_end) for block in pairs] == [
        (3, 4), (6, 7), (9, 10), (12, 13), (15, 16), (18, 19), (21, 22), (24, 25), (27, 28), (30, 31),
    ]
    assert [block.text.split(":")[0] for block in pairs] == [f"Q{n}" for n in range(1, 11)]
    assert {block.heading for block in pairs} == {CFTC_TITLE}


def test_cftc_q1_text_has_no_carriage_returns_and_keeps_special_characters() -> None:
    q1 = extract_txt(CFTC).blocks[1]
    assert q1.text == (
        "Q1: What is the official name of the reporting system?\n"
        "A1: The system is called the Commodity Futures Trading Commission Trade Reporting System (CFTC\u2011TRS)."
    )
    assert "\r" not in extract_txt(CFTC).text


# --- Encodings ---------------------------------------------------------------

def test_utf8() -> None:
    assert decode_text("caf\u00e9".encode("utf-8")) == ("caf\u00e9", "utf-8", [])


def test_utf8_with_bom_drops_the_bom() -> None:
    text, encoding, warnings = decode_text(b"\xef\xbb\xbfcaf\xc3\xa9")
    assert (text, encoding, warnings) == ("caf\u00e9", "utf-8-sig", [])


def test_bom_is_dropped_even_when_the_rest_is_not_utf8() -> None:
    text, encoding, _warnings = decode_text(b"\xef\xbb\xbfcaf\xe9")
    assert (text, encoding) == ("caf\u00e9", "cp1252")


def test_windows_1252_fallback_warns() -> None:
    text, encoding, warnings = decode_text(b"caf\xe9 \x93quoted\x94")  # e-acute and curly quotes in Windows-1252
    assert text == "caf\u00e9 \u201cquoted\u201d"
    assert encoding == "cp1252"
    assert warnings == ["The file is not valid UTF-8; it was read as Windows-1252."]


def test_unknown_bytes_are_replaced_with_a_warning() -> None:
    text, encoding, warnings = decode_text(b"bad \x81 byte")  # 0x81 is unused in Windows-1252 too
    assert text == "bad \ufffd byte"
    assert encoding == "cp1252"
    assert warnings[1] == "Some characters could not be decoded and were replaced with U+FFFD."


def test_extract_reports_the_encoding_warning(tmp_path: Path) -> None:
    document = txt_file(tmp_path, b"Price \xa3100\r\n")
    assert document.blocks[0].text == "Price \u00a3100"
    assert document.warnings == ["The file is not valid UTF-8; it was read as Windows-1252."]


# --- Line endings and line numbers -------------------------------------------

@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"], ids=["LF", "CRLF", "CR"])
def test_every_line_ending_gives_the_same_blocks(tmp_path: Path, ending: bytes) -> None:
    data = ending.join([b"Q1: one?", b"A1: yes.", b"", b"Q2: two?", b"A2: no.", b""])
    document = txt_file(tmp_path, data)
    assert [(block.text, block.line_start, block.line_end) for block in document.blocks] == [
        ("Q1: one?\nA1: yes.", 1, 2),
        ("Q2: two?\nA2: no.", 4, 5),
    ]


def test_split_lines_only_breaks_at_real_line_endings() -> None:
    assert split_lines("a\r\nb\rc\nd") == ["a", "b", "c", "d"]
    assert split_lines("a\u2028b\x0cc") == ["a\u2028b\x0cc"]  # str.splitlines() would give 3 lines


def test_blank_lines_are_counted(tmp_path: Path) -> None:
    document = txt_file(tmp_path, b"\n\nfirst\nsecond\n\n\n   \nthird\n")
    assert [(block.line_start, block.line_end) for block in document.blocks] == [(3, 4), (8, 8)]


# --- Headings ----------------------------------------------------------------

def test_underlined_title_becomes_the_heading(tmp_path: Path) -> None:
    document = txt_file(tmp_path, b"Intro line\nPart A\n======\nBody of part A\n")
    assert [(block.kind, block.text, block.heading, block.line_start, block.line_end)
            for block in document.blocks] == [
        ("paragraph", "Intro line", None, 1, 1),
        ("heading", "Part A", "Part A", 2, 3),
        ("paragraph", "Body of part A", "Part A", 4, 4),
    ]


def test_dashes_without_a_line_above_are_just_text(tmp_path: Path) -> None:
    document = txt_file(tmp_path, b"Text\n\n-----\nMore\n")
    assert [(block.kind, block.text) for block in document.blocks] == [
        ("paragraph", "Text"),
        ("paragraph", "-----\nMore"),
    ]


def test_empty_file_gives_a_warning(tmp_path: Path) -> None:
    document = txt_file(tmp_path, b" \r\n\r\n\t\r\n")
    assert document.blocks == []
    assert document.warnings == ["No text found in the file."]