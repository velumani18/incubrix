import uuid
import urllib.request
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, Header, BackgroundTasks, status
from pydantic import BaseModel
from app.core.config import settings
from app.core.storage import storage
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.services.intake import intake_service
from app.services.transcription import transcription_service
from app.services.project_manager import project_manager, Project
from app.services.jobs import job_manager, Job

router = APIRouter(prefix="/projects", tags=["Projects"])

class CreateProjectUrlRequest(BaseModel):
    video_url: str
    task: str = "transcribe"  # "transcribe" or "translate"
    language: Optional[str] = None

def run_project_intake_and_transcription(
    project_id: str,
    saved_path: Path,
    safe_name: str,
    task: str = "transcribe",
    language: Optional[str] = None,
    job_id: Optional[str] = None
) -> Project:
    """Core pipeline: intake probe -> FFmpeg audio extraction -> STT transcription/translation."""
    if job_id:
        job_manager.update_job(job_id, status="running", progress=15)

    # 1. FFprobe metadata & hash calculation (leaves original video unchanged)
    source_media, intake_warnings = intake_service.process_intake(saved_path, safe_name)
    if job_id:
        job_manager.update_job(job_id, progress=35)

    # 2. FFmpeg audio extraction (16kHz mono WAV)
    extracted_audio = None
    if source_media.has_audio:
        extracted_audio = intake_service.extract_audio(saved_path)
    if job_id:
        job_manager.update_job(job_id, progress=55)

    # 3. Speech-to-Text Transcription or Translation
    stt_warnings = []
    if extracted_audio and extracted_audio.exists():
        transcript, stt_warnings = transcription_service.transcribe(
            audio_path=extracted_audio,
            task=task,
            language=language,
            project_id=project_id,
        )
    else:
        # Video without audio or no speech
        from app.engine.timeline import Transcript
        transcript = Transcript(
            project_id=project_id,
            language_detected="none" if not source_media.has_audio else "unknown",
            transcript_language="en",
            task=task,
            segments=[],
        )
        if not source_media.has_audio:
            intake_warnings.append("Video has no audio stream; speech transcription bypassed.")

    if job_id:
        job_manager.update_job(job_id, progress=85)

    # 4. Construct & Persist Project
    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(saved_path),
        transcript=transcript,
    )
    project.extracted_audio_path = str(extracted_audio) if extracted_audio else None
    project.status = "ready"
    project.progress = 100
    project.warnings = intake_warnings + stt_warnings
    project_manager.save_project(project)

    if job_id:
        job_manager.update_job(
            job_id,
            status="succeeded",
            progress=100,
            result_links={
                "project_id": project_id,
                "status_url": f"/api/v1/projects/{project_id}/status",
                "transcript_url": f"/api/v1/projects/{project_id}/transcript",
            }
        )

    return project

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    task: str = Form("transcribe"),  # "transcribe" or "translate"
    language: Optional[str] = Form(None),
    async_mode: bool = Form(False),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Upload a video and create a project.
    - Uses FFmpeg to extract audio from the uploaded video.
    - Runs speech-to-text or translation into English using pretrained model.
    - Generates word-level timestamps with segment-level fallback.
    - Stores transcript associated with the project.
    - Preserves the untouched original raw media file.
    """
    logger.info(f"Received video upload: {file.filename} (task: {task}, lang: {language})")

    # 1. Validate file extension
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise IncuBrixException(
            code=ErrorCodes.INVALID_FILE_TYPE,
            message=f"File extension '{ext}' is not permitted. Allowed: {settings.ALLOWED_EXTENSIONS}",
            retryable=False
        )

    # 2. Save file safely
    saved_path, safe_name, sha256_hash = storage.save_upload(file.file, file.filename or "video.mp4")
    project_id = f"proj_{uuid.uuid4().hex[:10]}"

    if async_mode:
        # Long-running async background job
        job = job_manager.create_job(idempotency_key=idempotency_key)
        def worker(curr_job: Job):
            try:
                run_project_intake_and_transcription(
                    project_id=project_id,
                    saved_path=saved_path,
                    safe_name=safe_name,
                    task=task,
                    language=language,
                    job_id=curr_job.job_id
                )
            except Exception as e:
                logger.error(f"Async ingestion failed: {e}", exc_info=True)
                job_manager.update_job(
                    curr_job.job_id,
                    status="failed",
                    error={"code": ErrorCodes.TRANSCRIPTION_FAILED, "message": str(e), "retryable": True}
                )

        job_manager.run_in_background(job.job_id, worker)
        return {
            "project_id": project_id,
            "job_id": job.job_id,
            "status": "processing",
            "progress": 10,
            "status_url": f"/api/v1/projects/{project_id}/status",
            "transcript_url": f"/api/v1/projects/{project_id}/transcript",
        }
    else:
        # Synchronous processing
        project = run_project_intake_and_transcription(
            project_id=project_id,
            saved_path=saved_path,
            safe_name=safe_name,
            task=task,
            language=language,
        )
        return {
            "project_id": project_id,
            "status": "ready",
            "progress": 100,
            "source_media": project.source_media.model_dump(),
            "timeline": project.timeline.model_dump(),
            "transcript": project.transcript.to_schema_dict(),
            "warnings": project.warnings,
            "status_url": f"/api/v1/projects/{project_id}/status",
            "transcript_url": f"/api/v1/projects/{project_id}/transcript",
        }

@router.post("/from_url", status_code=status.HTTP_201_CREATED)
def create_project_from_url(req: CreateProjectUrlRequest):
    """Downloads video from URL, extracts audio with FFmpeg, and transcribes."""
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

    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    project = run_project_intake_and_transcription(
        project_id=project_id,
        saved_path=dest_path,
        safe_name=safe_name,
        task=req.task,
        language=req.language,
    )

    return {
        "project_id": project_id,
        "status": "ready",
        "progress": 100,
        "source_media": project.source_media.model_dump(),
        "timeline": project.timeline.model_dump(),
        "transcript": project.transcript.to_schema_dict(),
        "warnings": project.warnings,
        "status_url": f"/api/v1/projects/{project_id}/status",
        "transcript_url": f"/api/v1/projects/{project_id}/transcript",
    }

@router.get("")
def list_projects():
    return project_manager.list_projects()

@router.get("/{project_id}/status")
def get_project_status(project_id: str):
    """Returns the current processing status and progress for frontend tracking."""
    proj = project_manager.get_project(project_id)
    return {
        "project_id": proj.project_id,
        "status": proj.status,
        "progress": proj.progress,
        "error_message": proj.error_message,
        "warnings": proj.warnings,
        "has_audio": proj.source_media.has_audio,
        "duration_s": proj.source_media.duration_s,
        "transcript_url": f"/api/v1/projects/{project_id}/transcript",
    }

@router.get("/{project_id}/transcript")
def get_project_transcript(project_id: str):
    """
    Returns the structured JSON transcript conforming to the required schema:
    {
      "project_id": "...",
      "language_detected": "...",
      "transcript_language": "...",
      "task": "...",
      "segments": [ ... ]
    }
    """
    proj = project_manager.get_project(project_id)
    return proj.transcript.to_schema_dict()

@router.get("/{project_id}")
def get_project(project_id: str):
    proj = project_manager.get_project(project_id)
    projected_caps = proj.timeline.get_projected_overlays(proj.transcript)
    return {
        "project_id": proj.project_id,
        "status": proj.status,
        "progress": proj.progress,
        "source_media": proj.source_media.model_dump(),
        "timeline": proj.timeline.model_dump(),
        "projected_overlays": projected_caps,
        "transcript": proj.transcript.to_schema_dict(),
        "pending_clarification": proj.pending_clarification,
        "warnings": proj.warnings,
    }

@router.get("/{project_id}/timeline")
def get_project_timeline(project_id: str):
    proj = project_manager.get_project(project_id)
    return proj.timeline.model_dump()

@router.get("/{project_id}/history")
def get_project_history(project_id: str):
    proj = project_manager.get_project(project_id)
    return [s.model_dump() for s in proj.history.past_steps]
