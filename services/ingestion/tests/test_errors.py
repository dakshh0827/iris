"""Tests for app/errors.py: codes, HTTP statuses, the class family and the problem JSON."""

from __future__ import annotations

import pytest

from app.errors import (
    EmptyFile,
    FileTooLarge,
    IngestionError,
    InvalidFileName,
    UnsupportedFileType,
    UploadRejected,
)


@pytest.mark.parametrize(
    ("error_class", "code", "status_code"),
    [
        (EmptyFile, "empty_file", 400),
        (FileTooLarge, "file_too_large", 413),
        (UnsupportedFileType, "unsupported_file_type", 415),
        (InvalidFileName, "invalid_file_name", 400),
    ],
)
def test_each_rejection_has_its_code_and_status(error_class: type[UploadRejected], code: str, status_code: int) -> None:
    error = error_class("some message")
    assert error.code == code
    assert error.status_code == status_code
    # Every intake error belongs to both families, so one `except` can catch them all.
    assert isinstance(error, UploadRejected)
    assert isinstance(error, IngestionError)


def test_message_is_the_error_text() -> None:
    error = EmptyFile("The file is empty (0 bytes).")
    assert str(error) == "The file is empty (0 bytes)."
    assert error.message == "The file is empty (0 bytes)."


def test_details_are_kept() -> None:
    error = FileTooLarge("Too big.", size_bytes=100, limit_bytes=50)
    assert error.details == {"size_bytes": 100, "limit_bytes": 50}


def test_base_class_catches_a_specific_error() -> None:
    with pytest.raises(UploadRejected) as caught:
        raise EmptyFile("The file is empty (0 bytes).")
    assert caught.value.code == "empty_file"


def test_to_problem_has_the_standard_keys_and_the_details() -> None:
    error = FileTooLarge("The file is 60.0 MB; the limit is 50 MB.", size_bytes=62914560, limit_bytes=52428800)
    assert error.to_problem() == {
        "type": "urn:reg-iris:error:file_too_large",
        "title": "File too large",
        "status": 413,
        "detail": "The file is 60.0 MB; the limit is 50 MB.",
        "size_bytes": 62914560,
        "limit_bytes": 52428800,
    }


def test_details_cannot_overwrite_standard_keys() -> None:
    error = EmptyFile("The file is empty (0 bytes).", status=200, title="fake")
    problem = error.to_problem()
    assert problem["status"] == 400
    assert problem["title"] == "Empty file"