"""Entry point of the ingestion service: creates the FastAPI application.

Run from services/ingestion:

    uvicorn app.main:app --reload --port 8000

Then open http://127.0.0.1:8000/docs in a browser.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Code that runs once when the server starts (before yield) and once when it stops (after)."""
    configure_logging()
    settings = get_settings()
    logger.info(
        "Starting %s v%s (environment=%s, data_dir=%s)",
        settings.app_name, __version__, settings.environment, settings.data_dir,
    )
    # Later phases add startup work here: database check, loading the embedding model.
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application with all routers attached."""
    settings = get_settings()
    app = FastAPI(
        title="Reg IRIS Ingestion Service",
        description="Uploads, extracts, chunks, embeds and indexes regulatory documents.",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    # Phase 9 adds: app.include_router(documents_router, prefix="/api/v1") and others.
    logger.debug("Created FastAPI app %s", settings.app_name)
    return app


# Uvicorn looks for this variable: "app.main:app" means module app.main, variable app.
app = create_app()
