from typing import Any, List, Optional
from fastapi import APIRouter, Header, status, HTTPException
from pydantic import BaseModel, Field
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.services.project_manager import project_manager
from app.services.assistant import assistant_service, AssistantProposalResult, ResolvedOperation
from app.engine.operations import cut_range, mute_range, add_caption, speed_range
from app.engine.timeline import Anchor

router = APIRouter(prefix="/projects", tags=["AI Editing Assistant"])

class ProposeEditRequest(BaseModel):
    instruction: str = Field(..., description="Natural language video editing instruction")
    provider: Optional[str] = Field(default=None, description="Optional LLM provider override ('mock', 'openai', 'gemini')")

class ApplyProposalRequest(BaseModel):
    proposal_id: str = Field(..., description="ID of the validated edit proposal to confirm and apply")
    operations: Optional[List[ResolvedOperation]] = Field(default=None, description="Optional operations override if user tweaked proposal")

@router.post("/{project_id}/assistant/propose", response_model=AssistantProposalResult)
def propose_assistant_edits(
    project_id: str,
    req: ProposeEditRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Submits user instruction to the AI Editing Assistant.
    Validates transcript context, resolves source timestamps, detects ambiguity,
    and returns a preview of the proposed changes without modifying project state.
    """
    proj = project_manager.get_project(project_id)

    result = assistant_service.propose_edits(
        instruction=req.instruction,
        timeline=proj.timeline,
        transcript=proj.transcript,
        provider=req.provider
    )
    return result

@router.post("/{project_id}/assistant/apply")
def apply_confirmed_proposal(
    project_id: str,
    req: ApplyProposalRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Applies confirmed AI-proposed edits deterministically using the editing engine.
    Records the operation in project history so undo/redo/restore work seamlessly.
    """
    proj = project_manager.get_project(project_id)
    cached = assistant_service.get_proposal(req.proposal_id)

    # Allow operations from request or fallback to cached proposal
    ops_to_apply = []
    instruction_text = "Applied AI edit proposal"

    if req.operations:
        ops_to_apply = [op.model_dump() for op in req.operations]
    elif cached and "operations" in cached:
        ops_to_apply = cached["operations"]
        instruction_text = cached.get("instruction", instruction_text)
    else:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message=f"Proposal '{req.proposal_id}' was not found or has expired. Please propose edits again.",
            status_code=400,
            retryable=False
        )

    if not ops_to_apply:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message="No operations to apply from this proposal.",
            status_code=400,
            retryable=False
        )

    before_duration = proj.timeline.duration_s
    before_hash = proj.timeline.timeline_hash
    operations_applied = []

    # Apply through deterministic engine
    for op in ops_to_apply:
        op_name = op.get("operation", "").lower()
        st = float(op.get("start_time", 0.0))
        en = float(op.get("end_time", 0.0))

        if op_name == "cut":
            res = cut_range(
                timeline=proj.timeline,
                start_s=st,
                end_s=en,
                transcript=proj.transcript,
                time_policy="SOURCE_TIME"
            )
            operations_applied.append(res)

        elif op_name == "mute":
            res = mute_range(
                timeline=proj.timeline,
                start_s=st,
                end_s=en,
                transcript=proj.transcript,
                time_policy="SOURCE_TIME",
                is_muted=True
            )
            operations_applied.append(res)

        elif op_name == "caption":
            w_ids = op.get("word_ids", [])
            anchor = (
                Anchor(type="word_range", start_word_id=w_ids[0], end_word_id=w_ids[-1], fallback_source_start=st, fallback_source_end=en)
                if w_ids
                else Anchor(type="source_range", fallback_source_start=st, fallback_source_end=en)
            )
            res = add_caption(
                timeline=proj.timeline,
                content=op.get("content") or "Caption",
                anchor=anchor,
                color=op.get("color") or "#FFFFFF",
                position=op.get("position") or "bottom"
            )
            operations_applied.append(res)

        elif op_name == "speed":
            res = speed_range(
                timeline=proj.timeline,
                start_s=st,
                end_s=en,
                speed_factor=float(op.get("speed_factor", 1.0)),
                transcript=proj.transcript,
                time_policy="SOURCE_TIME"
            )
            operations_applied.append(res)

    # Record step in versioned project history
    proj.history.record_step(
        instruction=f"AI: {instruction_text}",
        operations=operations_applied,
        summary=f"AI Assistant applied {len(operations_applied)} operation(s)",
        current_timeline=proj.timeline
    )

    project_manager.save_project(proj)

    return {
        "status": "applied",
        "proposal_id": req.proposal_id,
        "project_id": project_id,
        "before_duration_s": before_duration,
        "after_duration_s": proj.timeline.duration_s,
        "duration_delta_s": round(proj.timeline.duration_s - before_duration, 4),
        "timeline_hash": proj.timeline.timeline_hash,
        "previous_timeline_hash": before_hash,
        "edl": [s.model_dump() for s in proj.timeline.segments],
        "projected_overlays": proj.timeline.get_projected_overlays(proj.transcript),
        "can_undo": proj.history.can_undo,
        "can_redo": proj.history.can_redo,
        "current_version": proj.history.current_version,
        "version": proj.history.current_version,
        "summary": f"AI Assistant applied {len(operations_applied)} operation(s)",
        "duration_s": proj.timeline.duration_s,
    }
