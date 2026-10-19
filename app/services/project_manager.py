import json
import uuid
from pathlib import Path
from typing import Any
from app.core.config import settings
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia
from app.engine.history import HistoryManager

class Project:
    def __init__(
        self,
        project_id: str,
        source_media: SourceMedia,
        source_file_path: str,
        transcript: Transcript,
        timeline: Timeline | None = None,
        history: HistoryManager | None = None,
    ):
        self.project_id = project_id
        self.source_media = source_media
        self.source_file_path = source_file_path
        self.transcript = transcript
        
        if timeline is None:
            # Build initial timeline: single segment covering whole video
            init_seg = Segment(
                segment_id="seg_0",
                source_start=0.0,
                source_end=source_media.duration_s,
                speed=1.0,
            )
            self.timeline = Timeline(
                timeline_id=f"tl_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                time_policy=settings.TIME_POLICY,
                source_media=source_media,
                segments=[init_seg],
            )
            self.timeline.recompute()
        else:
            self.timeline = timeline

        self.history = history or HistoryManager(self.timeline)
        self.pending_clarification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_media": self.source_media.model_dump(),
            "source_file_path": self.source_file_path,
            "transcript": self.transcript.model_dump(),
            "timeline": self.timeline.model_dump(),
            "pending_clarification": self.pending_clarification,
            "history_steps": [s.model_dump() for s in self.history.past_steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        src_media = SourceMedia.model_validate(data["source_media"])
        transcript = Transcript.model_validate(data["transcript"])
        timeline = Timeline.model_validate(data["timeline"])
        timeline.recompute()
        
        proj = cls(
            project_id=data["project_id"],
            source_media=src_media,
            source_file_path=data["source_file_path"],
            transcript=transcript,
            timeline=timeline,
        )
        proj.pending_clarification = data.get("pending_clarification")
        return proj

class ProjectManager:
    def __init__(self, projects_dir: Path = settings.PROJECTS_DIR):
        self.projects_dir = projects_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Project] = {}

    def get_project(self, project_id: str) -> Project:
        if project_id in self._cache:
            return self._cache[project_id]

        file_path = self.projects_dir / f"{project_id}.json"
        if not file_path.exists():
            raise IncuBrixException(
                code=ErrorCodes.PROJECT_NOT_FOUND,
                message=f"Project with ID '{project_id}' was not found.",
                status_code=404,
                retryable=False
            )

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        proj = Project.from_dict(data)
        self._cache[project_id] = proj
        return proj

    def save_project(self, project: Project) -> None:
        self._cache[project.project_id] = project
        file_path = self.projects_dir / f"{project.project_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, indent=2)

    def list_projects(self) -> list[dict[str, Any]]:
        results = []
        for p in self.projects_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "project_id": data["project_id"],
                    "filename": data["source_media"]["filename"],
                    "duration_s": data["timeline"]["duration_s"],
                    "timeline_hash": data["timeline"]["timeline_hash"],
                })
            except Exception:
                continue
        return results

project_manager = ProjectManager()
