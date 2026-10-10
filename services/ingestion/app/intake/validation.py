"""Checks that decide whether an uploaded file is acceptable.

Step 2 (this version): detect the real file type from the file's first bytes
and make sure it matches the file extension. Steps 3 and 4 add the size,
empty-file and file-name checks to this same file.

Try it from services/ingestion:   python -m app.intake.validation
"""

from __future__ import annotations

import codecs
from pathlib import PurePath
from typing import Literal

from app.errors import UnsupportedFileType

# The three types the pipeline can extract text from (Phase 3).
FileType = Literal["pdf", "html", "txt"]

# Which extension promises which type. .htm is an older spelling of .html.
EXTENSION_TO_TYPE: dict[str, FileType] = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".txt": "txt",
}

# How many bytes from the start of a file we look at. Enough to see the PDF
# signature or the start of an HTML page, without reading a whole large file.
SNIFF_SIZE = 8192

PDF_SIGNATURE = b"%PDF-"
UTF8_BOM = b"\xef\xbb\xbf"  # 3 invisible bytes some editors put at the start of UTF-8 text
HTML_STARTS = ("<!doctype html", "<html")


def _looks_like_utf8_text(head: bytes) -> str | None:
    """Return the decoded text if `head` is valid UTF-8 text, else None.

    `head` may be cut in the middle of a multi-byte character (for example the
    3 bytes of an en dash, U+2013). An incremental decoder with final=False keeps
    such an unfinished character for later instead of calling it an error.
    A NUL byte never appears in real text files, so it means binary content.
    """
    if b"\x00" in head:
        return None
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return None


def sniff_file_type(head: bytes) -> FileType | None:
    """Detect the file type from the first bytes of a file.

    Returns "pdf", "html", "txt", or None when the content is none of these
    (for example a Word file, an image or a program).
    """
    if head.startswith(PDF_SIGNATURE):
        return "pdf"

    text = _looks_like_utf8_text(head.removeprefix(UTF8_BOM))
    if text is None:
        return None
    if text.lstrip().lower().startswith(HTML_STARTS):
        return "html"
    return "txt"


def check_file_type(file_name: str, head: bytes) -> FileType:
    """Return the file's type, or raise UnsupportedFileType.

    Three things must be true:
      1. the extension is one we accept (.pdf, .html, .htm, .txt);
      2. the content is really PDF, HTML or text;
      3. the content matches the extension (a renamed .exe called .pdf fails).
    """
    extension = PurePath(file_name).suffix.lower()
    expected = EXTENSION_TO_TYPE.get(extension)
    if expected is None:
        raise UnsupportedFileType(
            f"Files ending in '{extension or '(no extension)'}' are not supported. "
            "Use PDF, HTML or TXT.",
            file_name=file_name,
            extension=extension,
        )

    detected = sniff_file_type(head)
    if detected is None:
        raise UnsupportedFileType(
            "The file content is not PDF, HTML or UTF-8 text.",
            file_name=file_name,
            extension=extension,
        )
    if detected != expected:
        raise UnsupportedFileType(
            f"The file ends in '{extension}' but its content is {detected.upper()}.",
            file_name=file_name,
            extension=extension,
            detected_type=detected,
        )
    return detected


if __name__ == "__main__":
    from app.config import get_settings

    regulations_dir = get_settings().regulations_dir
    for path in sorted(regulations_dir.iterdir()):
        with path.open("rb") as file:
            head = file.read(SNIFF_SIZE)
        print(f"{path.name:<32} {check_file_type(path.name, head)}")

    print()
    fakes = {
        "program.pdf": b"MZ\x90\x00\x03\x00\x00\x00",  # a Windows program renamed to .pdf
        "report.docx": b"PK\x03\x04\x14\x00\x06\x00",  # a Word file
        "notes.txt": b"%PDF-1.7\r\n",                   # a PDF renamed to .txt
    }
    for name, head in fakes.items():
        try:
            check_file_type(name, head)
        except UnsupportedFileType as error:
            print(f"{name:<32} rejected: {error}")