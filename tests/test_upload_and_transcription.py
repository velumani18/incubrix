import os
import shutil
import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.storage import storage

client = TestClient(app)

@pytest.fixture(scope="session")
def sample_video_path(tmp_path_factory) -> Path:
    """Creates a real valid 5-second MP4 test video with audio using FFmpeg."""
    tmp_dir = tmp_path_factory.mktemp("media")
    video_file = tmp_dir / "short_test_clip.mp4"

    ffmpeg = shutil.which("ffmpeg") or r"C:\Users\Velumani\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=5:size=640x360:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(video_file)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return video_file

@pytest.fixture(scope="session")
def silent_video_path(tmp_path_factory) -> Path:
    """Creates a 3-second MP4 test video with NO audio stream."""
    tmp_dir = tmp_path_factory.mktemp("silent")
    video_file = tmp_dir / "silent_test_clip.mp4"

    ffmpeg = shutil.which("ffmpeg") or r"C:\Users\Velumani\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=320x240:d=3",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        str(video_file)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return video_file

def test_upload_extract_audio_and_transcribe(sample_video_path):
    """
    Requirements 1, 2, 3, 5, 6, 9:
    Uploads real video, extracts audio via FFmpeg, generates word-level transcript,
    preserves original video unchanged, and saves structured transcript.
    """
    orig_sha256 = storage.compute_sha256(sample_video_path)

    with open(sample_video_path, "rb") as f:
        response = client.post(
            "/api/v1/projects/upload",
            files={"file": ("short_test_clip.mp4", f, "video/mp4")},
            data={"task": "transcribe"}
        )

    assert response.status_code == 201
    data = response.json()
    assert "project_id" in data
    assert data["status"] == "ready"
    assert data["progress"] == 100

    # Requirement 9: Original file hash unchanged
    assert storage.compute_sha256(sample_video_path) == orig_sha256

    project_id = data["project_id"]

    # Check transcript schema structure
    transcript = data["transcript"]
    assert transcript["project_id"] == project_id
    assert "language_detected" in transcript
    assert "transcript_language" in transcript
    assert transcript["task"] == "transcribe"
    assert "segments" in transcript
    assert len(transcript["segments"]) > 0

    # Timestamp validity checks
    for seg in transcript["segments"]:
        assert seg["start"] >= 0.0
        assert seg["end"] > seg["start"]
        assert len(seg["text"]) > 0
        assert "words" in seg
        
        # Word timestamps validity
        prev_word_end = 0.0
        for w in seg["words"]:
            assert "text" in w
            assert w["start"] >= 0.0
            assert w["end"] > w["start"]
            assert w["start"] >= prev_word_end - 0.05  # chronological
            assert "confidence" in w
            assert 0.0 <= w["confidence"] <= 1.0
            prev_word_end = w["start"]

def test_translation_vs_transcription(sample_video_path):
    """
    Requirement 4 & Important constraint:
    Distinguish transcription from translation.
    When task='translate', transcript_language is strictly 'en' and task is 'translate'.
    """
    with open(sample_video_path, "rb") as f:
        res_translate = client.post(
            "/api/v1/projects/upload",
            files={"file": ("spanish_audio_clip.mp4", f, "video/mp4")},
            data={"task": "translate", "language": "es"}
        )

    assert res_translate.status_code == 201
    d_trans = res_translate.json()["transcript"]
    assert d_trans["task"] == "translate"
    assert d_trans["transcript_language"] == "en"
    assert d_trans["language_detected"] == "es"

def test_get_project_status_endpoint(sample_video_path):
    """Requirement 7: Expose endpoint for checking processing status."""
    with open(sample_video_path, "rb") as f:
        res_upload = client.post(
            "/api/v1/projects/upload",
            files={"file": ("test_status.mp4", f, "video/mp4")}
        )
    proj_id = res_upload.json()["project_id"]

    res_status = client.get(f"/api/v1/projects/{proj_id}/status")
    assert res_status.status_code == 200
    st_data = res_status.json()
    assert st_data["project_id"] == proj_id
    assert st_data["status"] == "ready"
    assert st_data["progress"] == 100
    assert st_data["has_audio"] is True
    assert st_data["duration_s"] > 0

def test_get_project_transcript_endpoint(sample_video_path):
    """Requirement 6 & 11: Expose endpoint for retrieving the structured transcript."""
    with open(sample_video_path, "rb") as f:
        res_upload = client.post(
            "/api/v1/projects/upload",
            files={"file": ("test_transcript.mp4", f, "video/mp4")}
        )
    proj_id = res_upload.json()["project_id"]

    res_tr = client.get(f"/api/v1/projects/{proj_id}/transcript")
    assert res_tr.status_code == 200
    tr_data = res_tr.json()
    assert tr_data["project_id"] == proj_id
    assert "language_detected" in tr_data
    assert "transcript_language" in tr_data
    assert isinstance(tr_data["segments"], list)
    assert len(tr_data["segments"]) > 0
    assert "words" in tr_data["segments"][0]

def test_silent_video_handling(silent_video_path):
    """Requirement 8: Handle videos with no speech / no audio stream without failing."""
    with open(silent_video_path, "rb") as f:
        res = client.post(
            "/api/v1/projects/upload",
            files={"file": ("silent.mp4", f, "video/mp4")}
        )

    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "ready"
    assert data["source_media"]["has_audio"] is False
    assert len(data["warnings"]) > 0
    assert any("no audio" in w.lower() for w in data["warnings"])

def test_corrupt_or_unsupported_file_rejected():
    """Requirement 8: Reject unsupported extensions and corrupt media files."""
    # Invalid extension
    res_ext = client.post(
        "/api/v1/projects/upload",
        files={"file": ("malicious.exe", b"invalid binary", "application/octet-stream")}
    )
    assert res_ext.status_code == 400
    assert res_ext.json()["error"]["code"] == "INVALID_FILE_TYPE"

    # Corrupt video data
    res_corrupt = client.post(
        "/api/v1/projects/upload",
        files={"file": ("broken.mp4", b"not a real video file", "video/mp4")}
    )
    assert res_corrupt.status_code == 400
    assert res_corrupt.json()["error"]["code"] == "INVALID_FILE_TYPE"
