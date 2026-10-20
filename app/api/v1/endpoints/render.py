from pathlib import Path
from fastapi import APIRouter, Header, status
from pydantic import BaseModel
from app.core.config import settings
from app.core.storage import storage
from app.services.project_manager import project_manager
from app.services.jobs import job_manager, Job
from app.services.renderer import renderer

router = APIRouter(prefix="/projects", tags=["Rendering"])

class RenderRequest(BaseModel):
    callback_url: str | None = None
    sync: bool | None = False

@router.post("/{project_id}/render", status_code=status.HTTP_202_ACCEPTED)
def start_render_job(
    project_id: str,
    req: RenderRequest | None = None,
    sync: bool = False,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Integration Standard Point 3 & 4:
    Renders timeline as an asynchronous background job or synchronously if sync=True.
    Returns 202 Accepted with job_id. Supports safe retries via Idempotency-Key.
    """
    proj = project_manager.get_project(project_id)
    is_sync = sync or (req and req.sync)

    if is_sync:
        src_path = Path(proj.source_file_path)
        out_video, manifest = renderer.render_timeline(
            timeline=proj.timeline,
            transcript=proj.transcript,
            source_media_path=src_path
        )
        return {
            "status": "succeeded",
            "progress": 100,
            "rendered_video_path": str(out_video),
            "result_links": {
                "video_url": f"/api/v1/outputs/download/{out_video.name}",
                "manifest_url": f"/api/v1/outputs/download/manifest_{manifest['render_id']}.json",
                "rendered_sha256": manifest["rendered_sha256"],
                "timeline_hash": manifest["timeline_hash"],
            },
            "video_url": f"/api/v1/outputs/download/{out_video.name}",
            "manifest_url": f"/api/v1/outputs/download/manifest_{manifest['render_id']}.json",
            "rendered_sha256": manifest["rendered_sha256"],
            "timeline_hash": manifest["timeline_hash"],
            "duration_s": manifest.get("target_duration_s", proj.timeline.duration_s)
        }

    # Check safe retry / idempotency
    if idempotency_key:
        existing = job_manager.get_job_by_idempotency_key(idempotency_key)
        if existing:
            return {"job_id": existing.job_id, "status": existing.status, "progress": existing.progress}

    callback = req.callback_url if req else None
    job = job_manager.create_job(callback_url=callback, idempotency_key=idempotency_key)

    def render_task(current_job: Job):
        job_manager.update_job(current_job.job_id, status="running", progress=25)
        src_path = Path(proj.source_file_path)
        
        # Execute rendering pipeline
        out_video, manifest = renderer.render_timeline(
            timeline=proj.timeline,
            transcript=proj.transcript,
            source_media_path=src_path
        )
        job_manager.update_job(current_job.job_id, progress=90)

        # Update job results with download links
        job_manager.update_job(
            current_job.job_id,
            status="succeeded",
            progress=100,
            result_links={
                "video_url": f"/api/v1/outputs/download/{out_video.name}",
                "manifest_url": f"/api/v1/outputs/download/manifest_{manifest['render_id']}.json",
                "rendered_sha256": manifest["rendered_sha256"],
                "timeline_hash": manifest["timeline_hash"],
            }
        )

    job_manager.run_in_background(job.job_id, render_task)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "check_status_url": f"/api/v1/jobs/{job.job_id}",
    }
