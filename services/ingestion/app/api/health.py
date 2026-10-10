"""Health endpoints: is the service running (/healthz), and can it do its work (/readyz)?"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """The JSON body returned by GET /healthz."""

    status: Literal["ok"]
    service: str
    version: str
    environment: str


@router.get("/healthz", response_model=HealthResponse, summary="Liveness check")
def healthz() -> HealthResponse:
    """Return 200 with basic service information if the process is running.

    This only proves the web server answers. /readyz checks the services it needs.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


class ReadyResponse(BaseModel):
    """The JSON body returned by GET /readyz."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, str]   # each part: "ok", or what is wrong


def check(name: str, test) -> tuple[str, str]:  # noqa: ANN001 - any function without arguments
    """Run one check; return (name, "ok") or (name, the error)."""
    try:
        test()
        return name, "ok"
    except Exception as error:  # we report every problem instead of failing on the first
        return name, f"{type(error).__name__}: {error}"


@router.get("/readyz", response_model=ReadyResponse, summary="Readiness check",
            responses={503: {"model": ReadyResponse, "description": "Something the service needs is missing"}})
def readyz(response: Response) -> ReadyResponse:
    """200 if PostgreSQL (with the team schema), Elasticsearch, Chroma and the embedding model are all usable.

    503 otherwise, with what is wrong for each part. Uploads need all four.
    """
    from app.db.session import check_schema
    from app.indexing.chroma_writer import open_collection
    from app.indexing.es_writer import get_client

    settings = get_settings()

    def model() -> None:
        if not (settings.embedding_model_dir / "modules.json").is_file():
            raise FileNotFoundError(f"not downloaded to {settings.embedding_model_dir}")

    checks = dict([
        check("postgres", check_schema),
        check("elasticsearch", get_client),   # get_client pings the server and raises if it is down
        check("chroma", lambda: open_collection(settings.embedding_model).count()),
        check("embedding_model", model),
    ])
    ready = all(result == "ok" for result in checks.values())
    if not ready:
        response.status_code = 503
    return ReadyResponse(status="ready" if ready else "not_ready", checks=checks)
