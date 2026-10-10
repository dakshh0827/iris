"""Tests for extract() in app/parsing/base.py (Phase 3, Steps 10 and 11).

Checks the registry, all 10 supplied files through the one entry point, and
the "bad input" cases: an unknown type, a missing file and empty files.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import CorruptDocument, UnsupportedFileType
from app.parsing.base import EXTRACTORS, describe_location, extract
from app.parsing.html import extract_html
from app.parsing.pdf import extract_pdf
from app.parsing.txt import extract_txt

REGULATIONS = get_settings().regulations_dir

# file name: (type, pages, blocks, warnings). Pages is None for HTML and TXT.
EXPECTED = {
    "CFTC_2022.txt": ("txt", None, 11, 0),
    "Dodd_Frank_2022.pdf": ("pdf", 2, 14, 0),
    "EMIR_2021_v1.pdf": ("pdf", 2, 12, 1),  # the blank page 2
    "EMIR_2021_v2.pdf": ("pdf", 3, 30, 0),  # includes the appended Dodd-Frank text (Phase 4 flags it)
    "ESMA_TRADE_REPORTING_2022.pdf": ("pdf", 2, 18, 0),
    "FCA_Guidance_2023.html": ("html", None, 19, 0),
    "MiFID_II_2023_v1.pdf": ("pdf", 2, 12, 0),
    "MiFID_II_2023_v2.pdf": ("pdf", 2, 14, 0),
    "SEC_GUIDANCE_2023_v1.pdf": ("pdf", 2, 14, 0),
    "SEC_GUIDANCE_2023_v2.pdf": ("pdf", 2, 16, 0),
}


def test_registry_has_one_extractor_per_type() -> None:
    assert EXTRACTORS == {"pdf": extract_pdf, "html": extract_html, "txt": extract_txt}


def test_the_dataset_has_exactly_these_ten_files() -> None:
    assert sorted(path.name for path in REGULATIONS.iterdir()) == sorted(EXPECTED)


@pytest.mark.parametrize("file_name", sorted(EXPECTED))
def test_every_supplied_file(file_name: str) -> None:
    file_type, pages, block_count, warning_count = EXPECTED[file_name]
    document = extract(REGULATIONS / file_name, file_type)
    assert document.source_name == file_name
    assert document.file_type == file_type
    assert document.page_count == pages
    assert len(document.blocks) == block_count
    assert len(document.warnings) == warning_count
    for block in document.blocks:
        if file_type == "pdf":
            assert block.page is not None
        elif file_type == "html":
            assert block.anchor is not None or block.kind == "heading"
        else:
            assert block.line_start is not None


def test_describe_location() -> None:
    pdf = extract(REGULATIONS / "MiFID_II_2023_v1.pdf", "pdf")
    html = extract(REGULATIONS / "FCA_Guidance_2023.html", "html")
    txt = extract(REGULATIONS / "CFTC_2022.txt", "txt")
    assert describe_location(pdf, 11) == "page 2"
    assert describe_location(html, 9) == "#p4"
    assert describe_location(html, 0) == "heading"
    assert describe_location(txt, 1) == "lines 3-4"


def test_extract_logs_one_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.parsing.base"):
        extract(REGULATIONS / "CFTC_2022.txt", "txt")
    assert caplog.messages == ["CFTC_2022.txt: extracted 11 blocks as txt, 0 warnings"]


# --- Bad input ---------------------------------------------------------------

def test_unknown_type_is_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(UnsupportedFileType) as caught:
        extract(path, "docx")  # type: ignore[arg-type]  # on purpose: not a FileType
    assert caught.value.details == {"file_name": "report.docx", "file_type": "docx"}


@pytest.mark.parametrize("file_type", ["pdf", "html", "txt"])
def test_missing_file_is_file_not_found_for_every_type(tmp_path: Path, file_type: str) -> None:
    with pytest.raises(FileNotFoundError):
        extract(tmp_path / f"missing.{file_type}", file_type)  # type: ignore[arg-type]


def test_empty_pdf_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(CorruptDocument):
        extract(path, "pdf")


@pytest.mark.parametrize(
    ("file_name", "data", "warning"),
    [
        ("empty.html", b"<html><body>  </body></html>", "No text found in the HTML."),
        ("empty.txt", b"\r\n\r\n", "No text found in the file."),
    ],
    ids=["html", "txt"],
)
def test_empty_html_and_txt_give_a_warning(tmp_path: Path, file_name: str, data: bytes, warning: str) -> None:
    path = tmp_path / file_name
    path.write_bytes(data)
    document = extract(path, file_name.rsplit(".", 1)[1])  # type: ignore[arg-type]
    assert document.blocks == []
    assert document.warnings == [warning]