"""Store chunk vectors in ChromaDB and search them (Phase 7).

ChromaDB is a vector database: it keeps each chunk's vector, its text and a
few labels (metadata), and finds the vectors closest to a question's vector.
The Search service's "semantic" mode reads only from here. PostgreSQL stays
the system of record; Chroma can always be rebuilt from it.

  open_collection()  the collection for our embedding model, created if needed
  upsert_version()   put one version's chunks in (again = same result, never duplicates)
  delete_version()   remove one version's chunks
  set_current()      mark which version of a document is the current one
  search()           the closest chunks to a vector, optionally filtered

Rules Chroma imposes on metadata: values must be text, numbers or True/False,
never None. So empty fields are left out, and dates are stored as numbers like
20230701 (yyyymmdd), which can be compared with $gte / $lte filters.

Try it from services/ingestion:   python -m app.indexing.chroma_writer
(needs the model from Phase 6; writes to data/chroma)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.indexing.records import (  # shared with es_writer.py; imported here so older imports keep working
    IndexingError, VersionInfo, chunk_id, date_number, demo_versions, describe_location,
)
from app.segmentation.models import Chunk

logger = logging.getLogger(__name__)

Metadata = dict[str, str | int | float | bool]


@dataclass(frozen=True)
class Hit:
    """One search result."""

    id: str
    score: float      # cosine similarity: 1.0 = same direction, 0 = unrelated
    text: str
    metadata: Metadata


def chunk_metadata(version: VersionInfo, chunk: Chunk, model_name: str) -> Metadata:
    """The labels stored with a chunk. Empty values are left out (Chroma rejects None)."""
    values: dict[str, Any] = {
        "version_id": version.version_id,
        "document_key": version.document_key,
        "file_name": version.file_name,
        "version_number": version.version_number,
        "is_current": version.is_current,
        "regulator": None if chunk.foreign_content else version.regulator,  # pasted text: not this regulator
        "jurisdiction": version.jurisdiction,
        "category": version.category,
        "effective_date": date_number(version.effective_date) if version.effective_date else None,
        "version_label": version.version_label,
        "chunk_index": chunk.ordinal,
        "clause_ref": chunk.clause_ref,
        "heading": chunk.heading,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "anchor": chunk.anchor,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "foreign_content": chunk.foreign_content,
        "embedded_source": chunk.embedded_source,
        "text_hash": chunk.text_hash,
        "embedding_model": model_name,
    }
    return {key: value for key, value in values.items() if value is not None}


def open_collection(model_name: str, path: Path | None = None, name: str | None = None):  # -> chromadb Collection
    """Open (or create) the collection for this embedding model, stored on disk.

    The collection remembers which model made its vectors. Opening it with a
    different model raises IndexingError: mixing two models' vectors gives
    meaningless search results.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    settings = get_settings()
    path = path or settings.chroma_dir
    name = name or settings.chroma_collection
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
    collection = client.get_or_create_collection(
        name,
        embedding_function=None,                      # we always give vectors ourselves (Phase 6)
        configuration={"hnsw": {"space": "cosine"}},  # compare by angle, as the vectors are normalised
        metadata={"embedding_model": model_name},
    )
    stored_model = (collection.metadata or {}).get("embedding_model")
    if stored_model != model_name:
        raise IndexingError(f"Collection '{name}' holds vectors from '{stored_model}', not '{model_name}'. "
                            "Use another collection name (IRIS_CHROMA_COLLECTION) for another model.",
                            collection=name, stored_model=stored_model, model=model_name)
    return collection


def version_ids(collection, version_id: str) -> list[str]:
    return collection.get(where={"version_id": version_id}, include=[])["ids"]


def upsert_version(collection, version: VersionInfo, chunks: Sequence[Chunk],
                   vectors: Sequence[Sequence[float]], model_name: str) -> int:
    """Store one version's chunks. Running it again replaces them: same ids, no duplicates.

    Chunks the version no longer has (it was re-chunked into fewer pieces) are removed.
    """
    if len(chunks) != len(vectors):
        raise IndexingError(f"{len(chunks)} chunks but {len(vectors)} vectors for {version.file_name}.")
    ids = [chunk_id(version.version_id, chunk.ordinal) for chunk in chunks]
    try:
        stale = set(version_ids(collection, version.version_id)) - set(ids)
        if stale:
            collection.delete(ids=sorted(stale))
        if ids:
            collection.upsert(
                ids=ids,
                embeddings=[list(vector) for vector in vectors],
                documents=[chunk.text for chunk in chunks],
                metadatas=[chunk_metadata(version, chunk, model_name) for chunk in chunks],
            )
    except IndexingError:
        raise
    except Exception as error:  # Chroma's own errors: bad values, disk problems ...
        raise IndexingError(f"ChromaDB refused the chunks of {version.file_name}: {error}") from error
    logger.info("%s: %d chunks in Chroma (%d stale removed)", version.file_name, len(ids), len(stale))
    return len(ids)


def delete_version(collection, version_id: str) -> int:
    """Remove all chunks of one version. Returns how many were removed."""
    ids = version_ids(collection, version_id)
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def set_current(collection, document_key: str, version_id: str) -> int:
    """Make version_id the current version of its document; all its other versions stop being current.

    Call it only after the version is fully indexed (Phase 5's mark_indexed rule).
    Returns the number of chunks whose label changed.
    """
    found = collection.get(where={"document_key": document_key}, include=["metadatas"])
    changes = [(id_, metadata["version_id"] == version_id) for id_, metadata in zip(found["ids"], found["metadatas"])
               if metadata.get("is_current") != (metadata["version_id"] == version_id)]
    if changes:
        collection.update(ids=[id_ for id_, _ in changes],
                          metadatas=[{"is_current": current} for _, current in changes])  # merged into the rest
    return len(changes)


def search(collection, vector: Sequence[float], limit: int = 5, where: dict[str, Any] | None = None) -> list[Hit]:
    """The `limit` chunks closest to `vector`, best first. `where` filters on metadata,
    e.g. {"is_current": True} or {"$and": [{"regulator": "EMIR"}, {"is_current": True}]}."""
    result = collection.query(query_embeddings=[list(vector)], n_results=limit, where=where,
                              include=["documents", "metadatas", "distances"])
    return [Hit(id=id_, score=1.0 - distance, text=text, metadata=metadata)
            for id_, text, metadata, distance in zip(result["ids"][0], result["documents"][0],
                                                     result["metadatas"][0], result["distances"][0])]


if __name__ == "__main__":
    from app.indexing.embedder import embed_chunks, get_embedder
    from app.logging_config import configure_logging
    from app.parsing.base import extract
    from app.segmentation.segmenter import segment

    configure_logging()
    logging.getLogger().setLevel(logging.ERROR)
    embedder = get_embedder()
    collection = open_collection(embedder.model_name)
    print(f"Collection '{collection.name}' in {get_settings().chroma_dir}: {collection.count()} chunks before")

    cache: dict[str, list[float]] = {}
    latest: dict[str, VersionInfo] = {}
    for version, path in demo_versions():
        chunks = segment(extract(path, path.suffix.lstrip(".").lower())).chunks
        vectors = embed_chunks(embedder, chunks, version.regulator, cache)
        upsert_version(collection, version, chunks, vectors, embedder.model_name)
        latest[version.document_key] = version  # files are in version order, so the last one wins
    for key, version in latest.items():
        set_current(collection, key, version.version_id)
    current = len(collection.get(where={"is_current": True}, include=[])["ids"])
    print(f"After indexing: {collection.count()} chunks, {current} of them in current versions")

    def show(question: str, where: dict[str, Any] | None = None, limit: int = 3) -> None:
        print(f"\n{question}" + (f"   where {where}" if where else ""))
        for hit in search(collection, embedder.embed([question])[0], limit, where):
            m = hit.metadata
            print(f"  {hit.score:.3f}  {m['file_name']:<24} {m['clause_ref']:<12} {describe_location(m):<11} "
                  f"current={m['is_current']}")

    show("clearing house identifier")
    show("clearing house identifier", {"is_current": True})
    show("How fast must swaps be reported?", {"regulator": "CFTC"})
