"""Tests for app/indexing/embedder.py (Phase 6).

Most tests use HashingEmbedder, a fake with the same interface, so they run in
milliseconds. The tests marked `slow` load the real all-MiniLM-L6-v2 from
models/; they are skipped until you have run python -m app.indexing.download_model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.indexing.embedder import (
    EMBEDDING_DIMENSION, EmbeddingError, HashingEmbedder, MiniLMEmbedder, ModelNotDownloaded, cosine,
    embed_chunks, embedding_text, get_embedder, text_key, vector_length,
)
from app.parsing.base import extract
from app.segmentation.models import Chunk
from app.segmentation.segmenter import segment

# Read once at import time, before the autouse fixture in conftest.py hides .env from the tests.
MODEL_DIR = Settings().embedding_model_dir
needs_model = pytest.mark.skipif(not (MODEL_DIR / "modules.json").is_file(),
                                 reason="embedding model not downloaded (python -m app.indexing.download_model)")
REGULATIONS = get_settings().regulations_dir


def chunk(text: str, heading: str | None = "Paragraph 1", ordinal: int = 0, foreign: bool = False) -> Chunk:
    return Chunk(ordinal=ordinal, clause_ref="Paragraph 1", heading=heading, text=text, text_hash=text_key(text),
                 page_start=1, page_end=1, foreign_content=foreign,
                 embedded_source="Other.pdf" if foreign else None)


# --- What we embed -----------------------------------------------------------

def test_embedding_text_adds_the_regulator_but_not_a_repeated_heading() -> None:
    assert embedding_text(chunk("Paragraph 1. Report trades."), "EMIR") == "EMIR | Paragraph 1. Report trades."


def test_embedding_text_adds_a_heading_the_text_does_not_start_with() -> None:
    assert embedding_text(chunk("Trades within 30 minutes.", heading="4. Timing"), "FCA") == \
        "FCA | 4. Timing | Trades within 30 minutes."


def test_embedding_text_without_regulator_or_heading() -> None:
    assert embedding_text(chunk("Q1: What?", heading=None)) == "Q1: What?"


def test_foreign_chunks_do_not_get_the_documents_regulator() -> None:
    # Dodd-Frank text pasted into EMIR_2021_v2.pdf must not be labelled "EMIR".
    assert embedding_text(chunk("Paragraph 1. Swaps.", foreign=True), "EMIR") == "Paragraph 1. Swaps."


# --- The fake embedder (and the rules every embedder follows) ---------------

def test_fake_vectors_are_normalised_and_repeatable() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed(["Trades must be reported", "Keep records for five years"])
    assert [len(v) for v in first] == [EMBEDDING_DIMENSION, EMBEDDING_DIMENSION]
    assert all(abs(vector_length(v) - 1.0) < 1e-9 for v in first)
    assert embedder.embed(["Trades must be reported"])[0] == first[0]


def test_similar_texts_score_higher_than_unrelated_ones() -> None:
    query, related, unrelated = HashingEmbedder().embed(
        ["report trades within 15 minutes", "trades reported within 15 minutes", "annual party in december"])
    assert cosine(query, related) > cosine(query, unrelated)
    assert cosine(query, query) == pytest.approx(1.0)


def test_embed_chunks_keeps_order_and_embeds_repeats_once() -> None:
    embedder = HashingEmbedder()
    chunks = [chunk("Paragraph 1. A.", ordinal=0), chunk("Paragraph 1. B.", ordinal=1),
              chunk("Paragraph 1. A.", ordinal=2)]
    vectors = embed_chunks(embedder, chunks, "EMIR")
    assert embedder.calls == [2]  # 3 chunks, 2 different texts, one call
    assert vectors[0] == vectors[2] != vectors[1]
    assert vectors == embedder.embed([embedding_text(c, "EMIR") for c in chunks])


def test_cache_reuses_vectors_across_documents() -> None:
    embedder = HashingEmbedder()
    cache: dict[str, list[float]] = {}
    embed_chunks(embedder, [chunk("Paragraph 1. Same."), chunk("Paragraph 1. Old.", ordinal=1)], "MiFID II", cache)
    embed_chunks(embedder, [chunk("Paragraph 1. Same."), chunk("Paragraph 1. New.", ordinal=1)], "MiFID II", cache)
    assert embedder.calls == [2, 1]  # v2 only embeds its changed clause
    assert len(cache) == 3


def test_no_chunks_means_no_model_call() -> None:
    embedder = HashingEmbedder()
    assert embed_chunks(embedder, []) == []
    assert embedder.calls == []


def test_wrong_vector_size_is_an_embedding_error() -> None:
    class Broken(HashingEmbedder):
        def embed(self, texts):  # type: ignore[no-untyped-def]
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(EmbeddingError, match="384 numbers"):
        embed_chunks(Broken(), [chunk("Paragraph 1. A.")])


def test_all_supplied_documents_embed_with_the_fake(tmp_path: Path) -> None:
    embedder = HashingEmbedder()
    cache: dict[str, list[float]] = {}
    total = 0
    for path in sorted(REGULATIONS.iterdir()):
        chunks = segment(extract(path, path.suffix.lstrip(".").lower())).chunks
        total += len(embed_chunks(embedder, chunks, None, cache))
    assert total == 78  # the Phase 4 chunk counts: 10+7+6+14+6+7+6+7+7+8
    assert len(cache) < total  # unchanged clauses between versions were embedded once


# --- Loading the real model --------------------------------------------------

def test_missing_model_folder_says_how_to_download(tmp_path: Path) -> None:
    with pytest.raises(ModelNotDownloaded, match="python -m app.indexing.download_model") as caught:
        MiniLMEmbedder(tmp_path / "nothing-here", "sentence-transformers/all-MiniLM-L6-v2")
    assert caught.value.status_code == 503
    assert isinstance(caught.value, EmbeddingError)


def test_broken_model_folder_is_an_embedding_error(tmp_path: Path) -> None:
    (tmp_path / "modules.json").write_text(json.dumps([{"idx": 0, "name": "0", "path": "missing",
                                                        "type": "sentence_transformers.models.Transformer"}]))
    with pytest.raises(EmbeddingError, match="Could not load the embedding model"):
        MiniLMEmbedder(tmp_path, "sentence-transformers/all-MiniLM-L6-v2")


def test_get_embedder_uses_the_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IRIS_MODELS_DIR", str(tmp_path))
    get_embedder.cache_clear()
    try:
        with pytest.raises(ModelNotDownloaded) as caught:
            get_embedder()
        assert caught.value.details["model_dir"] == str(tmp_path / "sentence-transformers__all-MiniLM-L6-v2")
    finally:
        get_embedder.cache_clear()


@pytest.mark.slow
@needs_model
def test_real_model_gives_384_normalised_repeatable_vectors() -> None:
    embedder = MiniLMEmbedder(MODEL_DIR, "sentence-transformers/all-MiniLM-L6-v2")
    assert embedder.dimension == 384
    first = embedder.embed(["Trades must be reported within 15 minutes.", "Keep records for five years."])
    assert all(abs(vector_length(v) - 1.0) < 1e-5 for v in first)
    again = embedder.embed(["Trades must be reported within 15 minutes."])[0]
    assert cosine(first[0], again) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.slow
@needs_model
def test_real_model_understands_meaning() -> None:
    embedder = MiniLMEmbedder(MODEL_DIR, "sentence-transformers/all-MiniLM-L6-v2")
    query, related, unrelated = embedder.embed([
        "How fast must swaps be reported?",
        "Swap transactions must be reported to a swap data repository within 15 minutes of execution.",
        "The annual staff party will be held in December.",
    ])
    assert cosine(query, related) > cosine(query, unrelated) + 0.2
