"""Accept one upload: run every Phase 2 check in order, then store the original.

    accept_upload(stream, "EMIR_2021_v2.pdf")
      1. safe_file_name()      Step 4   InvalidFileName
      2. read the first 8 KB, refuse 0 bytes          EmptyFile
      3. check_file_type()     Step 2   UnsupportedFileType
      4. derive_family_key()   Step 6   (unless the caller gives one)
      5. new_version_id()      Step 7
      6. store_original()      Step 7   FileTooLarge, EmptyFile
    -> ValidatedFile (everything later phases need to know about the file)

If any check fails, the error is raised and nothing is stored. A successful
result only means "the original is safely on disk". It does NOT mean the
document is indexed: Phase 5 records it with status "uploaded", and only the
full pipeline (Phases 3 to 9) moves it to "indexed".

Try it from services/ingestion:   python -m app.intake.accept
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.errors import EmptyFile
from app.intake.families import derive_family_key
from app.intake.storage import new_version_id, store_original
from app.intake.validation import SNIFF_SIZE, FileType, check_file_type, safe_file_name


@dataclass(frozen=True)
class ValidatedFile:
    """An upload that passed every check and is stored on disk."""

    original_name: str   # exactly as uploaded, for showing to people
    safe_name: str       # the name used on disk
    file_type: FileType  # "pdf", "html" or "txt", from the content
    family_key: str      # which document this is a version of
    version_id: str      # this upload's id, also its folder name
    path: Path           # where the original is stored
    size_bytes: int
    sha256: str


def accept_upload(
    stream: BinaryIO,
    file_name: str,
    *,
    family_key: str | None = None,
    uploads_dir: Path | None = None,
    max_bytes: int | None = None,
) -> ValidatedFile:
    """Validate and store one upload. Raises an UploadRejected error if it is refused.

    `stream` must be seekable (files opened with open(..., "rb") and FastAPI
    uploads both are), because we look at its first bytes and then go back
    to copy the whole file.
    """
    safe_name = safe_file_name(file_name)

    start = stream.tell()
    head = stream.read(SNIFF_SIZE)
    stream.seek(start)  # go back, so the copy below gets every byte
    if not head:
        raise EmptyFile("The file is empty (0 bytes).", file_name=file_name, size_bytes=0)

    file_type = check_file_type(safe_name, head)
    family_key = safe_file_name(family_key) if family_key else derive_family_key(safe_name)
    version_id = new_version_id()

    stored = store_original(
        stream,
        family_key=family_key,
        version_id=version_id,
        file_name=safe_name,
        uploads_dir=uploads_dir,
        max_bytes=max_bytes,
    )
    return ValidatedFile(
        original_name=file_name,
        safe_name=safe_name,
        file_type=file_type,
        family_key=family_key,
        version_id=version_id,
        path=stored.path,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )


if __name__ == "__main__":
    import io
    import tempfile

    from app.config import get_settings
    from app.errors import UploadRejected
    from app.intake.validation import format_size

    # A temporary folder, so the check doesn't leave files in data/uploads.
    with tempfile.TemporaryDirectory() as temp_dir:
        uploads_dir = Path(temp_dir)
        accepted = 0
        for path in sorted(get_settings().regulations_dir.iterdir()):
            with path.open("rb") as stream:
                result = accept_upload(stream, path.name, uploads_dir=uploads_dir)
            accepted += 1
            print(
                f"ACCEPTED  {result.original_name:<30} {result.file_type:<4} "
                f"{format_size(result.size_bytes):>9}  {result.family_key:<26} {result.sha256[:16]}..."
            )

        print()
        fakes = {
            "invoice.pdf": b"MZ\x90\x00\x03\x00\x00\x00" * 10,  # a program renamed to .pdf
            "report.docx": b"PK\x03\x04" + b"\x00" * 50,       # a Word file
            "notes.txt": b"",                                   # empty
            "../..": b"%PDF-1.7\n",                             # no usable name
        }
        for name, data in fakes.items():
            try:
                accept_upload(io.BytesIO(data), name, uploads_dir=uploads_dir)
                print(f"ACCEPTED  {name}   <- should not happen!")
            except UploadRejected as error:
                print(f"REJECTED  {name:<14} {error.status_code}  {error}")

        stored_files = [p for p in uploads_dir.rglob("*") if p.is_file()]
        print()
        print(f"{accepted} of 10 dataset files accepted; {len(stored_files)} files stored; "
              f"{sum(1 for p in stored_files if p.suffix == '.partial')} partial files")