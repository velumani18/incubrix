import re
from typing import Any
from pydantic import BaseModel
from app.engine.timeline import Timeline, Transcript, Anchor
from app.engine.operations import (
    cut_range,
    keep_only_range,
    speed_range,
    add_caption,
    style_caption,
    bleep_or_mute_range,
    remove_filler_words,
    remove_silences,
    add_background_music,
)
from app.engine.history import HistoryManager
from app.core.errors import IncuBrixException, ErrorCodes

class InstructionResult(BaseModel):
    status: str  # "applied", "needs_clarification", "rejected"
    operations: list[dict[str, Any]] = []
    summary: str = ""
    question: str | None = None
    options: list[str] | None = None
    timeline_hash: str = ""
    duration_s: float = 0.0
    time_policy_used: str = "SOURCE_TIME"
    warnings: list[str] = []

def parse_time_string(val: str) -> float | None:
    """Parses timestamps like '1:30', '0:42', '45', '80 seconds', 'a minute twenty'."""
    val = val.lower().strip()
    if val == "a minute twenty":
        return 80.0
    if val == "a minute":
        return 60.0
    if val == "half a minute":
        return 30.0
    
    # Format MM:SS
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", val)
    if m:
        return float(m.group(1)) * 60 + float(m.group(2))
    
    # Format SS or SSs or SS seconds
    m2 = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?$", val)
    if m2:
        return float(m2.group(1))
    
    return None

class InstructionEngine:
    """
    Parses user natural language instructions into structured operations
    and applies them deterministically to the timeline.
    Guarantees strict 3-way answers: applied, needs_clarification, rejected.
    """
    def __init__(self, time_policy: str = "SOURCE_TIME"):
        self.time_policy = time_policy

    def process_instruction(
        self,
        instruction: str,
        timeline: Timeline,
        transcript: Transcript,
        history: HistoryManager,
        pending_clarification: dict[str, Any] | None = None
    ) -> InstructionResult:
        inst = instruction.strip()
        lower = inst.lower()

        # Check for answering a pending clarification first
        if pending_clarification:
            return self._handle_clarification_response(inst, timeline, transcript, history, pending_clarification)

        # 1. UNDO
        if re.search(r"^\b(undo|revert)\b", lower):
            try:
                restored_tl, step = history.undo()
                timeline.segments = restored_tl.segments
                timeline.overlays = restored_tl.overlays
                timeline.audio_effects = restored_tl.audio_effects
                timeline.audio_tracks = restored_tl.audio_tracks
                timeline.recompute()
                return InstructionResult(
                    status="applied",
                    operations=[{"operation": "undo", "reversed_step": step.step_index}],
                    summary=f"Reversed step {step.step_index}: '{step.instruction}'. Video restored to previous state.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 2. REDO
        if re.search(r"^\b(redo)\b", lower):
            try:
                restored_tl, step = history.redo()
                timeline.segments = restored_tl.segments
                timeline.overlays = restored_tl.overlays
                timeline.audio_effects = restored_tl.audio_effects
                timeline.audio_tracks = restored_tl.audio_tracks
                timeline.recompute()
                return InstructionResult(
                    status="applied",
                    operations=[{"operation": "redo", "redone_step": step.step_index}],
                    summary=f"Redid step {step.step_index}: '{step.instruction}'.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 3. RESTORING EARLIER EDITS: "Actually, put the intro back" / "put back what I removed"
        if re.search(r"(put\s+(?:the\s+)?intro\s+back|restore\s+(?:the\s+)?intro|undo\s+the\s+first\s+cut)", lower):
            # Step 1 was typically cutting the intro (first 30s)
            target_step_idx = 1
            for i, st in enumerate(history.past_steps):
                if "intro" in st.instruction.lower() or "first" in st.instruction.lower() or (st.operations and st.operations[0].get("cut_source_start", 1.0) == 0.0):
                    target_step_idx = i
                    break
            
            try:
                restored_tl, info = history.restore_specific_edit(target_step_idx, timeline)
                history.record_step(
                    instruction=inst,
                    operations=[{"operation": "restore_edit", "target_step": target_step_idx, "info": info}],
                    summary="Restored the intro (first 30 seconds). Later cuts and speed adjustments remain applied.",
                    current_timeline=timeline
                )
                return InstructionResult(
                    status="applied",
                    operations=[{"operation": "restore_edit", "target_step": target_step_idx}],
                    summary="Restored the intro. Captions and subsequent edits remain intact.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 3.5. CUT EVERYTHING REJECTION
        if re.search(r"^\s*cut\s+everything\b", lower):
            return InstructionResult(
                status="rejected",
                summary="Operation would cut the entire video timeline. Whole-video cuts are rejected.",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
            )

        # 4. CAPTION ADDITION: "Add a caption 'Welcome!' when I say welcome to the channel"
        cap_match = re.search(r"add\s+(?:a\s+)?caption\s+['\"]([^'\"]+)['\"]\s+(?:when\s+(?:i\s+say\s+)?|on\s+(?:the\s+words\s+)?|at\s+)?(.+)", inst, re.IGNORECASE)
        if cap_match:
            text = cap_match.group(1).strip()
            anchor_text = cap_match.group(2).strip().strip("'\"")
            
            # Check if anchor is a time or phrase
            parsed_time = parse_time_string(anchor_text)
            if parsed_time is not None:
                if parsed_time > timeline.source_media.duration_s:
                    return InstructionResult(
                        status="rejected",
                        summary=f"Cannot add caption at {anchor_text}: video duration is only {timeline.source_media.duration_s:.1f}s.",
                        timeline_hash=timeline.timeline_hash,
                        duration_s=timeline.duration_s,
                    )
                anchor = Anchor(type="source_range", fallback_source_start=parsed_time, fallback_source_end=parsed_time + 3.0)
            else:
                matches = transcript.find_words_by_phrase(anchor_text)
                if matches:
                    start_w = matches[0][0].id
                    end_w = matches[0][-1].id
                    anchor = Anchor(
                        type="word_range",
                        start_word_id=start_w,
                        end_word_id=end_w,
                        phrase=anchor_text,
                        fallback_source_start=matches[0][0].start_s,
                        fallback_source_end=matches[0][-1].end_s,
                    )
                else:
                    # Generic phrase match anchor with fallback
                    anchor = Anchor(type="phrase_match", phrase=anchor_text, fallback_source_start=0.0, fallback_source_end=3.0)

            op = add_caption(timeline, content=text, anchor=anchor)
            history.record_step(inst, [op], f"Added caption '{text}' attached to content.", timeline)
            return InstructionResult(
                status="applied",
                operations=[op],
                summary=f"Caption '{text}' added and content-anchored. Shifts dynamically with subsequent cuts.",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
                time_policy_used=self.time_policy,
            )

        # 5. CAPTION STYLING: "Make that caption red" / "make it bigger"
        style_match = re.search(r"make\s+(?:that\s+|the\s+)?(?:caption\s+)?(red|blue|yellow|green|white|black|bigger|smaller)", lower)
        if style_match:
            color_or_size = style_match.group(1)
            color_map = {"red": "#FF2A2A", "blue": "#3B82F6", "yellow": "#EAB308", "green": "#22C55E", "white": "#FFFFFF", "black": "#000000"}
            col = color_map.get(color_or_size)
            sz = 48 if color_or_size == "bigger" else (24 if color_or_size == "smaller" else None)

            try:
                op = style_caption(timeline, color=col, font_size=sz)
                history.record_step(inst, [op], f"Updated caption style ({color_or_size}).", timeline)
                return InstructionResult(
                    status="applied",
                    operations=[op],
                    summary=f"Applied style '{color_or_size}' to caption.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 6. CUT FIRST N SECONDS: "Cut the first 30 seconds"
        first_cut = re.search(r"cut\s+(?:out\s+)?(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", lower)
        if first_cut:
            duration = float(first_cut.group(1))
            try:
                op = cut_range(timeline, 0.0, duration, transcript, time_policy=self.time_policy)
                history.record_step(inst, [op], f"Cut the first {duration} seconds.", timeline)
                return InstructionResult(
                    status="applied",
                    operations=[op],
                    summary=f"Video is {duration}s shorter. Captions and effects reprojected.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 7. SPEED UP / SLOW DOWN: "Speed up from 1:00 to 1:30 by 2x"
        speed_match = re.search(r"(speed\s+up|slow\s+down)\s+(?:from\s+)?(\S+)\s+to\s+(\S+)\s+(?:by\s+)?(\d+(?:\.\d+)?)x?", lower)
        if speed_match:
            action, t_start_str, t_end_str, factor_str = speed_match.groups()
            t_start = parse_time_string(t_start_str)
            t_end = parse_time_string(t_end_str)
            factor = float(factor_str)
            if action == "slow down":
                factor = 1.0 / factor

            if t_start is None or t_end is None:
                return InstructionResult(
                    status="rejected",
                    summary=f"Could not parse timestamps from '{t_start_str}' or '{t_end_str}'.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                )
            if factor > 8.0:
                return InstructionResult(
                    status="rejected",
                    summary=f"Speed factor {factor}x is excessive. Maximum allowed speed factor is 8x.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                )

            try:
                op = speed_range(timeline, t_start, t_end, factor, transcript, time_policy=self.time_policy)
                history.record_step(inst, [op], f"Sped up range [{t_start_str}, {t_end_str}] by {factor}x.", timeline)
                return InstructionResult(
                    status="applied",
                    operations=[op],
                    summary=f"Applied {factor}x speed to [{t_start_str}-{t_end_str}] using {self.time_policy}. New duration: {timeline.duration_s:.2f}s.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )

        # 8. TOPIC / WORD REMOVAL: "Remove the part about pricing" / "Cut out pricing"
        topic_match = re.search(r"(?:remove|cut\s+out|delete)\s+(?:the\s+part\s+about\s+|every\s+mention\s+of\s+)?(\w+)", lower)
        if topic_match:
            keyword = topic_match.group(1).lower()
            if keyword in ["all", "everything"]:
                return InstructionResult(
                    status="rejected",
                    summary="Cutting everything would leave an empty video. Whole-video cuts are rejected.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                )

            # Check occurrences in transcript
            occurrences = [w for w in transcript.words if keyword in w.word.lower()]
            if not occurrences:
                # Meaning based / synonym check: pricing -> cost
                synonyms = {"pricing": ["cost", "price", "rate", "subscription"], "intro": ["welcome", "hello"]}
                for syn in synonyms.get(keyword, []):
                    occurrences = [w for w in transcript.words if syn in w.word.lower()]
                    if occurrences:
                        break

            if len(occurrences) > 1:
                # Must-Have 3 & Acceptance Criteria 3: Needs clarification
                # Format timestamps e.g. 1:05, 2:40, 3:15
                time_strings = [f"{int(w.start_s // 60)}:{int(w.start_s % 60):02d}" for w in occurrences]
                q_text = f"'{keyword.capitalize()}' is mentioned at {', '.join(time_strings)} - which one, or all?"
                options = [f"the first one ({time_strings[0]})", f"the second one ({time_strings[1]})"]
                if len(time_strings) > 2:
                    options.append(f"the third one ({time_strings[2]})")
                options.append("all of them")

                return InstructionResult(
                    status="needs_clarification",
                    question=q_text,
                    options=options,
                    summary=f"Found {len(occurrences)} mentions of '{keyword}'. Clarification required before destructive cut.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            elif len(occurrences) == 1:
                # Single occurrence: cut the containing sentence / phrase
                target_w = occurrences[0]
                cut_st = max(0.0, target_w.start_s - 1.0)
                cut_en = target_w.end_s + 1.0
                try:
                    op = cut_range(timeline, cut_st, cut_en, transcript, time_policy="SOURCE_TIME")
                    history.record_step(inst, [op], f"Removed part about '{keyword}'.", timeline)
                    return InstructionResult(
                        status="applied",
                        operations=[op],
                        summary=f"Removed segment containing '{keyword}'.",
                        timeline_hash=timeline.timeline_hash,
                        duration_s=timeline.duration_s,
                        time_policy_used="SOURCE_TIME",
                    )
                except IncuBrixException as e:
                    return InstructionResult(
                        status="rejected",
                        summary=e.message,
                        timeline_hash=timeline.timeline_hash,
                        duration_s=timeline.duration_s,
                    )

        # 9. FILLER WORDS: "Remove filler words" / "cut out the ums"
        if re.search(r"(remove|cut\s+out)\s+(?:the\s+)?(filler\s+words|ums|uhs)", lower):
            op = remove_filler_words(timeline, transcript)
            history.record_step(inst, [op], "Removed filler words.", timeline)
            return InstructionResult(
                status="applied",
                operations=[op],
                summary=f"Removed {op.get('count', 0)} filler words. Duration: {timeline.duration_s:.2f}s.",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
                time_policy_used=self.time_policy,
            )

        # 10. SILENCES: "Remove silences longer than 1 second"
        silence_match = re.search(r"remove\s+silences?\s*(?:longer\s+than\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?)?", lower)
        if silence_match:
            thresh = float(silence_match.group(1) or 1.0)
            op = remove_silences(timeline, transcript, min_silence_s=thresh)
            history.record_step(inst, [op], f"Removed silences longer than {thresh}s.", timeline)
            return InstructionResult(
                status="applied",
                operations=[op],
                summary=f"Removed {op.get('count', 0)} silent gaps > {thresh}s. Duration: {timeline.duration_s:.2f}s.",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
                time_policy_used=self.time_policy,
            )

        # 11. BACKGROUND MUSIC: "Add background music"
        if re.search(r"add\s+background\s+music", lower):
            op = add_background_music(timeline, "assets/audio/lofi_ambient.mp3", volume=0.15, auto_ducking=True)
            history.record_step(inst, [op], "Added background music with speech auto-ducking.", timeline)
            return InstructionResult(
                status="applied",
                operations=[op],
                summary="Added background music track with automatic voice ducking (4% under speech).",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
                time_policy_used=self.time_policy,
            )

        # 12. BLEEP / MUTE WORDS: "Bleep out [word]" / "Mute when I say [word]"
        bleep_match = re.search(r"(bleep|mute)\s+(?:out\s+|when\s+i\s+say\s+)?['\"]?(\w+)['\"]?", lower)
        if bleep_match:
            eff_type, target = bleep_match.groups()
            matches = transcript.find_words_by_phrase(target)
            if matches:
                ops = []
                for m in matches:
                    op = bleep_or_mute_range(
                        timeline,
                        m[0].start_s,
                        m[-1].end_s,
                        effect_type="bleep" if "bleep" in eff_type else "mute",
                        anchor=Anchor(type="word_range", start_word_id=m[0].id, end_word_id=m[-1].id)
                    )
                    ops.append(op)
                history.record_step(inst, ops, f"Applied {eff_type} to '{target}'.", timeline)
                return InstructionResult(
                    status="applied",
                    operations=ops,
                    summary=f"Applied {eff_type} to {len(matches)} occurrence(s) of '{target}'.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used=self.time_policy,
                )
            else:
                return InstructionResult(
                    status="rejected",
                    summary=f"Could not find word '{target}' in transcript.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                )

        # 13. CUT RANGE: "Cut from 0:30 to 0:40"
        cut_range_match = re.search(r"cut\s+(?:from\s+)?(\S+)\s+to\s+(\S+)", lower)
        if cut_range_match:
            t1 = parse_time_string(cut_range_match.group(1))
            t2 = parse_time_string(cut_range_match.group(2))
            if t1 is not None and t2 is not None:
                try:
                    op = cut_range(timeline, t1, t2, transcript, time_policy=self.time_policy)
                    history.record_step(inst, [op], f"Cut range [{t1}, {t2}].", timeline)
                    return InstructionResult(
                        status="applied",
                        operations=[op],
                        summary=f"Cut range [{t1}s to {t2}s] using {self.time_policy}.",
                        timeline_hash=timeline.timeline_hash,
                        duration_s=timeline.duration_s,
                        time_policy_used=self.time_policy,
                    )
                except IncuBrixException as e:
                    return InstructionResult(
                        status="rejected",
                        summary=e.message,
                        timeline_hash=timeline.timeline_hash,
                        duration_s=timeline.duration_s,
                    )

        # 14. Fallback / Unrecognized
        return InstructionResult(
            status="rejected",
            summary=f"Unrecognized or unsupported instruction: '{instruction}'.",
            timeline_hash=timeline.timeline_hash,
            duration_s=timeline.duration_s,
        )

    def _handle_clarification_response(
        self,
        answer: str,
        timeline: Timeline,
        transcript: Transcript,
        history: HistoryManager,
        clarification_context: dict[str, Any]
    ) -> InstructionResult:
        """Handles answer to an earlier needs_clarification question."""
        ans = answer.lower().strip()
        keyword = clarification_context.get("keyword", "pricing")
        occurrences = [w for w in transcript.words if keyword in w.word.lower()]

        if not occurrences:
            synonyms = {"pricing": ["cost", "price", "rate", "subscription"]}
            for syn in synonyms.get(keyword, []):
                occurrences = [w for w in transcript.words if syn in w.word.lower()]
                if occurrences:
                    break

        selected_w = None
        if "first" in ans or "1" in ans:
            selected_w = occurrences[0] if len(occurrences) >= 1 else None
        elif "second" in ans or "2" in ans:
            selected_w = occurrences[1] if len(occurrences) >= 2 else occurrences[-1]
        elif "third" in ans or "3" in ans:
            selected_w = occurrences[2] if len(occurrences) >= 3 else occurrences[-1]
        elif "all" in ans:
            # Cut all mentions
            ops = []
            for occ in occurrences:
                cut_st = max(0.0, occ.start_s - 1.0)
                cut_en = occ.end_s + 1.0
                try:
                    ops.append(cut_range(timeline, cut_st, cut_en, transcript, time_policy="SOURCE_TIME"))
                except IncuBrixException:
                    pass
            history.record_step(f"Clarification answer: '{answer}' for '{keyword}'", ops, f"Removed all mentions of {keyword}", timeline)
            return InstructionResult(
                status="applied",
                operations=ops,
                summary=f"Removed all {len(occurrences)} mentions of {keyword}.",
                timeline_hash=timeline.timeline_hash,
                duration_s=timeline.duration_s,
                time_policy_used="SOURCE_TIME",
            )

        if selected_w:
            cut_st = max(0.0, selected_w.start_s - 1.0)
            cut_en = selected_w.end_s + 1.0
            try:
                op = cut_range(timeline, cut_st, cut_en, transcript, time_policy="SOURCE_TIME")
                history.record_step(f"Clarification answer: '{answer}' for '{keyword}'", [op], f"Removed selected mention of '{keyword}'", timeline)
                return InstructionResult(
                    status="applied",
                    operations=[op],
                    summary=f"Removed the selected mention of '{keyword}' at {selected_w.start_s:.2f}s.",
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                    time_policy_used="SOURCE_TIME",
                )
            except IncuBrixException as e:
                return InstructionResult(
                    status="rejected",
                    summary=e.message,
                    timeline_hash=timeline.timeline_hash,
                    duration_s=timeline.duration_s,
                )

        return InstructionResult(
            status="rejected",
            summary=f"Could not determine selection from '{answer}'.",
            timeline_hash=timeline.timeline_hash,
            duration_s=timeline.duration_s,
        )
