"""Tests for app/indexing/es_writer.py (Phase 8).

The first tests need nothing. The others talk to a real Elasticsearch and are
skipped unless IRIS_TEST_ELASTICSEARCH_URL is set (in .env, e.g.
http://localhost:9200). Each of those tests creates its own "test_..." index
and deletes it afterwards, so your real reg_paragraphs index is never touched.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import date

import pytest

from app.config import Settings, get_settings
from app.indexing.es_writer import (
    MAPPING_FILE, chunk_document, delete_version, ensure_index, get_client, index_version, search, set_current,
)
from app.indexing.records import IndexingError, VersionInfo
from app.parsing.base import extract
from app.segmentation.models import Chunk
from app.segmentation.segmenter import segment

# Read once at import time, before the autouse fixture in conftest.py hides .env from the tests.
TEST_ES_URL = Settings().test_elasticsearch_url
needs_es = pytest.mark.skipif(TEST_ES_URL is None, reason="IRIS_TEST_ELASTICSEARCH_URL is not set")

REGULATIONS = get_settings().regulations_dir
MIFID_V1 = VersionInfo(version_id="aaaaaaaa-0000-0000-0000-000000000001", document_key="MiFID_II_2023",
                       file_name="MiFID_II_2023_v1.pdf", version_number=1, regulator="MiFID II", jurisdiction="EU",
                       category="Trade reporting", effective_date=date(2023, 7, 1), version_label="v1.0")
MIFID_V2 = VersionInfo(version_id="aaaaaaaa-0000-0000-0000-000000000002", document_key="MiFID_II_2023",
                       file_name="MiFID_II_2023_v2.pdf", version_number=2, regulator="MiFID II", jurisdiction="EU",
                       category="Trade reporting", effective_date=date(2023, 7, 1), version_label="v2.0")
EMIR_V2 = VersionInfo(version_id="bbbbbbbb-0000-0000-0000-000000000002", document_key="EMIR_2021",
                      file_name="EMIR_2021_v2.pdf", version_number=2, regulator="EMIR", jurisdiction="EU",
                      effective_date=date(2021, 1, 15))


def chunks_of(file_name: str) -> list[Chunk]:
    path = REGULATIONS / file_name
    return segment(extract(path, path.suffix.lstrip(".").lower())).chunks


# --- No Elasticsearch needed -------------------------------------------------

def test_documents_have_no_empty_fields_and_iso_dates() -> None:
    document = chunk_document(MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf")[2])
    assert None not in document.values()
    assert document["chunk_id"] == "aaaaaaaa-0000-0000-0000-000000000001:0002"
    assert (document["effective_date"], document["page_start"], document["regulator"]) == ("2023-07-01", 1, "MiFID II")
    assert "anchor" not in document and "line_start" not in document


def test_pasted_dodd_frank_text_is_not_labelled_emir() -> None:
    documents = [chunk_document(EMIR_V2, chunk) for chunk in chunks_of("EMIR_2021_v2.pdf")]
    foreign = [d for d in documents if d["foreign_content"]]
    assert len(foreign) == 7 and all("regulator" not in d for d in foreign)


def test_every_document_field_is_in_the_mapping() -> None:
    # The mapping is "strict": a field it doesn't know would be refused at index time.
    mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    known = set(mapping["mappings"]["properties"])
    for file_name in ("MiFID_II_2023_v1.pdf", "FCA_Guidance_2023.html", "CFTC_2022.txt", "EMIR_2021_v2.pdf"):
        for chunk in chunks_of(file_name):
            assert set(chunk_document(MIFID_V1, chunk)) <= known


def test_unreachable_server_is_an_indexing_error() -> None:
    with pytest.raises(IndexingError, match="not reachable at http://127.0.0.1:9"):
        get_client("http://127.0.0.1:9")  # port 9: nothing listens there


# --- With a real Elasticsearch ------------------------------------------------

@pytest.fixture
def es() -> Iterator[tuple[object, str]]:
    client = get_client(TEST_ES_URL)
    alias = f"test_reg_paragraphs_{uuid.uuid4().hex[:8]}"
    ensure_index(client, alias)
    yield client, alias
    client.indices.delete(index=f"{alias}_v1", ignore_unavailable=True)


@needs_es
def test_ensure_index_is_safe_to_call_again(es) -> None:
    client, alias = es
    assert ensure_index(client, alias) == f"{alias}_v1"
    assert list(client.indices.get_alias(name=alias)) == [f"{alias}_v1"]


@needs_es
def test_indexing_twice_gives_no_duplicates(es) -> None:
    client, alias = es
    chunks = chunks_of("MiFID_II_2023_v1.pdf")
    assert index_version(client, alias, MIFID_V1, chunks) == 6
    assert index_version(client, alias, MIFID_V1, chunks) == 6
    assert client.count(index=alias)["count"] == 6


@needs_es
def test_rechunking_into_fewer_chunks_removes_the_extra_ones(es) -> None:
    client, alias = es
    chunks = chunks_of("MiFID_II_2023_v1.pdf")
    index_version(client, alias, MIFID_V1, chunks)
    index_version(client, alias, MIFID_V1, chunks[:4])
    assert client.count(index=alias)["count"] == 4


@needs_es
def test_field_name_search_finds_the_lei_paragraph_with_its_page(es) -> None:
    client, alias = es
    index_version(client, alias, MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf"))
    best = search(client, alias, "Counterparty_LEI")[0]
    assert (best.source["clause_ref"], best.source["page_start"]) == ("Paragraph 3", 1)
    assert "**LEI**" in best.highlights[0]


@needs_es
def test_lei_and_its_synonym_find_counterparty_lei(es) -> None:
    client, alias = es
    index_version(client, alias, MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf"))
    for text in ("LEI", "legal entity identifier"):
        assert search(client, alias, text)[0].source["clause_ref"] == "Paragraph 3", text


@needs_es
def test_stemming_matches_other_word_forms(es) -> None:
    client, alias = es
    index_version(client, alias, MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf"))
    assert search(client, alias, "reported")  # the text says "report" / "reporting"


@needs_es
def test_filters_by_regulator_date_and_current_version(es) -> None:
    client, alias = es
    index_version(client, alias, MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf"))
    index_version(client, alias, MIFID_V2, chunks_of("MiFID_II_2023_v2.pdf"))
    index_version(client, alias, EMIR_V2, chunks_of("EMIR_2021_v2.pdf"))
    assert {h.source["regulator"] for h in search(client, alias, "LEI", limit=50, regulator="MiFID II")} == {"MiFID II"}
    hits = search(client, alias, "LEI", limit=50, effective_to=date(2022, 12, 31))
    assert hits and {h.source["file_name"] for h in hits} == {"EMIR_2021_v2.pdf"}
    assert search(client, alias, "LEI", current_only=True) == []  # nothing is current yet


@needs_es
def test_set_current_and_delete_version(es) -> None:
    client, alias = es
    index_version(client, alias, MIFID_V1, chunks_of("MiFID_II_2023_v1.pdf"))
    index_version(client, alias, MIFID_V2, chunks_of("MiFID_II_2023_v2.pdf"))
    assert set_current(client, alias, "MiFID_II_2023", MIFID_V1.version_id) == 6
    assert set_current(client, alias, "MiFID_II_2023", MIFID_V2.version_id) == 13  # 6 off, 7 on
    assert set_current(client, alias, "MiFID_II_2023", MIFID_V2.version_id) == 0
    current = search(client, alias, "LEI", limit=50, current_only=True)
    assert current and {h.source["file_name"] for h in current} == {"MiFID_II_2023_v2.pdf"}
    assert delete_version(client, alias, MIFID_V1.version_id) == 6
    assert client.count(index=alias)["count"] == 7
