"""Error types for the ingestion service.

When the service refuses something on purpose (an empty file, a file that is
too big, a type we don't support), it raises one of these errors instead of a
plain ValueError. That gives every refusal:

  - a fixed `code` that programs can check, e.g. "file_too_large"
  - the HTTP status the API will answer with (used from Phase 9)
  - a human-readable message and optional details

Try it from services/ingestion:   python -m app.errors
"""

from __future__ import annotations

from typing import Any


class IngestionError(Exception):
    """Base class for every error the ingestion service raises on purpose.

    Catching IngestionError catches all of them; catching a subclass catches
    only that kind.
    """

    code: str = "ingestion_error"     # stable name for programs and tests
    title: str = "Ingestion error"    # short title for people
    status_code: int = 500            # HTTP status used by the API (Phase 9)

    def __init__(self, message: str, **details: Any) -> None:
        """Store the message and any extra facts, e.g. size_bytes=..., limit_bytes=...."""
        super().__init__(message)
        self.message = message
        self.details = details

    def to_problem(self) -> dict[str, Any]:
        """Return the error as an RFC 7807 "problem details" dictionary.

        This is the JSON shape the API will send back for errors (Phase 9).
        The standard keys come last, so a detail can never overwrite them.
        """
        return {
            **self.details,
            "type": f"urn:reg-iris:error:{self.code}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.message,
        }


class UploadRejected(IngestionError):
    """Base class for files refused at intake (Phase 2). Nothing is stored for these."""

    code = "upload_rejected"
    title = "Upload rejected"
    status_code = 400


class EmptyFile(UploadRejected):
    """The upload has 0 bytes."""

    code = "empty_file"
    title = "Empty file"
    status_code = 400


class FileTooLarge(UploadRejected):
    """The upload is bigger than IRIS_MAX_UPLOAD_SIZE_MB."""

    code = "file_too_large"
    title = "File too large"
    status_code = 413  # HTTP 413 Content Too Large


class UnsupportedFileType(UploadRejected):
    """The content is not PDF, HTML or TXT, or does not match the file extension."""

    code = "unsupported_file_type"
    title = "Unsupported file type"
    status_code = 415  # HTTP 415 Unsupported Media Type


class InvalidFileName(UploadRejected):
    """Nothing usable is left of the file name after removing unsafe characters."""

    code = "invalid_file_name"
    title = "Invalid file name"
    status_code = 400


if __name__ == "__main__":
    for error in [
        EmptyFile("The file is empty (0 bytes).", file_name="notes.txt"),
        FileTooLarge("The file is 60.0 MB; the limit is 50 MB.", size_bytes=62914560, limit_bytes=52428800),
        UnsupportedFileType("Only PDF, HTML and TXT files are supported.", file_name="report.docx"),
        InvalidFileName("The file name has no usable characters.", file_name="../.."),
    ]:
        print(f"{type(error).__name__:<20} code={error.code:<22} status={error.status_code}  {error}")

    print()
    try:
        raise FileTooLarge("The file is 60.0 MB; the limit is 50 MB.", size_bytes=62914560, limit_bytes=52428800)
    except UploadRejected as caught:  # the base class catches every kind of rejection
        print("Caught as UploadRejected:", type(caught).__name__)
        print("As problem JSON:", caught.to_problem())