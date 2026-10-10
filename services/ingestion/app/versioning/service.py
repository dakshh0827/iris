"""Versioning service (Phase 5), writing to the team schema.

register_upload(): the decision for every accepted upload
  - same SHA-256 as a stored version -> duplicate: no new version, the new copy
    on disk is removed; the attempt is still logged in audit_events
  - same family (document_key) as an existing document -> next version_number
  - otherwise -> new regulatory_documents row with version 1
The previous version is always version_number - 1 of the same document.

Status (document_versions.status, as allowed by the team schema):
  uploaded -> parsed -> indexed, and failed from uploaded or parsed.
Each stage (store, parse, chunk ...) is also an ingestion_jobs row, with attempts.
is_current only moves to a version when it is indexed (mark_indexed).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, DocumentChunk, DocumentVersion, IngestionJob, RegulatoryDocument
from app.intake.accept import ValidatedFile
from app.segmentation.models import SegmentedDocument
from app.versioning.metadata_loader import DocMetadata

logger = logging.getLogger(__name__)

Outcome = Literal["new_document", "new_version", "duplicate"]
UPLOAD_ACTION = "document.upload"

# Which status may follow which. Anything else is a bug and raises InvalidStatusChange.
ALLOWED_MOVES: dict[str, set[str]] = {
    "uploaded": {"parsed", "failed"},
    "parsed": {"indexed", "failed", "parsed"},  # parsed again = chunks re-saved
    "indexed": {"parsed"},                      # re-processing; never back to "uploaded"
    "failed": {"parsed"},                       # a retry that worked
}


class InvalidStatusChange(ValueError):
    """A status move that ALLOWED_MOVES doesn't permit."""


@dataclass(frozen=True)
class RegisterResult:
    version: DocumentVersion
    outcome: Outcome
    warnings: list[str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def log_upload(session: Session, version_id: uuid.UUID, upload: ValidatedFile, outcome: Outcome) -> None:
    session.add(AuditEvent(actor_label="ingestion", action=UPLOAD_ACTION, entity_type="document_version",
                           entity_id=str(version_id),
                           details={"outcome": outcome, "file_name": upload.original_name, "sha256": upload.sha256}))


def register_upload(session: Session, upload: ValidatedFile, metadata: DocMetadata,
                    metadata_warnings: list[str] | None = None) -> RegisterResult:
    """Record an accepted upload as a duplicate, a new version or a new document."""
    existing = session.scalar(select(DocumentVersion).where(DocumentVersion.file_sha256 == upload.sha256))
    if existing is not None:
        log_upload(session, existing.id, upload, "duplicate")
        if str(upload.path.resolve()) != existing.storage_path and upload.path.exists():
            upload.path.unlink()             # the same bytes are already stored
            try:
                upload.path.parent.rmdir()   # the version folder, if now empty
            except OSError:
                pass
        logger.info("%s: duplicate of version %s", upload.original_name, existing.id)
        return RegisterResult(existing, "duplicate", [])

    document = session.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_key == upload.family_key))
    outcome: Outcome = "new_version"
    if document is None:
        outcome = "new_document"
        document = RegulatoryDocument(
            document_key=upload.family_key,
            title=upload.family_key,  # no title in the metadata; the key until someone sets a real one
            regulator=metadata.regulator, jurisdiction=metadata.jurisdiction, category=metadata.category)
        session.add(document)
        session.flush()

    last_number = session.scalar(select(func.max(DocumentVersion.version_number))
                                 .where(DocumentVersion.document_id == document.id)) or 0
    warnings = list(metadata_warnings or [])
    version = DocumentVersion(
        id=uuid.UUID(upload.version_id), document_id=document.id, version_number=last_number + 1,
        is_current=False, original_filename=upload.original_name, storage_path=str(upload.path.resolve()),
        file_type=upload.file_type, file_size_bytes=upload.size_bytes, file_sha256=upload.sha256,
        effective_date=metadata.effective_date, status="uploaded",
        version_label=metadata.version_label, warnings=warnings,
    )
    session.add(version)
    session.flush()
    session.add(IngestionJob(document_version_id=version.id, stage="store", status="succeeded", attempt=1,
                             items_processed=1, started_at=utc_now(), finished_at=utc_now()))
    log_upload(session, version.id, upload, outcome)
    session.flush()
    logger.info("%s: %s, %s version %d", upload.original_name, outcome, upload.family_key, version.version_number)
    return RegisterResult(version, outcome, warnings)


def previous_version(session: Session, version: DocumentVersion) -> DocumentVersion | None:
    return session.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == version.document_id,
        DocumentVersion.version_number == version.version_number - 1))


def start_job(session: Session, version: DocumentVersion, stage: str) -> IngestionJob:
    """A new ingestion_jobs row for this stage: attempt 1, or one more than the last attempt."""
    last = session.scalar(select(func.max(IngestionJob.attempt)).where(
        IngestionJob.document_version_id == version.id, IngestionJob.stage == stage)) or 0
    job = IngestionJob(document_version_id=version.id, stage=stage, status="running",
                       attempt=last + 1, started_at=utc_now())
    session.add(job)
    session.flush()
    return job


def finish_job(session: Session, job: IngestionJob, ok: bool, items: int | None = None,
               error: str | None = None) -> None:
    job.status = "succeeded" if ok else "failed"
    job.items_processed = items
    job.error_message = None if ok else error
    job.finished_at = utc_now()
    session.flush()


def change_status(session: Session, version: DocumentVersion, new_status: str, error: str | None = None) -> None:
    """Move a version to a new status, if that move is allowed."""
    if new_status not in ALLOWED_MOVES.get(version.status, set()):
        raise InvalidStatusChange(f"Version {version.id} can't go from '{version.status}' to '{new_status}'.")
    version.status = new_status
    version.error_message = error if new_status == "failed" else None
    session.flush()


def save_chunks(session: Session, version: DocumentVersion, segmented: SegmentedDocument,
                page_count: int | None = None) -> int:
    """Replace the version's chunks with these ones. Running it twice gives the same rows."""
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
    session.add_all(
        DocumentChunk(
            document_version_id=version.id, chunk_index=chunk.ordinal, section_label=chunk.clause_ref,
            text_content=chunk.text, text_sha256=chunk.text_hash,
            page_number=chunk.page_start, page_end=chunk.page_end, anchor=chunk.anchor,
            line_start=chunk.line_start, line_end=chunk.line_end, heading=chunk.heading,
            foreign_content=chunk.foreign_content, embedded_source=chunk.embedded_source,
        )
        for chunk in segmented.chunks
    )
    version.warnings = list(version.warnings) + [w for w in segmented.warnings if w not in version.warnings]
    if page_count is not None:
        version.page_count = page_count
    session.flush()
    session.expire(version, ["chunks"])
    return len(segmented.chunks)


def mark_indexed(session: Session, version: DocumentVersion) -> None:
    """Status indexed, and make this the current version of its document, in one transaction."""
    change_status(session, version, "indexed")
    session.execute(update(DocumentVersion).where(DocumentVersion.document_id == version.document_id,
                                                  DocumentVersion.id != version.id).values(is_current=False))
    session.flush()  # the old current one must be cleared before the unique index sees the new one
    version.is_current = True
    session.flush()


def count_upload_events(session: Session, version_id: uuid.UUID) -> int:
    return session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == UPLOAD_ACTION, AuditEvent.entity_id == str(version_id)))
