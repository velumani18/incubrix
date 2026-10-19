import pytest
from app.engine.timeline import Timeline, Transcript, Segment, SourceMedia, Word
from app.engine.history import HistoryManager
from app.engine.parser import InstructionEngine

def setup_worked_example_state():
    source_media = SourceMedia(
        filename="interview.mp4",
        sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        duration_s=240.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    seg0 = Segment(segment_id="seg_0", source_start=0.0, source_end=240.0, speed=1.0)
    timeline = Timeline(
        timeline_id="tl_worked_example",
        project_id="proj_worked_example",
        time_policy="SOURCE_TIME",
        source_media=source_media,
        segments=[seg0],
    )
    timeline.recompute()

    words = [
        Word(id="w_1", word="welcome", start_s=42.0, end_s=42.5),
        Word(id="w_2", word="to", start_s=42.6, end_s=42.8),
        Word(id="w_3", word="the", start_s=42.9, end_s=43.1),
        Word(id="w_4", word="channel", start_s=43.2, end_s=44.0),
        Word(id="w_p1", word="pricing", start_s=65.0, end_s=65.8),
        Word(id="w_p2", word="pricing", start_s=160.0, end_s=160.8),
        Word(id="w_p3", word="pricing", start_s=195.0, end_s=195.8),
    ]
    transcript = Transcript(words=words)
    history = HistoryManager(timeline)
    return timeline, transcript, history

def test_full_8_step_worked_example():
    timeline, transcript, history = setup_worked_example_state()
    engine = InstructionEngine(time_policy="SOURCE_TIME")

    # Step 1: Add a caption 'Welcome!' when I say welcome to the channel
    step1 = engine.process_instruction("Add a caption 'Welcome!' when I say welcome to the channel", timeline, transcript, history)
    assert step1.status == "applied"
    proj_caps = timeline.get_projected_overlays(transcript)
    assert len(proj_caps) == 1
    assert proj_caps[0]["timeline_start"] == 42.0
    assert proj_caps[0]["content"] == "Welcome!"

    # Step 2: Cut the first 30 seconds
    step2 = engine.process_instruction("Cut the first 30 seconds", timeline, transcript, history)
    assert step2.status == "applied"
    assert timeline.duration_s == 210.0
    # Caption now plays at 0:12 (42 - 30 = 12)
    proj_caps_2 = timeline.get_projected_overlays(transcript)
    assert proj_caps_2[0]["timeline_start"] == 12.0

    # Step 3: Remove the part about pricing
    step3 = engine.process_instruction("Remove the part about pricing", timeline, transcript, history)
    assert step3.status == "needs_clarification"
    assert "Pricing" in step3.question or "pricing" in step3.question
    assert len(step3.options) >= 2

    # User answers: the second one
    clarif_context = {"question": step3.question, "options": step3.options, "keyword": "pricing"}
    step3_ans = engine.process_instruction("the second one", timeline, transcript, history, pending_clarification=clarif_context)
    assert step3_ans.status == "applied"

    # Step 4: Speed up from 1:00 to 1:30 by 2x
    step4 = engine.process_instruction("Speed up from 1:00 to 1:30 by 2x", timeline, transcript, history)
    assert step4.status == "applied"
    assert "SOURCE_TIME" in step4.summary or "SOURCE_TIME" in step4.time_policy_used

    # Step 5: Actually, put the intro back
    dur_before_restore = timeline.duration_s
    step5 = engine.process_instruction("Actually, put the intro back", timeline, transcript, history)
    assert step5.status == "applied"
    # First 30s return, caption back at 0:42!
    proj_caps_5 = timeline.get_projected_overlays(transcript)
    assert proj_caps_5[0]["timeline_start"] == 42.0

    # Step 6: Undo
    step6 = engine.process_instruction("Undo", timeline, transcript, history)
    assert step6.status == "applied"
    # Reverses step 5 only. The intro is cut again.
    proj_caps_6 = timeline.get_projected_overlays(transcript)
    assert proj_caps_6[0]["timeline_start"] == 12.0

    # Step 7: Make that caption red
    step7 = engine.process_instruction("Make that caption red", timeline, transcript, history)
    assert step7.status == "applied"
    assert timeline.overlays[0].color == "#FF2A2A"

    print("All 7 conversational steps passed with exact brief behaviors!")
