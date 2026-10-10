"""Download the embedding model once into models/ (Phase 6).

The only step that needs the internet. Afterwards the service loads the model
from the folder and works offline. The folder is in .gitignore (about 90 MB).

Run from services/ingestion:   python -m app.indexing.download_model
"""

from __future__ import annotations

import shutil
import sys

from app.config import get_settings
from app.indexing.embedder import EMBEDDING_DIMENSION, MiniLMEmbedder


def download_model() -> None:
    settings = get_settings()
    target = settings.embedding_model_dir
    if (target / "modules.json").is_file():
        print(f"Already downloaded: {target}")
    else:
        from sentence_transformers import SentenceTransformer

        print(f"Downloading {settings.embedding_model} (about 90 MB) ...")
        try:
            model = SentenceTransformer(settings.embedding_model, device="cpu")
        except Exception as error:  # no internet, a proxy, a typo in IRIS_EMBEDDING_MODEL ...
            sys.exit(f"Download failed: {type(error).__name__}: {error}\n"
                     "Check your internet connection (huggingface.co must be reachable) and try again.")
        partial = target.with_name(target.name + ".partial")  # never leave a half-written model folder
        shutil.rmtree(partial, ignore_errors=True)
        model.save(str(partial))
        partial.rename(target)
        print(f"Saved to {target}")

    # Check it offline, the way the service will load it.
    embedder = MiniLMEmbedder(target, settings.embedding_model)
    vector = embedder.embed(["Trades must be reported within 15 minutes."])[0]
    assert embedder.dimension == len(vector) == EMBEDDING_DIMENSION, embedder.dimension
    print(f"Check OK: {len(vector)} numbers per text, loaded from {target}")


if __name__ == "__main__":
    download_model()
