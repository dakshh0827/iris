"""Run one stored version through the whole pipeline (Phase 9).

    uploaded --parse--> --chunk--> parsed --embed--> --index--> indexed
                 (any step fails: failed, with the error saved)

  parse   extract the text with locations (Phase 3)
  chunk   clean and split into chunks, save them in PostgreSQL (Phases 4, 5)
  embed   one vector per chunk (Phase 6)
  index   chunks into Elasticsearch and Chroma (Phases 7, 8), then the last step:
          status "indexed" and this version becomes the current one, in
          PostgreSQL and in both indexes. Only then is it committed, so a
          version is never "indexed" unless both indexes have it.

Each step is an ingestion_jobs row (attempt 1, 2, ... when re-run), so
GET /api/v1/jobs/{id} shows where a run is. Each step is committed as soon as
it is done: the chunks are safe in PostgreSQL before the slow embedding starts.

run_pipeline() opens its own database session. It runs after the HTTP
response has been sent (FastAPI BackgroundTasks), when the request's session
is already closed.

Try it from services/ingestion:   python -m app.pipeline <version_id>
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DocumentChunk, DocumentVersion
from app.db.session import session_scope
from app.errors import IngestionError, NotFound
from app.indexing import chroma_writer, es_writer
from app.indexing.embedder import Embedder, embed_chunks, get_embedder
from app.indexing.records import VersionInfo, chunk_id
from app.parsing.base import extract
from app.segmentation.models import Chunk
from app.segmentation.segmenter import segment
from app.versioning.service import change_status, finish_job, mark_indexed, save_chunks, start_job, utc_now

logger = logging.getLogger(__name__)


class SearchIndex(Protocol):
    """What the pipeline needs from an index. Elasticsearch, Chroma and the test fake all fit."""

    name: str

    def write(self, version: VersionInfo, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    def set_current(self, document_key: str, version_id: str) -> None: ...


class ElasticIndex:
    name = "elasticsearch"

    def __init__(self, client, alias: str) -> None:
        self.client, self.alias = client, alias

    def write(self, version, chunks, vectors) -> None:
        es_writer.index_version(self.client, self.alias, version, chunks)  # keyword search needs no vectors

    def set_current(self, document_key, version_id) -> None:
        es_writer.set_current(self.client, self.alias, document_key, version_id)


class ChromaIndex:
    name = "chroma"

    def __init__(self, collection, model_name: str) -> None:
        self.collection, self.model_name = collection, model_name

    def write(self, version, chunks, vectors) -> None:
        chroma_writer.upsert_version(self.collection, version, chunks, vectors, self.model_name)

    def set_current(self, document_key, version_id) -> None:
        chroma_writer.set_current(self.collection, document_key, version_id)


@dataclass
class Indexers:
    """The embedding model and the indexes a run writes to."""

    embedder: Embedder
    indexes: list[SearchIndex] = field(default_factory=list)


@lru_cache
def get_indexers() -> Indexers:
    """The real ones, made once per process: loading the model and connecting is slow."""
    embedder = get_embedder()
    client = es_writer.get_client()
    alias = get_settings().es_index
    es_writer.ensure_index(client, alias)
    collection = chroma_writer.open_collection(embedder.model_name)
    return Indexers(embedder, [ElasticIndex(client, alias), ChromaIndex(collection, embedder.model_name)])


def version_info(version: DocumentVersion) -> VersionInfo:
    """What the indexes store about the version.

    is_current is copied from PostgreSQL: a new version starts False and set_current() turns it on at
    the end; re-indexing the current version keeps it True, so it stays searchable even if the run fails.
    """
    document = version.document
    return VersionInfo(
        version_id=str(version.id), document_key=document.document_key, file_name=version.original_filename,
        version_number=version.version_number, is_current=version.is_current,
        regulator=document.regulator, jurisdiction=document.jurisdiction,
        category=document.category, effective_date=version.effective_date, version_label=version.version_label)


def error_text(error: Exception) -> str:
    """A short message for status and job rows: our own errors have a readable one already."""
    return error.message if isinstance(error, IngestionError) else f"{type(error).__name__}: {error}"


def run_pipeline(version_id: str | uuid.UUID, indexers: Indexers | None = None,
                 engine: Engine | None = None) -> str:
    """Process one stored version from its file to both indexes. Returns the final status.

    Safe to run again on the same version (re-index): chunks and index entries are replaced.
    """
    version_id = uuid.UUID(str(version_id))
    with session_scope(engine) as session:
        version = session.get(DocumentVersion, version_id)
        if version is None:
            raise NotFound(f"Version {version_id} does not exist.", version_id=str(version_id))
        if version.status == "indexed":
            change_status(session, version, "parsed")  # being processed again
            session.commit()
        job = None  # the step that is running, so a failure can be recorded on it
        try:
            job = start_job(session, version, "parse")
            session.commit()
            document = extract(Path(version.storage_path), version.file_type)
            finish_job(session, job, ok=True, items=len(document.blocks))

            job = start_job(session, version, "chunk")
            segmented = segment(document)
            count = save_chunks(session, version, segmented, page_count=document.page_count)
            finish_job(session, job, ok=True, items=count)
            change_status(session, version, "parsed")
            session.commit()  # the chunks are safe in PostgreSQL now

            job = start_job(session, version, "embed")
            session.commit()
            indexers = indexers or get_indexers()  # loads the model and connects (first run only)
            vectors = embed_chunks(indexers.embedder, segmented.chunks, version.document.regulator)
            finish_job(session, job, ok=True, items=len(vectors))

            job = start_job(session, version, "index")
            session.commit()
            info = version_info(version)
            for index in indexers.indexes:
                index.write(info, segmented.chunks, vectors)
            record_index_ids(session, version, indexers)
            mark_indexed(session, version)  # status indexed + is_current in PostgreSQL ...
            for index in indexers.indexes:  # ... and in both indexes
                index.set_current(info.document_key, info.version_id)
            finish_job(session, job, ok=True, items=count)
            session.commit()  # only now does anyone see "indexed"
            logger.info("%s: indexed, %d chunks", version.original_filename, count)
            return version.status
        except Exception as error:  # any failure: save it on the version and the step, then stop
            message = error_text(error)
            logger.error("%s: failed at %s: %s", version.original_filename, job.stage if job else "start", message)
            session.rollback()
            if job is not None:
                finish_job(session, session.merge(job), ok=False, error=message)
            change_status(session, version, "failed", error=message)
            session.commit()
            return version.status


def record_index_ids(session: Session, version: DocumentVersion, indexers: Indexers) -> None:
    """Note on each chunk row where it is in Elasticsearch and Chroma (same id in both)."""
    now = utc_now()
    names = {index.name for index in indexers.indexes}
    rows = session.scalars(select(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
    for row in rows:
        key = chunk_id(str(version.id), row.chunk_index)
        if "elasticsearch" in names:
            row.es_document_id, row.es_indexed_at = key, now
        if "chroma" in names:
            row.vector_id, row.embedding_model, row.embedded_at = key, indexers.embedder.model_name, now
    session.flush()


if __name__ == "__main__":
    import sys

    from app.logging_config import configure_logging

    configure_logging()
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m app.pipeline <version_id>")
    print("Final status:", run_pipeline(sys.argv[1]))
