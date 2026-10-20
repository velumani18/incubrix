from typing import Any, List, Optional
from fastapi import APIRouter, Header, status, HTTPException
from pydantic import BaseModel, Field, model_validator
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.services.project_manager import project_manager
from app.engine.operations import cut_range, mute_range, add_caption, speed_range
from app.engine.timeline import Anchor

router = APIRouter(prefix="/projects", tags=["Deterministic Edits"])

class EditOperation(BaseModel):
    operation: str = Field(..., description="Action to perform: 'cut', 'mute', 'caption', 'speed'")
    start_time: float = Field(..., description="Start timestamp in seconds")
    end_time: float = Field(..., description="End timestamp in seconds")
    time_policy: Optional[str] = Field("SOURCE_TIME", description="'SOURCE_TIME' or 'TIMELINE_TIME'")
    word_ids: Optional[List[str]] = Field(default=None, description="Optional word IDs selected in transcript")
    content: Optional[str] = Field(default=None, description="Caption or text overlay content")
    color: Optional[str] = Field(default="#FFFFFF", description="Hex or name color")
    position: Optional[str] = Field(default="bottom", description="Position: 'bottom', 'top', 'center'")
    speed_factor: Optional[float] = Field(default=1.0, description="Speed multiplier")
    snap_to_words: Optional[bool] = Field(default=False, description="Whether to snap cut boundaries")

class EditsRequest(BaseModel):
    operations: List[EditOperation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def allow_single_or_list(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If user sent a single operation directly { "operation": "cut", ... }
            if "operation" in data and "operations" not in data:
                return {"operations": [data]}
        return data

@router.post("/{project_id}/edits", status_code=status.HTTP_200_OK)
def apply_edits(
    project_id: str,
    req: EditsRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Applies deterministic text-based video editing operations to the timeline.
    Validates all timestamps against original media duration and rules.
    Maintains the canonical Edit Decision List (EDL) and preserves immutable original media.
    """
    proj = project_manager.get_project(project_id)
    source_duration = proj.source_media.duration_s

    # 1. Handle empty edit lists cleanly
    if not req.operations:
        return {
            "status": "applied",
            "project_id": project_id,
            "operations_applied": [],
            "before_duration_s": proj.timeline.duration_s,
            "after_duration_s": proj.timeline.duration_s,
            "timeline_hash": proj.timeline.timeline_hash,
            "edl": [s.model_dump() for s in proj.timeline.segments],
            "timeline": proj.timeline.model_dump(),
            "source_media": proj.source_media.model_dump(),
            "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
            "message": "Empty edit list provided. No changes made."
        }

    # 2. Strict validation of every operation in the batch
    for idx, op in enumerate(req.operations):
        op_name = op.operation.lower()
        if op_name not in ("cut", "mute", "caption", "speed"):
            raise IncuBrixException(
                code=ErrorCodes.INSTRUCTION_REJECTED,
                message=f"Operation '{op.operation}' at index {idx} is not supported. Allowed: cut, mute, caption, speed.",
                status_code=422,
                retryable=False
            )

        # Validate start < end
        if op.start_time >= op.end_time:
            raise IncuBrixException(
                code=ErrorCodes.INSTRUCTION_REJECTED,
                message=f"Invalid timestamp range at index {idx}: start_time ({op.start_time}) must be strictly less than end_time ({op.end_time}).",
                status_code=422,
                retryable=False
            )

        # Validate negative timestamps
        if op.start_time < 0:
            raise IncuBrixException(
                code=ErrorCodes.INSTRUCTION_REJECTED,
                message=f"Invalid timestamp at index {idx}: start_time ({op.start_time}) cannot be negative.",
                status_code=422,
                retryable=False
            )

        # Validate bounds against source video duration
        # Allow tiny epsilon 0.05s for floating point rounding at file end
        if op.start_time > source_duration:
            raise IncuBrixException(
                code=ErrorCodes.TIMELINE_OUT_OF_BOUNDS,
                message=f"Timestamp out of bounds at index {idx}: start_time ({op.start_time}) exceeds source media duration ({source_duration}s).",
                status_code=422,
                retryable=False
            )
        if op.end_time > source_duration + 0.1:
            raise IncuBrixException(
                code=ErrorCodes.TIMELINE_OUT_OF_BOUNDS,
                message=f"Timestamp out of bounds at index {idx}: end_time ({op.end_time}) exceeds source media duration ({source_duration}s).",
                status_code=422,
                retryable=False
            )

        # Caption specific validation
        if op_name == "caption" and not op.content:
            raise IncuBrixException(
                code=ErrorCodes.INSTRUCTION_REJECTED,
                message=f"Caption operation at index {idx} requires non-empty 'content'.",
                status_code=422,
                retryable=False
            )

    before_duration = proj.timeline.duration_s
    before_hash = proj.timeline.timeline_hash
    operations_applied = []

    # 3. Apply operations deterministically
    for op in req.operations:
        op_name = op.operation.lower()
        policy = op.time_policy or proj.timeline.time_policy

        if op_name == "cut":
            res = cut_range(
                timeline=proj.timeline,
                start_s=op.start_time,
                end_s=op.end_time,
                transcript=proj.transcript,
                time_policy=policy,
                snap_to_words=bool(op.snap_to_words)
            )
            operations_applied.append(res)

        elif op_name == "mute":
            res = mute_range(
                timeline=proj.timeline,
                start_s=op.start_time,
                end_s=op.end_time,
                transcript=proj.transcript,
                time_policy=policy,
                is_muted=True
            )
            operations_applied.append(res)

        elif op_name == "caption":
            # Map word_ids if provided
            if op.word_ids and len(op.word_ids) >= 1:
                anchor = Anchor(
                    type="word_range",
                    start_word_id=op.word_ids[0],
                    end_word_id=op.word_ids[-1],
                    fallback_source_start=op.start_time,
                    fallback_source_end=op.end_time
                )
            else:
                anchor = Anchor(
                    type="source_range",
                    fallback_source_start=op.start_time,
                    fallback_source_end=op.end_time
                )

            res = add_caption(
                timeline=proj.timeline,
                content=op.content or "Caption",
                anchor=anchor,
                color=op.color or "#FFFFFF",
                position=op.position or "bottom"
            )
            operations_applied.append(res)

        elif op_name == "speed":
            res = speed_range(
                timeline=proj.timeline,
                start_s=op.start_time,
                end_s=op.end_time,
                speed_factor=op.speed_factor or 1.0,
                transcript=proj.transcript,
                time_policy=policy
            )
            operations_applied.append(res)

    # 4. Record step in project history for undo/redo
    proj.history.record_step(
        instruction=f"Applied {len(operations_applied)} structured edit(s)",
        operations=operations_applied,
        summary=f"Deterministic edits applied: {len(operations_applied)} operation(s)",
        current_timeline=proj.timeline
    )

    # 5. Persist updated project state
    project_manager.save_project(proj)

    return {
        "status": "applied",
        "project_id": project_id,
        "operations_applied": operations_applied,
        "before_duration_s": before_duration,
        "after_duration_s": proj.timeline.duration_s,
        "duration_delta_s": round(proj.timeline.duration_s - before_duration, 4),
        "timeline_hash": proj.timeline.timeline_hash,
        "previous_timeline_hash": before_hash,
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "timeline": proj.timeline.model_dump(),
        "source_media": proj.source_media.model_dump(),
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
    }

@router.get("/{project_id}/edl")
def get_project_edl(project_id: str):
    """
    Returns the canonical Edit Decision List (EDL) for the project.
    """
    proj = project_manager.get_project(project_id)
    return {
        "project_id": project_id,
        "timeline_id": proj.timeline.timeline_id,
        "schema_version": proj.timeline.schema_version,
        "time_policy": proj.timeline.time_policy,
        "duration_s": proj.timeline.duration_s,
        "timeline_hash": proj.timeline.timeline_hash,
        "source_media": proj.source_media.model_dump(),
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "overlays": [o.model_dump() for o in proj.timeline.overlays],
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
    }

@router.get("/{project_id}/versions")
def get_project_versions(project_id: str):
    """
    Returns the complete list of versioned timeline state changes.
    """
    proj = project_manager.get_project(project_id)
    return {
        "project_id": project_id,
        "current_version": proj.history.current_version,
        "can_undo": proj.history.can_undo,
        "can_redo": proj.history.can_redo,
        "versions": proj.history.get_versions_summary(),
        "redo_stack_count": len(proj.history.redo_steps),
    }

@router.post("/{project_id}/restore/{version}")
def restore_project_version(project_id: str, version: int):
    """
    Restores the project to a previous saved project version (0 <= version < total_versions).
    Creates a new explicit versioned history state recording the restoration.
    """
    proj = project_manager.get_project(project_id)
    restored_timeline, new_step = proj.history.restore_version(version)
    proj.timeline = restored_timeline
    project_manager.save_project(proj)
    return {
        "status": "applied",
        "action": "restore",
        "restored_version": version,
        "new_version": new_step.version,
        "summary": new_step.summary,
        "duration_s": proj.timeline.duration_s,
        "timeline_hash": proj.timeline.timeline_hash,
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
        "can_undo": proj.history.can_undo,
        "can_redo": proj.history.can_redo,
    }

@router.post("/{project_id}/edits/undo")
def undo_edit(project_id: str):
    """Reverts the last edit operation step."""
    proj = project_manager.get_project(project_id)
    restored_timeline, undone_step = proj.history.undo()
    proj.timeline = restored_timeline
    project_manager.save_project(proj)
    return {
        "status": "applied",
        "action": "undo",
        "undone_step_index": undone_step.step_index,
        "current_version": proj.history.current_version,
        "duration_s": proj.timeline.duration_s,
        "timeline_hash": proj.timeline.timeline_hash,
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
        "can_undo": proj.history.can_undo,
        "can_redo": proj.history.can_redo,
    }

@router.post("/{project_id}/edits/redo")
def redo_edit(project_id: str):
    """Re-applies the previously undone edit step."""
    proj = project_manager.get_project(project_id)
    restored_timeline, redone_step = proj.history.redo()
    proj.timeline = restored_timeline
    project_manager.save_project(proj)
    return {
        "status": "applied",
        "action": "redo",
        "redone_step_index": redone_step.step_index,
        "current_version": proj.history.current_version,
        "duration_s": proj.timeline.duration_s,
        "timeline_hash": proj.timeline.timeline_hash,
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
        "can_undo": proj.history.can_undo,
        "can_redo": proj.history.can_redo,
    }
