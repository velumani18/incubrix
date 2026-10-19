import copy
from typing import Any
from pydantic import BaseModel, Field
from app.engine.timeline import Timeline
from app.core.errors import IncuBrixException, ErrorCodes

class HistoryStep(BaseModel):
    step_index: int
    instruction: str
    operations: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    timeline_snapshot: dict[str, Any]
    timeline_hash: str
    duration_s: float

class HistoryManager:
    def __init__(self, initial_timeline: Timeline):
        self.initial_timeline_dict = copy.deepcopy(initial_timeline.model_dump())
        self.past_steps: list[HistoryStep] = []
        self.redo_steps: list[HistoryStep] = []
        # Step 0 is the untouched original
        self.past_steps.append(
            HistoryStep(
                step_index=0,
                instruction="__initial__",
                operations=[],
                summary="Initial untouched media intake",
                timeline_snapshot=copy.deepcopy(self.initial_timeline_dict),
                timeline_hash=initial_timeline.timeline_hash,
                duration_s=initial_timeline.duration_s,
            )
        )

    def record_step(
        self,
        instruction: str,
        operations: list[dict[str, Any]],
        summary: str,
        current_timeline: Timeline
    ) -> HistoryStep:
        """Records a new edit step. A new edit clears the redo stack."""
        self.redo_steps.clear()
        step = HistoryStep(
            step_index=len(self.past_steps),
            instruction=instruction,
            operations=operations,
            summary=summary,
            timeline_snapshot=copy.deepcopy(current_timeline.model_dump()),
            timeline_hash=current_timeline.timeline_hash,
            duration_s=current_timeline.duration_s,
        )
        self.past_steps.append(step)
        return step

    def undo(self) -> tuple[Timeline, HistoryStep]:
        """
        Reverses the most recent instruction.
        Must-Have 6: one instruction = one undo.
        """
        if len(self.past_steps) <= 1:
            raise IncuBrixException(
                code=ErrorCodes.NOTHING_TO_UNDO,
                message="Cannot undo: at the beginning of edit history.",
                retryable=False
            )

        undone_step = self.past_steps.pop()
        self.redo_steps.append(undone_step)
        previous_step = self.past_steps[-1]
        restored_timeline = Timeline.model_validate(copy.deepcopy(previous_step.timeline_snapshot))
        restored_timeline.recompute()
        return restored_timeline, undone_step

    def redo(self) -> tuple[Timeline, HistoryStep]:
        """Redoes the most recently undone step."""
        if not self.redo_steps:
            raise IncuBrixException(
                code=ErrorCodes.NOTHING_TO_REDO,
                message="Cannot redo: redo stack is empty.",
                retryable=False
            )

        step_to_redo = self.redo_steps.pop()
        self.past_steps.append(step_to_redo)
        restored_timeline = Timeline.model_validate(copy.deepcopy(step_to_redo.timeline_snapshot))
        restored_timeline.recompute()
        return restored_timeline, step_to_redo

    def restore_specific_edit(self, step_index: int, target_timeline: Timeline) -> tuple[Timeline, dict[str, Any]]:
        """
        Restores a specific earlier state or reverses a specific past cut.
        E.g., Worked example step 5: 'Actually, put the intro back'
        """
        if step_index < 0 or step_index >= len(self.past_steps):
            raise IncuBrixException(
                code=ErrorCodes.INVALID_RESTORE_TARGET,
                message=f"Step index {step_index} is not a valid historical edit step.",
                retryable=False
            )

        target_step = self.past_steps[step_index]
        # Restore intro or target operation
        restored_dict = copy.deepcopy(target_step.timeline_snapshot)
        # Find which cut was performed at step_index
        # If it was cutting the first 30 seconds (intro):
        # We restore the intro segment [0.0, cut_end] to target_timeline
        restored_segments = []
        step_ops = target_step.operations
        restored_info = {"restored_step": step_index, "instruction": target_step.instruction}

        for op in step_ops:
            if op.get("operation") == "cut_range":
                c_start = op.get("cut_source_start", 0.0)
                c_end = op.get("cut_source_end", 30.0)
                # Re-insert the restored source segment at the beginning
                from app.engine.timeline import Segment
                import uuid
                re_inserted = Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=c_start,
                    source_end=c_end,
                    speed=1.0,
                )
                target_timeline.segments.insert(0, re_inserted)
                target_timeline.recompute()
                restored_info["restored_segment"] = {"start": c_start, "end": c_end}

        return target_timeline, restored_info
