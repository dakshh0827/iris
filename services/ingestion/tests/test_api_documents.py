"""Tests for the document and job endpoints and the pipeline behind them (Phase 9).

They use the real test database (IRIS_TEST_DATABASE_URL, skipped without it),
the fake HashingEmbedder and a MemoryIndex instead of Elasticsearch and Chroma
(tests/test_pipeline.py uses the real ones). FastAPI's TestClient runs the
background pipeline before it returns, so the result can be checked at once.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_helpers import TEST_DB_URL, MemoryIndex, empty_test_database
from app import pipeline
from app.config import get_settings
from app.db.session import get_engine
from app.indexing.embedder import HashingEmbedder
from app.main import create_app

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(TEST_DB_URL is None, reason="IRIS_TEST_DATABASE_URL is not set")]

REAL_DATA = get_settings().data_dir  # read before the fixtures point IRIS_DATA_DIR elsewhere
REGULATIONS = REAL_DATA / "regulations"


@pytest.fixture
def index() -> MemoryIndex:
    return MemoryIndex()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, index: MemoryIndex) -> Iterator[TestClient]:
    """The app on the test database, with uploads in tmp_path and fake indexes."""
    empty_test_database()
    data = tmp_path / "data"
    (data / "metadata").mkdir(parents=True)
    shutil.copy(REAL_DATA / "metadata" / "doc_metadata.csv", data / "metadata")
    monkeypatch.setenv("IRIS_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("IRIS_DATA_DIR", str(data))
    get_settings.cache_clear()
    get_engine.cache_clear()
    monkeypatch.setattr(pipeline, "get_indexers", lambda: pipeline.Indexers(HashingEmbedder(), [index]))
    with TestClient(create_app()) as test_client:
        yield test_client
    get_engine().dispose()
    get_engine.cache_clear()


def upload(client: TestClient, name: str, data: bytes | None = None, **form: str):
    data = data if data is not None else (REGULATIONS / name).read_bytes()
    return client.post("/api/v1/documents", files={"file": (name, data, "application/octet-stream")}, data=form)


def version_of(client: TestClient, response) -> dict:
    return client.get(response.json()["status_url"]).json()


# --- Upload and processing -----------------------------------------------------

def test_upload_answers_202_and_ends_indexed(client: TestClient, index: MemoryIndex) -> None:
    response = upload(client, "MiFID_II_2023_v1.pdf")
    assert response.status_code == 202
    body = response.json()
    assert (body["outcome"], body["version_number"], body["document_key"]) == ("new_document", 1, "MiFID_II_2023")
    assert body["status"] == "uploaded"  # the answer comes before the processing
    version = version_of(client, response)
    assert (version["status"], version["is_current"], version["chunk_count"]) == ("indexed", True, 6)
    assert (version["page_count"], version["version_label"], version["effective_date"]) == (2, "v1.0", "2023-07-01")
    assert [(job["stage"], job["status"]) for job in version["jobs"]] == [
        ("store", "succeeded"), ("parse", "succeeded"), ("chunk", "succeeded"),
        ("embed", "succeeded"), ("index", "succeeded")]
    assert len(index.chunks) == 6 and index.current == {"MiFID_II_2023": body["version_id"]}


def test_same_file_again_is_a_duplicate_with_200(client: TestClient) -> None:
    first = upload(client, "CFTC_2022.txt").json()
    again = upload(client, "CFTC_2022.txt")
    assert again.status_code == 200
    assert (again.json()["outcome"], again.json()["version_id"]) == ("duplicate", first["version_id"])


def test_v2_becomes_current_and_v1_stops_being_current(client: TestClient, index: MemoryIndex) -> None:
    v1 = upload(client, "MiFID_II_2023_v1.pdf")
    v2 = upload(client, "MiFID_II_2023_v2.pdf")
    assert v2.json()["outcome"] == "new_version" and v2.json()["version_number"] == 2
    assert version_of(client, v1)["is_current"] is False
    assert version_of(client, v2)["is_current"] is True
    assert index.current == {"MiFID_II_2023": v2.json()["version_id"]}
    document = client.get(f"/api/v1/documents/{v1.json()['document_id']}").json()
    assert (document["version_count"], document["current_version"]) == (2, 2)
    assert [v["version_number"] for v in document["versions"]] == [1, 2]


def test_form_fields_win_over_the_csv_and_missing_metadata_is_a_warning(client: TestClient) -> None:
    response = upload(client, "notes.txt", b"Paragraph 1. Firms must report trades.", regulator="FCA",
                      effective_date="2024-02-01", jurisdiction="")
    body = response.json()
    assert body["warnings"] == ["No metadata for: jurisdiction, category, version_label. These fields are left empty."]
    document = client.get(f"/api/v1/documents/{body['document_id']}").json()
    assert (document["regulator"], document["jurisdiction"]) == ("FCA", None)
    assert version_of(client, response)["effective_date"] == "2024-02-01"


# --- Refusals and failures ------------------------------------------------------

@pytest.mark.parametrize(("name", "data", "status", "code"), [
    ("report.docx", b"PK\x03\x04 not a supported type", 415, "unsupported_file_type"),
    ("empty.txt", b"", 400, "empty_file"),
])
def test_refused_uploads_get_problem_json(client: TestClient, name: str, data: bytes, status: int, code: str) -> None:
    response = upload(client, name, data)
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == f"urn:reg-iris:error:{code}"
    assert client.get("/api/v1/documents").json() == []  # nothing was registered


def test_a_broken_pdf_ends_failed_with_the_reason(client: TestClient) -> None:
    response = upload(client, "broken.pdf", b"%PDF-1.7\nthis is not really a PDF")
    assert response.status_code == 202
    version = version_of(client, response)
    assert version["status"] == "failed" and version["is_current"] is False
    assert "broken.pdf" in version["error_message"] or "PDF" in version["error_message"]
    parse = [job for job in version["jobs"] if job["stage"] == "parse"][0]
    assert parse["status"] == "failed" and parse["error_message"] == version["error_message"]


def test_index_failure_keeps_the_chunks_but_never_says_indexed(client: TestClient, index: MemoryIndex) -> None:
    index.fail = True
    response = upload(client, "EMIR_2021_v1.pdf")
    version = version_of(client, response)
    assert (version["status"], version["is_current"], version["chunk_count"]) == ("failed", False, 6)
    assert version["error_message"] == "The test index is switched off."
    assert [job["status"] for job in version["jobs"] if job["stage"] == "index"] == ["failed"]


def test_reindex_after_a_failure_works_and_counts_attempts(client: TestClient, index: MemoryIndex) -> None:
    index.fail = True
    response = upload(client, "EMIR_2021_v1.pdf")
    index.fail = False
    body = response.json()
    again = client.post(f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/reindex")
    assert again.status_code == 202
    assert again.json()["previous_status"] == "failed"
    version = version_of(client, response)
    assert (version["status"], version["error_message"], version["chunk_count"]) == ("indexed", None, 6)
    assert [job["attempt"] for job in version["jobs"] if job["stage"] == "index"] == [1, 2]


# --- Reading ---------------------------------------------------------------------

def test_chunks_have_their_locations(client: TestClient) -> None:
    for name, expected in (("CFTC_2022.txt", "lines 3-4"), ("FCA_Guidance_2023.html", "#p1")):
        body = upload(client, name).json()
        chunks = client.get(f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/chunks").json()
        assert expected in [chunk["location"] for chunk in chunks], name
        assert all(chunk["page_number"] is None for chunk in chunks)  # no invented pages


def test_original_file_can_be_downloaded(client: TestClient) -> None:
    body = upload(client, "FCA_Guidance_2023.html").json()
    response = client.get(f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/file")
    assert response.status_code == 200
    assert response.content == (REGULATIONS / "FCA_Guidance_2023.html").read_bytes()
    assert 'filename="FCA_Guidance_2023.html"' in response.headers["content-disposition"]


def test_list_documents_with_filters(client: TestClient) -> None:
    upload(client, "CFTC_2022.txt")
    upload(client, "EMIR_2021_v1.pdf")
    assert [d["document_key"] for d in client.get("/api/v1/documents").json()] == ["CFTC_2022", "EMIR_2021"]
    assert [d["document_key"] for d in client.get("/api/v1/documents", params={"regulator": "EMIR"}).json()] == \
        ["EMIR_2021"]
    later = client.get("/api/v1/documents", params={"uploaded_after": "2999-01-01T00:00:00Z"}).json()
    assert later == []


def test_job_endpoint_and_not_found_answers(client: TestClient) -> None:
    body = upload(client, "CFTC_2022.txt").json()
    job = version_of(client, upload(client, "ESMA_TRADE_REPORTING_2022.pdf"))["jobs"][0]
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["stage"] == "store"
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/v1/jobs/{missing}").status_code == 404
    assert client.get(f"/api/v1/documents/{missing}").status_code == 404
    # A real version asked for under the wrong document is not found either.
    wrong = client.get(f"/api/v1/documents/{missing}/versions/{body['version_id']}")
    assert wrong.status_code == 404 and wrong.json()["type"] == "urn:reg-iris:error:not_found"
