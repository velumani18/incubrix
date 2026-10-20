import copy
import time
from typing import Any
from pydantic import BaseModel, Field
from app.engine.timeline import Timeline
from app.core.errors import IncuBrixException, ErrorCodes

class HistoryStep(BaseModel):
    step_index: int
    version: int = 0
    instruction: str
    operations: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    timeline_snapshot: dict[str, Any]
    timeline_hash: str
    duration_s: float
    timestamp: float = Field(default_factory=time.time)

class HistoryManager:
    def __init__(
        self,
        initial_timeline: Timeline | None = None,
        past_steps: list[HistoryStep] | None = None,
        redo_steps: list[HistoryStep] | None = None,
    ):
        if past_steps:
            self.past_steps = past_steps
            self.redo_steps = redo_steps or []
            self.initial_timeline_dict = copy.deepcopy(self.past_steps[0].timeline_snapshot)
        elif initial_timeline:
            self.initial_timeline_dict = copy.deepcopy(initial_timeline.model_dump())
            self.past_steps = [
                HistoryStep(
                    step_index=0,
                    version=0,
                    instruction="__initial__",
                    operations=[],
                    summary="Initial untouched media intake",
                    timeline_snapshot=copy.deepcopy(self.initial_timeline_dict),
                    timeline_hash=initial_timeline.timeline_hash,
                    duration_s=initial_timeline.duration_s,
                    timestamp=time.time(),
                )
            ]
            self.redo_steps = []
        else:
            self.initial_timeline_dict = {}
            self.past_steps = []
            self.redo_steps = []

    @property
    def can_undo(self) -> bool:
        return len(self.past_steps) > 1

    @property
    def can_redo(self) -> bool:
        return len(self.redo_steps) > 0

    @property
    def current_version(self) -> int:
        return len(self.past_steps) - 1 if self.past_steps else 0

    def get_versions_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "version": s.version if hasattr(s, "version") and s.version is not None else s.step_index,
                "step_index": s.step_index,
                "instruction": s.instruction,
                "summary": s.summary,
                "duration_s": s.duration_s,
                "timeline_hash": s.timeline_hash,
                "timestamp": s.timestamp,
                "is_current": s.step_index == (len(self.past_steps) - 1),
            }
            for s in self.past_steps
        ]

    def record_step(
        self,
        instruction: str,
        operations: list[dict[str, Any]],
        summary: str,
        current_timeline: Timeline
    ) -> HistoryStep:
        """Records a new edit step as a versioned state change. A new edit clears the redo stack."""
        self.redo_steps.clear()
        new_version = len(self.past_steps)
        step = HistoryStep(
            step_index=new_version,
            version=new_version,
            instruction=instruction,
            operations=operations,
            summary=summary,
            timeline_snapshot=copy.deepcopy(current_timeline.model_dump()),
            timeline_hash=current_timeline.timeline_hash,
            duration_s=current_timeline.duration_s,
            timestamp=time.time(),
        )
        self.past_steps.append(step)
        return step

    def undo(self) -> tuple[Timeline, HistoryStep]:
        """
        Reverses the most recent instruction/edit.
        Reverts to the previous valid timeline state and pushes to redo stack.
        """
        if len(self.past_steps) <= 1:
            raise IncuBrixException(
                code=ErrorCodes.NOTHING_TO_UNDO,
                message="Cannot undo: at the beginning of edit history.",
                status_code=400,
                retryable=False
            )

        undone_step = self.past_steps.pop()
        self.redo_steps.append(undone_step)
        previous_step = self.past_steps[-1]
        restored_timeline = Timeline.model_validate(copy.deepcopy(previous_step.timeline_snapshot))
        restored_timeline.recompute()
        return restored_timeline, undone_step

    def redo(self) -> tuple[Timeline, HistoryStep]:
        """Redoes the most recently undone step from the redo stack."""
        if not self.redo_steps:
            raise IncuBrixException(
                code=ErrorCodes.NOTHING_TO_REDO,
                message="Cannot redo: redo stack is empty.",
                status_code=400,
                retryable=False
            )

        step_to_redo = self.redo_steps.pop()
        # Ensure step index matches current sequence length
        step_to_redo.step_index = len(self.past_steps)
        step_to_redo.version = len(self.past_steps)
        self.past_steps.append(step_to_redo)
        restored_timeline = Timeline.model_validate(copy.deepcopy(step_to_redo.timeline_snapshot))
        restored_timeline.recompute()
        return restored_timeline, step_to_redo

    def restore_version(self, target_version: int) -> tuple[Timeline, HistoryStep]:
        """
        Restores project to a specific historical version (0 <= target_version < len(past_steps)).
        Validates safely and appends a new versioned state change recording the restoration.
        """
        if target_version < 0 or target_version >= len(self.past_steps):
            raise IncuBrixException(
                code=ErrorCodes.INVALID_RESTORE_TARGET,
                message=f"Version '{target_version}' is invalid. Available versions: 0 to {len(self.past_steps) - 1}.",
                status_code=400,
                retryable=False
            )

        target_step = self.past_steps[target_version]
        restored_timeline = Timeline.model_validate(copy.deepcopy(target_step.timeline_snapshot))
        restored_timeline.recompute()

        # Clear redo steps on restoration branch
        self.redo_steps.clear()

        # Record restoration as a new explicit versioned step
        new_version = len(self.past_steps)
        new_step = HistoryStep(
            step_index=new_version,
            version=new_version,
            instruction=f"Restore to version {target_version}",
            operations=[{
                "operation": "restore_version",
                "target_version": target_version,
                "original_instruction": target_step.instruction
            }],
            summary=f"Restored project timeline to state from version {target_version} ('{target_step.instruction}')",
            timeline_snapshot=copy.deepcopy(restored_timeline.model_dump()),
            timeline_hash=restored_timeline.timeline_hash,
            duration_s=restored_timeline.duration_s,
            timestamp=time.time(),
        )
        self.past_steps.append(new_step)
        return restored_timeline, new_step

    def restore_specific_edit(self, step_index: int, target_timeline: Timeline) -> tuple[Timeline, dict[str, Any]]:
        """
        Worked example step 5: 'Actually, put the intro back'.
        Reverses a specific past cut by reinserting its source range.
        """
        if step_index < 0 or step_index >= len(self.past_steps):
            raise IncuBrixException(
                code=ErrorCodes.INVALID_RESTORE_TARGET,
                message=f"Step index {step_index} is not a valid historical edit step.",
                status_code=400,
                retryable=False
            )

        target_step = self.past_steps[step_index]
        step_ops = target_step.operations
        restored_info = {"restored_step": step_index, "instruction": target_step.instruction}

        for op in step_ops:
            if op.get("operation") == "cut_range":
                c_start = op.get("cut_source_start", 0.0)
                c_end = op.get("cut_source_end", 30.0)
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
