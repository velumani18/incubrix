from fastapi import APIRouter
from app.services.jobs import job_manager
from app.core.errors import IncuBrixException, ErrorCodes

router = APIRouter(prefix="/jobs", tags=["Jobs"])

@router.get("/{job_id}")
def get_job_status(job_id: str):
    job = job_manager.jobs.get(job_id)
    if not job:
        raise IncuBrixException(
            code=ErrorCodes.JOB_NOT_FOUND,
            message=f"Job with ID '{job_id}' does not exist.",
            status_code=404,
            retryable=False
        )
    return job.model_dump()
