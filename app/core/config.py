import os
from pathlib import Path
from typing import Any
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.example"), env_file_encoding="utf-8", extra="ignore")

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

    # Transcription Settings (Defaults to local faster-whisper, falls back to mock if desired)
    STT_PROVIDER: str = os.getenv("STT_PROVIDER", "faster-whisper")
    WHISPER_MODEL_SIZE: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "float32"

    # LLM Assistant Settings (auto-detects Gemini or OpenAI from .env)
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    LLM_API_KEY: str | None = None
    LLM_PROVIDER: str = "mock"
    LLM_MODEL: str = "gemini-flash-lite-latest"
    LLM_TEMPERATURE: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = self.GEMINI_API_KEY or self.OPENAI_API_KEY
        if self.GEMINI_API_KEY and self.LLM_PROVIDER in ["mock", "gemini"]:
            self.LLM_PROVIDER = "gemini"
        elif self.OPENAI_API_KEY and self.LLM_PROVIDER in ["mock", "openai"]:
            self.LLM_PROVIDER = "openai"

    # Limits & Validation
    MAX_UPLOAD_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB
    ALLOWED_EXTENSIONS: Any = [".mp4", ".mov", ".mkv"]

    # Security & Callbacks
    WEBHOOK_SECRET: str = "incubrix-secret-test-key-2026"
    CORS_ORIGINS: Any = ["*"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("ALLOWED_EXTENSIONS", mode="before")
    @classmethod
    def parse_allowed_extensions(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

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
