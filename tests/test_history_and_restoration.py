import shutil
import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.storage import storage
from app.services.project_manager import project_manager, Project
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Word

client = TestClient(app)

@pytest.fixture(scope="module")
def history_test_video(tmp_path_factory) -> Path:
    """Creates a 6.0-second MP4 test video for history & restoration testing."""
    tmp_dir = tmp_path_factory.mktemp("hist_media")
    video_file = tmp_dir / "hist_clip_6s.mp4"

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
def fresh_history_project(history_test_video) -> Project:
    """Creates an isolated fresh project for each history test."""
    import uuid
    project_id = f"proj_hist_{uuid.uuid4().hex[:8]}"
    source_media = SourceMedia(
        filename=history_test_video.name,
        sha256=storage.compute_sha256(history_test_video),
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
        Word(id="w_0", word="Start", start_s=0.2, end_s=0.8),
        Word(id="w_1", word="first", start_s=1.2, end_s=1.8),
        Word(id="w_2", word="second", start_s=2.2, end_s=2.8),
        Word(id="w_3", word="third", start_s=3.2, end_s=3.8),
        Word(id="w_4", word="end", start_s=5.0, end_s=5.8),
    ]
    transcript = Transcript(project_id=project_id, words=words)

    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(history_test_video),
        transcript=transcript,
        timeline=timeline,
    )
    project.status = "ready"
    project_manager.save_project(project)
    return project

def test_multiple_edits_followed_by_undo(fresh_history_project):
    """
    Requirements:
    - Multiple edits followed by step-by-step undo.
    - Each undo reverses exactly one state transition.
    """
    pid = fresh_history_project.project_id

    # Edit 1: Cut [1.0, 2.0] -> 6.0s down to 5.0s
    res1 = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 1.0, "end_time": 2.0}]
    })
    assert res1.status_code == 200
    assert round(res1.json()["after_duration_s"], 2) == 5.00

    # Edit 2: Cut [3.0, 4.0] -> 5.0s down to 4.0s
    res2 = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 3.0, "end_time": 4.0}]
    })
    assert res2.status_code == 200
    assert round(res2.json()["after_duration_s"], 2) == 4.00

    # Edit 3: Mute [4.5, 5.5] -> duration remains 4.0s, segment muted
    res3 = client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "mute", "start_time": 4.5, "end_time": 5.5}]
    })
    assert res3.status_code == 200
    assert round(res3.json()["after_duration_s"], 2) == 4.00
    assert any(s["is_muted"] for s in res3.json()["edl"])

    # Undo 1: Reverses mute
    u1 = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert u1.status_code == 200
    assert not any(s["is_muted"] for s in u1.json()["edl"])
    assert round(u1.json()["duration_s"], 2) == 4.00

    # Undo 2: Reverses Edit 2 (restores 3.0 to 4.0)
    u2 = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert u2.status_code == 200
    assert round(u2.json()["duration_s"], 2) == 5.00

    # Undo 3: Reverses Edit 1 (restores 1.0 to 2.0)
    u3 = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert u3.status_code == 200
    assert round(u3.json()["duration_s"], 2) == 6.00
    assert len(u3.json()["edl"]) == 1

def test_redo_after_undo(fresh_history_project):
    """
    Requirements:
    - Redo after undo restores the undone state.
    """
    pid = fresh_history_project.project_id

    # Edit: Cut [0.0, 2.0] -> duration drops to 4.0s
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 0.0, "end_time": 2.0}]
    })

    # Undo -> duration back to 6.0s
    u = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert u.status_code == 200
    assert u.json()["duration_s"] == 6.00
    assert u.json()["can_redo"] is True

    # Redo -> duration restored to 4.0s
    r = client.post(f"/api/v1/projects/{pid}/edits/redo")
    assert r.status_code == 200
    assert round(r.json()["duration_s"], 2) == 4.00
    assert r.json()["can_undo"] is True
    assert r.json()["can_redo"] is False

def test_restoration_to_older_version(fresh_history_project):
    """
    Requirements:
    - Retrieve version history.
    - Restore directly to an older version (e.g. Version 1 from Version 3).
    - New explicit version created recording the restoration.
    """
    pid = fresh_history_project.project_id

    # Edit 1 (Version 1): Cut [1.0, 2.0] -> 5.0s
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 1.0, "end_time": 2.0}]
    })
    # Edit 2 (Version 2): Cut [2.5, 3.5] -> 4.0s
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 2.5, "end_time": 3.5}]
    })
    # Edit 3 (Version 3): Cut [4.0, 5.0] -> 3.0s
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 4.0, "end_time": 5.0}]
    })

    # Check versions summary endpoint
    v_res = client.get(f"/api/v1/projects/{pid}/versions")
    assert v_res.status_code == 200
    v_data = v_res.json()
    assert len(v_data["versions"]) == 4  # v0 (initial), v1, v2, v3
    assert v_data["current_version"] == 3

    # Restore to Version 1 (when duration was 5.0s)
    restore_res = client.post(f"/api/v1/projects/{pid}/restore/1")
    assert restore_res.status_code == 200
    r_data = restore_res.json()
    assert r_data["action"] == "restore"
    assert r_data["restored_version"] == 1
    assert round(r_data["duration_s"], 2) == 5.00
    # Creates version 4 documenting the restoration
    assert r_data["new_version"] == 4

    # Verify project state reloaded from backend matches restored version
    proj_reload = client.get(f"/api/v1/projects/{pid}").json()
    assert round(proj_reload["timeline"]["duration_s"], 2) == 5.00
    assert proj_reload["current_version"] == 4

def test_invalid_history_operations(fresh_history_project):
    """
    Requirements:
    - Reject invalid state transitions safely.
    - Undo at step 0 -> 400 NOTHING_TO_UNDO.
    - Redo with empty stack -> 400 NOTHING_TO_REDO.
    - Restore with invalid version (-1 or 999) -> 400 INVALID_RESTORE_TARGET.
    """
    pid = fresh_history_project.project_id

    # 1. Undo at step 0 (initial untouched)
    u_err = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert u_err.status_code == 400
    assert u_err.json()["error"]["code"] == "NOTHING_TO_UNDO"

    # 2. Redo when redo stack is empty
    r_err = client.post(f"/api/v1/projects/{pid}/edits/redo")
    assert r_err.status_code == 400
    assert r_err.json()["error"]["code"] == "NOTHING_TO_REDO"

    # 3. Restore to negative version
    res_neg = client.post(f"/api/v1/projects/{pid}/restore/-1")
    assert res_neg.status_code == 400
    assert res_neg.json()["error"]["code"] == "INVALID_RESTORE_TARGET"

    # 4. Restore to out of bounds version
    res_oob = client.post(f"/api/v1/projects/{pid}/restore/999")
    assert res_oob.status_code == 400
    assert res_oob.json()["error"]["code"] == "INVALID_RESTORE_TARGET"

def test_persistence_across_app_restart(fresh_history_project):
    """
    Requirements:
    - Persist history so it survives application restarts.
    - Clear in-memory cache, reload from disk JSON, verify past_steps and redo_steps survive.
    """
    pid = fresh_history_project.project_id

    # Step 1: Apply an edit
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 1.0, "end_time": 2.0}]
    })

    # Step 2: Apply a second edit
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 3.0, "end_time": 4.0}]
    })

    # Step 3: Undo one edit so that redo_steps has an item
    client.post(f"/api/v1/projects/{pid}/edits/undo")

    # Simulate total server restart by clearing the in-memory cache
    project_manager._cache.clear()
    assert pid not in project_manager._cache

    # Reload project from disk
    reloaded = project_manager.get_project(pid)
    assert len(reloaded.history.past_steps) == 2  # v0, v1
    assert len(reloaded.history.redo_steps) == 1  # 1 undone step ready for redo
    assert reloaded.history.can_undo is True
    assert reloaded.history.can_redo is True

    # Test redo works after reload
    redo_res = client.post(f"/api/v1/projects/{pid}/edits/redo")
    assert redo_res.status_code == 200
    assert round(redo_res.json()["duration_s"], 2) == 4.00

def test_transcript_and_original_media_immutability(fresh_history_project):
    """
    Requirements:
    - Implement undo and redo without corrupting transcript timestamps or the original media.
    - Keep original source timestamps distinct from edited-output timestamps.
    """
    pid = fresh_history_project.project_id
    original_sha = fresh_history_project.source_media.sha256
    original_dur = fresh_history_project.source_media.duration_s
    original_words = [(w.id, w.start_s, w.end_s, w.word) for w in fresh_history_project.transcript.words]

    # Perform multiple destructive-looking edits
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 0.0, "end_time": 3.0}]
    })
    client.post(f"/api/v1/projects/{pid}/edits", json={
        "operations": [{"operation": "cut", "start_time": 4.0, "end_time": 5.0}]
    })

    # Undo
    client.post(f"/api/v1/projects/{pid}/edits/undo")

    # Restore to Version 0
    client.post(f"/api/v1/projects/{pid}/restore/0")

    # Reload and inspect
    project_manager._cache.clear()
    final_proj = project_manager.get_project(pid)

    # 1. Source media sha256 and duration are identical
    assert final_proj.source_media.sha256 == original_sha
    assert final_proj.source_media.duration_s == original_dur
    assert storage.compute_sha256(Path(final_proj.source_file_path)) == original_sha

    # 2. Transcript words retain their exact original source timestamps
    current_words = [(w.id, w.start_s, w.end_s, w.word) for w in final_proj.transcript.words]
    assert current_words == original_words
