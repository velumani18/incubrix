import os
import shutil
import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO
from app.core.config import settings
from app.core.errors import IncuBrixException, ErrorCodes

class StorageService:
    def save_upload(self, file_obj: BinaryIO, original_filename: str) -> tuple[Path, str, str]:
        """
        Saves uploaded file securely. Never trusts client filename.
        Returns: (file_path, secure_filename, sha256_hash)
        """
        raise NotImplementedError

    def get_download_url(self, file_path: Path) -> str:
        raise NotImplementedError

class LocalStorageService(StorageService):
    def __init__(self, upload_dir: Path = settings.UPLOAD_DIR, outputs_dir: Path = settings.OUTPUTS_DIR):
        self.upload_dir = upload_dir
        self.outputs_dir = outputs_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, file_obj: BinaryIO, original_filename: str) -> tuple[Path, str, str]:
        # Validate extension
        ext = Path(original_filename).suffix.lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise IncuBrixException(
                code=ErrorCodes.INVALID_FILE_TYPE,
                message=f"File extension '{ext}' is not permitted. Allowed: {settings.ALLOWED_EXTENSIONS}",
                retryable=False
            )

        # Generate untrusted-safe filename
        safe_filename = f"{uuid.uuid4().hex}{ext}"
        destination = self.upload_dir / safe_filename

        hasher = hashlib.sha256()
        total_bytes = 0

        with open(destination, "wb") as f_out:
            while chunk := file_obj.read(1024 * 1024):  # 1MB chunks
                total_bytes += len(chunk)
                if total_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
                    destination.unlink(missing_ok=True)
                    raise IncuBrixException(
                        code=ErrorCodes.FILE_TOO_LARGE,
                        message=f"File size exceeds maximum permitted limit of {settings.MAX_UPLOAD_SIZE_BYTES / (1024*1024)} MB",
                        retryable=False
                    )
                hasher.update(chunk)
                f_out.write(chunk)

        sha256 = hasher.hexdigest()
        return destination, safe_filename, sha256

    def compute_sha256(self, file_path: Path) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    def get_download_url(self, file_path: Path) -> str:
        # Returns relative endpoint URL for download
        return f"/api/v1/outputs/download/{file_path.name}"

storage = LocalStorageService()
