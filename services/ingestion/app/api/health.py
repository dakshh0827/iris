"""Health endpoint: lets people and tools check that the service is running."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
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

    This only proves the web server answers. It does not check the database or
    search services; a separate /readyz endpoint will do that in Phase 9.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )
