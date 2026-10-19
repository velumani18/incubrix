from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.errors import IncuBrixException, ErrorCodes

router = APIRouter(tags=["Outputs & Media"])

@router.get("/outputs/download/{filename}")
def download_output_file(filename: str):
    file_path = settings.OUTPUTS_DIR / filename
    if not file_path.exists():
        raise IncuBrixException(
            code=ErrorCodes.FILE_NOT_FOUND,
            message=f"Output file '{filename}' was not found.",
            status_code=404,
            retryable=False
        )
    return FileResponse(path=file_path, filename=filename)

@router.get("/media/{filename}")
def stream_media_file(filename: str):
    file_path = settings.UPLOAD_DIR / filename
    if not file_path.exists():
        file_path = settings.OUTPUTS_DIR / filename
    if not file_path.exists():
        raise IncuBrixException(
            code=ErrorCodes.FILE_NOT_FOUND,
            message=f"Media file '{filename}' was not found.",
            status_code=404,
            retryable=False
        )
    return FileResponse(path=file_path, media_type="video/mp4")
