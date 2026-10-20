import json
import hashlib
from typing import Any
from pydantic import BaseModel, Field, computed_field

class Word(BaseModel):
    id: str
    word: str
    start_s: float
    end_s: float
    speaker: str = "speaker_0"
    confidence: float = 1.0

    @property
    def text(self) -> str:
        return self.word

class WordToken(BaseModel):
    text: str
    start: float
    end: float
    confidence: float = 1.0
    id: str | None = None
    speaker: str = "speaker_0"

class TranscriptSegment(BaseModel):
    id: str | None = None
    start: float
    end: float
    text: str
    speaker: str = "speaker_0"
    words: list[WordToken] = Field(default_factory=list)
    has_word_alignment: bool = True

class Transcript(BaseModel):
    project_id: str = ""
    language_detected: str = "en"
    transcript_language: str = "en"
    task: str = "transcribe"  # "transcribe" or "translate"
    segments: list[TranscriptSegment] = Field(default_factory=list)
    words: list[Word] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        self.sync_words_and_segments()

    def sync_words_and_segments(self) -> None:
        """Keeps segments and flat words array seamlessly synchronized."""
        if self.segments and not self.words:
            idx = 0
            flat: list[Word] = []
            for seg in self.segments:
                if seg.words:
                    for wt in seg.words:
                        w_id = wt.id or f"w_{idx}_{int(wt.start*1000)}"
                        flat.append(
                            Word(
                                id=w_id,
                                word=wt.text,
                                start_s=wt.start,
                                end_s=wt.end,
                                speaker=seg.speaker,
                                confidence=wt.confidence,
                            )
                        )
                        idx += 1
                else:
                    # Segment level fallback: treat segment as a single word token
                    flat.append(
                        Word(
                            id=f"w_seg_{idx}",
                            word=seg.text,
                            start_s=seg.start,
                            end_s=seg.end,
                            speaker=seg.speaker,
                            confidence=1.0,
                        )
                    )
                    idx += 1
            self.words = flat
        elif self.words and not self.segments:
            # Group words into sentence-like segments
            curr_words: list[WordToken] = []
            seg_start = self.words[0].start_s if self.words else 0.0
            for w in self.words:
                curr_words.append(
                    WordToken(
                        text=w.word,
                        start=w.start_s,
                        end=w.end_s,
                        confidence=w.confidence,
                        id=w.id,
                        speaker=w.speaker,
                    )
                )
                if w.word.endswith((".", "!", "?")) or len(curr_words) >= 12:
                    text_str = " ".join(wt.text for wt in curr_words)
                    self.segments.append(
                        TranscriptSegment(
                            id=f"seg_{len(self.segments)}",
                            start=seg_start,
                            end=curr_words[-1].end,
                            text=text_str,
                            speaker=curr_words[0].speaker,
                            words=curr_words,
                            has_word_alignment=True,
                        )
                    )
                    curr_words = []
                    seg_start = w.end_s
            if curr_words:
                text_str = " ".join(wt.text for wt in curr_words)
                self.segments.append(
                    TranscriptSegment(
                        id=f"seg_{len(self.segments)}",
                        start=seg_start,
                        end=curr_words[-1].end,
                        text=text_str,
                        speaker=curr_words[0].speaker,
                        words=curr_words,
                        has_word_alignment=True,
                    )
                )

    def to_schema_dict(self) -> dict[str, Any]:
        """Serializes to the exact JSON schema requested by user."""
        return {
            "project_id": self.project_id,
            "language_detected": self.language_detected,
            "transcript_language": self.transcript_language,
            "task": self.task,
            "segments": [
                {
                    "start": round(s.start, 3),
                    "end": round(s.end, 3),
                    "text": s.text,
                    "has_word_alignment": s.has_word_alignment,
                    "words": [
                        {
                            "text": wt.text,
                            "start": round(wt.start, 3),
                            "end": round(wt.end, 3),
                            "confidence": round(wt.confidence, 3),
                        }
                        for wt in s.words
                    ]
                }
                for s in self.segments
            ]
        }

    def find_words_by_phrase(self, phrase: str) -> list[list[Word]]:
        """Finds all matching sequences of words for a given phrase (case-insensitive)."""
        clean_target = [w.lower().strip(".,!?:;\"'()[]") for w in phrase.split()]
        if not clean_target:
            return []
        
        matches = []
        n = len(clean_target)
        for i in range(len(self.words) - n + 1):
            window = [self.words[i + j].word.lower().strip(".,!?:;\"'()[]") for j in range(n)]
            if window == clean_target:
                matches.append(self.words[i : i + n])
        return matches

    def find_words_by_source_range(self, start_s: float, end_s: float) -> list[Word]:
        return [w for w in self.words if (w.start_s >= start_s and w.end_s <= end_s) or (w.start_s <= start_s < w.end_s) or (start_s <= w.start_s < end_s)]

class Segment(BaseModel):
    segment_id: str
    source_start: float
    source_end: float
    speed: float = 1.0
    is_muted: bool = False
    volume: float = 1.0
    speaker: str | None = None

    @computed_field
    @property
    def timeline_duration(self) -> float:
        return (self.source_end - self.source_start) / max(self.speed, 0.01)

class Anchor(BaseModel):
    type: str  # "word_range", "phrase_match", "source_range"
    start_word_id: str | None = None
    end_word_id: str | None = None
    phrase: str | None = None
    fallback_source_start: float | None = None
    fallback_source_end: float | None = None

class Overlay(BaseModel):
    overlay_id: str
    type: str = "caption"  # caption or text_overlay
    content: str
    color: str = "#FFFFFF"
    font_size: int = 36
    position: str = "bottom"
    anchor: Anchor

class AudioEffect(BaseModel):
    effect_id: str
    type: str  # "bleep", "mute"
    anchor: Anchor
    bleep_frequency: int = 1000

class AudioTrack(BaseModel):
    track_id: str
    type: str = "background_music"
    file_path: str
    volume: float = 0.15
    ducking: dict[str, Any] = Field(default_factory=lambda: {
        "enabled": True,
        "ducked_volume": 0.04,
        "attack_ms": 150,
        "release_ms": 400
    })

class SourceMedia(BaseModel):
    filename: str
    sha256: str
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool
    sample_rate: int = 44100

class Timeline(BaseModel):
    schema_version: str = "1.0.0"
    timeline_id: str
    project_id: str
    time_policy: str = "SOURCE_TIME"
    source_media: SourceMedia
    segments: list[Segment] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    audio_effects: list[AudioEffect] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    duration_s: float = 0.0
    timeline_hash: str = ""

    def recompute(self) -> None:
        """Recomputes total timeline duration and deterministic hash."""
        # 1. Total duration
        total = sum(seg.timeline_duration for seg in self.segments)
        self.duration_s = round(total, 4)

        # 2. Canonical serialization for determinism (Must-Have 4)
        canonical_dict = {
            "schema_version": self.schema_version,
            "source_sha256": self.source_media.sha256,
            "time_policy": self.time_policy,
            "segments": [
                {
                    "start": round(s.source_start, 4),
                    "end": round(s.source_end, 4),
                    "speed": round(s.speed, 4),
                    "muted": s.is_muted,
                    "vol": round(s.volume, 4),
                }
                for s in self.segments
            ],
            "overlays": [
                {
                    "content": o.content,
                    "color": o.color,
                    "font_size": o.font_size,
                    "position": o.position,
                    "anchor": o.anchor.model_dump(),
                }
                for o in self.overlays
            ],
            "audio_effects": [
                {
                    "type": e.type,
                    "anchor": e.anchor.model_dump(),
                }
                for e in self.audio_effects
            ],
            "audio_tracks": [
                {
                    "vol": round(t.volume, 4),
                    "file": t.file_path,
                }
                for t in self.audio_tracks
            ],
            "duration_s": self.duration_s,
        }
        encoded = json.dumps(canonical_dict, sort_keys=True).encode("utf-8")
        self.timeline_hash = hashlib.sha256(encoded).hexdigest()

    def resolve_anchor_source_span(self, anchor: Anchor, transcript: Transcript) -> tuple[float, float] | None:
        """Returns the original raw source time span (start_s, end_s) for an anchor."""
        if anchor.type == "word_range":
            w_map = {w.id: w for w in transcript.words}
            w_start = w_map.get(anchor.start_word_id or "")
            w_end = w_map.get(anchor.end_word_id or "")
            if w_start and w_end:
                return (w_start.start_s, w_end.end_s)
        elif anchor.type == "phrase_match" and anchor.phrase:
            matches = transcript.find_words_by_phrase(anchor.phrase)
            if matches:
                # First match
                return (matches[0][0].start_s, matches[0][-1].end_s)
        elif anchor.type == "source_range":
            if anchor.fallback_source_start is not None and anchor.fallback_source_end is not None:
                return (anchor.fallback_source_start, anchor.fallback_source_end)
        
        # Fallback
        if anchor.fallback_source_start is not None and anchor.fallback_source_end is not None:
            return (anchor.fallback_source_start, anchor.fallback_source_end)
        return None

    def source_time_to_timeline_time(self, src_time: float) -> float | None:
        """
        Maps a source time in seconds to the current projected timeline clock position.
        Returns None if that source time is currently cut out.
        """
        accumulated_tl = 0.0
        for seg in self.segments:
            if seg.source_start <= src_time <= seg.source_end:
                offset_in_seg = (src_time - seg.source_start) / seg.speed
                return accumulated_tl + offset_in_seg
            accumulated_tl += seg.timeline_duration
        return None

    def timeline_time_to_source_time(self, tl_time: float) -> float | None:
        """
        Maps a timeline playback clock position to source media time.
        """
        accumulated_tl = 0.0
        for seg in self.segments:
            seg_dur = seg.timeline_duration
            if accumulated_tl <= tl_time <= accumulated_tl + seg_dur:
                offset_in_seg = (tl_time - accumulated_tl) * seg.speed
                return seg.source_start + offset_in_seg
            accumulated_tl += seg_dur
        return None

    def get_projected_overlays(self, transcript: Transcript) -> list[dict[str, Any]]:
        """
        Computes dynamic start and end playback times on the current timeline
        for each active overlay. Captions whose underlying content was cut are omitted
        or marked inactive.
        """
        projected = []
        for ov in self.overlays:
            span = self.resolve_anchor_source_span(ov.anchor, transcript)
            if not span:
                continue
            src_start, src_end = span
            tl_start = self.source_time_to_timeline_time(src_start)
            tl_end = self.source_time_to_timeline_time(src_end)

            # If both start and end exist on the timeline:
            if tl_start is not None and tl_end is not None and tl_end > tl_start:
                # Ensure readable minimum duration for sped-up speech (brief specifies ~0.7s)
                min_dur = 0.7
                if (tl_end - tl_start) < min_dur:
                    tl_end = min(self.duration_s, tl_start + min_dur)
                
                projected.append({
                    "overlay_id": ov.overlay_id,
                    "type": ov.type,
                    "content": ov.content,
                    "color": ov.color,
                    "font_size": ov.font_size,
                    "position": ov.position,
                    "timeline_start": round(tl_start, 3),
                    "timeline_end": round(tl_end, 3),
                    "source_start": round(src_start, 3),
                    "source_end": round(src_end, 3),
                })
        return projected
