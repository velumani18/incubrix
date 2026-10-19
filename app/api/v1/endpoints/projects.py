import uuid
import urllib.request
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Header, BackgroundTasks, status
from pydantic import BaseModel
from app.core.config import settings
from app.core.storage import storage
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.services.intake import intake_service
from app.services.transcription import transcription_service
from app.services.project_manager import project_manager, Project

router = APIRouter(prefix="/projects", tags=["Projects"])

class CreateProjectUrlRequest(BaseModel):
    video_url: str

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Accepts video file upload, runs integrity check and transcription.
    Never trusts client filename. Validates size & extension.
    """
    logger.info(f"Received file upload: {file.filename}")
    # 1. Save uploaded file securely
    saved_path, safe_name, sha256_hash = storage.save_upload(file.file, file.filename or "video.mp4")

    # 2. Probe media with FFprobe
    source_media, intake_warnings = intake_service.process_intake(saved_path, safe_name)

    # 3. Transcribe audio
    transcript, stt_warnings = transcription_service.transcribe(saved_path)

    # 4. Initialize Project
    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(saved_path),
        transcript=transcript,
    )
    project_manager.save_project(project)

    return {
        "project_id": project_id,
        "source_media": source_media.model_dump(),
        "timeline": project.timeline.model_dump(),
        "transcript_word_count": len(transcript.words),
        "warnings": intake_warnings + stt_warnings,
    }

@router.post("/from_url", status_code=status.HTTP_201_CREATED)
def create_project_from_url(req: CreateProjectUrlRequest):
    """Downloads video from URL, creates project."""
    safe_name = f"{uuid.uuid4().hex}.mp4"
    dest_path = settings.UPLOAD_DIR / safe_name
    try:
        urllib.request.urlretrieve(req.video_url, str(dest_path))
    except Exception as e:
        raise IncuBrixException(
            code=ErrorCodes.INVALID_FILE_TYPE,
            message=f"Failed to fetch video from URL: {str(e)}",
            retryable=True
        )

    source_media, intake_warnings = intake_service.process_intake(dest_path, safe_name)
    transcript, stt_warnings = transcription_service.transcribe(dest_path)

    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(dest_path),
        transcript=transcript,
    )
    project_manager.save_project(project)

    return {
        "project_id": project_id,
        "source_media": source_media.model_dump(),
        "timeline": project.timeline.model_dump(),
        "transcript_word_count": len(transcript.words),
        "warnings": intake_warnings + stt_warnings,
    }

@router.get("")
def list_projects():
    return project_manager.list_projects()

@router.get("/{project_id}")
def get_project(project_id: str):
    proj = project_manager.get_project(project_id)
    projected_caps = proj.timeline.get_projected_overlays(proj.transcript)
    return {
        "project_id": proj.project_id,
        "source_media": proj.source_media.model_dump(),
        "timeline": proj.timeline.model_dump(),
        "projected_overlays": projected_caps,
        "transcript": proj.transcript.model_dump(),
        "pending_clarification": proj.pending_clarification,
    }

@router.get("/{project_id}/timeline")
def get_project_timeline(project_id: str):
    proj = project_manager.get_project(project_id)
    return proj.timeline.model_dump()

@router.get("/{project_id}/history")
def get_project_history(project_id: str):
    proj = project_manager.get_project(project_id)
    return [s.model_dump() for s in proj.history.past_steps]
