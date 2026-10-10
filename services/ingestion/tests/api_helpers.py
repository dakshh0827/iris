"""Shared helpers for the API and pipeline tests (Phase 9). Not a test file itself."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import make_url, text

from app.config import Settings
from app.db.session import make_engine
from app.indexing.records import IndexingError, VersionInfo, chunk_id
from app.segmentation.models import Chunk

# Read once at import time, before the autouse fixture in conftest.py hides .env from the tests.
_url = Settings().test_database_url
TEST_DB_URL = _url.get_secret_value() if _url else None
TEST_ES_URL = Settings().test_elasticsearch_url

INGESTION_TABLES = "regulatory_documents, document_versions, document_chunks, ingestion_jobs, audit_events"


def empty_test_database() -> None:
    """Remove all ingestion rows from the TEST database (refuses any other database)."""
    database = make_url(TEST_DB_URL).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(f"Refusing to empty '{database}': the test database name must end with _test.")
    engine = make_engine(TEST_DB_URL)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {INGESTION_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()


class MemoryIndex:
    """A fake search index that only remembers what the pipeline sent it."""

    name = "memory"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.chunks: dict[str, str] = {}     # chunk id -> version id
        self.current: dict[str, str] = {}    # document key -> current version id

    def write(self, version: VersionInfo, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if self.fail:
            raise IndexingError("The test index is switched off.")
        assert len(chunks) == len(vectors)
        for chunk in chunks:
            self.chunks[chunk_id(version.version_id, chunk.ordinal)] = version.version_id

    def set_current(self, document_key: str, version_id: str) -> None:
        self.current[document_key] = version_id
