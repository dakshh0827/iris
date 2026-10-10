"""Turn chunks into 384-number vectors with all-MiniLM-L6-v2 (Phase 6).

A vector is a list of numbers that stands for the meaning of a text. Texts
with similar meaning get vectors that point in a similar direction, so
"How fast must swaps be reported?" lands near "within 15 minutes of execution"
even though they share almost no words. Phase 7 stores the vectors in ChromaDB.

  embedding_text()  what we embed for a chunk: "regulator | heading | text"
  MiniLMEmbedder    the real model, loaded once from models/ (never downloaded here)
  HashingEmbedder   a tiny fake with the same interface, for fast unit tests
  embed_chunks()    vectors for a list of chunks, in the same order, reusing
                    vectors for text it has seen before (unchanged clauses
                    between v1 and v2 are embedded only once)

Every vector is normalised: its length is 1.0. Then the dot product of two
vectors is their cosine similarity (1.0 = same meaning, near 0 = unrelated).

Try it from services/ingestion:   python -m app.indexing.embedder
(needs the model: python -m app.indexing.download_model)
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.errors import IngestionError
from app.segmentation.models import Chunk

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSION = 384  # all-MiniLM-L6-v2 gives 384 numbers per text


class EmbeddingError(IngestionError):
    """The embedding model could not be loaded or failed on some text."""

    code = "embedding_failed"
    title = "Embedding failed"
    status_code = 500


class ModelNotDownloaded(EmbeddingError):
    """The model folder is missing: run the download script once."""

    code = "embedding_model_missing"
    title = "Embedding model not downloaded"
    status_code = 503


class Embedder(Protocol):
    """What every embedder offers. MiniLMEmbedder and HashingEmbedder both fit."""

    model_name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One normalised vector per text, in the same order."""
        ...


def embedding_text(chunk: Chunk, regulator: str | None = None) -> str:
    """The text we embed for a chunk: "{regulator} | {heading} | {text}".

    The regulator and heading give the model context ("EMIR", "Paragraph 7").
    Parts that are empty are left out. Chunk text usually starts with its own
    heading line, and then the heading is not repeated. A chunk pasted in from
    another document (foreign_content) gets no regulator: the document's
    regulator would be wrong for it, and we don't guess the right one.
    """
    parts = []
    if regulator and not chunk.foreign_content:
        parts.append(regulator)
    if chunk.heading and not chunk.text.startswith(chunk.heading):
        parts.append(chunk.heading)
    parts.append(chunk.text)
    return " | ".join(parts)


def text_key(text: str) -> str:
    """SHA-256 of the exact text we embed: same key, same vector."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. For normalised vectors this is just the dot product."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def vector_length(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


class MiniLMEmbedder:
    """The real model, loaded from a local folder. Create it once and reuse it (get_embedder)."""

    def __init__(self, model_dir: Path, model_name: str, batch_size: int = 64) -> None:
        if not (model_dir / "modules.json").is_file():
            raise ModelNotDownloaded(
                f"The embedding model is not in {model_dir}. Download it once with: "
                "python -m app.indexing.download_model", model_dir=str(model_dir))
        # Never go online while the service runs: everything must come from model_dir.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            # Imported here, not at the top: loading PyTorch takes seconds, and most code never needs it.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(str(model_dir), device="cpu", local_files_only=True)
        except Exception as error:  # a broken or half-downloaded folder, a missing library ...
            raise EmbeddingError(f"Could not load the embedding model from {model_dir}: {error}",
                                 model_dir=str(model_dir)) from error
        self.model_name = model_name
        self.batch_size = batch_size
        self.dimension = int(self._model.get_embedding_dimension())
        logger.info("Loaded %s (%d dimensions) from %s", model_name, self.dimension, model_dir)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = self._model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True,
                                         convert_to_numpy=True, show_progress_bar=False)
        except Exception as error:
            raise EmbeddingError(f"The embedding model failed on {len(texts)} texts: {error}") from error
        return vectors.tolist()


class HashingEmbedder:
    """A fake embedder for unit tests: fast, no model, same interface.

    Each word adds 1 to one of `dimension` slots (picked by hashing the word),
    then the vector is normalised. Texts sharing words get similar vectors, so
    tests can check ordering and similarity without loading PyTorch. It does
    not understand meaning; never use it for real search.
    """

    model_name = "hashing-fake"

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension
        self.calls: list[int] = []  # how many texts each embed() call got (tests check batching/reuse)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in re.findall(r"\w+", text.lower()):
                slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % self.dimension
                vector[slot] += 1.0
            length = vector_length(vector) or 1.0
            vectors.append([x / length for x in vector])
        return vectors


def embed_chunks(embedder: Embedder, chunks: Sequence[Chunk], regulator: str | None = None,
                 cache: dict[str, list[float]] | None = None) -> list[list[float]]:
    """One vector per chunk, in the same order.

    Texts already in `cache` (keyed by text_key) are not embedded again; new
    ones are embedded in one call and added to it. Pass the same dict for
    several documents to reuse vectors of unchanged clauses across versions.
    """
    cache = {} if cache is None else cache
    texts = [embedding_text(chunk, regulator) for chunk in chunks]
    keys = [text_key(text) for text in texts]
    missing = {key: text for key, text in zip(keys, texts) if key not in cache}  # also drops repeats
    if missing:
        vectors = embedder.embed(list(missing.values()))
        if len(vectors) != len(missing) or any(len(v) != embedder.dimension for v in vectors):
            raise EmbeddingError(f"Expected {len(missing)} vectors of {embedder.dimension} numbers "
                                 f"from {embedder.model_name}.")
        cache.update(zip(missing, vectors))
    return [cache[key] for key in keys]


@lru_cache
def get_embedder() -> MiniLMEmbedder:
    """The one shared model for this process: loading it per request would be slow."""
    settings = get_settings()
    return MiniLMEmbedder(settings.embedding_model_dir, settings.embedding_model, settings.embedding_batch_size)


if __name__ == "__main__":
    import time

    from app.logging_config import configure_logging
    from app.parsing.base import extract
    from app.segmentation.segmenter import segment
    from app.versioning.metadata_loader import load_metadata_csv

    configure_logging()
    logging.getLogger().setLevel(logging.ERROR)
    started = time.perf_counter()
    embedder = get_embedder()
    print(f"Model: {embedder.model_name}, {embedder.dimension} dimensions, "
          f"loaded in {time.perf_counter() - started:.1f} s")

    settings = get_settings()
    csv_rows = load_metadata_csv()
    cache: dict[str, list[float]] = {}
    everything: list[tuple[str, Chunk, list[float]]] = []
    started = time.perf_counter()
    for name in sorted(csv_rows, key=lambda n: csv_rows[n].source_doc_id or 0):
        path = settings.regulations_dir / name
        chunks = segment(extract(path, path.suffix.lstrip(".").lower())).chunks
        before = len(cache)
        vectors = embed_chunks(embedder, chunks, csv_rows[name].regulator, cache)
        lengths = [vector_length(v) for v in vectors]
        print(f"  {name:<30} {len(vectors):>2} vectors  new {len(cache) - before:>2}  "
              f"size {len(vectors[0])}  length {min(lengths):.4f}-{max(lengths):.4f}")
        everything += [(name, chunk, vector) for chunk, vector in zip(chunks, vectors)]
    print(f"{len(everything)} chunks, {len(cache)} distinct texts embedded in {time.perf_counter() - started:.1f} s")

    for question in ("How fast must swaps be reported?", "Which identifier must a clearing house use?"):
        query = embedder.embed([question])[0]
        best = sorted(everything, key=lambda item: cosine(query, item[2]), reverse=True)[:3]
        print(f"\n{question}")
        for name, chunk, vector in best:
            print(f"  {cosine(query, vector):.3f}  {name:<26} {chunk.clause_ref:<13} {chunk.text[:60]!r}")
