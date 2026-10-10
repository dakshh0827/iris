"""Tests for app/indexing/chroma_writer.py (Phase 7).

Each test gets its own Chroma folder in pytest's tmp_path. Vectors come from
the fake HashingEmbedder (Phase 6), so no model download is needed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.config import get_settings
from app.indexing.chroma_writer import (
    IndexingError, VersionInfo, chunk_id, chunk_metadata, date_number, delete_version, describe_location,
    open_collection, search, set_current, upsert_version,
)
from app.indexing.embedder import HashingEmbedder, embed_chunks
from app.parsing.base import extract
from app.segmentation.models import Chunk
from app.segmentation.segmenter import segment

MODEL = HashingEmbedder.model_name
REGULATIONS = get_settings().regulations_dir
EMIR_V1 = VersionInfo(version_id="11111111-1111-1111-1111-111111111111", document_key="EMIR_2021",
                      file_name="EMIR_2021_v1.pdf", version_number=1, regulator="EMIR", jurisdiction="EU",
                      category="Trade reporting", effective_date=date(2021, 1, 15), version_label="v1.0")
EMIR_V2 = VersionInfo(version_id="22222222-2222-2222-2222-222222222222", document_key="EMIR_2021",
                      file_name="EMIR_2021_v2.pdf", version_number=2, regulator="EMIR", jurisdiction="EU")


def load(file_name: str) -> tuple[list[Chunk], list[list[float]]]:
    path = REGULATIONS / file_name
    chunks = segment(extract(path, path.suffix.lstrip(".").lower())).chunks
    return chunks, embed_chunks(HashingEmbedder(), chunks, "EMIR")


@pytest.fixture
def collection(tmp_path: Path):
    return open_collection(MODEL, path=tmp_path / "chroma", name="test_chunks")


# --- Metadata ----------------------------------------------------------------

def test_metadata_has_no_empty_values_and_dates_are_numbers() -> None:
    chunks, _ = load("EMIR_2021_v1.pdf")
    metadata = chunk_metadata(EMIR_V1, chunks[0], MODEL)
    assert None not in metadata.values()
    assert metadata["effective_date"] == 20210115 == date_number(date(2021, 1, 15))  # "15-01-21" in the CSV
    assert (metadata["page_start"], metadata["is_current"], metadata["regulator"]) == (1, False, "EMIR")
    assert "anchor" not in metadata and "line_start" not in metadata  # a PDF chunk has pages only
    assert chunk_id(EMIR_V1.version_id, 7) == "11111111-1111-1111-1111-111111111111:0007"


def test_pasted_dodd_frank_text_is_not_labelled_emir() -> None:
    chunks, _ = load("EMIR_2021_v2.pdf")
    foreign = [chunk_metadata(EMIR_V2, chunk, MODEL) for chunk in chunks if chunk.foreign_content]
    assert len(foreign) == 7
    assert all("regulator" not in m and m["embedded_source"] == "Dodd_Frank_2022.pdf" for m in foreign)


# --- Writing -----------------------------------------------------------------

def test_upsert_twice_gives_the_same_count(collection) -> None:
    chunks, vectors = load("EMIR_2021_v1.pdf")
    assert upsert_version(collection, EMIR_V1, chunks, vectors, MODEL) == 6
    assert upsert_version(collection, EMIR_V1, chunks, vectors, MODEL) == 6
    assert collection.count() == 6


def test_rechunking_into_fewer_chunks_removes_the_extra_ones(collection) -> None:
    chunks, vectors = load("EMIR_2021_v1.pdf")
    upsert_version(collection, EMIR_V1, chunks, vectors, MODEL)
    upsert_version(collection, EMIR_V1, chunks[:4], vectors[:4], MODEL)
    assert collection.count() == 4


def test_chunk_and_vector_counts_must_match(collection) -> None:
    chunks, vectors = load("EMIR_2021_v1.pdf")
    with pytest.raises(IndexingError, match="6 chunks but 5 vectors"):
        upsert_version(collection, EMIR_V1, chunks, vectors[:5], MODEL)


def test_delete_version_removes_only_that_version(collection) -> None:
    upsert_version(collection, EMIR_V1, *load("EMIR_2021_v1.pdf"), MODEL)
    upsert_version(collection, EMIR_V2, *load("EMIR_2021_v2.pdf"), MODEL)
    assert collection.count() == 20
    assert delete_version(collection, EMIR_V1.version_id) == 6
    assert collection.count() == 14
    assert delete_version(collection, EMIR_V1.version_id) == 0


def test_set_current_moves_to_the_new_version(collection) -> None:
    upsert_version(collection, EMIR_V1, *load("EMIR_2021_v1.pdf"), MODEL)
    upsert_version(collection, EMIR_V2, *load("EMIR_2021_v2.pdf"), MODEL)
    current = lambda: collection.get(where={"is_current": True}, include=["metadatas"])["metadatas"]  # noqa: E731
    assert current() == []  # stored, but nothing is current until indexing is finished
    assert set_current(collection, "EMIR_2021", EMIR_V1.version_id) == 6
    assert {m["file_name"] for m in current()} == {"EMIR_2021_v1.pdf"}
    assert set_current(collection, "EMIR_2021", EMIR_V2.version_id) == 20  # 6 off, 14 on
    assert {m["file_name"] for m in current()} == {"EMIR_2021_v2.pdf"}
    assert set_current(collection, "EMIR_2021", EMIR_V2.version_id) == 0  # already so
    # The update only changed is_current; the other labels are still there.
    assert current()[0]["regulator"] == "EMIR"


# --- Reading -----------------------------------------------------------------

def test_search_finds_the_matching_clause_with_its_page(collection) -> None:
    chunks, vectors = load("EMIR_2021_v1.pdf")
    upsert_version(collection, EMIR_V1, chunks, vectors, MODEL)
    best = search(collection, vectors[3], limit=2)
    assert best[0].id == chunk_id(EMIR_V1.version_id, 3)
    assert best[0].score == pytest.approx(1.0, abs=1e-4)
    assert best[0].text == chunks[3].text
    assert describe_location(best[0].metadata).startswith("page")
    assert best[0].score >= best[1].score


def test_search_with_a_filter(collection) -> None:
    upsert_version(collection, EMIR_V1, *load("EMIR_2021_v1.pdf"), MODEL)
    upsert_version(collection, EMIR_V2, *load("EMIR_2021_v2.pdf"), MODEL)
    set_current(collection, "EMIR_2021", EMIR_V2.version_id)
    query = HashingEmbedder().embed(["reporting obligations"])[0]
    hits = search(collection, query, limit=20, where={"is_current": True})
    assert len(hits) == 14 and all(hit.metadata["version_number"] == 2 for hit in hits)
    hits = search(collection, query, limit=20, where={"$and": [{"is_current": True}, {"foreign_content": True}]})
    assert len(hits) == 7


def test_data_is_still_there_after_reopening(tmp_path: Path) -> None:
    first = open_collection(MODEL, path=tmp_path / "chroma", name="test_chunks")
    upsert_version(first, EMIR_V1, *load("EMIR_2021_v1.pdf"), MODEL)
    again = open_collection(MODEL, path=tmp_path / "chroma", name="test_chunks")
    assert again.count() == 6


def test_another_model_cannot_use_the_same_collection(tmp_path: Path) -> None:
    open_collection(MODEL, path=tmp_path / "chroma", name="test_chunks")
    with pytest.raises(IndexingError, match="holds vectors from 'hashing-fake'"):
        open_collection("sentence-transformers/all-MiniLM-L6-v2", path=tmp_path / "chroma", name="test_chunks")


def test_html_and_txt_locations() -> None:
    assert describe_location({"anchor": "p4"}) == "#p4"
    assert describe_location({"line_start": 3, "line_end": 4}) == "lines 3-4"
    assert describe_location({"page_start": 2, "page_end": 3}) == "pages 2-3"
    assert describe_location({"heading": "Q1"}) == "Q1"
