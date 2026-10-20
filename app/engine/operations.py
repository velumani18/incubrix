import uuid
from typing import Any
from app.engine.timeline import (
    Timeline,
    Segment,
    Overlay,
    Anchor,
    AudioEffect,
    AudioTrack,
    Transcript,
    Word,
)
from app.core.errors import IncuBrixException, ErrorCodes

FILLER_WORDS = {"um", "uh", "erm", "ah"}

def resolve_span(timeline: Timeline, start_s: float, end_s: float, time_policy: str) -> tuple[float, float]:
    """Resolves start_s and end_s to source timestamps based on the active time policy."""
    if time_policy == "TIMELINE_TIME":
        src_start = timeline.timeline_time_to_source_time(start_s)
        src_end = timeline.timeline_time_to_source_time(end_s)
        if src_start is None or src_end is None:
            raise IncuBrixException(
                code=ErrorCodes.TIMELINE_OUT_OF_BOUNDS,
                message=f"Requested timeline range [{start_s}, {end_s}] does not map to active segments.",
                retryable=False
            )
        return min(src_start, src_end), max(src_start, src_end)
    else:  # SOURCE_TIME
        return min(start_s, end_s), max(start_s, end_s)

def snap_cut_to_word_boundaries(src_start: float, src_end: float, transcript: Transcript, snap_threshold: float = 0.25) -> tuple[float, float]:
    """
    Outstanding Requirement: Snaps cuts to silence gaps so no spoken word is clipped.
    If a cut boundary lands inside or very close to a word, snap outward to the silence gap.
    """
    new_start = src_start
    new_end = src_end

    for w in transcript.words:
        # If start boundary clips inside a word:
        if w.start_s - 0.05 < new_start < w.end_s + 0.05:
            if abs(new_start - w.start_s) < snap_threshold:
                new_start = max(0.0, w.start_s - 0.05)
            elif abs(new_start - w.end_s) < snap_threshold:
                new_start = w.end_s + 0.05
        # If end boundary clips inside a word:
        if w.start_s - 0.05 < new_end < w.end_s + 0.05:
            if abs(new_end - w.end_s) < snap_threshold:
                new_end = w.end_s + 0.05
            elif abs(new_end - w.start_s) < snap_threshold:
                new_end = max(0.0, w.start_s - 0.05)

    return new_start, new_end

def cut_range(
    timeline: Timeline,
    start_s: float,
    end_s: float,
    transcript: Transcript,
    time_policy: str | None = None,
    snap_to_words: bool = False
) -> dict[str, Any]:
    policy = time_policy or timeline.time_policy
    raw_start, raw_end = resolve_span(timeline, start_s, end_s, policy)
    if snap_to_words and transcript and transcript.words:
        cut_start, cut_end = snap_cut_to_word_boundaries(raw_start, raw_end, transcript)
        if cut_start >= cut_end:
            cut_start, cut_end = raw_start, raw_end
    else:
        cut_start, cut_end = raw_start, raw_end

    if cut_start >= cut_end:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message=f"Cut range [{start_s}, {end_s}] is invalid or empty.",
            retryable=False
        )

    new_segments: list[Segment] = []
    affected = False

    for seg in timeline.segments:
        # Case 1: Segment is completely outside cut range
        if seg.source_end <= cut_start or seg.source_start >= cut_end:
            new_segments.append(seg)
        # Case 2: Cut lies strictly inside segment -> split into two
        elif seg.source_start < cut_start and seg.source_end > cut_end:
            new_segments.append(
                Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=seg.source_start,
                    source_end=cut_start,
                    speed=seg.speed,
                    is_muted=seg.is_muted,
                    volume=seg.volume,
                    speaker=seg.speaker,
                )
            )
            new_segments.append(
                Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=cut_end,
                    source_end=seg.source_end,
                    speed=seg.speed,
                    is_muted=seg.is_muted,
                    volume=seg.volume,
                    speaker=seg.speaker,
                )
            )
            affected = True
        # Case 3: Cut trims beginning of segment
        elif seg.source_start >= cut_start and seg.source_end > cut_end:
            new_segments.append(
                Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=cut_end,
                    source_end=seg.source_end,
                    speed=seg.speed,
                    is_muted=seg.is_muted,
                    volume=seg.volume,
                    speaker=seg.speaker,
                )
            )
            affected = True
        # Case 4: Cut trims end of segment
        elif seg.source_start < cut_start and seg.source_end <= cut_end:
            new_segments.append(
                Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=seg.source_start,
                    source_end=cut_start,
                    speed=seg.speed,
                    is_muted=seg.is_muted,
                    volume=seg.volume,
                    speaker=seg.speaker,
                )
            )
            affected = True
        # Case 5: Cut completely encompasses segment -> omit segment
        else:
            affected = True

    if not new_segments:
        raise IncuBrixException(
            code=ErrorCodes.EMPTY_TIMELINE,
            message="Operation would cut the entire video timeline. Whole-video cuts are rejected.",
            retryable=False
        )

    timeline.segments = new_segments
    timeline.recompute()

    return {
        "operation": "cut_range",
        "cut_source_start": round(cut_start, 3),
        "cut_source_end": round(cut_end, 3),
        "time_policy_used": policy,
        "affected": affected,
        "new_duration_s": timeline.duration_s,
    }

def keep_only_range(
    timeline: Timeline,
    start_s: float,
    end_s: float,
    transcript: Transcript,
    time_policy: str | None = None
) -> dict[str, Any]:
    policy = time_policy or timeline.time_policy
    raw_start, raw_end = resolve_span(timeline, start_s, end_s, policy)
    keep_start, keep_end = snap_cut_to_word_boundaries(raw_start, raw_end, transcript)

    new_segments: list[Segment] = []
    for seg in timeline.segments:
        overlap_start = max(seg.source_start, keep_start)
        overlap_end = min(seg.source_end, keep_end)
        if overlap_end > overlap_start:
            new_segments.append(
                Segment(
                    segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                    source_start=overlap_start,
                    source_end=overlap_end,
                    speed=seg.speed,
                    is_muted=seg.is_muted,
                    volume=seg.volume,
                    speaker=seg.speaker,
                )
            )

    if not new_segments:
        raise IncuBrixException(
            code=ErrorCodes.EMPTY_TIMELINE,
            message=f"Keep range [{start_s}, {end_s}] contains no active video segments.",
            retryable=False
        )

    timeline.segments = new_segments
    timeline.recompute()
    return {
        "operation": "keep_only_range",
        "keep_source_start": round(keep_start, 3),
        "keep_source_end": round(keep_end, 3),
        "time_policy_used": policy,
        "new_duration_s": timeline.duration_s,
    }

def mute_range(
    timeline: Timeline,
    start_s: float,
    end_s: float,
    transcript: Transcript,
    time_policy: str | None = None,
    is_muted: bool = True
) -> dict[str, Any]:
    policy = time_policy or timeline.time_policy
    src_start, src_end = resolve_span(timeline, start_s, end_s, policy)

    if src_start >= src_end:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message=f"Mute range [{start_s}, {end_s}] is invalid or empty.",
            retryable=False
        )

    new_segments: list[Segment] = []
    affected = False

    for seg in timeline.segments:
        # Segment completely outside mute range
        if seg.source_end <= src_start or seg.source_start >= src_end:
            new_segments.append(seg)
        # Inside
        elif seg.source_start >= src_start and seg.source_end <= src_end:
            seg.is_muted = is_muted
            seg.volume = 0.0 if is_muted else 1.0
            new_segments.append(seg)
            affected = True
        # Overlapping
        else:
            pts = sorted(list({seg.source_start, seg.source_end, max(seg.source_start, src_start), min(seg.source_end, src_end)}))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                in_range = (src_start <= p1 and p2 <= src_end)
                new_segments.append(
                    Segment(
                        segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                        source_start=p1,
                        source_end=p2,
                        speed=seg.speed,
                        is_muted=is_muted if in_range else seg.is_muted,
                        volume=0.0 if (in_range and is_muted) else (seg.volume if not in_range else 1.0),
                        speaker=seg.speaker,
                    )
                )
            affected = True

    timeline.segments = new_segments
    timeline.recompute()
    return {
        "operation": "mute_range",
        "source_start": round(src_start, 3),
        "source_end": round(src_end, 3),
        "is_muted": is_muted,
        "affected": affected,
        "time_policy_used": policy,
        "new_duration_s": timeline.duration_s,
    }

def speed_range(
    timeline: Timeline,
    start_s: float,
    end_s: float,
    speed_factor: float,
    transcript: Transcript,
    time_policy: str | None = None
) -> dict[str, Any]:
    policy = time_policy or timeline.time_policy
    src_start, src_end = resolve_span(timeline, start_s, end_s, policy)

    if speed_factor < 0.1 or speed_factor > 16.0:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message=f"Speed factor {speed_factor}x is out of supported range (0.1x - 16x).",
            retryable=False
        )

    new_segments: list[Segment] = []
    for seg in timeline.segments:
        # Seg completely outside
        if seg.source_end <= src_start or seg.source_start >= src_end:
            new_segments.append(seg)
        # Inside
        elif seg.source_start >= src_start and seg.source_end <= src_end:
            seg.speed = round(seg.speed * speed_factor, 3)
            new_segments.append(seg)
        # Overlapping start/end
        else:
            pts = sorted(list({seg.source_start, seg.source_end, max(seg.source_start, src_start), min(seg.source_end, src_end)}))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                sp = round(seg.speed * speed_factor, 3) if (src_start <= p1 and p2 <= src_end) else seg.speed
                new_segments.append(
                    Segment(
                        segment_id=f"seg_{uuid.uuid4().hex[:8]}",
                        source_start=p1,
                        source_end=p2,
                        speed=sp,
                        is_muted=seg.is_muted,
                        volume=seg.volume,
                        speaker=seg.speaker,
                    )
                )

    timeline.segments = new_segments
    timeline.recompute()
    return {
        "operation": "speed_range",
        "source_start": round(src_start, 3),
        "source_end": round(src_end, 3),
        "speed_factor": speed_factor,
        "time_policy_used": policy,
        "new_duration_s": timeline.duration_s,
    }

def add_caption(
    timeline: Timeline,
    content: str,
    anchor: Anchor,
    color: str = "#FFFFFF",
    position: str = "bottom",
    font_size: int = 36
) -> dict[str, Any]:
    overlay = Overlay(
        overlay_id=f"ov_{uuid.uuid4().hex[:8]}",
        type="caption",
        content=content,
        color=color,
        position=position,
        font_size=font_size,
        anchor=anchor,
    )
    timeline.overlays.append(overlay)
    timeline.recompute()
    return {
        "operation": "add_caption",
        "overlay_id": overlay.overlay_id,
        "content": content,
        "color": color,
        "anchor_type": anchor.type,
    }

def style_caption(
    timeline: Timeline,
    color: str | None = None,
    font_size: int | None = None,
    caption_ref: str | None = None
) -> dict[str, Any]:
    if not timeline.overlays:
        raise IncuBrixException(
            code=ErrorCodes.INSTRUCTION_REJECTED,
            message="No captions exist on this timeline to style.",
            retryable=False
        )

    # If only one exists or matched ref
    target = None
    if caption_ref:
        for ov in timeline.overlays:
            if caption_ref.lower() in ov.content.lower() or caption_ref.lower() in (ov.anchor.phrase or "").lower():
                target = ov
                break
    if not target:
        target = timeline.overlays[-1]  # Most recent

    if color:
        target.color = color
    if font_size:
        target.font_size = font_size

    timeline.recompute()
    return {
        "operation": "style_caption",
        "overlay_id": target.overlay_id,
        "content": target.content,
        "color": target.color,
        "font_size": target.font_size,
    }

def bleep_or_mute_range(
    timeline: Timeline,
    start_s: float,
    end_s: float,
    effect_type: str = "bleep",
    anchor: Anchor | None = None
) -> dict[str, Any]:
    eff = AudioEffect(
        effect_id=f"eff_{uuid.uuid4().hex[:8]}",
        type=effect_type,
        anchor=anchor or Anchor(type="source_range", fallback_source_start=start_s, fallback_source_end=end_s),
    )
    timeline.audio_effects.append(eff)
    timeline.recompute()
    return {
        "operation": "audio_effect",
        "effect_id": eff.effect_id,
        "type": effect_type,
        "start_s": start_s,
        "end_s": end_s,
    }

def remove_filler_words(timeline: Timeline, transcript: Transcript) -> dict[str, Any]:
    filler_occurrences = []
    for w in transcript.words:
        w_clean = w.word.lower().strip(".,!?:;\"'()[]")
        if w_clean in FILLER_WORDS:
            filler_occurrences.append(w)
    
    if not filler_occurrences:
        return {
            "operation": "remove_filler_words",
            "count": 0,
            "message": "No filler words found in transcript.",
            "new_duration_s": timeline.duration_s,
        }

    # Cut each filler word span
    for fw in filler_occurrences:
        cut_start = max(0.0, fw.start_s - 0.03)
        cut_end = fw.end_s + 0.03
        try:
            cut_range(timeline, cut_start, cut_end, transcript, time_policy="SOURCE_TIME")
        except IncuBrixException:
            pass  # Avoid empty timeline if entire thing is filler

    return {
        "operation": "remove_filler_words",
        "count": len(filler_occurrences),
        "removed_words": [w.word for w in filler_occurrences],
        "new_duration_s": timeline.duration_s,
    }

def remove_silences(timeline: Timeline, transcript: Transcript, min_silence_s: float = 1.0) -> dict[str, Any]:
    silences = []
    if not transcript.words:
        return {"operation": "remove_silences", "count": 0, "new_duration_s": timeline.duration_s}

    sorted_words = sorted(transcript.words, key=lambda w: w.start_s)
    # Check start silence
    if sorted_words[0].start_s > min_silence_s:
        silences.append((0.0, sorted_words[0].start_s - 0.1))

    # Gaps between words
    for i in range(len(sorted_words) - 1):
        gap = sorted_words[i+1].start_s - sorted_words[i].end_s
        if gap >= min_silence_s:
            silences.append((sorted_words[i].end_s + 0.05, sorted_words[i+1].start_s - 0.05))

    # Cut identified silences
    for s_start, s_end in silences:
        if s_end > s_start:
            try:
                cut_range(timeline, s_start, s_end, transcript, time_policy="SOURCE_TIME")
            except IncuBrixException:
                pass

    return {
        "operation": "remove_silences",
        "count": len(silences),
        "min_silence_s": min_silence_s,
        "new_duration_s": timeline.duration_s,
    }

def add_background_music(
    timeline: Timeline,
    music_file_path: str,
    volume: float = 0.15,
    auto_ducking: bool = True
) -> dict[str, Any]:
    track = AudioTrack(
        track_id=f"track_{uuid.uuid4().hex[:8]}",
        type="background_music",
        file_path=music_file_path,
        volume=volume,
        ducking={"enabled": auto_ducking, "ducked_volume": 0.04, "attack_ms": 150, "release_ms": 400}
    )
    timeline.audio_tracks.append(track)
    timeline.recompute()
    return {
        "operation": "add_background_music",
        "track_id": track.track_id,
        "volume": volume,
        "auto_ducking": auto_ducking,
    }
