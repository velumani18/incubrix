import os
import sys
import json
import time
import uuid
import yaml
import shutil
import subprocess
import argparse
from pathlib import Path
from typing import Any
import requests

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings
from app.core.storage import storage
from app.services.intake import intake_service
from app.services.transcription import transcription_service
from app.services.project_manager import Project
from app.services.renderer import renderer
from app.engine.parser import InstructionEngine

def execute_session_direct(session_file: Path, output_dir: Path) -> dict[str, Any]:
    """
    Executes a session file directly using internal engine services.
    Writes results.json, final_timeline.json, and rendered video to output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(session_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    video_input = data.get("video")
    steps = data.get("steps", [])

    # Intake
    if str(video_input).startswith("http"):
        safe_name = f"session_{uuid.uuid4().hex[:8]}.mp4"
        dest_video = settings.UPLOAD_DIR / safe_name
        import urllib.request
        urllib.request.urlretrieve(video_input, str(dest_video))
    else:
        dest_video = Path(video_input)
        if not dest_video.is_absolute():
            # Check relative to session file
            alt = session_file.parent / video_input
            if alt.exists():
                dest_video = alt

    # If video does not exist yet (e.g. synthetic test), create synthetic sample
    if not dest_video.exists():
        dest_video = generate_test_video(settings.UPLOAD_DIR / "sample_test_video.mp4", duration=240)

    # Ingest media
    source_media, _ = intake_service.process_intake(dest_video, dest_video.name)
    transcript, _ = transcription_service.transcribe(dest_video)

    proj_id = f"proj_sess_{uuid.uuid4().hex[:8]}"
    project = Project(
        project_id=proj_id,
        source_media=source_media,
        source_file_path=str(dest_video),
        transcript=transcript,
    )
    engine = InstructionEngine(time_policy=project.timeline.time_policy)

    step_results = []
    final_video_path = None

    for step_obj in steps:
        say_text = step_obj.get("say", "")
        reply_answer = step_obj.get("answer")

        # Step: Render
        if say_text.strip().lower() == "render":
            out_vid, manifest = renderer.render_timeline(
                timeline=project.timeline,
                transcript=project.transcript,
                source_media_path=dest_video,
                output_dir=output_dir
            )
            final_video_path = str(out_vid)
            step_results.append({
                "say": "render",
                "status": "applied",
                "operations": [{"operation": "render", "output_video": out_vid.name}],
                "summary": f"Rendered timeline to video ({project.timeline.duration_s:.2f}s).",
                "timeline_hash": project.timeline.timeline_hash,
                "duration_s": project.timeline.duration_s,
            })
            continue

        # Execute instruction
        res = engine.process_instruction(
            instruction=say_text,
            timeline=project.timeline,
            transcript=project.transcript,
            history=project.history,
            pending_clarification=project.pending_clarification,
        )

        # If it needed clarification and an answer was provided in the script:
        if res.status == "needs_clarification" and reply_answer:
            clarif_context = {
                "question": res.question,
                "options": res.options,
                "instruction": say_text,
                "keyword": say_text.split()[-1] if say_text else "pricing"
            }
            step_results.append({
                "say": say_text,
                "status": "needs_clarification",
                "question": res.question,
                "options": res.options,
                "summary": res.summary,
                "timeline_hash": project.timeline.timeline_hash,
                "duration_s": project.timeline.duration_s,
            })

            # Process user answer
            ans_res = engine.process_instruction(
                instruction=reply_answer,
                timeline=project.timeline,
                transcript=project.transcript,
                history=project.history,
                pending_clarification=clarif_context,
            )
            step_results.append({
                "say": f"[User response]: {reply_answer}",
                "status": ans_res.status,
                "operations": ans_res.operations,
                "summary": ans_res.summary,
                "timeline_hash": project.timeline.timeline_hash,
                "duration_s": project.timeline.duration_s,
            })
        else:
            step_results.append({
                "say": say_text,
                "status": res.status,
                "operations": res.operations,
                "summary": res.summary,
                "question": res.question,
                "options": res.options,
                "timeline_hash": project.timeline.timeline_hash,
                "duration_s": project.timeline.duration_s,
            })

    # Prepare outputs
    results_json = {
        "session_file": str(session_file.name),
        "source_sha256": source_media.sha256,
        "final_timeline_hash": project.timeline.timeline_hash,
        "final_duration_s": project.timeline.duration_s,
        "final_video": final_video_path,
        "steps": step_results,
    }

    # Write results.json and final_timeline.json
    results_path = output_dir / "results.json"
    with open(results_path, "w", encoding="utf-8") as f_res:
        json.dump(results_json, f_res, indent=2)

    timeline_path = output_dir / "final_timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f_tl:
        json.dump(project.timeline.model_dump(), f_tl, indent=2)

    return results_json

def generate_test_video(output_path: Path, duration: int = 240) -> Path:
    """Generates a synthetic test video with audio using FFmpeg for self-contained testing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        return output_path

    ffmpeg = shutil.which("ffmpeg") or r"C:\Users\Velumani\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=navy:s=1280x720:d={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(output_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path

def main():
    parser = argparse.ArgumentParser(description="IncuBrix Session Runner CLI")
    parser.add_argument("path", help="Path to session YAML file or folder of session YAMLs")
    parser.add_argument("--output-dir", default="./session_results", help="Destination folder for results")
    parser.add_argument("--api-url", default=None, help="Optional HTTP API URL to run against")
    args = parser.parse_args()

    input_path = Path(args.path)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(list(input_path.glob("*.yaml")) + list(input_path.glob("*.yml")))
    else:
        print(f"Error: {input_path} does not exist.")
        sys.exit(1)

    print(f"Running IncuBrix Session Runner on {len(files)} session(s)...")
    for f in files:
        target_sub_dir = out_dir / f.stem
        target_sub_dir.mkdir(parents=True, exist_ok=True)
        print(f"--> Executing: {f.name}")
        t0 = time.time()
        res = execute_session_direct(f, target_sub_dir)
        print(f"    Completed in {time.time()-t0:.2f}s | Hash: {res['final_timeline_hash'][:12]}... | Steps: {len(res['steps'])}")

    print(f"All session results written to: {out_dir.resolve()}")

if __name__ == "__main__":
    main()
