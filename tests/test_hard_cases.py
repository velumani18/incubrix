import pytest
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Word, Anchor
from app.engine.operations import cut_range, add_caption
from app.engine.history import HistoryManager
from app.engine.parser import InstructionEngine

def create_hard_case_state():
    source_media = SourceMedia(
        filename="short_video.mp4",
        sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        duration_s=60.0,  # Short 60-second video
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    seg0 = Segment(segment_id="seg_0", source_start=0.0, source_end=60.0, speed=1.0)
    timeline = Timeline(
        timeline_id="tl_hard",
        project_id="proj_hard",
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg0],
    )
    timeline.recompute()

    words = [
        Word(id="w_0", word="hello", start_s=5.0, end_s=6.0),
        Word(id="w_1", word="sentence", start_s=15.0, end_s=16.0),
        Word(id="w_2", word="one", start_s=16.1, end_s=17.0),
        Word(id="w_3", word="um", start_s=25.0, end_s=25.5),
        Word(id="w_4", word="thanks", start_s=45.0, end_s=45.5),
    ]
    transcript = Transcript(words=words)
    history = HistoryManager(timeline)
    return timeline, transcript, history

def test_hard_case_caption_cut_and_restored():
    """A caption added, sentence under it cut (caption omitted), cut undone (caption reappears)."""
    timeline, transcript, history = create_hard_case_state()
    engine = InstructionEngine()

    # Add caption on sentence one (15s-17s) via instruction engine
    res_cap = engine.process_instruction("Add a caption 'Key Sentence' on sentence one", timeline, transcript, history)
    assert res_cap.status == "applied"

    assert len(timeline.get_projected_overlays(transcript)) == 1

    # Cut 10s to 20s
    engine.process_instruction("Cut from 10 to 20", timeline, transcript, history)
    # The caption's source range is cut out, so it shouldn't project
    assert len(timeline.get_projected_overlays(transcript)) == 0

    # Undo the cut
    engine.process_instruction("Undo", timeline, transcript, history)
    # The caption should return!
    assert len(timeline.get_projected_overlays(transcript)) == 1

def test_hard_case_caption_at_5_minutes_on_short_video_rejected():
    """Add caption at 5:00 on a 1-minute video is rejected."""
    timeline, transcript, history = create_hard_case_state()
    engine = InstructionEngine()

    res = engine.process_instruction("Add a caption 'Too Late' at 5:00", timeline, transcript, history)
    assert res.status == "rejected"
    assert "video duration is only 60.0s" in res.summary

def test_hard_case_style_caption_when_none_exists_rejected():
    """Make it bigger when no caption exists is rejected."""
    timeline, transcript, history = create_hard_case_state()
    engine = InstructionEngine()

    res = engine.process_instruction("Make that caption bigger", timeline, transcript, history)
    assert res.status == "rejected"
    assert "no captions exist" in res.summary.lower()

def test_hard_case_cut_everything_rejected():
    """Cutting everything is rejected (empty timeline prevention)."""
    timeline, transcript, history = create_hard_case_state()
    engine = InstructionEngine()

    res = engine.process_instruction("Cut everything", timeline, transcript, history)
    assert res.status == "rejected"
    assert "whole-video cuts are rejected" in res.summary.lower()

def test_hard_case_time_expressions():
    """Parses natural time expressions: 'a minute twenty', '80 seconds'."""
    from app.engine.parser import parse_time_string
    assert parse_time_string("a minute twenty") == 80.0
    assert parse_time_string("80 seconds") == 80.0
    assert parse_time_string("1:20") == 80.0
    assert parse_time_string("half a minute") == 30.0
