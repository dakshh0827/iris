"""Shapes of the JSON the API sends back (Phase 9).

Each class is one kind of answer. FastAPI uses them to check what we return
and to draw the "Schemas" section of the Swagger page (/docs).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    # from_attributes: can be built straight from a database row (version.status, ...).
    model_config = ConfigDict(from_attributes=True)


class UploadResponse(ApiModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    document_key: str
    version_number: int
    outcome: Literal["new_document", "new_version", "duplicate"]
    status: str
    warnings: list[str]
    status_url: str        # GET this to follow the processing


class ReindexResponse(ApiModel):
    version_id: uuid.UUID
    previous_status: str   # the status before this run; the run itself happens in the background
    status_url: str


class JobOut(ApiModel):
    id: uuid.UUID
    document_version_id: uuid.UUID
    stage: str
    status: str
    attempt: int
    items_processed: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


class VersionSummary(ApiModel):
    id: uuid.UUID
    version_number: int
    version_label: str | None
    original_filename: str
    file_type: str
    status: str
    is_current: bool
    effective_date: date | None
    uploaded_at: datetime


class VersionDetail(VersionSummary):
    document_id: uuid.UUID
    document_key: str
    file_size_bytes: int
    file_sha256: str
    page_count: int | None
    chunk_count: int
    warnings: list[str]
    error_message: str | None
    jobs: list[JobOut]


class DocumentSummary(ApiModel):
    id: uuid.UUID
    document_key: str
    title: str
    regulator: str | None
    jurisdiction: str | None
    category: str | None
    version_count: int
    current_version: int | None   # version_number of the current (indexed) version, if any


class DocumentDetail(DocumentSummary):
    versions: list[VersionSummary]


class ChunkOut(ApiModel):
    chunk_index: int
    clause_ref: str | None
    heading: str | None
    location: str                 # "page 2", "pages 2-3", "#p4", "lines 3-4"
    page_number: int | None
    page_end: int | None
    anchor: str | None
    line_start: int | None
    line_end: int | None
    foreign_content: bool
    embedded_source: str | None
    text: str
