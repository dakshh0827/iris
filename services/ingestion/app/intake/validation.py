"""Checks that decide whether an uploaded file is acceptable.

- check_file_type(): the real type, from the file's first bytes (Step 2)
- check_size(): not empty, not over the upload limit (Step 3)
- safe_file_name(): a file name that is safe to use on disk (Step 4)

Try it from services/ingestion:   python -m app.intake.validation
"""

from __future__ import annotations

import codecs
import re
from pathlib import PurePath
from typing import Literal

from app.config import get_settings
from app.errors import EmptyFile, FileTooLarge, InvalidFileName, UnsupportedFileType

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

# File names: anything that is not a letter, digit, dot, underscore or hyphen
# becomes "_". Spaces, slashes, colons, quotes and accents are all replaced.
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
MAX_NAME_LENGTH = 200  # well under the 255-character limit of Windows and Linux
# Names Windows reserves for devices. A file called "CON.pdf" can't be created there.
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}


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


def format_size(size_bytes: int) -> str:
    """Return a size people can read: 512 -> "512 bytes", 3930212 -> "3.7 MB"."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def check_size(size_bytes: int, limit_bytes: int | None = None) -> None:
    """Raise EmptyFile or FileTooLarge if the size is not acceptable.

    `limit_bytes` defaults to the IRIS_MAX_UPLOAD_SIZE_MB setting. Tests pass
    their own limit so they don't depend on the settings. A file exactly at
    the limit is accepted; one byte more is refused.
    """
    if limit_bytes is None:
        limit_bytes = get_settings().max_upload_size_bytes

    if size_bytes == 0:
        raise EmptyFile("The file is empty (0 bytes).", size_bytes=0)
    if size_bytes > limit_bytes:
        raise FileTooLarge(
            # Exact bytes too: 1 byte over 50 MB would otherwise read "50.0 MB; the limit is 50.0 MB".
            f"The file is {format_size(size_bytes)} ({size_bytes:,} bytes); "
            f"the limit is {format_size(limit_bytes)} ({limit_bytes:,} bytes).",
            size_bytes=size_bytes,
            limit_bytes=limit_bytes,
        )


def safe_file_name(file_name: str) -> str:
    """Return a version of `file_name` that is safe to use as a file on disk.

    The name comes from the person uploading, so it can contain anything,
    including folder parts like "../../" that would write outside our folder.
    We never use it directly as a path. Instead we:
      1. keep only the last part after any / or \\ (drops "../" and "C:\\Users\\...")
      2. replace every character except A-Z a-z 0-9 . _ - with "_"
      3. squeeze repeated "_" into one
      4. strip dots, "_" and "-" from the start and end ("..", ".env", "name.")
      5. rename Windows device names ("CON.pdf" -> "file_CON.pdf")
      6. shorten very long names, keeping the extension

    Raises InvalidFileName if nothing usable is left.
    The original name is kept separately (Phase 5 stores it in the database).
    """
    last_part = file_name.replace("\\", "/").split("/")[-1]
    name = UNSAFE_NAME_CHARS.sub("_", last_part)
    name = re.sub(r"_+", "_", name)
    name = name.strip("._-")

    if not name:
        raise InvalidFileName(
            "The file name has no usable characters. Rename the file and try again.",
            file_name=file_name,
        )

    # Windows looks at the part before the first dot: "con.tar.gz" is reserved too.
    if name.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        name = f"file_{name}"

    if len(name) > MAX_NAME_LENGTH:
        extension = PurePath(name).suffix  # ".pdf", or "" when there is none
        keep = MAX_NAME_LENGTH - len(extension)
        name = name[:keep].rstrip("._-") + extension
    return name


if __name__ == "__main__":
    settings = get_settings()
    print(f"Upload limit: {format_size(settings.max_upload_size_bytes)}")
    print()

    regulations_dir = settings.regulations_dir
    for path in sorted(regulations_dir.iterdir()):
        with path.open("rb") as file:
            head = file.read(SNIFF_SIZE)
        size_bytes = path.stat().st_size  # the size from the file system, without reading the file
        check_size(size_bytes)
        print(f"{path.name:<32} {check_file_type(path.name, head):<5} {format_size(size_bytes):>9}")

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

    print()
    for name in ["MiFID_II_2023_v1.pdf", "../../hack.pdf", "C:\\Users\\me\\My Rules (final).pdf",
                 "r\u00e8gles 2023.txt", ".env", "CON.pdf"]:
        print(f"{name!r:<36} -> {safe_file_name(name)}")
    try:
        safe_file_name("../..")
    except InvalidFileName as error:
        print(f"{'../..'!r:<36} rejected: {error}")

    print()
    for size_bytes in [0, settings.max_upload_size_bytes + 1]:
        try:
            check_size(size_bytes)
        except (EmptyFile, FileTooLarge) as error:
            print(f"{'size ' + str(size_bytes) + ' bytes':<32} rejected: {error}")