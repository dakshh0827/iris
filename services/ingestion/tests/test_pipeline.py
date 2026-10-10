"""run_pipeline() with the real Elasticsearch and Chroma (Phase 9).

Needs IRIS_TEST_DATABASE_URL and IRIS_TEST_ELASTICSEARCH_URL (skipped otherwise).
Uses the fake HashingEmbedder, a Chroma folder in tmp_path and a throw-away
"test_..." Elasticsearch index.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from api_helpers import TEST_DB_URL, TEST_ES_URL, MemoryIndex, empty_test_database
from app.config import get_settings
from app.db.models import DocumentChunk, DocumentVersion
from app.db.session import make_engine, session_scope
from app.indexing import chroma_writer, es_writer
from app.indexing.embedder import HashingEmbedder
from app.intake.accept import accept_upload
from app.pipeline import ChromaIndex, ElasticIndex, Indexers, run_pipeline
from app.versioning.metadata_loader import load_metadata_csv, resolve_metadata
from app.versioning.service import register_upload

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(TEST_DB_URL is None or TEST_ES_URL is None,
                                 reason="needs IRIS_TEST_DATABASE_URL and IRIS_TEST_ELASTICSEARCH_URL")]

REGULATIONS = get_settings().regulations_dir
CSV_ROWS = load_metadata_csv(get_settings().metadata_csv)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple]:
    empty_test_database()
    engine = make_engine(TEST_DB_URL)
    client = es_writer.get_client(TEST_ES_URL)
    alias = f"test_reg_paragraphs_{uuid.uuid4().hex[:8]}"
    es_writer.ensure_index(client, alias)
    embedder = HashingEmbedder()
    collection = chroma_writer.open_collection(embedder.model_name, path=tmp_path / "chroma", name="test_chunks")
    indexers = Indexers(embedder, [ElasticIndex(client, alias), ChromaIndex(collection, embedder.model_name)])
    yield engine, indexers, client, alias, collection, tmp_path
    client.indices.delete(index=f"{alias}_v1", ignore_unavailable=True)
    engine.dispose()


def store(engine, uploads: Path, name: str) -> uuid.UUID:
    """Phase 2 + 5: accept the file and register it (what POST /documents does before the pipeline)."""
    with session_scope(engine) as session:
        upload = accept_upload(io.BytesIO((REGULATIONS / name).read_bytes()), name, uploads_dir=uploads)
        metadata, warnings = resolve_metadata(upload.safe_name, csv_rows=CSV_ROWS)
        return register_upload(session, upload, metadata, warnings).version.id


def test_a_version_ends_up_in_postgres_elasticsearch_and_chroma(setup) -> None:
    engine, indexers, client, alias, collection, tmp_path = setup
    version_id = store(engine, tmp_path / "uploads", "MiFID_II_2023_v1.pdf")
    assert run_pipeline(version_id, indexers, engine) == "indexed"
    assert client.count(index=alias)["count"] == collection.count() == 6
    hits = es_writer.search(client, alias, "Counterparty_LEI", current_only=True)
    assert (hits[0].source["clause_ref"], hits[0].source["page_start"]) == ("Paragraph 3", 1)
    with session_scope(engine) as session:
        rows = session.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)).all()
        assert rows[3].es_document_id == rows[3].vector_id == f"{version_id}:0003"
        assert rows[3].embedding_model == "hashing-fake"


def test_running_again_and_a_new_version(setup) -> None:
    engine, indexers, client, alias, collection, tmp_path = setup
    v1 = store(engine, tmp_path / "uploads", "MiFID_II_2023_v1.pdf")
    run_pipeline(v1, indexers, engine)
    assert run_pipeline(v1, indexers, engine) == "indexed"  # re-index: replaced, not added
    assert client.count(index=alias)["count"] == collection.count() == 6
    v2 = store(engine, tmp_path / "uploads", "MiFID_II_2023_v2.pdf")
    run_pipeline(v2, indexers, engine)
    assert client.count(index=alias)["count"] == collection.count() == 13
    current_es = {h.source["file_name"] for h in es_writer.search(client, alias, "LEI", limit=50, current_only=True)}
    current_chroma = {m["file_name"] for m in collection.get(where={"is_current": True})["metadatas"]}
    assert current_es == current_chroma == {"MiFID_II_2023_v2.pdf"}
    with session_scope(engine) as session:
        assert session.get(DocumentVersion, v1).is_current is False
        assert session.get(DocumentVersion, v2).is_current is True


def test_a_failed_reindex_keeps_the_current_version_searchable(setup) -> None:
    engine, indexers, client, alias, collection, tmp_path = setup
    v1 = store(engine, tmp_path / "uploads", "MiFID_II_2023_v1.pdf")
    run_pipeline(v1, indexers, engine)
    broken = Indexers(indexers.embedder, [indexers.indexes[0], MemoryIndex(fail=True)])  # Chroma "down"
    assert run_pipeline(v1, broken, engine) == "failed"
    hits = es_writer.search(client, alias, "Counterparty_LEI", current_only=True)
    assert hits and hits[0].source["file_name"] == "MiFID_II_2023_v1.pdf"
    with session_scope(engine) as session:
        assert session.get(DocumentVersion, v1).is_current is True  # still the one users should see
