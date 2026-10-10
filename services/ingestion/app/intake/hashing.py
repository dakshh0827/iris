"""SHA-256 fingerprints of files.

A SHA-256 hash is a 64-character "fingerprint" of a file's exact bytes. The
same bytes always give the same hash; changing even one byte gives a
completely different one. We use it to spot a file that was uploaded before
(Phase 5 stores it and refuses duplicates).

Try it from services/ingestion:   python -m app.intake.hashing
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

# Read files 1 MB at a time. Memory use stays at about 1 MB whatever the file size.
CHUNK_SIZE = 1024 * 1024


def sha256_of_stream(stream: BinaryIO, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the SHA-256 of everything left in an open binary stream.

    Works for files opened with open(path, "rb") and, later, for uploaded
    files in FastAPI (their .file attribute). Reads from the current position
    to the end, one chunk at a time, so the whole file is never in memory.
    """
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_of_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the SHA-256 of a file on disk, as 64 lowercase hex characters."""
    with path.open("rb") as stream:
        return sha256_of_stream(stream, chunk_size)


if __name__ == "__main__":
    from app.config import get_settings

    regulations_dir = get_settings().regulations_dir
    hashes: dict[str, str] = {}
    for path in sorted(regulations_dir.iterdir()):
        sha256 = sha256_of_file(path)
        hashes[path.name] = sha256
        print(f"{path.name:<32} {sha256}")

    print()
    unique = len(set(hashes.values()))
    print(f"{len(hashes)} files, {unique} different hashes")