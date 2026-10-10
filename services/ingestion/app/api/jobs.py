"""Job endpoint: one processing step of one version (Phase 9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.db.models import IngestionJob
from app.errors import NotFound
from app.schemas import JobOut

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut, summary="One processing step (parse, chunk, embed, index ...)")
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session)) -> JobOut:
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise NotFound(f"Job {job_id} does not exist.", job_id=str(job_id))
    return JobOut.model_validate(job)
