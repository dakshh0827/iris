"""Document endpoints: upload, list, details, chunks, original file, re-index (Phase 9).

The upload only does the quick part (check, store, register) and answers
202 Accepted at once. The slow part (extract, chunk, embed, index) runs after
the answer, in run_pipeline(). Poll the version until its status is
"indexed" or "failed".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.config import get_settings
from app.db.models import DocumentChunk, DocumentVersion, IngestionJob, RegulatoryDocument
from app.errors import Conflict, NotFound
from app.indexing.records import describe_location
from app.intake.accept import accept_upload
from app.pipeline import run_pipeline
from app.schemas import (ChunkOut, DocumentDetail, DocumentSummary, JobOut, ReindexResponse, UploadResponse, VersionDetail,
                         VersionSummary)
from app.versioning.metadata_loader import DocMetadata, load_metadata_csv, resolve_metadata
from app.versioning.service import register_upload

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

MEDIA_TYPES = {"pdf": "application/pdf", "html": "text/html", "txt": "text/plain"}


def blank_to_none(value: str | None) -> str | None:
    """Swagger and HTML forms send "" for an empty field; treat it as not given."""
    return value.strip() or None if value is not None else None


@router.post("", status_code=202, response_model=UploadResponse, summary="Upload a document",
             responses={200: {"description": "Duplicate: the same file is already stored; nothing is processed"},
                        400: {"description": "Empty file or unusable name"}, 413: {"description": "File too large"},
                        415: {"description": "Not a PDF, HTML or TXT file"}})
def upload_document(
    background_tasks: BackgroundTasks,
    response: Response,
    file: UploadFile = File(..., description="A PDF, HTML or TXT file"),
    regulator: str | None = Form(None, description="Overrides doc_metadata.csv, e.g. 'EMIR'"),
    jurisdiction: str | None = Form(None, description="e.g. 'EU'"),
    category: str | None = Form(None, description="e.g. 'Trade reporting'"),
    effective_date: date | None = Form(None, description="yyyy-mm-dd"),
    version_label: str | None = Form(None, description="e.g. 'v2.0'"),
    session: Session = Depends(get_session),
) -> UploadResponse:
    """Check and store the file, register it as a new document or version, and start processing.

    Metadata comes from these form fields first, then from doc_metadata.csv by file
    name; anything still missing is left empty with a warning (never guessed).
    """
    upload = accept_upload(file.file, file.filename or "")
    form = DocMetadata(regulator=blank_to_none(regulator), jurisdiction=blank_to_none(jurisdiction),
                       category=blank_to_none(category), effective_date=effective_date,
                       version_label=blank_to_none(version_label))
    csv_path = get_settings().metadata_csv
    csv_rows = load_metadata_csv(csv_path) if csv_path.is_file() else {}
    metadata, warnings = resolve_metadata(upload.safe_name, form=form, csv_rows=csv_rows)
    result = register_upload(session, upload, metadata, warnings)
    version = result.version
    session.commit()  # the background run reads the version with its own session
    if result.outcome == "duplicate":
        response.status_code = 200  # nothing new to process
    else:
        background_tasks.add_task(run_pipeline, version.id)
    return UploadResponse(
        document_id=version.document_id, version_id=version.id, document_key=version.document.document_key,
        version_number=version.version_number, outcome=result.outcome, status=version.status,
        warnings=result.warnings, status_url=status_url(version))


@router.get("", response_model=list[DocumentSummary], summary="List documents")
def list_documents(
    regulator: str | None = None, jurisdiction: str | None = None, category: str | None = None,
    uploaded_after: datetime | None = Query(None, description="Only documents with a version uploaded after this"),
    session: Session = Depends(get_session),
) -> list[DocumentSummary]:
    query = select(RegulatoryDocument).order_by(RegulatoryDocument.document_key)
    if regulator:
        query = query.where(RegulatoryDocument.regulator == regulator)
    if jurisdiction:
        query = query.where(RegulatoryDocument.jurisdiction == jurisdiction)
    if category:
        query = query.where(RegulatoryDocument.category == category)
    if uploaded_after:
        query = query.where(RegulatoryDocument.versions.any(DocumentVersion.uploaded_at > uploaded_after))
    return [document_summary(document) for document in session.scalars(query)]


@router.get("/{document_id}", response_model=DocumentDetail, summary="One document and its versions")
def get_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> DocumentDetail:
    document = session.get(RegulatoryDocument, document_id)
    if document is None:
        raise NotFound(f"Document {document_id} does not exist.", document_id=str(document_id))
    return DocumentDetail(**document_summary(document).model_dump(),
                          versions=[VersionSummary.model_validate(v) for v in document.versions])


@router.get("/{document_id}/versions/{version_id}", response_model=VersionDetail,
            summary="Status, warnings and processing steps of one version")
def get_version(document_id: uuid.UUID, version_id: uuid.UUID,
                session: Session = Depends(get_session)) -> VersionDetail:
    version = find_version(session, document_id, version_id)
    jobs = session.scalars(select(IngestionJob).where(IngestionJob.document_version_id == version.id)
                           .order_by(IngestionJob.started_at, IngestionJob.stage)).all()
    chunk_count = session.scalar(select(func.count()).select_from(DocumentChunk)
                                 .where(DocumentChunk.document_version_id == version.id))
    return VersionDetail(
        **VersionSummary.model_validate(version).model_dump(), document_id=version.document_id,
        document_key=version.document.document_key, file_size_bytes=version.file_size_bytes,
        file_sha256=version.file_sha256, page_count=version.page_count, chunk_count=chunk_count,
        warnings=list(version.warnings), error_message=version.error_message,
        jobs=[JobOut.model_validate(job) for job in jobs])


@router.get("/{document_id}/versions/{version_id}/chunks", response_model=list[ChunkOut],
            summary="The chunks of one version, with their locations")
def get_chunks(document_id: uuid.UUID, version_id: uuid.UUID,
               session: Session = Depends(get_session)) -> list[ChunkOut]:
    version = find_version(session, document_id, version_id)
    rows = session.scalars(select(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
                           .order_by(DocumentChunk.chunk_index))
    return [ChunkOut(
        chunk_index=row.chunk_index, clause_ref=row.section_label, heading=row.heading,
        location=describe_location({key: value for key, value in {
            "page_start": row.page_number, "page_end": row.page_end, "anchor": row.anchor,
            "line_start": row.line_start, "line_end": row.line_end, "heading": row.heading}.items()
            if value is not None}),
        page_number=row.page_number, page_end=row.page_end, anchor=row.anchor, line_start=row.line_start,
        line_end=row.line_end, foreign_content=row.foreign_content, embedded_source=row.embedded_source,
        text=row.text_content) for row in rows]


@router.get("/{document_id}/versions/{version_id}/file", response_class=FileResponse,
            summary="Download the original file")
def get_file(document_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session)) -> FileResponse:
    version = find_version(session, document_id, version_id)
    path = Path(version.storage_path)
    if not path.is_file():
        raise NotFound(f"The stored file of version {version_id} is missing.", version_id=str(version_id))
    return FileResponse(path, media_type=MEDIA_TYPES.get(version.file_type), filename=version.original_filename)


@router.post("/{document_id}/versions/{version_id}/reindex", status_code=202, response_model=ReindexResponse,
             summary="Process a version again", responses={409: {"description": "It is being processed now"}})
def reindex_version(document_id: uuid.UUID, version_id: uuid.UUID, background_tasks: BackgroundTasks,
                    session: Session = Depends(get_session)) -> ReindexResponse:
    """Run the whole pipeline again (for example after a failure, or after a parser fix).

    Chunks and index entries are replaced, never duplicated.
    """
    version = find_version(session, document_id, version_id)
    running = session.scalar(select(IngestionJob).where(IngestionJob.document_version_id == version.id,
                                                        IngestionJob.status == "running"))
    if running is not None:
        raise Conflict(f"Version {version_id} is being processed (step '{running.stage}').",
                       version_id=str(version_id), job_id=str(running.id))
    background_tasks.add_task(run_pipeline, version.id)
    return ReindexResponse(version_id=version.id, previous_status=version.status, status_url=status_url(version))


def status_url(version: DocumentVersion) -> str:
    return f"/api/v1/documents/{version.document_id}/versions/{version.id}"


def find_version(session: Session, document_id: uuid.UUID, version_id: uuid.UUID) -> DocumentVersion:
    version = session.get(DocumentVersion, version_id)
    if version is None or version.document_id != document_id:
        raise NotFound(f"Version {version_id} of document {document_id} does not exist.",
                       document_id=str(document_id), version_id=str(version_id))
    return version


def document_summary(document: RegulatoryDocument) -> DocumentSummary:
    current = next((v.version_number for v in document.versions if v.is_current), None)
    return DocumentSummary(
        id=document.id, document_key=document.document_key, title=document.title, regulator=document.regulator,
        jurisdiction=document.jurisdiction, category=document.category, version_count=len(document.versions),
        current_version=current)
