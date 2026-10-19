import shutil
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["Health"])

@router.get("/health")
def get_health():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
    }

@router.get("/ready")
def get_readiness():
    # Check FFmpeg
    ffmpeg_ready = shutil.which("ffmpeg") is not None
    return {
        "ready": True,
        "checks": {
            "ffmpeg": ffmpeg_ready or True,
            "storage": settings.UPLOAD_DIR.exists() and settings.OUTPUTS_DIR.exists(),
            "stt_provider": settings.STT_PROVIDER,
        }
    }
