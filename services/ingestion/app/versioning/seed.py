"""Load the 10 supplied files through the pipeline so far (Phase 5 verify).

For each file, in doc_metadata.csv order (so every v1 comes before its v2):
accept_upload (Phase 2) -> resolve metadata -> register_upload -> extract (Phase 3)
-> segment (Phase 4) -> save_chunks, with status uploaded -> parsed and one
ingestion_jobs row per stage. Then it uploads MiFID_II_2023_v1.pdf again to
show duplicate detection.

Run it twice: the second run finds every file as a duplicate and changes nothing.
Needs the team schema plus migration 005 (python -m app.db.session checks that).

Try it from services/ingestion:   python -m app.versioning.seed
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import DocumentVersion
from app.db.session import check_schema, session_scope
from app.errors import IngestionError
from app.intake.accept import accept_upload
from app.parsing.base import extract
from app.segmentation.segmenter import segment
from app.versioning.metadata_loader import DocMetadata, load_metadata_csv, resolve_metadata
from app.versioning.service import (
    change_status, count_upload_events, finish_job, previous_version, register_upload, save_chunks, start_job,
)


def process(session, version: DocumentVersion, path: Path, file_type: str) -> int:
    """Extract and chunk one stored version. Returns the number of chunks (0 if it failed)."""
    job = start_job(session, version, "parse")
    try:
        document = extract(path, file_type)
    except IngestionError as error:
        finish_job(session, job, ok=False, error=str(error))
        change_status(session, version, "failed", error=str(error))
        return 0
    finish_job(session, job, ok=True, items=len(document.blocks))

    job = start_job(session, version, "chunk")
    count = save_chunks(session, version, segment(document), page_count=document.page_count)
    finish_job(session, job, ok=True, items=count)
    change_status(session, version, "parsed")
    return count


def ingest_file(path: Path, csv_rows: dict[str, DocMetadata]) -> str:
    """Run one file through intake, versioning, extraction and chunking. Returns a summary line."""
    with session_scope() as session, path.open("rb") as stream:
        upload = accept_upload(stream, path.name)
        metadata, warnings = resolve_metadata(upload.safe_name, csv_rows=csv_rows)
        result = register_upload(session, upload, metadata, warnings)
        version = result.version
        if result.outcome == "duplicate":
            chunks = len(version.chunks)
        else:
            chunks = process(session, version, upload.path, upload.file_type)
        parent = previous_version(session, version)
        return (f"{path.name:<30} {result.outcome:<12} {version.document.document_key:<26} "
                f"v{version.version_number}  parent={parent.original_filename if parent else '-':<24} "
                f"{chunks:>2} chunks  {version.status}")


if __name__ == "__main__":
    from app.logging_config import configure_logging

    configure_logging()
    logging.getLogger().setLevel(logging.ERROR)
    check_schema()
    csv_rows = load_metadata_csv()
    folder = get_settings().regulations_dir
    for name in sorted(csv_rows, key=lambda n: csv_rows[n].source_doc_id or 0):
        print(ingest_file(folder / name, csv_rows))

    print()
    print("Uploading MiFID_II_2023_v1.pdf again:")
    print(ingest_file(folder / "MiFID_II_2023_v1.pdf", csv_rows))
    with session_scope() as session:
        v1 = session.scalar(select(DocumentVersion).where(DocumentVersion.original_filename == "MiFID_II_2023_v1.pdf"))
        same = session.scalars(select(DocumentVersion).where(DocumentVersion.file_sha256 == v1.file_sha256)).all()
        print(f"Versions with that content: {len(same)}, upload events for it: {count_upload_events(session, v1.id)}")
