from fastapi import APIRouter, Header
from pydantic import BaseModel
from app.core.config import settings
from app.services.project_manager import project_manager
from app.engine.parser import InstructionEngine

router = APIRouter(prefix="/projects", tags=["Instructions"])

class InstructionRequest(BaseModel):
    instruction: str

@router.post("/{project_id}/instructions")
def execute_instruction(
    project_id: str,
    req: InstructionRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Executes a natural language editing instruction on the project's timeline.
    Returns strictly one of: 'applied', 'needs_clarification', or 'rejected'.
    """
    proj = project_manager.get_project(project_id)
    engine = InstructionEngine(time_policy=proj.timeline.time_policy)

    result = engine.process_instruction(
        instruction=req.instruction,
        timeline=proj.timeline,
        transcript=proj.transcript,
        history=proj.history,
        pending_clarification=proj.pending_clarification,
    )

    if result.status == "needs_clarification":
        proj.pending_clarification = {
            "question": result.question,
            "options": result.options,
            "instruction": req.instruction,
            "keyword": req.instruction.split()[-1] if req.instruction else "pricing"
        }
    else:
        # Clear any answered or resolved clarification
        proj.pending_clarification = None

    # Persist updated project state
    project_manager.save_project(proj)

    return result.model_dump()
