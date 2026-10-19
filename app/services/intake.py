import json
import subprocess
import shutil
from pathlib import Path
from typing import Any
from app.core.config import settings
from app.core.logging import logger
from app.core.storage import storage
from app.core.errors import IncuBrixException, ErrorCodes
from app.engine.timeline import SourceMedia

class IntakeService:
    def __init__(self):
        # Find ffprobe executable
        self.ffprobe_path = shutil.which("ffprobe")
        if not self.ffprobe_path:
            # Check winget path
            win_path = Path(r"C:\Users\Velumani\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffprobe.exe")
            if win_path.exists():
                self.ffprobe_path = str(win_path)
            else:
                self.ffprobe_path = "ffprobe"

    def probe_media(self, file_path: Path) -> dict[str, Any]:
        """Runs ffprobe on the target media file and parses streams."""
        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except Exception as e:
            logger.error(f"FFprobe extraction failed on {file_path}: {e}")
            raise IncuBrixException(
                code=ErrorCodes.INVALID_FILE_TYPE,
                message=f"Failed to probe media file format: {str(e)}",
                retryable=False
            )

    def process_intake(self, file_path: Path, original_filename: str) -> tuple[SourceMedia, list[str]]:
        """
        Validates media file, reads duration, fps, resolution, audio track,
        computes SHA-256 hash, and generates warnings (e.g. VFR, missing audio).
        """
        if not file_path.exists():
            raise IncuBrixException(
                code=ErrorCodes.FILE_NOT_FOUND,
                message=f"Media file not found at {file_path}",
                retryable=False
            )

        # 1. Compute SHA-256 (Must-Have 1 verification)
        sha256_hash = storage.compute_sha256(file_path)

        # 2. Extract Streams via FFprobe
        info = self.probe_media(file_path)
        streams = info.get("streams", [])
        fmt = info.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            raise IncuBrixException(
                code=ErrorCodes.INVALID_FILE_TYPE,
                message="Uploaded file does not contain a valid video stream.",
                retryable=False
            )

        duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
        width = int(video_stream.get("width") or 1920)
        height = int(video_stream.get("height") or 1080)

        # Frame rate parsing
        fps_str = video_stream.get("r_frame_rate", "30/1")
        try:
            num, den = map(float, fps_str.split("/"))
            fps = round(num / den, 2) if den > 0 else 30.0
        except Exception:
            fps = 30.0

        warnings = []
        # Check Variable Frame Rate (VFR)
        avg_fps_str = video_stream.get("avg_frame_rate", fps_str)
        if avg_fps_str != fps_str:
            warnings.append("Video has variable frame rate (VFR); will be normalized to constant frame rate during render.")

        has_audio = audio_stream is not None
        sample_rate = int(audio_stream.get("sample_rate", 44100)) if audio_stream else 44100
        if not has_audio:
            warnings.append("Video contains no audio stream. Speech transcription and audio ducking will be bypassed.")

        source_media = SourceMedia(
            filename=file_path.name,
            sha256=sha256_hash,
            duration_s=round(duration, 3),
            width=width,
            height=height,
            fps=fps,
            has_audio=has_audio,
            sample_rate=sample_rate,
        )

        return source_media, warnings

intake_service = IntakeService()
