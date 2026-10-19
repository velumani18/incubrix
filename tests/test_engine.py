import pytest
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Anchor, Word
from app.engine.operations import cut_range, speed_range, add_caption, style_caption
from app.engine.history import HistoryManager
from app.engine.parser import InstructionEngine

def create_sample_timeline_and_transcript():
    source_media = SourceMedia(
        filename="test_video.mp4",
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        duration_s=240.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    seg0 = Segment(segment_id="seg_0", source_start=0.0, source_end=240.0, speed=1.0)
    timeline = Timeline(
        timeline_id="tl_test",
        project_id="proj_test",
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg0],
    )
    timeline.recompute()

    words = [
        Word(id="w_0", word="hello", start_s=5.0, end_s=5.5),
        Word(id="w_1", word="welcome", start_s=42.0, end_s=42.5),
        Word(id="w_2", word="to", start_s=42.6, end_s=42.8),
        Word(id="w_3", word="the", start_s=42.9, end_s=43.1),
        Word(id="w_4", word="channel", start_s=43.2, end_s=44.0),
        Word(id="w_5", word="pricing", start_s=65.0, end_s=65.8),
        Word(id="w_6", word="pricing", start_s=160.0, end_s=160.8),
        Word(id="w_7", word="pricing", start_s=195.0, end_s=195.8),
    ]
    transcript = Transcript(words=words)
    history = HistoryManager(timeline)
    return timeline, transcript, history

def test_must_have_1_never_damage_original():
    """Must-Have 1: Original source media sha256 and duration are never modified."""
    timeline, transcript, history = create_sample_timeline_and_transcript()
    original_sha = timeline.source_media.sha256
    original_duration = timeline.source_media.duration_s

    # Perform multiple edits
    cut_range(timeline, 0.0, 30.0, transcript)
    speed_range(timeline, 60.0, 90.0, 2.0, transcript)

    assert timeline.source_media.sha256 == original_sha
    assert timeline.source_media.duration_s == original_duration
    assert timeline.duration_s < original_duration

def test_must_have_2_content_anchoring():
    """
    Must-Have 2: Edits stick to content, not to clock position.
    A caption added at 0:42-0:44 must automatically project to 0:12-0:14
    after the first 30 seconds are cut.
    """
    timeline, transcript, history = create_sample_timeline_and_transcript()

    # Add caption anchored to words 'welcome to the channel'
    anchor = Anchor(type="word_range", start_word_id="w_1", end_word_id="w_4")
    add_caption(timeline, content="Welcome!", anchor=anchor)

    # Check projection before cut
    proj_before = timeline.get_projected_overlays(transcript)
    assert len(proj_before) == 1
    assert proj_before[0]["timeline_start"] == 42.0

    # Cut the first 30 seconds
    cut_range(timeline, 0.0, 30.0, transcript)

    # Check projection after cut: 42 - 30 = 12
    proj_after = timeline.get_projected_overlays(transcript)
    assert len(proj_after) == 1
    assert proj_after[0]["timeline_start"] == 12.0
    assert proj_after[0]["content"] == "Welcome!"

def test_must_have_3_three_way_answers():
    """Must-Have 3: Every instruction returns applied, needs_clarification, or rejected."""
    timeline, transcript, history = create_sample_timeline_and_transcript()
    engine = InstructionEngine()

    # 1. Applied
    res1 = engine.process_instruction("Cut the first 30 seconds", timeline, transcript, history)
    assert res1.status == "applied"
    assert res1.duration_s == 210.0

    # 2. Needs clarification (ambiguous pricing mentions)
    res2 = engine.process_instruction("Remove the part about pricing", timeline, transcript, history)
    assert res2.status == "needs_clarification"
    assert res2.question is not None
    assert len(res2.options) >= 2

    # 3. Rejected (impossible or out of bounds)
    res3 = engine.process_instruction("Speed up from 1:00 to 1:30 by 50x", timeline, transcript, history)
    assert res3.status == "rejected"
    assert "excessive" in res3.summary.lower()

def test_must_have_4_determinism():
    """Must-Have 4: Running identical session 3 times produces 100% identical timeline_hash."""
    hashes = []
    for _ in range(3):
        tl, tr, hist = create_sample_timeline_and_transcript()
        cut_range(tl, 0.0, 30.0, tr)
        speed_range(tl, 60.0, 90.0, 2.0, tr)
        add_caption(tl, "Welcome!", Anchor(type="word_range", start_word_id="w_1", end_word_id="w_4"))
        hashes.append(tl.timeline_hash)

    assert hashes[0] == hashes[1] == hashes[2]
    assert len(hashes[0]) == 64

def test_must_have_6_undo_redo():
    """Must-Have 6: 1 instruction = 1 undo; new edit clears redo."""
    timeline, transcript, history = create_sample_timeline_and_transcript()
    engine = InstructionEngine()

    init_dur = timeline.duration_s
    engine.process_instruction("Cut the first 30 seconds", timeline, transcript, history)
    assert timeline.duration_s == init_dur - 30.0

    # Undo
    undo_res = engine.process_instruction("Undo", timeline, transcript, history)
    assert undo_res.status == "applied"
    assert timeline.duration_s == init_dur

    # Redo
    redo_res = engine.process_instruction("Redo", timeline, transcript, history)
    assert redo_res.status == "applied"
    assert timeline.duration_s == init_dur - 30.0
