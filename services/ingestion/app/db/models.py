"""The team's database tables that ingestion writes to, as SQLAlchemy classes (Phase 5).

The tables are NOT created here. They belong to the team schema (migrations
001-004 in the backend, plus 005 from scripts/team-db/). These classes only
describe the columns ingestion uses, so SQLAlchemy can read and write them.

  regulatory_documents  one row per document family, e.g. "MiFID_II_2023"
  document_versions     one row per distinct file, with status and is_current
  ingestion_jobs        one row per pipeline stage attempt (store, parse, chunk ...)
  document_chunks       the chunks of a version (Phase 4 output)
  audit_events          append-only log; every upload attempt is one row here
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# document_versions.status values allowed by the team schema.
STATUSES = ("uploaded", "parsed", "indexed", "failed")
# ingestion_jobs.stage and .status values allowed by the team schema.
STAGES = ("store", "parse", "chunk", "index", "embed", "diff")
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "skipped")


class Base(DeclarativeBase):
    """Parent class of the mapped tables."""


class RegulatoryDocument(Base):
    __tablename__ = "regulatory_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    document_key: Mapped[str] = mapped_column(Text, unique=True)   # our family_key
    title: Mapped[str] = mapped_column(Text)
    regulator: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", order_by="DocumentVersion.version_number")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)  # = the version_id from intake
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("regulatory_documents.id"))
    version_number: Mapped[int] = mapped_column(Integer)             # 1, 2, 3 ... per document
    is_current: Mapped[bool] = mapped_column(Boolean, default=False) # only true once indexed
    original_filename: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(Text)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64))
    effective_date: Mapped[date | None] = mapped_column(Date)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version_label: Mapped[str | None] = mapped_column(Text)          # migration 005
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list) # migration 005

    document: Mapped[RegulatoryDocument] = relationship(back_populates="versions")
    chunks: Mapped[list[DocumentChunk]] = relationship(order_by="DocumentChunk.chunk_index", viewonly=True)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    stage: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    items_processed: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # the database numbers them
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)       # first page (PDF only, 005)
    section_label: Mapped[str | None] = mapped_column(Text)        # our clause_ref, e.g. "Paragraph 3"
    text_content: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    page_end: Mapped[int | None] = mapped_column(Integer)          # migration 005 from here on
    anchor: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    foreign_content: Mapped[bool] = mapped_column(Boolean, default=False)
    embedded_source: Mapped[str | None] = mapped_column(Text)
    # Set by the pipeline (Phase 9) once the chunk is in each index; NULL = not there yet.
    es_document_id: Mapped[str | None] = mapped_column(Text)
    es_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vector_id: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_label: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
