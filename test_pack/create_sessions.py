import yaml
from pathlib import Path

sessions_dir = Path("test_pack/sessions")
sessions_dir.mkdir(parents=True, exist_ok=True)

test_sessions = [
    ("session_01_worked_example.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Add a caption 'Welcome!' when I say welcome to the channel"},
            {"say": "Cut the first 30 seconds"},
            {"say": "Remove the part about pricing", "answer": "the second one"},
            {"say": "Speed up from 1:00 to 1:30 by 2x"},
            {"say": "Actually, put the intro back"},
            {"say": "Undo"},
            {"say": "Make that caption red"},
            {"say": "render"}
        ]
    }),
    ("session_02_cut_and_restore_intro.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Cut the first 30 seconds"},
            {"say": "Actually, put the intro back"},
            {"say": "render"}
        ]
    }),
    ("session_03_caption_preservation.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Add a caption 'Intro Note' at 0:10"},
            {"say": "Cut the first 5 seconds"},
            {"say": "Make that caption blue"}
        ]
    }),
    ("session_04_filler_word_removal.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Remove filler words"},
            {"say": "render"}
        ]
    }),
    ("session_05_silence_removal.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Remove silences longer than 1 second"},
            {"say": "render"}
        ]
    }),
    ("session_06_speed_changes_and_cuts.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Speed up from 0:10 to 0:40 by 2x"},
            {"say": "Cut from 0:50 to 1:00"}
        ]
    }),
    ("session_07_ambiguous_pricing_clarification.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Remove the part about pricing", "answer": "the first one"}
        ]
    }),
    ("session_08_background_music_ducking.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Add background music"},
            {"say": "render"}
        ]
    }),
    ("session_09_caption_color_and_styling.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Add a caption 'Spotlight' at 0:20"},
            {"say": "Make that caption yellow"},
            {"say": "Make that caption bigger"}
        ]
    }),
    ("session_10_undo_redo_sequence.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Cut the first 30 seconds"},
            {"say": "Undo"},
            {"say": "Redo"}
        ]
    }),
    ("session_11_out_of_bounds_caption_rejected.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Add a caption 'Late' at 10:00"}
        ]
    }),
    ("session_12_excessive_speed_rejected.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Speed up from 0:10 to 0:20 by 10x"}
        ]
    }),
    ("session_13_cut_everything_rejected.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Cut everything"}
        ]
    }),
    ("session_14_bleep_and_mute.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Bleep out pricing"}
        ]
    }),
    ("session_15_double_cut_idempotency.yaml", {
        "video": "data/uploads/sample_test_video.mp4",
        "steps": [
            {"say": "Cut from 0:30 to 0:40"},
            {"say": "Cut from 0:30 to 0:40"}
        ]
    })
]

for filename, content in test_sessions:
    with open(sessions_dir / filename, "w", encoding="utf-8") as f:
        yaml.dump(content, f, sort_keys=False)

print(f"Generated {len(test_sessions)} session files in test_pack/sessions")
