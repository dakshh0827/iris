"""Tests for the /healthz endpoint (Phase 1, Step 6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "reg-iris-ingestion",
        "version": __version__,
        "environment": "dev",
    }


def test_healthz_reports_environment_from_settings(client: TestClient, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("IRIS_ENVIRONMENT", "test")
    get_settings.cache_clear()  # make the next get_settings() read the new value

    response = client.get("/healthz")

    assert response.json()["environment"] == "test"


def test_unknown_path_returns_404(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_openapi_spec_lists_healthz(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    assert spec["info"]["title"] == "Reg IRIS Ingestion Service"
    assert "/healthz" in spec["paths"]