"""Tests for the size checks in app/intake/validation.py (Step 3)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.errors import EmptyFile, FileTooLarge
from app.intake.validation import check_size, format_size

MB = 1024 * 1024


def test_empty_file_is_rejected() -> None:
    with pytest.raises(EmptyFile) as caught:
        check_size(0, limit_bytes=10 * MB)
    assert caught.value.status_code == 400
    assert str(caught.value) == "The file is empty (0 bytes)."


@pytest.mark.parametrize("size_bytes", [1, 10 * MB - 1, 10 * MB])
def test_sizes_up_to_the_limit_are_accepted(size_bytes: int) -> None:
    check_size(size_bytes, limit_bytes=10 * MB)  # passes if no error is raised


def test_one_byte_over_the_limit_is_rejected() -> None:
    with pytest.raises(FileTooLarge) as caught:
        check_size(10 * MB + 1, limit_bytes=10 * MB)
    assert caught.value.status_code == 413
    assert caught.value.details == {"size_bytes": 10 * MB + 1, "limit_bytes": 10 * MB}


def test_error_message_shows_readable_sizes() -> None:
    with pytest.raises(FileTooLarge) as caught:
        check_size(60 * MB, limit_bytes=50 * MB)
    assert str(caught.value) == (
        "The file is 60.0 MB (62,914,560 bytes); the limit is 50.0 MB (52,428,800 bytes)."
    )


def test_default_limit_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    check_size(1 * MB)
    with pytest.raises(FileTooLarge):
        check_size(1 * MB + 1)


def test_every_supplied_file_is_within_the_default_limit() -> None:
    for path in get_settings().regulations_dir.iterdir():
        check_size(path.stat().st_size)


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (0, "0 bytes"),
        (1023, "1023 bytes"),
        (1024, "1.0 KB"),
        (47_600, "46.5 KB"),
        (3_930_212, "3.7 MB"),
        (50 * MB, "50.0 MB"),
    ],
)
def test_format_size(size_bytes: int, expected: str) -> None:
    assert format_size(size_bytes) == expected