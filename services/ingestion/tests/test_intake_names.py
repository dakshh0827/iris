"""Tests for safe_file_name() in app/intake/validation.py (Step 4)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.errors import InvalidFileName
from app.intake.validation import MAX_NAME_LENGTH, safe_file_name


def test_supplied_file_names_are_unchanged() -> None:
    for path in get_settings().regulations_dir.iterdir():
        assert safe_file_name(path.name) == path.name


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("../../hack.pdf", "hack.pdf"),                         # climbing out of the folder
        ("..\\..\\windows\\system32\\evil.txt", "evil.txt"),    # same, Windows style
        ("/etc/passwd.txt", "passwd.txt"),                      # absolute path
        ("C:\\Users\\me\\My Rules (final).pdf", "My_Rules_final_.pdf"),
        ("report  v2 !!.pdf", "report_v2_.pdf"),                # spaces and symbols
        ("r\u00e8gles 2023.txt", "r_gles_2023.txt"),           # accented letter
        (".env", "env"),                                         # hidden file
        ("name.pdf.", "name.pdf"),                               # trailing dot (Windows drops it)
        ("-rf.txt", "rf.txt"),                                   # looks like a command option
        ("CON.pdf", "file_CON.pdf"),                             # Windows device name
        ("nul.tar.txt", "file_nul.tar.txt"),                     # device name, any case
        ("Console.pdf", "Console.pdf"),                          # only the exact names count
    ],
)
def test_unsafe_names_are_cleaned(original: str, expected: str) -> None:
    assert safe_file_name(original) == expected


@pytest.mark.parametrize("original", ["", "../..", "...", "///", "\u00e8\u00e8\u00e8"])
def test_names_with_nothing_usable_are_rejected(original: str) -> None:
    with pytest.raises(InvalidFileName) as caught:
        safe_file_name(original)
    assert caught.value.status_code == 400
    assert caught.value.details["file_name"] == original


def test_long_names_are_shortened_and_keep_the_extension() -> None:
    result = safe_file_name("a" * 300 + ".pdf")
    assert len(result) == MAX_NAME_LENGTH
    assert result.endswith(".pdf")


def test_result_never_contains_a_folder_separator() -> None:
    for original in ["a/b.pdf", "a\\b.pdf", "../x.pdf", "x/../../y.pdf"]:
        result = safe_file_name(original)
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result