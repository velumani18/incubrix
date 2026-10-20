import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.storage import storage
from app.services.project_manager import project_manager, Project
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Word

client = TestClient(app)

@pytest.fixture
def assistant_project(tmp_path) -> Project:
    """
    Creates a project with rich transcript containing:
    - An introduction: "Welcome to the video. In this introduction we will discuss pricing." (0.5s - 4.5s)
    - A pause / silence gap: (4.5s to 6.5s = 2.0s silence)
    - Second part: "Next up is the product demo." (6.5s - 8.5s)
    - Later pricing mention: "Here is the second pricing breakdown." (12.0s - 14.5s)
    - Total duration: 30.0s
    """
    import uuid
    project_id = f"proj_asst_{uuid.uuid4().hex[:8]}"
    video_file = tmp_path / "dummy_video.mp4"
    video_file.write_bytes(b"dummy mp4 video bytes")

    source_media = SourceMedia(
        filename=video_file.name,
        sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        duration_s=30.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    seg0 = Segment(segment_id="seg_0", source_start=0.0, source_end=30.0, speed=1.0)
    timeline = Timeline(
        timeline_id=f"tl_{project_id}",
        project_id=project_id,
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg0],
    )
    timeline.recompute()

    words = [
        Word(id="w_0", word="Welcome", start_s=0.5, end_s=1.0),
        Word(id="w_1", word="to", start_s=1.1, end_s=1.3),
        Word(id="w_2", word="the", start_s=1.4, end_s=1.6),
        Word(id="w_3", word="video", start_s=1.7, end_s=2.2),
        Word(id="w_4", word="In", start_s=2.4, end_s=2.6),
        Word(id="w_5", word="this", start_s=2.7, end_s=2.9),
        Word(id="w_6", word="introduction", start_s=3.0, end_s=3.6),
        Word(id="w_7", word="we", start_s=3.7, end_s=3.8),
        Word(id="w_8", word="will", start_s=3.9, end_s=4.0),
        Word(id="w_9", word="discuss", start_s=4.1, end_s=4.3),
        Word(id="w_10", word="pricing", start_s=4.3, end_s=4.5),
        # 2.0s gap from 4.5s to 6.5s
        Word(id="w_11", word="Next", start_s=6.5, end_s=7.0),
        Word(id="w_12", word="up", start_s=7.1, end_s=7.3),
        Word(id="w_13", word="is", start_s=7.4, end_s=7.5),
        Word(id="w_14", word="the", start_s=7.6, end_s=7.7),
        Word(id="w_15", word="product", start_s=7.8, end_s=8.1),
        Word(id="w_16", word="demo", start_s=8.2, end_s=8.5),
        # Later pricing mention at 12s-14.5s
        Word(id="w_17", word="Here", start_s=12.0, end_s=12.4),
        Word(id="w_18", word="is", start_s=12.5, end_s=12.7),
        Word(id="w_19", word="the", start_s=12.8, end_s=13.0),
        Word(id="w_20", word="second", start_s=13.1, end_s=13.5),
        Word(id="w_21", word="pricing", start_s=13.6, end_s=14.0),
        Word(id="w_22", word="breakdown", start_s=14.1, end_s=14.5),
        # Contraction sample: "I'm not sure."
        Word(id="w_23", word="I'm", start_s=20.0, end_s=20.3),
        Word(id="w_24", word="not", start_s=20.3, end_s=20.6),
        Word(id="w_25", word="sure.", start_s=20.6, end_s=21.0),
    ]
    transcript = Transcript(project_id=project_id, words=words)

    project = Project(
        project_id=project_id,
        source_media=source_media,
        source_file_path=str(video_file),
        transcript=transcript,
        timeline=timeline,
    )
    project.status = "ready"
    project_manager.save_project(project)
    return project

def test_valid_request_remove_sentence_discuss_pricing(assistant_project):
    """
    Test valid request: 'Remove the sentence where I say we will discuss pricing.'
    - Resolves phrase 'discuss pricing' to source timestamps [4.1s, 4.5s]
    - Returns proposal with duration preview
    - Confirms and applies proposal
    - Verifies undo works on applied AI edit
    """
    pid = assistant_project.project_id

    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Remove the sentence where I say we will discuss pricing."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    assert len(data["operations"]) == 1
    op = data["operations"][0]
    assert op["operation"] == "cut"
    assert op["start_time"] in (0.5, 4.1)
    assert op["end_time"] == 4.5
    assert data["preview"]["before_duration_s"] == 30.0
    assert data["preview"]["after_duration_s"] < 30.0

    # Confirm and apply the proposal
    apply_res = client.post(f"/api/v1/projects/{pid}/assistant/apply", json={
        "proposal_id": data["proposal_id"]
    })
    assert apply_res.status_code == 200
    apply_data = apply_res.json()
    assert apply_data["status"] == "applied"
    assert round(apply_data["after_duration_s"], 2) == 29.60

    # Test undo works immediately on the AI edit
    undo_res = client.post(f"/api/v1/projects/{pid}/edits/undo")
    assert undo_res.status_code == 200
    assert undo_res.json()["duration_s"] == 30.0

def test_valid_request_mute_audio_range(assistant_project):
    """
    Test valid request: 'Mute the audio from 10 to 15 seconds.'
    - Resolves timestamps [10.0, 15.0]
    - Generates mute operation
    - Confirms and applies
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Mute the audio from 10 to 15 seconds."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    assert len(data["operations"]) == 1
    assert data["operations"][0]["operation"] == "mute"
    assert data["operations"][0]["start_time"] == 10.0
    assert data["operations"][0]["end_time"] == 15.0

    # Apply
    apply_res = client.post(f"/api/v1/projects/{pid}/assistant/apply", json={
        "proposal_id": data["proposal_id"]
    })
    assert apply_res.status_code == 200
    assert any(s["is_muted"] for s in apply_res.json()["edl"])

def test_valid_request_add_captions_entire_video(assistant_project):
    """
    Test valid request: 'Add captions to the entire video.'
    - Proposes caption overlays for all transcript segments
    - Confirms and applies
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Add captions to the entire video."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    assert len(data["operations"]) == len(assistant_project.transcript.segments)
    assert all(op["operation"] == "caption" for op in data["operations"])

    # Apply
    apply_res = client.post(f"/api/v1/projects/{pid}/assistant/apply", json={
        "proposal_id": data["proposal_id"]
    })
    assert apply_res.status_code == 200
    assert len(apply_res.json()["projected_overlays"]) > 0

def test_valid_request_remove_long_pause(assistant_project):
    """
    Test valid request: 'Remove the long pause after the introduction.'
    - Identifies silence gap between 4.5s and 6.5s
    - Proposes cut
    - Confirms and applies
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Remove the long pause after the introduction."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    op = data["operations"][0]
    assert op["operation"] == "cut"
    # Silence gap is ~4.55s to 6.45s (~1.9s cut)
    assert 4.5 <= op["start_time"] <= 4.6
    assert 6.4 <= op["end_time"] <= 6.5

    # Apply
    apply_res = client.post(f"/api/v1/projects/{pid}/assistant/apply", json={
        "proposal_id": data["proposal_id"]
    })
    assert apply_res.status_code == 200
    assert apply_res.json()["after_duration_s"] < 29.0

def test_ambiguous_instruction_needs_clarification(assistant_project):
    """
    Test ambiguous instruction: 'Remove the part about pricing'
    When multiple pricing mentions exist in transcript:
    - Returns status='needs_clarification'
    - Includes clarification question and selectable options
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Remove the part about pricing"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "needs_clarification"
    assert data["question"] is not None
    assert len(data["options"]) >= 2
    assert any("First mention" in opt for opt in data["options"])

def test_nonexistent_transcript_phrase_rejection(assistant_project):
    """
    Test hallucinated / nonexistent phrase: 'Remove the sentence about flying to Mars'
    - Transcript does not mention Mars
    - Rejects safely with status='rejected'
    - Explains limitation that phrase was not found in transcript
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Remove the sentence about flying to Mars"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert "not appear in the video transcript" in data["rejection_reason"]

def test_invalid_timestamps_out_of_bounds(assistant_project):
    """
    Test out-of-bounds timestamps: 'Cut from 500 to 520' on a 30s video
    - Rejects safely with status='rejected'
    - Explains range exceeds source video duration
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Cut from 500 to 520"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert "exceeds source video duration" in data["rejection_reason"]

def test_model_failure_handling(assistant_project):
    """
    Test model failure handling:
    - When LLM provider fails or throws network error
    - Assistant handles failure gracefully without crashing
    - Returns status='rejected' with clear provider error message
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "Cut from 1 to 2",
        "provider": "error_simulation"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert "unavailable or failed" in data["rejection_reason"]

def test_natural_language_sentence_present_in_transcript(assistant_project):
    """
    Test natural-language sentence matching when phrase exists in transcript:
    - "remove the sentence where it says product demo"
    - Extracts 'product demo'
    - Matches words in transcript (7.4s to 8.5s)
    - Proposes valid cut operation
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "remove the sentence where it says product demo"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    assert len(data["operations"]) == 1
    assert data["operations"][0]["operation"] == "cut"
    assert data["operations"][0]["start_time"] == pytest.approx(7.4, 0.1)
    assert data["operations"][0]["end_time"] == pytest.approx(8.5, 0.1)

def test_natural_language_sentence_with_contraction(assistant_project):
    """
    Test user prompt with expanded contraction against transcript with contracted form:
    User prompt: 'remove the sentence where it says i am not sure'
    Transcript: 'I'm not sure.'
    - Expands contractions and normalizes tokens
    - Successfully resolves exact word timestamps [20.0s, 21.0s]
    - Proposes valid cut
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "remove the sentence where it says i am not sure"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "proposed"
    assert len(data["operations"]) == 1
    assert data["operations"][0]["operation"] == "cut"
    assert data["operations"][0]["start_time"] == pytest.approx(20.0, 0.05)
    assert data["operations"][0]["end_time"] == pytest.approx(21.0, 0.05)

def test_natural_language_sentence_missing_from_transcript(assistant_project):
    """
    Test natural-language sentence cut when phrase is NOT in the transcript:
    - "remove the sentence where it says we are flying to mars"
    - Correctly recognizes user intent as cutting a phrase
    - Explains that the phrase is not in the transcript instead of generic rejection
    """
    pid = assistant_project.project_id
    res = client.post(f"/api/v1/projects/{pid}/assistant/propose", json={
        "instruction": "remove the sentence where it says we are flying to mars"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert "we are flying to mars" in data["rejection_reason"]
    assert "does not appear in the video transcript" in data["rejection_reason"]

