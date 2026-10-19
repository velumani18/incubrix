import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.engine.timeline import SourceMedia, Timeline, Segment, Transcript, Word
from app.services.project_manager import project_manager, Project

client = TestClient(app)

def setup_test_project():
    source_media = SourceMedia(
        filename="api_test.mp4",
        sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        duration_s=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    seg = Segment(segment_id="seg_0", source_start=0.0, source_end=120.0, speed=1.0)
    timeline = Timeline(
        timeline_id="tl_api_test",
        project_id="proj_api_1",
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg],
    )
    timeline.recompute()
    transcript = Transcript(
        words=[
            Word(id="w_1", word="welcome", start_s=10.0, end_s=11.0),
            Word(id="w_2", word="pricing", start_s=40.0, end_s=41.0),
            Word(id="w_3", word="pricing", start_s=80.0, end_s=81.0),
        ]
    )
    proj = Project(
        project_id="proj_api_1",
        source_media=source_media,
        source_file_path="sample.mp4",
        transcript=transcript,
        timeline=timeline,
    )
    project_manager.save_project(proj)
    return proj

def test_integration_point_8_health_and_ready():
    """GET /health and GET /ready endpoints work and return JSON."""
    res_h = client.get("/health")
    assert res_h.status_code == 200
    assert res_h.json()["status"] == "healthy"

    res_r = client.get("/ready")
    assert res_r.status_code == 200
    assert res_r.json()["ready"] is True

def test_integration_point_5_standardized_json_errors():
    """Errors return JSON: {"error": {"code", "message", "retryable", "details"}}."""
    # Request non-existent project
    res = client.get("/api/v1/projects/non_existent_id_999")
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "PROJECT_NOT_FOUND"
    assert "not found" in err["message"].lower()
    assert err["retryable"] is False

def test_integration_point_2_instruction_endpoint():
    """POST /api/v1/projects/{id}/instructions returns 3-way answers."""
    setup_test_project()

    # 1. Applied
    res1 = client.post(
        "/api/v1/projects/proj_api_1/instructions",
        json={"instruction": "Cut the first 30 seconds"}
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["status"] == "applied"
    assert d1["duration_s"] == 90.0

    # 2. Needs clarification (two pricing mentions)
    res2 = client.post(
        "/api/v1/projects/proj_api_1/instructions",
        json={"instruction": "Remove the part about pricing"}
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["status"] == "needs_clarification"
    assert d2["question"] is not None
    assert len(d2["options"]) >= 2

def test_integration_point_3_background_jobs_and_idempotency():
    """POST render returns 202 with job_id; Idempotency-Key prevents duplicate jobs."""
    setup_test_project()
    key = "idem-test-key-12345"

    res1 = client.post(
        "/api/v1/projects/proj_api_1/render",
        headers={"Idempotency-Key": key}
    )
    assert res1.status_code == 202
    d1 = res1.json()
    job_id = d1["job_id"]
    assert job_id.startswith("job_")

    # Safe retry with same Idempotency-Key
    res2 = client.post(
        "/api/v1/projects/proj_api_1/render",
        headers={"Idempotency-Key": key}
    )
    assert res2.status_code == 202
    d2 = res2.json()
    assert d2["job_id"] == job_id  # Exact same job returned!

    # Check job status endpoint
    res_status = client.get(f"/api/v1/jobs/{job_id}")
    assert res_status.status_code == 200
    st_data = res_status.json()
    assert st_data["status"] in ["queued", "running", "succeeded"]
