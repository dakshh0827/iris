"""Tests for the versioning service against the team's PostgreSQL schema (Phase 5).

They need IRIS_TEST_DATABASE_URL (in .env) pointing at a test copy of the team
database: migrations 001-004 plus scripts/team-db/005_ingestion_locations.sql.
The tests never create or drop tables; before each test they empty the
ingestion tables. To protect real data they refuse to run unless the database
name ends with "_test". Without the URL they are skipped, and pytest says so.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, make_url, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AuditEvent, DocumentChunk, DocumentVersion, IngestionJob
from app.db.session import SchemaMissing, check_schema, make_engine, session_scope
from app.intake.accept import ValidatedFile, accept_upload
from app.parsing.base import extract
from app.segmentation.segmenter import segment
from app.versioning.metadata_loader import DocMetadata
from app.versioning.service import (
    InvalidStatusChange, change_status, count_upload_events, finish_job, mark_indexed, previous_version,
    register_upload, save_chunks, start_job,
)

# Read once at import time, before the autouse fixture in conftest.py hides .env from the tests.
_url = Settings().test_database_url
TEST_DB_URL = _url.get_secret_value() if _url else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(TEST_DB_URL is None, reason="IRIS_TEST_DATABASE_URL is not set"),
]

REGULATIONS = get_settings().regulations_dir
META = DocMetadata(regulator="MiFID II", jurisdiction="EU", category="Trade reporting",
                   effective_date=date(2023, 7, 1), version_label="v1.0")

# The tables ingestion writes to. CASCADE also empties tables that point at them.
INGESTION_TABLES = "regulatory_documents, document_versions, document_chunks, ingestion_jobs, audit_events"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    database = make_url(TEST_DB_URL).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"IRIS_TEST_DATABASE_URL points at '{database}'. The tests empty tables, "
                    "so they only run on a database whose name ends with _test.")
    engine = make_engine(TEST_DB_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Empty ingestion tables for every test (the tables themselves stay)."""
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {INGESTION_TABLES} RESTART IDENTITY CASCADE"))
    with session_scope(engine) as session:
        yield session


def upload(tmp_path: Path, name: str, data: bytes | None = None) -> ValidatedFile:
    """Run a real file (or given bytes) through Phase 2 intake into tmp_path."""
    data = data if data is not None else (REGULATIONS / name).read_bytes()
    return accept_upload(io.BytesIO(data), name, uploads_dir=tmp_path)


def test_schema_has_everything_ingestion_needs(engine: Engine) -> None:
    assert "document_chunks" in check_schema(engine)


def test_schema_without_migration_005_is_reported(engine: Engine) -> None:
    # Drop a 005 column inside a transaction we roll back: Postgres can undo table changes.
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("ALTER TABLE document_chunks DROP COLUMN anchor"))
        with pytest.raises(SchemaMissing, match=r"document_chunks\.anchor.*005_ingestion_locations"):
            check_schema(connection)
        transaction.rollback()
    check_schema(engine)  # back to normal


def test_first_upload_creates_a_document_and_version_1(session: Session, tmp_path: Path) -> None:
    result = register_upload(session, upload(tmp_path, "MiFID_II_2023_v1.pdf"), META)
    version = result.version
    assert result.outcome == "new_document"
    assert (version.version_number, version.status, version.is_current) == (1, "uploaded", False)
    assert previous_version(session, version) is None
    assert version.document.document_key == "MiFID_II_2023"
    assert (version.document.regulator, version.document.jurisdiction) == ("MiFID II", "EU")
    assert (version.effective_date, version.version_label) == (date(2023, 7, 1), "v1.0")
    assert version.file_type == "pdf" and len(version.file_sha256) == 64
    jobs = session.scalars(select(IngestionJob)).all()
    assert [(job.stage, job.status, job.attempt) for job in jobs] == [("store", "succeeded", 1)]


def test_v2_becomes_version_2_with_v1_before_it(session: Session, tmp_path: Path) -> None:
    v1 = register_upload(session, upload(tmp_path, "MiFID_II_2023_v1.pdf"), META).version
    result = register_upload(session, upload(tmp_path, "MiFID_II_2023_v2.pdf"), META)
    assert result.outcome == "new_version"
    assert result.version.version_number == 2
    assert result.version.document_id == v1.document_id
    assert previous_version(session, result.version).id == v1.id


def test_same_file_twice_is_one_version_and_two_events(session: Session, tmp_path: Path) -> None:
    first = register_upload(session, upload(tmp_path, "CFTC_2022.txt"), META).version
    second_upload = upload(tmp_path, "CFTC_2022.txt")
    result = register_upload(session, second_upload, META)
    assert result.outcome == "duplicate"
    assert result.version.id == first.id
    assert session.scalars(select(DocumentVersion)).all() == [first]
    assert count_upload_events(session, first.id) == 2
    assert not second_upload.path.exists()  # the second copy on disk was removed
    assert Path(first.storage_path).exists()


def test_same_content_under_another_name_is_a_duplicate(session: Session, tmp_path: Path) -> None:
    data = (REGULATIONS / "CFTC_2022.txt").read_bytes()
    first = register_upload(session, upload(tmp_path, "CFTC_2022.txt", data), META).version
    result = register_upload(session, upload(tmp_path, "cftc_copy.txt", data), META)
    assert result.outcome == "duplicate" and result.version.id == first.id
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [(e.action, e.details["file_name"], e.details["outcome"]) for e in events] == [
        ("document.upload", "CFTC_2022.txt", "new_document"),
        ("document.upload", "cftc_copy.txt", "duplicate"),
    ]


def test_metadata_warnings_are_stored(session: Session, tmp_path: Path) -> None:
    version = register_upload(session, upload(tmp_path, "notes.txt", b"Some text."), DocMetadata(),
                              ["No metadata for: regulator."]).version
    session.expire_all()  # read back what Postgres stored
    assert version.warnings == ["No metadata for: regulator."]
    assert version.document.regulator is None


def test_status_moves(session: Session, tmp_path: Path) -> None:
    version = register_upload(session, upload(tmp_path, "CFTC_2022.txt"), META).version
    with pytest.raises(InvalidStatusChange, match="from 'uploaded' to 'indexed'"):
        change_status(session, version, "indexed")  # can't skip parsing
    change_status(session, version, "failed", error="Disk full")
    assert (version.status, version.error_message) == ("failed", "Disk full")
    change_status(session, version, "parsed")  # a retry that worked
    assert version.error_message is None
    mark_indexed(session, version)
    with pytest.raises(InvalidStatusChange):
        change_status(session, version, "uploaded")  # indexed never goes back to uploaded


def test_is_current_moves_only_when_indexed(session: Session, tmp_path: Path) -> None:
    v1 = register_upload(session, upload(tmp_path, "MiFID_II_2023_v1.pdf"), META).version
    v2 = register_upload(session, upload(tmp_path, "MiFID_II_2023_v2.pdf"), META).version
    for version in (v1, v2):
        change_status(session, version, "parsed")
    assert not v1.is_current and not v2.is_current  # saved, but not searchable yet
    mark_indexed(session, v1)
    assert v1.is_current
    mark_indexed(session, v2)
    session.refresh(v1)
    assert (v1.is_current, v2.is_current) == (False, True)


def test_database_allows_only_one_current_version(session: Session, tmp_path: Path) -> None:
    v1 = register_upload(session, upload(tmp_path, "MiFID_II_2023_v1.pdf"), META).version
    v2 = register_upload(session, upload(tmp_path, "MiFID_II_2023_v2.pdf"), META).version
    with pytest.raises(IntegrityError, match="ux_document_versions_current"):
        session.execute(update(DocumentVersion).where(DocumentVersion.id.in_([v1.id, v2.id]))
                        .values(is_current=True))
        session.flush()
    session.rollback()


def test_save_chunks_twice_gives_the_same_rows(session: Session, tmp_path: Path) -> None:
    validated = upload(tmp_path, "EMIR_2021_v2.pdf")
    version = register_upload(session, validated, META).version
    document = extract(validated.path, validated.file_type)
    segmented = segment(document)
    assert save_chunks(session, version, segmented, page_count=document.page_count) == 14
    assert save_chunks(session, version, segmented, page_count=document.page_count) == 14  # replaced, not added
    rows = session.scalars(select(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
                           .order_by(DocumentChunk.chunk_index)).all()
    assert [row.chunk_index for row in rows] == list(range(14))
    assert sum(row.foreign_content for row in rows) == 7
    assert {row.embedded_source for row in rows if row.foreign_content} == {"Dodd_Frank_2022.pdf"}
    assert all(row.page_number is not None for row in rows)  # a PDF: every chunk has a page
    assert version.page_count == document.page_count
    assert version.warnings == segmented.warnings  # the integrity warning, stored once


def test_html_chunks_have_an_anchor_and_no_page(session: Session, tmp_path: Path) -> None:
    validated = upload(tmp_path, "FCA_Guidance_2023.html")
    version = register_upload(session, validated, META).version
    save_chunks(session, version, segment(extract(validated.path, validated.file_type)))
    rows = session.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)).all()
    assert len(rows) == 7
    assert all(row.page_number is None and row.anchor for row in rows)  # no invented page numbers
    assert version.page_count is None


def test_ingestion_jobs_count_attempts(session: Session, tmp_path: Path) -> None:
    version = register_upload(session, upload(tmp_path, "CFTC_2022.txt"), META).version
    first = start_job(session, version, "parse")
    finish_job(session, first, ok=False, error="Parser crashed")
    second = start_job(session, version, "parse")
    finish_job(session, second, ok=True, items=5)
    jobs = session.scalars(select(IngestionJob).where(IngestionJob.stage == "parse")
                           .order_by(IngestionJob.attempt)).all()
    assert [(job.attempt, job.status, job.error_message, job.items_processed) for job in jobs] == [
        (1, "failed", "Parser crashed", None),
        (2, "succeeded", None, 5),
    ]
    assert session.scalar(select(func.count()).select_from(IngestionJob)) == 3  # plus the store job
