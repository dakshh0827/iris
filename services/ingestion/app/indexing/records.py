"""What the Chroma and Elasticsearch writers share (Phases 7 and 8).

  VersionInfo        the facts about a version that both indexes store with its chunks
  chunk_id()         the id of a chunk in both indexes: "{version_id}:{chunk_index:04d}"
  IndexingError      writing to or reading from an index failed
  describe_location  "page 2", "#p4", "lines 3-4" for a search result
  demo_versions()    the 10 supplied files as versions, for the demos (no database needed)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import IngestionError


class IndexingError(IngestionError):
    """Writing to or reading from a search index failed."""

    code = "indexing_failed"
    title = "Indexing failed"
    status_code = 500


@dataclass(frozen=True)
class VersionInfo:
    """What Chroma needs to know about the version the chunks belong to."""

    version_id: str              # document_versions.id (a UUID as text)
    document_key: str            # e.g. "EMIR_2021"
    file_name: str               # e.g. "EMIR_2021_v2.pdf"
    version_number: int
    is_current: bool = False     # only True once the version is fully indexed
    regulator: str | None = None
    jurisdiction: str | None = None
    category: str | None = None
    effective_date: date | None = None
    version_label: str | None = None


def chunk_id(version_id: str, chunk_index: int) -> str:
    """Stable id of a chunk in Chroma, e.g. "3f2a...:0007". Same chunk, same id."""
    return f"{version_id}:{chunk_index:04d}"


def date_number(value: date) -> int:
    """date(2023, 7, 1) -> 20230701."""
    return value.year * 10000 + value.month * 100 + value.day


def describe_location(metadata: dict[str, Any]) -> str:
    """Where a hit is in its file: "page 2", "pages 2-3", "#p4", "lines 3-4" or its heading."""
    if "page_start" in metadata:
        start, end = metadata["page_start"], metadata.get("page_end", metadata["page_start"])
        return f"page {start}" if start == end else f"pages {start}-{end}"
    if "anchor" in metadata:
        return f"#{metadata['anchor']}"
    if "line_start" in metadata:
        return f"lines {metadata['line_start']}-{metadata['line_end']}"
    return str(metadata.get("heading", "-"))


def demo_versions() -> list[tuple[VersionInfo, Path]]:
    """The 10 supplied files as versions, without PostgreSQL (the pipeline in Phase 9 uses the real rows).

    Version ids are derived from the file's SHA-256, so every run gives the same ids.
    The highest version of each document is marked current, as if all were indexed.
    """
    from app.intake.families import derive_family_key
    from app.versioning.metadata_loader import load_metadata_csv

    csv_rows = load_metadata_csv()
    folder = get_settings().regulations_dir
    names = sorted(csv_rows, key=lambda n: csv_rows[n].source_doc_id or 0)
    counts: dict[str, int] = {}
    versions = []
    for name in names:
        key = derive_family_key(name)
        counts[key] = counts.get(key, 0) + 1
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        meta = csv_rows[name]
        versions.append(VersionInfo(
            version_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"reg-iris:{digest}")), document_key=key,
            file_name=name, version_number=counts[key], regulator=meta.regulator,
            jurisdiction=meta.jurisdiction, category=meta.category, effective_date=meta.effective_date,
            version_label=meta.version_label))
    return [(v, folder / v.file_name) for v in versions]
