import shutil
import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.storage import storage
from app.services.intake import intake_service
from app.services.project_manager import project_manager, Project
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Word

client = TestClient(app)

@pytest.fixture(scope="module")
def sample_video_6s(tmp_path_factory) -> Path:
    """Creates a real 6.0-second valid MP4 test video with audio using FFmpeg."""
    tmp_dir = tmp_path_factory.mktemp("edits_media")
    video_file = tmp_dir / "clip_6s.mp4"

    ffmpeg = shutil.which("ffmpeg") or r"C:\Users\Velumani\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=6:size=320x240:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=6",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(video_file)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return video_file

@pytest.fixture
def test_project(sample_video_6s) -> Project:
    """Creates and persists an isolated test project with 6.0s duration."""
    project_id = f"proj_test_edits_{Path(sample_video_6s).stem}"
    source_media = SourceMedia(
        filename=sample_video_6s.name,
        sha256=storage.compute_sha256(sample_video_6s),
        duration_s=6.0,
        width=320,
        height=240,
        fps=30.0,
        has_audio=True,
    )
    seg0 = Segment(segment_id="seg_0", source_start=0.0, source_end=6.0, speed=1.0)
    timeline = Timeline(
        timeline_id=f"tl_{project_id}",
        project_id=project_id,
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg0],
    )
    timeline.recompute()

    words = [
        Word(id="w_0", word="Welcome", start_s=0.5, end_s=1.2),
        Word(id="w_1", word="to", start_s=1.3, end_s=1.5),
        Word(id="w_2", word="the", start_s=1.6, end_s=1.8),
        Word(id="w_3", word="editor", start_s=1.9, end_s=2.5),
        Word(id="w_4", word="sample", start_s=3.0, end_s=3.8),
        Word(id="w_5", word="video", start_s=4.0, end_s=4.8),
    ]
    transcript = Transcript(project_id=project_id, words=words)

    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(sample_video_6s),
        transcript=transcript,
        timeline=timeline,
    )
    project.status = "ready"
    project.progress = 100
    project_manager.save_project(project)
    return project

def test_invalid_timestamps(test_project):
    """
    Validation Test:
    1. start_time >= end_time must fail.
    2. negative timestamps must fail.
    3. timestamps beyond media duration must fail.
    4. nonexistent project returns 404.
    """
    pid = test_project.project_id

    # 1. start >= end
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 4.0, "end_time": 2.0}]
    })
    assert res.status_code == 422
    assert "strictly less than" in res.json()["error"]["message"]

    # 2. start == end
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 3.0, "end_time": 3.0}]
    })
    assert res.status_code == 422

    # 3. negative start
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": -1.0, "end_time": 2.0}]
    })
    assert res.status_code == 422
    assert "negative" in res.json()["error"]["message"]

    # 4. end exceeds media duration (6.0s)
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 2.0, "end_time": 10.0}]
    })
    assert res.status_code == 422
    assert "exceeds source media duration" in res.json()["error"]["message"]

    # 5. Nonexistent project ID
    res = client.post("/api/v1/projects/proj_nonexistent_xyz/edits", json={
        "operations": [{"operation": "cut", "start_time": 1.0, "end_time": 2.0}]
    })
    assert res.status_code == 404

def test_empty_edit_list(test_project):
    """
    Empty edit list validation:
    Must validate gracefully, making no changes and preserving duration and hash.
    """
    pid = test_project.project_id
    initial_dur = test_project.timeline.duration_s
    initial_hash = test_project.timeline.timeline_hash

    res = client.post(f"/api/v1/projects/{pid}/edits", json={"operations": []})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "applied"
    assert data["before_duration_s"] == initial_dur
    assert data["after_duration_s"] == initial_dur
    assert data["timeline_hash"] == initial_hash
    assert len(data["operations_applied"]) == 0

def test_multiple_sequential_cuts(test_project):
    """
    Multiple sequential cuts:
    Cuts [1.0, 2.0] and [3.0, 4.0].
    Total duration drops from 6.0s to 4.0s.
    EDL has segments: [0.0 - 1.0], [2.0 - 3.0], [4.0 - 6.0].
    """
    pid = test_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [
            {"operation": "cut", "start_time": 1.0, "end_time": 2.0},
            {"operation": "cut", "start_time": 3.0, "end_time": 4.0},
        ]
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "applied"
    assert round(data["after_duration_s"], 2) == 4.00
    assert len(data["edl"]) == 3

    # Check segment boundaries in EDL
    seg0, seg1, seg2 = data["edl"]
    assert seg0["source_start"] == 0.0 and seg0["source_end"] == 1.0
    assert seg1["source_start"] == 2.0 and seg1["source_end"] == 3.0
    assert seg2["source_start"] == 4.0 and seg2["source_end"] == 6.0

def test_overlapping_cuts(test_project):
    """
    Overlapping cuts test:
    First cut [2.0, 4.0], then cut overlapping range [3.0, 5.0].
    Original 6.0s video:
    After cut [2.0, 4.0]: segments are [0.0, 2.0] and [4.0, 6.0].
    Next cut [3.0, 5.0]: trims [4.0, 6.0] to [5.0, 6.0].
    Remaining segments: [0.0, 2.0] and [5.0, 6.0], total duration = 3.0s.
    """
    pid = test_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [
            {"operation": "cut", "start_time": 2.0, "end_time": 4.0},
            {"operation": "cut", "start_time": 3.0, "end_time": 5.0},
        ]
    })
    assert res.status_code == 200
    data = res.json()
    assert round(data["after_duration_s"], 2) == 3.00
    assert len(data["edl"]) == 2
    assert data["edl"][0]["source_start"] == 0.0 and data["edl"][0]["source_end"] == 2.0
    assert data["edl"][1]["source_start"] == 5.0 and data["edl"][1]["source_end"] == 6.0

def test_mute_and_caption_operations(test_project):
    """
    Test mute and caption operations:
    Mutes range [2.0, 3.0] and adds caption on [4.0, 5.5].
    Duration remains 6.0s (muting does not shorten duration).
    """
    pid = test_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [
            {"operation": "mute", "start_time": 2.0, "end_time": 3.0},
            {"operation": "caption", "start_time": 4.0, "end_time": 5.5, "content": "Important Note", "color": "#FF2A2A"}
        ]
    })
    assert res.status_code == 200
    data = res.json()
    assert round(data["after_duration_s"], 2) == 6.00

    # Verify that a segment has is_muted=True
    muted_segs = [s for s in data["edl"] if s["is_muted"]]
    assert len(muted_segs) == 1
    assert muted_segs[0]["source_start"] == 2.0
    assert muted_segs[0]["source_end"] == 3.0

    # Verify caption projected overlay
    assert len(data["projected_overlays"]) == 1
    assert data["projected_overlays"][0]["content"] == "Important Note"

def test_undo_and_redo(test_project):
    """
    Test undo and redo operations.
    """
    pid = test_project.project_id
    # Cut [0.0, 2.0]
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 0.0, "end_time": 2.0}]
    })

    # Undo
    undo_res = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert undo_res.status_code == 200
    assert undo_res.json()["duration_s"] == 6.0

    # Redo
    redo_res = client.post(f"/api/v1/projects/{pid}/edits/redo")
    assert redo_res.status_code == 200
    assert round(redo_res.json()["duration_s"], 2) == 4.0

def test_render_and_ffprobe_verification(test_project):
    """
    Render and media inspection test:
    1. Cut 2 seconds from the 6-second video (cut 2.0 to 4.0). Expected timeline: 4.0s.
    2. Render output via synchronous render endpoint.
    3. Inspect rendered media file with FFprobe.
    4. Verify rendered duration matches expected timeline duration within 0.1s.
    5. Verify immutable reference to source video (source media unchanged).
    """
    pid = test_project.project_id
    original_sha = test_project.source_media.sha256
    original_dur = test_project.source_media.duration_s

    # Cut 2.0 to 4.0
    cut_res = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 2.0, "end_time": 4.0}]
    })
    assert cut_res.status_code == 200
    assert round(cut_res.json()["after_duration_s"], 2) == 4.00

    # Render synchronously
    render_res = client.post(f"/api/v1/projects/{pid}/render?sync=true")
    assert render_res.status_code == 202 or render_res.status_code == 200
    render_data = render_res.json()
    assert render_data["status"] == "succeeded"

    rendered_file = Path(render_data["rendered_video_path"])
    assert rendered_file.exists()
    assert rendered_file.stat().st_size > 0

    # Inspect rendered media with FFprobe
    probe_info = intake_service.probe_media(rendered_file)
    ffprobe_duration = float(probe_info["format"]["duration"])

    # Duration must match 4.0s within 0.15s tolerance
    assert abs(ffprobe_duration - 4.0) < 0.15, f"Expected ~4.0s, got {ffprobe_duration}s"

    # Verify original media was NEVER modified
    reloaded_proj = project_manager.get_project(pid)
    assert reloaded_proj.source_media.sha256 == original_sha
    assert reloaded_proj.source_media.duration_s == original_dur
    assert Path(reloaded_proj.source_file_path).exists()
    assert storage.compute_sha256(Path(reloaded_proj.source_file_path)) == original_sha
