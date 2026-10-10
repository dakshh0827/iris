"""Store the original uploaded file on disk, safely.

Layout:   data/uploads/{family_key}/{version_id}/{safe_file_name}
Example:  data/uploads/EMIR_2021/3f2a9c1e-.../EMIR_2021_v2.pdf

Rules:
  - The bytes are copied in 1 MB chunks, so memory stays low.
  - The size limit is checked while copying, so a huge upload is stopped
    early instead of filling the disk first.
  - The SHA-256 is computed during the same copy (one read, not two).
  - Data goes to a temporary ".partial" file first and is renamed to the real
    name only when everything worked. A crash or an error never leaves a
    half-written file under the real name.
  - An existing original is never overwritten.

Try it from services/ingestion:   python -m app.intake.storage
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings
from app.errors import FileTooLarge
from app.intake.hashing import CHUNK_SIZE
from app.intake.validation import check_size, format_size, safe_file_name

logger = logging.getLogger(__name__)

PARTIAL_SUFFIX = ".partial"


@dataclass(frozen=True)
class StoredFile:
    """What store_original() saved: where, how big, and its fingerprint."""

    path: Path
    size_bytes: int
    sha256: str


def new_version_id() -> str:
    """Return a new random version id, e.g. "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90".

    A UUID4 is random, so two uploads practically never get the same id, and
    it contains only letters, digits and "-", so it is safe as a folder name.
    """
    return str(uuid.uuid4())


def store_original(
    stream: BinaryIO,
    *,
    family_key: str,
    version_id: str,
    file_name: str,
    uploads_dir: Path | None = None,
    max_bytes: int | None = None,
) -> StoredFile:
    """Copy `stream` to uploads_dir/family_key/version_id/file_name and return a StoredFile.

    `uploads_dir` and `max_bytes` default to the settings; tests pass their own.
    Raises:
      ValueError         version_id is not a UUID
      EmptyFile          the stream had no bytes
      FileTooLarge       the stream had more than max_bytes
      FileExistsError    a file is already stored at that place
      InvalidFileName    family_key or file_name has nothing usable
    """
    settings = get_settings()
    uploads_dir = uploads_dir or settings.uploads_dir
    max_bytes = max_bytes or settings.max_upload_size_bytes

    # Every part of the path is checked, even though callers should already
    # pass safe values: a path built from unchecked text is how files escape.
    version_id = str(uuid.UUID(version_id))  # raises ValueError if not a UUID
    folder = uploads_dir / safe_file_name(family_key) / version_id
    final_path = folder / safe_file_name(file_name)
    partial_path = final_path.with_name(final_path.name + PARTIAL_SUFFIX)

    if final_path.exists():
        raise FileExistsError(f"An original is already stored at {final_path}")
    folder.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size_bytes = 0
    try:
        # "xb": create a new file for writing bytes; fail if it already exists.
        with partial_path.open("xb") as target:
            while chunk := stream.read(CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise FileTooLarge(
                        f"The file is larger than the limit of {format_size(max_bytes)} "
                        f"({max_bytes:,} bytes).",
                        limit_bytes=max_bytes,
                    )
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())  # make sure the bytes are really on disk
        check_size(size_bytes, max_bytes)  # raises EmptyFile for 0 bytes
        partial_path.replace(final_path)  # the rename: all or nothing
    except BaseException:
        # Clean up whatever we created, then let the error continue.
        partial_path.unlink(missing_ok=True)
        _remove_if_empty(folder)
        _remove_if_empty(folder.parent)
        raise

    logger.info("Stored %s (%s, sha256 %s)", final_path, format_size(size_bytes), digest.hexdigest())
    return StoredFile(path=final_path, size_bytes=size_bytes, sha256=digest.hexdigest())


def _remove_if_empty(folder: Path) -> None:
    """Delete `folder` if it exists and is empty; otherwise leave it alone."""
    try:
        folder.rmdir()  # only works on an empty folder
    except OSError:
        pass


if __name__ == "__main__":
    import tempfile

    from app.intake.families import derive_family_key

    source = get_settings().regulations_dir / "EMIR_2021_v2.pdf"
    # A temporary folder, so the demo doesn't leave files in data/uploads.
    with tempfile.TemporaryDirectory() as temp_dir:
        uploads_dir = Path(temp_dir)
        with source.open("rb") as stream:
            stored = store_original(
                stream,
                family_key=derive_family_key(source.name),
                version_id=new_version_id(),
                file_name=source.name,
                uploads_dir=uploads_dir,
            )
        print("Stored at:   ", stored.path.relative_to(uploads_dir).as_posix())
        print("Size:        ", f"{stored.size_bytes:,} bytes")
        print("SHA-256:     ", stored.sha256)
        print("Same bytes:  ", stored.path.read_bytes() == source.read_bytes())
        print("Files there: ", [p.name for p in stored.path.parent.iterdir()])