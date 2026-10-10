"""Tests for broken and password-protected PDFs in app/parsing/pdf.py (Phase 3, Step 5).

All the bad PDFs are made by the tests in pytest's temporary folder.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.config import get_settings
from app.errors import CorruptDocument, IngestionError, UploadRejected
from app.parsing.pdf import extract_pdf

MIFID_V1 = get_settings().regulations_dir / "MiFID_II_2023_v1.pdf"


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_corrupt_document_error_type() -> None:
    error = CorruptDocument("Broken.", file_name="x.pdf")
    assert error.code == "corrupt_document"
    assert error.status_code == 422
    assert isinstance(error, IngestionError)
    assert not isinstance(error, UploadRejected)  # the upload was fine; reading it failed


@pytest.mark.parametrize(
    ("file_name", "data"),
    [
        ("garbage.pdf", b"%PDF-1.7\nthis is not really a pdf\n"),  # right signature, nothing behind it
        ("zeros.pdf", b"%PDF-1.7\n" + b"\x00" * 5000),
        ("empty.pdf", b""),
    ],
    ids=["garbage", "zeros", "empty"],  # short names in "pytest -v" instead of 5000 zero bytes
)
def test_unreadable_pdf_raises_corrupt_document(tmp_path: Path, file_name: str, data: bytes) -> None:
    with pytest.raises(CorruptDocument) as caught:
        extract_pdf(write(tmp_path / file_name, data))
    assert str(caught.value) == "The PDF can't be opened: it is damaged or not a real PDF."
    assert caught.value.details == {"file_name": file_name}
    # "raise ... from error" keeps PyMuPDF's original error for the log.
    assert isinstance(caught.value.__cause__, pymupdf.FileDataError)


def test_password_protected_pdf_raises_corrupt_document(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Secret text")
    pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    pdf.close()

    with pytest.raises(CorruptDocument, match="password-protected"):
        extract_pdf(path)


def test_pdf_with_only_an_owner_password_is_read(tmp_path: Path) -> None:
    # An "owner" password only limits printing or editing; anyone can still open and read it.
    path = tmp_path / "owner_only.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Readable text")
    pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", permissions=0)
    pdf.close()

    document = extract_pdf(path)
    assert [block.text for block in document.blocks] == ["Readable text"]
    assert document.warnings == []


def test_truncated_pdf_is_repaired_with_a_warning(tmp_path: Path) -> None:
    # The first third of a real PDF: MuPDF can rebuild enough of it to read the text.
    data = MIFID_V1.read_bytes()
    document = extract_pdf(write(tmp_path / "truncated.pdf", data[: len(data) // 3]))
    assert document.warnings[0] == "The PDF was damaged and has been repaired; some text may be missing."
    assert document.blocks[0].text == "Paragraph 1. Scope and Objective"


def test_supplied_pdfs_are_not_reported_as_repaired() -> None:
    for path in get_settings().regulations_dir.glob("*.pdf"):
        assert not any("repaired" in warning for warning in extract_pdf(path).warnings), path.name