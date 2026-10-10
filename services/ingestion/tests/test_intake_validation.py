"""Tests for app/intake/validation.py (Step 2: file type detection)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.errors import UnsupportedFileType
from app.intake.validation import SNIFF_SIZE, check_file_type, sniff_file_type

EXPECTED_TYPES = {
    "CFTC_2022.txt": "txt",
    "Dodd_Frank_2022.pdf": "pdf",
    "EMIR_2021_v1.pdf": "pdf",
    "EMIR_2021_v2.pdf": "pdf",
    "ESMA_TRADE_REPORTING_2022.pdf": "pdf",
    "FCA_Guidance_2023.html": "html",
    "MiFID_II_2023_v1.pdf": "pdf",
    "MiFID_II_2023_v2.pdf": "pdf",
    "SEC_GUIDANCE_2023_v1.pdf": "pdf",
    "SEC_GUIDANCE_2023_v2.pdf": "pdf",
}


@pytest.mark.parametrize(("file_name", "expected_type"), sorted(EXPECTED_TYPES.items()))
def test_every_supplied_file_is_accepted(file_name: str, expected_type: str) -> None:
    path = get_settings().regulations_dir / file_name
    with path.open("rb") as file:
        head = file.read(SNIFF_SIZE)
    assert check_file_type(file_name, head) == expected_type


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\r\n", "pdf"),
        (b"<!DOCTYPE html>\n<html>", "html"),
        (b"\n  <html lang='en'>", "html"),           # spaces before the tag are fine
        (b"\xef\xbb\xbf<!doctype html>", "html"),     # UTF-8 byte order mark first
        (b"Plain regulatory text.\r\n", "txt"),
        (b"MZ\x90\x00\x03\x00", None),                 # Windows program
        (b"PK\x03\x04\x14\x00", None),                 # Word / zip file
        (b"\xff\xd8\xff\xe0", None),                    # JPEG image
        (b"caf\xe9 au lait", None),                     # Latin-1 text, not UTF-8
    ],
)
def test_sniff_file_type(head: bytes, expected: str | None) -> None:
    assert sniff_file_type(head) == expected


def test_text_cut_inside_a_character_is_still_text() -> None:
    # "CFTC (2022) " + an en dash (U+2013, 3 bytes: e2 80 93). Cut after the first of them.
    head = "CFTC (2022) \u2013".encode("utf-8")[:13]
    assert head.endswith(b"\xe2")
    assert sniff_file_type(head) == "txt"


def test_extension_is_case_insensitive() -> None:
    assert check_file_type("REPORT.PDF", b"%PDF-1.7") == "pdf"
    assert check_file_type("page.htm", b"<html>") == "html"


@pytest.mark.parametrize(
    ("file_name", "head"),
    [
        ("program.pdf", b"MZ\x90\x00\x03\x00"),   # renamed program
        ("notes.txt", b"%PDF-1.7\r\n"),            # PDF pretending to be text
        ("page.pdf", b"<!DOCTYPE html>"),          # HTML pretending to be PDF
        ("report.docx", b"PK\x03\x04"),            # extension not supported
        ("README", b"plain text"),                 # no extension at all
    ],
)
def test_wrong_or_unsupported_files_are_rejected(file_name: str, head: bytes) -> None:
    with pytest.raises(UnsupportedFileType) as caught:
        check_file_type(file_name, head)
    assert caught.value.status_code == 415
    assert caught.value.details["file_name"] == file_name


def test_mismatch_error_names_the_detected_type() -> None:
    with pytest.raises(UnsupportedFileType) as caught:
        check_file_type("notes.txt", b"%PDF-1.7")
    assert str(caught.value) == "The file ends in '.txt' but its content is PDF."
    assert caught.value.details["detected_type"] == "pdf"