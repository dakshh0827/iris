"""Tests for app/intake/hashing.py (Step 5)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from app.config import get_settings
from app.intake.hashing import sha256_of_file, sha256_of_stream

# Published SHA-256 test values (from the standard, FIPS 180-2), so we compare
# against numbers we did not compute ourselves.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_known_values() -> None:
    assert sha256_of_stream(io.BytesIO(b"")) == EMPTY_SHA256
    assert sha256_of_stream(io.BytesIO(b"abc")) == ABC_SHA256


def test_result_is_64_lowercase_hex_characters() -> None:
    result = sha256_of_stream(io.BytesIO(b"abc"))
    assert len(result) == 64
    assert result == result.lower()
    int(result, 16)  # raises ValueError if it is not hexadecimal


@pytest.mark.parametrize("chunk_size", [1, 7, 4096, 1024 * 1024])
def test_chunk_size_does_not_change_the_result(chunk_size: int) -> None:
    data = bytes(range(256)) * 100  # 25,600 bytes
    assert sha256_of_stream(io.BytesIO(data), chunk_size) == hashlib.sha256(data).hexdigest()


def test_largest_supplied_file_matches_reading_it_whole() -> None:
    path = get_settings().regulations_dir / "EMIR_2021_v2.pdf"  # 3.7 MB, so several chunks
    assert sha256_of_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_one_changed_byte_changes_the_hash(tmp_path: Path) -> None:
    original = tmp_path / "a.txt"
    changed = tmp_path / "b.txt"
    original.write_bytes(b"Paragraph 7. Reporting deadline is T+1.")
    changed.write_bytes(b"Paragraph 7. Reporting deadline is T+2.")
    assert sha256_of_file(original) != sha256_of_file(changed)


def test_same_bytes_give_the_same_hash_whatever_the_name(tmp_path: Path) -> None:
    source = get_settings().regulations_dir / "CFTC_2022.txt"
    copy = tmp_path / "renamed_copy.txt"
    copy.write_bytes(source.read_bytes())
    assert sha256_of_file(copy) == sha256_of_file(source)


def test_supplied_files_are_all_different() -> None:
    paths = list(get_settings().regulations_dir.iterdir())
    hashes = {sha256_of_file(path) for path in paths}
    assert len(hashes) == len(paths) == 10