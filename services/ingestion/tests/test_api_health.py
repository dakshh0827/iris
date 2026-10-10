"""Tests for /readyz and for answers when nothing is configured (Phase 9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_engine


@pytest.fixture
def bare_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The app with no database, an unreachable Elasticsearch and an empty data folder."""
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("IRIS_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("IRIS_ELASTICSEARCH_URL", "http://127.0.0.1:9")  # nothing listens on port 9
    get_settings.cache_clear()
    get_engine.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    get_engine.cache_clear()


def test_readyz_says_what_is_missing(bare_client: TestClient) -> None:
    response = bare_client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"].startswith("DatabaseNotConfigured")
    assert body["checks"]["elasticsearch"].startswith("IndexingError")
    assert body["checks"]["embedding_model"].startswith("FileNotFoundError")
    assert body["checks"]["chroma"] == "ok"  # Chroma is a folder: it can always be opened


def test_healthz_still_works_without_any_service(bare_client: TestClient) -> None:
    assert bare_client.get("/healthz").status_code == 200


def test_documents_without_a_database_is_503_problem_json(bare_client: TestClient) -> None:
    response = bare_client.get("/api/v1/documents")
    assert response.status_code == 503
    assert response.json()["type"] == "urn:reg-iris:error:service_unavailable"
    assert "IRIS_DATABASE_URL" in response.json()["detail"]
