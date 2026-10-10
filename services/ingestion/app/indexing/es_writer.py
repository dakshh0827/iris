"""Index chunks in Elasticsearch for keyword search (Phase 8).

Vectors (Phase 7) find text by meaning. Keyword search finds exact terms that
vectors blur: "Counterparty_LEI", "LEI", "Article 28", "ISIN". It also gives
highlights (the matching words in context) and fast filters by regulator,
date or current version.

  ensure_index()    create the index from es_mapping.json if missing (safe to call every time)
  index_version()   put one version's chunks in (again = same result, never duplicates)
  delete_version()  remove one version's chunks
  set_current()     mark which version of a document is the current one
  search()          keyword search with filters and highlights

How words are matched (es_mapping.json, analyzer "reg_en"):
  - "_" counts as a space, so Counterparty_LEI is found by "LEI" and by "counterparty LEI";
  - lower case and English stemming: "reporting", "reported", "reports" all match "report";
  - when searching, synonyms are added: "LEI" also finds "legal entity identifier", etc.

Writes go to the alias "reg_paragraphs", which points to the real index
"reg_paragraphs_v1". After a mapping change we can build reg_paragraphs_v2
and move the alias, without the search side noticing.

Try it from services/ingestion:   python -m app.indexing.es_writer
(needs Elasticsearch running: docker compose up -d elasticsearch)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from elasticsearch import ApiError, Elasticsearch, TransportError, helpers

from app.config import get_settings
from app.indexing.records import IndexingError, VersionInfo, chunk_id, demo_versions, describe_location
from app.segmentation.models import Chunk

logger = logging.getLogger(__name__)

MAPPING_FILE = Path(__file__).with_name("es_mapping.json")


@dataclass(frozen=True)
class KeywordHit:
    """One keyword search result."""

    id: str
    score: float                 # BM25 relevance; only comparable within one search
    source: dict[str, Any]       # the stored fields (file_name, clause_ref, page_start ...)
    highlights: list[str]        # bits of text with the matches marked **like this**


def get_client(url: str | None = None) -> Elasticsearch:
    """A client for the configured Elasticsearch. It checks the connection, so a stopped server fails here."""
    settings = get_settings()
    api_key = settings.elasticsearch_api_key.get_secret_value() if settings.elasticsearch_api_key else None
    client = Elasticsearch(url or settings.elasticsearch_url, api_key=api_key, request_timeout=30)
    try:
        client.info()
    except (ApiError, TransportError) as error:
        raise IndexingError(f"Elasticsearch is not reachable at {url or settings.elasticsearch_url}: {error}. "
                            "Start it with: docker compose up -d elasticsearch") from error
    return client


def ensure_index(client: Elasticsearch, alias: str | None = None) -> str:
    """Create "{alias}_v1" with our mapping and point the alias at it, unless the alias exists.

    Returns the real index name. Calling it again changes nothing.
    """
    alias = alias or get_settings().es_index
    if client.indices.exists_alias(name=alias):
        return next(iter(client.indices.get_alias(name=alias)))
    index = f"{alias}_v1"
    mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    client.indices.create(index=index, settings=mapping["settings"], mappings=mapping["mappings"],
                          aliases={alias: {}}, wait_for_active_shards="1")
    logger.info("Created Elasticsearch index %s with alias %s", index, alias)
    return index


def chunk_document(version: VersionInfo, chunk: Chunk) -> dict[str, Any]:
    """The fields stored for one chunk. Empty values are left out."""
    values: dict[str, Any] = {
        "chunk_id": chunk_id(version.version_id, chunk.ordinal),
        "version_id": version.version_id,
        "document_key": version.document_key,
        "file_name": version.file_name,
        "version_number": version.version_number,
        "version_label": version.version_label,
        "is_current": version.is_current,
        "regulator": None if chunk.foreign_content else version.regulator,  # pasted text: not this regulator
        "jurisdiction": version.jurisdiction,
        "category": version.category,
        "effective_date": version.effective_date.isoformat() if version.effective_date else None,
        "chunk_index": chunk.ordinal,
        "clause_ref": chunk.clause_ref,
        "heading": chunk.heading,
        "text": chunk.text,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "anchor": chunk.anchor,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "foreign_content": chunk.foreign_content,
        "embedded_source": chunk.embedded_source,
        "text_hash": chunk.text_hash,
    }
    return {key: value for key, value in values.items() if value is not None}


def index_version(client: Elasticsearch, alias: str, version: VersionInfo, chunks: Sequence[Chunk]) -> int:
    """Store one version's chunks, using chunk_id as the document id, so indexing again replaces them.

    Chunks the version no longer has (it was re-chunked into fewer pieces) are removed.
    The index is refreshed before returning, so the chunks are searchable at once.
    """
    documents = [chunk_document(version, chunk) for chunk in chunks]
    ids = [document["chunk_id"] for document in documents]
    try:
        client.delete_by_query(index=alias, refresh=True, query={"bool": {
            "filter": [{"term": {"version_id": version.version_id}}],
            "must_not": [{"ids": {"values": ids}}],
        }})
        if documents:
            helpers.bulk(client, ({"_index": alias, "_id": d["chunk_id"], "_source": d} for d in documents),
                         refresh="wait_for")
    except helpers.BulkIndexError as error:
        first = error.errors[0] if error.errors else {}
        raise IndexingError(f"Elasticsearch refused {len(error.errors)} chunks of {version.file_name}: {first}") \
            from error
    except (ApiError, TransportError) as error:
        raise IndexingError(f"Elasticsearch failed on {version.file_name}: {error}") from error
    logger.info("%s: %d chunks in Elasticsearch", version.file_name, len(documents))
    return len(documents)


def delete_version(client: Elasticsearch, alias: str, version_id: str) -> int:
    """Remove all chunks of one version. Returns how many were removed."""
    result = client.delete_by_query(index=alias, refresh=True, query={"term": {"version_id": version_id}})
    return result["deleted"]


def set_current(client: Elasticsearch, alias: str, document_key: str, version_id: str) -> int:
    """Make version_id the current version of its document; its other versions stop being current.

    Call it only after the version is fully indexed. Returns the number of chunks changed.
    """
    changed = 0
    for is_current, versions in ((False, {"must_not": [{"term": {"version_id": version_id}}]}),
                                 (True, {"filter": [{"term": {"version_id": version_id}}]})):
        query = {"bool": {
            "filter": [{"term": {"document_key": document_key}},
                       {"term": {"is_current": not is_current}}] + versions.get("filter", []),
            "must_not": versions.get("must_not", []),
        }}
        result = client.update_by_query(index=alias, refresh=True, conflicts="proceed", query=query, script={
            "source": "ctx._source.is_current = params.value", "params": {"value": is_current}})
        changed += result["updated"]
    return changed


def search(client: Elasticsearch, alias: str, text: str, limit: int = 5, regulator: str | None = None,
           current_only: bool = False, effective_from: date | None = None,
           effective_to: date | None = None) -> list[KeywordHit]:
    """Keyword search over chunk text, heading and clause, best first, with highlights."""
    filters: list[dict[str, Any]] = []
    if regulator:
        filters.append({"term": {"regulator": regulator}})
    if current_only:
        filters.append({"term": {"is_current": True}})
    if effective_from or effective_to:
        dates = {"gte": effective_from.isoformat() if effective_from else None,
                 "lte": effective_to.isoformat() if effective_to else None}
        filters.append({"range": {"effective_date": {k: v for k, v in dates.items() if v}}})
    response = client.search(
        index=alias, size=limit,
        query={"bool": {"must": [{"multi_match": {"query": text, "fields": ["text", "heading^2", "clause_ref.text^2"]}}],
                        "filter": filters}},
        highlight={"fields": {"text": {"fragment_size": 120, "number_of_fragments": 2}},
                   "pre_tags": ["**"], "post_tags": ["**"]},
    )
    return [KeywordHit(id=hit["_id"], score=hit["_score"], source=hit["_source"],
                       highlights=[" ".join(part.split()) for part in hit.get("highlight", {}).get("text", [])])
            for hit in response["hits"]["hits"]]  # highlights on one line each: easier to show


if __name__ == "__main__":
    from app.logging_config import configure_logging
    from app.parsing.base import extract
    from app.segmentation.segmenter import segment

    configure_logging()
    logging.getLogger().setLevel(logging.ERROR)
    settings = get_settings()
    client = get_client()
    alias = settings.es_index
    index = ensure_index(client, alias)
    print(f"Elasticsearch {client.info()['version']['number']}, index '{index}' (alias '{alias}'): "
          f"{client.count(index=alias)['count']} chunks before")

    latest: dict[str, VersionInfo] = {}
    for version, path in demo_versions():
        index_version(client, alias, version, segment(extract(path, path.suffix.lstrip(".").lower())).chunks)
        latest[version.document_key] = version  # files are in version order, so the last one wins
    for key, version in latest.items():
        set_current(client, alias, key, version.version_id)
    current = client.count(index=alias, query={"term": {"is_current": True}})["count"]
    print(f"After indexing: {client.count(index=alias)['count']} chunks, {current} of them in current versions")

    def show(text: str, **filters: Any) -> None:
        print(f"\n\"{text}\"   " + ", ".join(f"{name}={value}" for name, value in filters.items()))
        for hit in search(client, alias, text, limit=3, **filters):
            s = hit.source
            print(f"  {hit.score:5.2f}  {s['file_name']:<24} {s['clause_ref']:<12} {describe_location(s):<11} "
                  f"current={s['is_current']}")
            if hit.highlights:
                print(f"         {hit.highlights[0]}")

    show("Counterparty_LEI", regulator="MiFID II", current_only=True)
    show("legal entity identifier", regulator="SEC")
    show("Clearing_House", effective_from=date(2021, 1, 1), effective_to=date(2021, 12, 31))
