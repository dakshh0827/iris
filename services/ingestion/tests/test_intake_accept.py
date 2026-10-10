"""Tests for app/intake/accept.py (Step 8): all Phase 2 checks together."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import EmptyFile, FileTooLarge, InvalidFileName, UnsupportedFileType
from app.intake.accept import accept_upload
from app.intake.hashing import sha256_of_file

EXPECTED = {
    # file name: (type, family key)
    "CFTC_2022.txt": ("txt", "CFTC_2022"),
    "Dodd_Frank_2022.pdf": ("pdf", "Dodd_Frank_2022"),
    "EMIR_2021_v1.pdf": ("pdf", "EMIR_2021"),
    "EMIR_2021_v2.pdf": ("pdf", "EMIR_2021"),
    "ESMA_TRADE_REPORTING_2022.pdf": ("pdf", "ESMA_TRADE_REPORTING_2022"),
    "FCA_Guidance_2023.html": ("html", "FCA_Guidance_2023"),
    "MiFID_II_2023_v1.pdf": ("pdf", "MiFID_II_2023"),
    "MiFID_II_2023_v2.pdf": ("pdf", "MiFID_II_2023"),
    "SEC_GUIDANCE_2023_v1.pdf": ("pdf", "SEC_GUIDANCE_2023"),
    "SEC_GUIDANCE_2023_v2.pdf": ("pdf", "SEC_GUIDANCE_2023"),
}


def stored_files(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*") if path.is_file()]


@pytest.mark.parametrize("file_name", sorted(EXPECTED))
def test_every_supplied_file_is_accepted(file_name: str, tmp_path: Path) -> None:
    source = get_settings().regulations_dir / file_name
    with source.open("rb") as stream:
        result = accept_upload(stream, file_name, uploads_dir=tmp_path)

    assert (result.file_type, result.family_key) == EXPECTED[file_name]
    assert result.original_name == result.safe_name == file_name
    assert result.path == tmp_path / result.family_key / result.version_id / file_name
    assert result.size_bytes == source.stat().st_size
    assert result.sha256 == sha256_of_file(source)
    # The whole file is stored, including the first 8 KB we looked at.
    assert result.path.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    ("file_name", "data", "error_class"),
    [
        ("invoice.pdf", b"MZ\x90\x00\x03\x00\x00\x00" * 10, UnsupportedFileType),  # fake PDF
        ("notes.txt", b"%PDF-1.7\n", UnsupportedFileType),                         # PDF called .txt
        ("report.docx", b"PK\x03\x04" + b"\x00" * 50, UnsupportedFileType),        # unsupported type
        ("notes.txt", b"", EmptyFile),                                              # empty
        ("../..", b"%PDF-1.7\n", InvalidFileName),                                  # unusable name
    ],
)
def test_bad_uploads_are_rejected_and_nothing_is_stored(
    file_name: str, data: bytes, error_class: type[Exception], tmp_path: Path
) -> None:
    with pytest.raises(error_class):
        accept_upload(io.BytesIO(data), file_name, uploads_dir=tmp_path)
    assert stored_files(tmp_path) == []


def test_oversized_upload_is_rejected_and_nothing_is_stored(tmp_path: Path) -> None:
    data = b"Plain text line.\n" * 1000  # 17,000 bytes
    with pytest.raises(FileTooLarge):
        accept_upload(io.BytesIO(data), "big.txt", uploads_dir=tmp_path, max_bytes=10_000)
    assert stored_files(tmp_path) == []


def test_path_traversal_name_is_stored_safely(tmp_path: Path) -> None:
    result = accept_upload(io.BytesIO(b"%PDF-1.7\n"), "../../hack_v2.pdf", uploads_dir=tmp_path)
    assert result.original_name == "../../hack_v2.pdf"
    assert result.safe_name == "hack_v2.pdf"
    assert result.family_key == "hack"
    assert result.path.resolve().is_relative_to(tmp_path.resolve())


def test_family_key_from_the_caller_wins(tmp_path: Path) -> None:
    result = accept_upload(
        io.BytesIO(b"Updated text.\n"), "EMIR_2021_updated.txt",
        family_key="EMIR_2021", uploads_dir=tmp_path,
    )
    assert result.family_key == "EMIR_2021"


def test_same_file_twice_gets_two_versions_with_the_same_hash(tmp_path: Path) -> None:
    # Phase 5 will use the equal hashes to refuse the second one as a duplicate.
    first = accept_upload(io.BytesIO(b"Same text.\n"), "a.txt", uploads_dir=tmp_path)
    second = accept_upload(io.BytesIO(b"Same text.\n"), "a.txt", uploads_dir=tmp_path)
    assert first.version_id != second.version_id
    assert first.sha256 == second.sha256


def test_stream_is_read_from_its_current_position(tmp_path: Path) -> None:
    stream = io.BytesIO(b"IGNORED PREFIX|Text after the prefix.\n")
    stream.seek(len(b"IGNORED PREFIX|"))
    result = accept_upload(stream, "part.txt", uploads_dir=tmp_path)
    assert result.path.read_bytes() == b"Text after the prefix.\n"