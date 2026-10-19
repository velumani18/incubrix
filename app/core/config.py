import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "IncuBrix Video Editing Engine"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "production"
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Base Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    DATA_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data"
    UPLOAD_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
    PROJECTS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data" / "projects"
    OUTPUTS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data" / "outputs"
    AUDIO_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data" / "audio"

    # Time Policy: SOURCE_TIME or TIMELINE_TIME
    TIME_POLICY: str = "SOURCE_TIME"

    # Transcription Settings
    STT_PROVIDER: str = "mock"  # "mock" or "faster-whisper"
    WHISPER_MODEL_SIZE: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "float32"

    # Limits & Validation
    MAX_UPLOAD_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB
    ALLOWED_EXTENSIONS: list[str] = [".mp4", ".mov", ".mkv"]

    # Security & Callbacks
    WEBHOOK_SECRET: str = "incubrix-secret-test-key-2026"
    CORS_ORIGINS: list[str] = ["*"]

    # Storage Backend
    STORAGE_BACKEND: str = "local"  # "local" or "s3"
    S3_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET_NAME: str = "incubrix-renders"

    def ensure_directories(self) -> None:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        self.AUDIO_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
