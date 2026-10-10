"""Shared pytest fixtures for the ingestion service tests.

pytest loads this file automatically before running any test in this folder.
A fixture is a function that prepares something a test needs; a test asks for
it simply by having a parameter with the same name.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test clean settings (autouse=True: applied to all tests).

    - Removes IRIS_* variables you may have set in your terminal, so a leftover
      IRIS_MAX_UPLOAD_SIZE_MB=10 can't change test results.
    - Ignores your personal .env file, so tests give the same result on every machine.
    - Clears the get_settings() cache before and after the test, so a value
      changed in one test never leaks into the next.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith("IRIS_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A test client that calls the app directly, without starting a real server.

    Using it in a `with` block runs the app's startup and shutdown (lifespan),
    exactly like uvicorn does.
    """
    with TestClient(create_app()) as test_client:
        yield test_client