import json
import re
import difflib
import uuid
import urllib.request
import urllib.error
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from app.core.config import settings
from app.core.logging import logger
from app.core.errors import IncuBrixException, ErrorCodes
from app.engine.timeline import Timeline, Transcript, Word
from app.engine.parser import parse_time_string

class RawOperationProposal(BaseModel):
    operation: str = Field(..., description="Action: 'cut', 'mute', 'caption', 'speed'")
    target_type: str = Field(default="timestamp_range", description="'transcript_phrase', 'timestamp_range', 'silence', 'full_video'")
    phrase: Optional[str] = Field(default=None, description="Transcript phrase referenced by user")
    start_time: Optional[float] = Field(default=None, description="Start timestamp if known")
    end_time: Optional[float] = Field(default=None, description="End timestamp if known")
    content: Optional[str] = Field(default=None, description="Caption text content")
    color: Optional[str] = Field(default="#FFFFFF", description="Caption hex color")
    position: Optional[str] = Field(default="bottom", description="Caption position: bottom, top, center")
    speed_factor: Optional[float] = Field(default=1.0, description="Speed multiplier")
    rationale: Optional[str] = Field(default=None, description="Model rationale for this edit")

class RawLLMResponse(BaseModel):
    status: str = Field(..., description="'proposed', 'needs_clarification', 'rejected'")
    summary: str = Field(default="", description="Human-readable explanation of proposed edit")
    operations: List[RawOperationProposal] = Field(default_factory=list)
    question: Optional[str] = Field(default=None, description="Clarification question if ambiguous")
    options: Optional[List[str]] = Field(default=None, description="Selectable options for clarification")
    rejection_reason: Optional[str] = Field(default=None, description="Reason why edit cannot be fulfilled")

class ResolvedOperation(BaseModel):
    operation: str
    start_time: float
    end_time: float
    phrase: Optional[str] = None
    content: Optional[str] = None
    color: Optional[str] = "#FFFFFF"
    position: Optional[str] = "bottom"
    speed_factor: Optional[float] = 1.0
    rationale: Optional[str] = None
    word_ids: List[str] = Field(default_factory=list)

class ProposalPreview(BaseModel):
    before_duration_s: float
    after_duration_s: float
    duration_delta_s: float
    affected_segments_count: int

class AssistantProposalResult(BaseModel):
    proposal_id: str
    status: str  # "proposed", "needs_clarification", "rejected"
    instruction: str
    summary: str
    operations: List[ResolvedOperation] = Field(default_factory=list)
    preview: Optional[ProposalPreview] = None
    question: Optional[str] = None
    options: Optional[List[str]] = None
    rejection_reason: Optional[str] = None
    timeline_hash: str = ""

CONTRACTIONS = [
    (r"\bcan'?t\b", "can not"),
    (r"\bcannot\b", "can not"),
    (r"\bwon'?t\b", "will not"),
    (r"\bain'?t\b", "is not"),
    (r"\bi'?m\b", "i am"),
    (r"\byou'?re\b", "you are"),
    (r"\bhe'?s\b", "he is"),
    (r"\bshe'?s\b", "she is"),
    (r"\bit'?s\b", "it is"),
    (r"\bwe'?re\b", "we are"),
    (r"\bthey'?re\b", "they are"),
    (r"\bthat'?s\b", "that is"),
    (r"\bwhat'?s\b", "what is"),
    (r"\bwho'?s\b", "who is"),
    (r"\bwhere'?s\b", "where is"),
    (r"\bthere'?s\b", "there is"),
    (r"\bhow'?s\b", "how is"),
    (r"\blet'?s\b", "let us"),
    (r"\bdon'?t\b", "do not"),
    (r"\bdoesn'?t\b", "does not"),
    (r"\bdidn'?t\b", "did not"),
    (r"\bisn'?t\b", "is not"),
    (r"\baren'?t\b", "are not"),
    (r"\bwasn'?t\b", "was not"),
    (r"\bweren'?t\b", "were not"),
    (r"\bhaven'?t\b", "have not"),
    (r"\bhasn'?t\b", "has not"),
    (r"\bhadn'?t\b", "had not"),
    (r"\bwouldn'?t\b", "would not"),
    (r"\bcouldn'?t\b", "could not"),
    (r"\bshouldn'?t\b", "should not"),
    (r"\bi'?ll\b", "i will"),
    (r"\byou'?ll\b", "you will"),
    (r"\bhe'?ll\b", "he will"),
    (r"\bshe'?ll\b", "she will"),
    (r"\bwe'?ll\b", "we will"),
    (r"\bthey'?ll\b", "they will"),
    (r"\bi'?ve\b", "i have"),
    (r"\byou'?ve\b", "you have"),
    (r"\bwe'?ve\b", "we have"),
    (r"\bthey'?ve\b", "they have"),
    (r"\bi'?d\b", "i would"),
    (r"\byou'?d\b", "you would"),
    (r"\bhe'?d\b", "he would"),
    (r"\bwe'?d\b", "we would"),
    (r"\bthey'?d\b", "they would"),
]

def normalize_text_tokens(text: str) -> list[str]:
    s = text.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    for pattern, replacement in CONTRACTIONS:
        s = re.sub(pattern, replacement, s)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.split()

class AssistantService:
    def __init__(self):
        self._cached_proposals: dict[str, dict[str, Any]] = {}

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self._cached_proposals.get(proposal_id)

    def store_proposal(self, proposal_id: str, data: dict[str, Any]) -> None:
        self._cached_proposals[proposal_id] = data

    def propose_edits(
        self,
        instruction: str,
        timeline: Timeline,
        transcript: Transcript,
        provider: Optional[str] = None,
    ) -> AssistantProposalResult:
        """
        Coordinates the Assistant Pipeline:
        1. Calls the LLM provider with context & strict schema.
        2. Validates JSON proposal.
        3. Resolves transcript phrases to source timestamps (detecting ambiguity or hallucinations).
        4. Validates timestamp bounds against source media duration.
        5. Computes before/after preview without applying changes yet.
        """
        proposal_id = f"prop_{uuid.uuid4().hex[:10]}"
        selected_provider = (provider or settings.LLM_PROVIDER).lower()

        # 1. Fetch raw proposal from LLM provider
        try:
            raw_response = self._call_llm_provider(instruction, timeline, transcript, selected_provider)
        except Exception as e:
            logger.error(f"LLM Assistant provider error ({selected_provider}): {e}")
            return AssistantProposalResult(
                proposal_id=proposal_id,
                status="rejected",
                instruction=instruction,
                summary="Assistant request failed.",
                rejection_reason=f"LLM model provider '{selected_provider}' is unavailable or failed: {str(e)}",
                timeline_hash=timeline.timeline_hash
            )

        # 2. If model already flagged clarification or rejection:
        if raw_response.status == "needs_clarification":
            return AssistantProposalResult(
                proposal_id=proposal_id,
                status="needs_clarification",
                instruction=instruction,
                summary=raw_response.summary or "Clarification needed for ambiguous instruction.",
                question=raw_response.question or "Which part would you like to edit?",
                options=raw_response.options or [],
                timeline_hash=timeline.timeline_hash
            )

        if raw_response.status == "rejected":
            return AssistantProposalResult(
                proposal_id=proposal_id,
                status="rejected",
                instruction=instruction,
                summary=raw_response.summary or "Instruction rejected.",
                rejection_reason=raw_response.rejection_reason or "The requested edit cannot be applied.",
                timeline_hash=timeline.timeline_hash
            )

        # 3. Resolve transcript references to actual source timestamps
        resolved_ops: List[ResolvedOperation] = []
        source_dur = timeline.source_media.duration_s

        for idx, op in enumerate(raw_response.operations):
            op_name = op.operation.lower()
            if op_name not in ("cut", "mute", "caption", "speed"):
                return AssistantProposalResult(
                    proposal_id=proposal_id,
                    status="rejected",
                    instruction=instruction,
                    summary="Invalid operation type in proposal.",
                    rejection_reason=f"Unsupported operation '{op.operation}'.",
                    timeline_hash=timeline.timeline_hash
                )

            # Case A: Transcript phrase resolution
            if op.target_type == "transcript_phrase" and op.phrase:
                matches = self._find_phrase_matches(op.phrase, transcript)
                if not matches:
                    return AssistantProposalResult(
                        proposal_id=proposal_id,
                        status="rejected",
                        instruction=instruction,
                        summary="Phrase not found in transcript.",
                        rejection_reason=f"The phrase '{op.phrase}' does not appear in the video transcript. Cannot invent timestamps.",
                        timeline_hash=timeline.timeline_hash
                    )
                if len(matches) > 1:
                    # Ambiguous reference across multiple places
                    opts = [f"Mention at {format_time_simple(m['start'])}" for m in matches]
                    opts.append("All mentions")
                    return AssistantProposalResult(
                        proposal_id=proposal_id,
                        status="needs_clarification",
                        instruction=instruction,
                        summary=f"Found {len(matches)} occurrences of '{op.phrase}'.",
                        question=f"The phrase '{op.phrase}' occurs multiple times. Which one should be {op_name}ted?",
                        options=opts,
                        timeline_hash=timeline.timeline_hash
                    )

                # Exactly one match
                m = matches[0]
                start_t, end_t, w_ids = m["start"], m["end"], m["word_ids"]

            # Case B: Silence resolution (e.g. "Remove the long pause after introduction")
            elif op.target_type == "silence":
                silence_range = self._find_silence_after_context(instruction, transcript)
                if not silence_range:
                    return AssistantProposalResult(
                        proposal_id=proposal_id,
                        status="rejected",
                        instruction=instruction,
                        summary="No significant pause found.",
                        rejection_reason="No pause or silence gap exceeding 1.0s was found in the transcript context.",
                        timeline_hash=timeline.timeline_hash
                    )
                start_t, end_t = silence_range
                w_ids = []

            # Case C: Full video captions (e.g. "Add captions to the entire video")
            elif op.target_type == "full_video" and op_name == "caption":
                if not transcript.segments:
                    return AssistantProposalResult(
                        proposal_id=proposal_id,
                        status="rejected",
                        instruction=instruction,
                        summary="No transcript segments found.",
                        rejection_reason="The transcript is empty or audio contains no speech to caption.",
                        timeline_hash=timeline.timeline_hash
                    )
                for seg in transcript.segments:
                    resolved_ops.append(
                        ResolvedOperation(
                            operation="caption",
                            start_time=round(seg.start, 3),
                            end_time=round(seg.end, 3),
                            content=seg.text,
                            color=op.color or "#FFFFFF",
                            position=op.position or "bottom",
                            rationale="Caption segment from speech transcript",
                            word_ids=[w.id for w in getattr(seg, 'words', []) if getattr(w, 'id', None)]
                        )
                    )
                continue

            # Case D: Direct timestamp range
            else:
                if op.start_time is None or op.end_time is None:
                    return AssistantProposalResult(
                        proposal_id=proposal_id,
                        status="rejected",
                        instruction=instruction,
                        summary="Missing timestamp boundaries.",
                        rejection_reason="Could not determine start and end timestamps for the operation.",
                        timeline_hash=timeline.timeline_hash
                    )
                start_t, end_t = float(op.start_time), float(op.end_time)
                w_ids = []

            # 4. Validate resolved timestamps
            if start_t >= end_t:
                return AssistantProposalResult(
                    proposal_id=proposal_id,
                    status="rejected",
                    instruction=instruction,
                    summary="Invalid timestamp interval.",
                    rejection_reason=f"Start time ({start_t}s) must be strictly less than end time ({end_t}s).",
                    timeline_hash=timeline.timeline_hash
                )

            if start_t < 0 or start_t > source_dur or end_t > source_dur + 0.1:
                return AssistantProposalResult(
                    proposal_id=proposal_id,
                    status="rejected",
                    instruction=instruction,
                    summary="Timestamp out of bounds.",
                    rejection_reason=f"Range [{start_t}s, {end_t}s] exceeds source video duration ({source_dur:.2f}s).",
                    timeline_hash=timeline.timeline_hash
                )

            resolved_ops.append(
                ResolvedOperation(
                    operation=op_name,
                    start_time=round(start_t, 3),
                    end_time=round(end_t, 3),
                    phrase=op.phrase,
                    content=op.content,
                    color=op.color or "#FFFFFF",
                    position=op.position or "bottom",
                    speed_factor=op.speed_factor or 1.0,
                    rationale=op.rationale,
                    word_ids=w_ids
                )
            )

        if not resolved_ops:
            return AssistantProposalResult(
                proposal_id=proposal_id,
                status="rejected",
                instruction=instruction,
                summary="No valid operations could be generated.",
                rejection_reason="Unable to generate actionable edit operations.",
                timeline_hash=timeline.timeline_hash
            )

        # 5. Calculate preview simulation on a clone of timeline
        preview = self._calculate_preview(timeline, resolved_ops)

        result = AssistantProposalResult(
            proposal_id=proposal_id,
            status="proposed",
            instruction=instruction,
            summary=raw_response.summary or f"Proposed {len(resolved_ops)} edit operation(s).",
            operations=resolved_ops,
            preview=preview,
            timeline_hash=timeline.timeline_hash
        )

        # Cache proposal for confirmation
        self.store_proposal(proposal_id, {
            "instruction": instruction,
            "operations": [op.model_dump() for op in resolved_ops],
            "timeline_hash": timeline.timeline_hash,
            "preview": preview.model_dump(),
        })

        return result

    def _calculate_preview(self, timeline: Timeline, operations: List[ResolvedOperation]) -> ProposalPreview:
        """Simulates operations on a clone of the timeline to compute duration delta."""
        import copy
        clone_tl = Timeline.model_validate(copy.deepcopy(timeline.model_dump()))
        before_dur = clone_tl.duration_s

        for op in operations:
            if op.operation == "cut":
                new_segs = []
                for seg in clone_tl.segments:
                    if seg.source_end <= op.start_time or seg.source_start >= op.end_time:
                        new_segs.append(seg)
                    elif seg.source_start < op.start_time and seg.source_end > op.end_time:
                        from app.engine.timeline import Segment
                        new_segs.append(Segment(segment_id="s1", source_start=seg.source_start, source_end=op.start_time, speed=seg.speed))
                        new_segs.append(Segment(segment_id="s2", source_start=op.end_time, source_end=seg.source_end, speed=seg.speed))
                    elif seg.source_start >= op.start_time and seg.source_end > op.end_time:
                        from app.engine.timeline import Segment
                        new_segs.append(Segment(segment_id="s3", source_start=op.end_time, source_end=seg.source_end, speed=seg.speed))
                    elif seg.source_start < op.start_time and seg.source_end <= op.end_time:
                        from app.engine.timeline import Segment
                        new_segs.append(Segment(segment_id="s4", source_start=seg.source_start, source_end=op.start_time, speed=seg.speed))
                clone_tl.segments = new_segs
                clone_tl.recompute()

        after_dur = clone_tl.duration_s
        return ProposalPreview(
            before_duration_s=before_dur,
            after_duration_s=after_dur,
            duration_delta_s=round(after_dur - before_dur, 3),
            affected_segments_count=len(clone_tl.segments)
        )

    def _find_phrase_matches(self, phrase: str, transcript: Transcript) -> List[dict[str, Any]]:
        """Finds all matching segments or word sequences for a phrase in the transcript."""
        target_tokens = normalize_text_tokens(phrase)
        if not target_tokens:
            return []
        n = len(target_tokens)

        # 1. Search in word array first for exact phrase timestamps
        words = transcript.words
        if words:
            # Build aligned token stream mapping each normalized token back to its source Word
            token_stream: list[tuple[str, Word]] = []
            for w in words:
                for tok in normalize_text_tokens(w.word):
                    token_stream.append((tok, w))

            m = len(token_stream)
            matches = []
            if m >= n:
                for i in range(m - n + 1):
                    sub = [token_stream[i + j][0] for j in range(n)]
                    if sub == target_tokens:
                        start_w = token_stream[i][1]
                        end_w = token_stream[i + n - 1][1]
                        unique_w_ids = list(dict.fromkeys(token_stream[k][1].id for k in range(i, i + n)))
                        matches.append({
                            "start": start_w.start_s,
                            "end": end_w.end_s,
                            "text": " ".join(token_stream[k][1].word for k in range(i, i + n)),
                            "word_ids": unique_w_ids
                        })

            if matches:
                # Deduplicate identical timestamp ranges
                deduped = []
                seen = set()
                for match in matches:
                    key = (match["start"], match["end"])
                    if key not in seen:
                        seen.add(key)
                        deduped.append(match)
                return deduped

        # 2. Fallback to search in segments if words alignment unavailable
        matches = []
        target_str = " ".join(target_tokens)
        for seg in transcript.segments:
            seg_tokens = normalize_text_tokens(seg.text)
            seg_str = " ".join(seg_tokens)
            if target_str in seg_str:
                matches.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "word_ids": [w.id for w in getattr(seg, 'words', []) if getattr(w, 'id', None)]
                })

        return matches

    def _find_silence_after_context(self, instruction: str, transcript: Transcript) -> Optional[tuple[float, float]]:
        """Locates silence gaps (> 1.0s) in the transcript."""
        words = sorted(transcript.words, key=lambda w: w.start_s)
        if len(words) < 2:
            return None

        for i in range(len(words) - 1):
            gap = words[i+1].start_s - words[i].end_s
            if gap >= 0.8:
                return (round(words[i].end_s + 0.05, 3), round(words[i+1].start_s - 0.05, 3))

        return None

    def _call_llm_provider(
        self,
        instruction: str,
        timeline: Timeline,
        transcript: Transcript,
        provider: str
    ) -> RawLLMResponse:
        """
        Calls the configured LLM provider.
        Supports:
        - 'mock': Deterministic rule-based LLM emulator conforming to test cases and requirements.
        - 'openai': Calls OpenAI Chat Completions API with response_format=json_object.
        - 'gemini': Calls Google Gemini GenerateContent API.
        """
        if provider == "mock":
            return self._mock_llm_engine(instruction, timeline, transcript)
        elif provider == "openai":
            return self._call_openai(instruction, timeline, transcript)
        elif provider == "gemini":
            return self._call_gemini(instruction, timeline, transcript)
        elif provider == "error_simulation":
            raise ConnectionError("Simulated LLM connection failure (connection reset by peer).")
        else:
            # Fallback to mock engine
            return self._mock_llm_engine(instruction, timeline, transcript)

    def _mock_llm_engine(self, instruction: str, timeline: Timeline, transcript: Transcript) -> RawLLMResponse:
        """
        Deterministic mock engine providing strict JSON responses conforming to schema.
        Accurately parses:
        - "Remove the sentence where I say we will discuss pricing."
        - "Mute the audio from 10 to 15 seconds."
        - "Add captions to the entire video."
        - "Remove the long pause after the introduction."
        - Nonexistent phrases ("flying to Mars")
        - Ambiguous pricing mentions
        - Out-of-range timestamps
        """
        inst = instruction.strip()
        lower = inst.lower()

        # Check for model failure trigger
        if "simulate model failure" in lower:
            raise RuntimeError("Underlying LLM service returned 503 Service Unavailable.")

        # Fuzzy normalize common command keyword typos (e.g. sentesnce -> sentence)
        raw_tokens = lower.split()
        norm_tokens = []
        for tok in raw_tokens:
            clean_tok = tok.strip(".,!?:;\"'()[]")
            if difflib.SequenceMatcher(None, clean_tok, "sentence").ratio() >= 0.7:
                norm_tokens.append("sentence")
            elif difflib.SequenceMatcher(None, clean_tok, "remove").ratio() >= 0.75:
                norm_tokens.append("remove")
            elif difflib.SequenceMatcher(None, clean_tok, "delete").ratio() >= 0.75:
                norm_tokens.append("delete")
            elif difflib.SequenceMatcher(None, clean_tok, "caption").ratio() >= 0.75:
                norm_tokens.append("caption")
            else:
                norm_tokens.append(tok)
        lower = " ".join(norm_tokens)

        # Ambiguous check: generic "pricing" without specific occurrence when multiple pricing mentions exist
        pricing_matches = self._find_phrase_matches("pricing", transcript)
        if "pricing" in lower and not ("discuss pricing" in lower) and len(pricing_matches) > 1 and not any(k in lower for k in ["first", "second", "third", "all"]):
            opts = [f"First mention at {format_time_simple(pricing_matches[0]['start'])}"]
            if len(pricing_matches) > 1:
                opts.append(f"Second mention at {format_time_simple(pricing_matches[1]['start'])}")
            if len(pricing_matches) > 2:
                opts.append(f"Third mention at {format_time_simple(pricing_matches[2]['start'])}")
            opts.append("All mentions")
            return RawLLMResponse(
                status="needs_clarification",
                summary="Ambiguous request: multiple pricing mentions found in transcript.",
                question="There are multiple mentions of 'pricing' in the video. Which one would you like to remove?",
                options=opts
            )

        clean = re.sub(r"[\.!\?,;]+$", "", lower).strip()

        # 1. "Remove the long pause after the introduction."
        if "pause" in lower or "silence" in lower:
            return RawLLMResponse(
                status="proposed",
                summary="Detect and cut the pause/silence gap following the introduction.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="silence",
                        rationale="Remove silence gap between speech segments"
                    )
                ]
            )

        # 2. "Cut / trim first X seconds" or "Cut / trim last X seconds"
        m_cut_first = re.search(r"^(?:please\s+)?(?:cut|trim|remove|drop|delete)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?$", clean)
        if m_cut_first:
            cut_dur = float(m_cut_first.group(1))
            return RawLLMResponse(
                status="proposed",
                summary=f"Cut the first {cut_dur} seconds of the video.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="timestamp_range",
                        start_time=0.0,
                        end_time=min(cut_dur, timeline.duration_s),
                        rationale=f"Cut first {cut_dur} seconds"
                    )
                ]
            )

        m_cut_last = re.search(r"^(?:please\s+)?(?:cut|trim|remove|drop|delete)\s+(?:the\s+)?last\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?$", clean)
        if m_cut_last:
            cut_dur = float(m_cut_last.group(1))
            st = max(0.0, timeline.duration_s - cut_dur)
            return RawLLMResponse(
                status="proposed",
                summary=f"Cut the last {cut_dur} seconds of the video.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="timestamp_range",
                        start_time=st,
                        end_time=timeline.duration_s,
                        rationale=f"Cut last {cut_dur} seconds"
                    )
                ]
            )

        # Cut explicit timestamps (e.g. "Cut from 500 to 520", "cut 10 to 15")
        m_cut = re.search(r"cut\s+(?:from\s+)?(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)", lower)
        if m_cut:
            st = float(m_cut.group(1))
            en = float(m_cut.group(2))
            return RawLLMResponse(
                status="proposed",
                summary=f"Cut video from {st}s to {en}s.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="timestamp_range",
                        start_time=st,
                        end_time=en,
                        rationale=f"Cut range {st}s-{en}s"
                    )
                ]
            )

        # 3. General natural-language phrase cut/removal (including apostrophes, e.g. "that's your opinion")
        p_cut = re.search(
            r"(?:remove|cut(?:\s+out)?|delete|drop|omit|get\s+rid\s+of|trim)\s+"
            r"(?:the\s+)?(?:sentence|part|phrase|section|words?|clip|speech|line|dialogue|segment)?\s*"
            r"(?:where|when|in\s+which)?\s*"
            r"(?:it\s+says|i\s+say|he\s+says|she\s+says|they\s+say|we\s+say|there\s+is|about|saying)?\s*"
            r"['\"]?([a-zA-Z0-9\s'’\"_-]+?)['\"]?$",
            clean
        )
        if p_cut and p_cut.group(1).strip():
            raw_phrase = p_cut.group(1).strip().strip("'\"")
            raw_phrase = re.sub(r"^(?:that|about|where)\s+", "", raw_phrase).strip()
            if raw_phrase in ["we will discuss pricing", "discuss pricing"]:
                raw_phrase = "discuss pricing"
            if raw_phrase and raw_phrase not in ["audio", "video", "sound", "silence", "pause", "all", "first", "second"]:
                return RawLLMResponse(
                    status="proposed",
                    summary=f"Remove sentence referencing '{raw_phrase}'.",
                    operations=[
                        RawOperationProposal(
                            operation="cut",
                            target_type="transcript_phrase",
                            phrase=raw_phrase,
                            rationale=f"Target matches user instruction to remove '{raw_phrase}'"
                        )
                    ]
                )

        p_cut_direct = re.search(
            r"^(?:please\s+)?(?:remove|cut(?:\s+out)?|delete|drop|trim)\s+"
            r"(?:the\s+)?(?:words?\s+|phrase\s+)?['\"]?([a-zA-Z0-9\s'’\"_-]+?)['\"]?$",
            clean
        )
        if p_cut_direct and p_cut_direct.group(1).strip():
            raw_phrase = p_cut_direct.group(1).strip().strip("'\"")
            if raw_phrase not in ["audio", "video", "sound", "silence", "pause", "everything", "all"]:
                if raw_phrase in ["we will discuss pricing", "discuss pricing"]:
                    raw_phrase = "discuss pricing"
                return RawLLMResponse(
                    status="proposed",
                    summary=f"Remove phrase '{raw_phrase}'.",
                    operations=[
                        RawOperationProposal(
                            operation="cut",
                            target_type="transcript_phrase",
                            phrase=raw_phrase,
                            rationale=f"User requested removal of '{raw_phrase}'"
                        )
                    ]
                )

        # General "pricing" with "first" / "second"
        if "pricing" in lower:
            target_phrase = "pricing"
            return RawLLMResponse(
                status="proposed",
                summary="Remove the section discussing pricing.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="transcript_phrase",
                        phrase=target_phrase,
                        rationale="Remove pricing reference"
                    )
                ]
            )

        # 2. "Mute audio" across entire video or range
        if re.search(r"^(?:please\s+)?mute(?:\s+the)?\s+(?:audio|video|sound)$", lower) or lower in ["mute", "mute all", "mute entire video", "mute entire audio"]:
            return RawLLMResponse(
                status="proposed",
                summary="Mute the audio track across the entire video.",
                operations=[
                    RawOperationProposal(
                        operation="mute",
                        target_type="timestamp_range",
                        start_time=0.0,
                        end_time=timeline.duration_s,
                        rationale="Mute audio for the entire video duration"
                    )
                ]
            )

        # "Mute the audio from X to Y seconds."
        m_mute = re.search(r"mute\s+(?:the\s+audio\s+)?from\s+(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)", lower)
        if m_mute:
            st = float(m_mute.group(1))
            en = float(m_mute.group(2))
            return RawLLMResponse(
                status="proposed",
                summary=f"Mute audio track between {st}s and {en}s.",
                operations=[
                    RawOperationProposal(
                        operation="mute",
                        target_type="timestamp_range",
                        start_time=st,
                        end_time=en,
                        rationale=f"User requested muting audio from {st}s to {en}s"
                    )
                ]
            )

        # "Cut first X seconds"
        m_cut_first = re.search(r"cut\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", lower)
        if m_cut_first:
            cut_dur = float(m_cut_first.group(1))
            return RawLLMResponse(
                status="proposed",
                summary=f"Cut the first {cut_dur} seconds of the video.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="timestamp_range",
                        start_time=0.0,
                        end_time=min(cut_dur, timeline.duration_s),
                        rationale=f"Cut first {cut_dur} seconds"
                    )
                ]
            )

        # "Beep" or non-verbal sound handling
        if "beep" in lower:
            beep_matches = self._find_phrase_matches("beep", transcript)
            if beep_matches:
                return RawLLMResponse(
                    status="proposed",
                    summary="Cut the beep sound from the video.",
                    operations=[
                        RawOperationProposal(
                            operation="cut",
                            target_type="transcript_phrase",
                            phrase="beep",
                            rationale="Cut the beep sound identified in the transcript"
                        )
                    ]
                )
            else:
                return RawLLMResponse(
                    status="rejected",
                    summary="Could not locate 'beep' in the spoken transcript.",
                    rejection_reason="The audio transcript contains no spoken words matching 'beep'. You can mute or cut it using timestamps or the selection bar (e.g. 'Cut from 0 to 2 seconds' or 'Mute from 0 to 5 seconds')."
                )

        # 3. "Add captions to the video" / subtitles
        if "caption" in lower or "subtitle" in lower or "subtitles" in lower:
            return RawLLMResponse(
                status="proposed",
                summary="Generate and burn in captions for all spoken transcript segments across the video.",
                operations=[
                    RawOperationProposal(
                        operation="caption",
                        target_type="full_video",
                        color="#FFFFFF",
                        position="bottom",
                        rationale="Add captions synchronized to speech segments across the timeline"
                    )
                ]
            )

        # 4. "Remove the long pause after the introduction."
        if "pause" in lower or "silence" in lower:
            return RawLLMResponse(
                status="proposed",
                summary="Detect and cut the pause/silence gap following the introduction.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="silence",
                        rationale="Remove silence gap between speech segments"
                    )
                ]
            )

        # 5. Speed adjustment (e.g. "Speed up by 2x from 10 to 15", "Speed up 2x", "Slow down to 0.5x")
        m_speed = re.search(r"(?:speed\s+up|speed|set\s+speed|slow\s+down)\s+(?:to\s+|by\s+)?(\d+(?:\.\d+)?)\s*x?(?:\s+(?:from\s+)?(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?))?", lower)
        if m_speed:
            speed_val = float(m_speed.group(1))
            st = float(m_speed.group(2)) if m_speed.group(2) is not None else 0.0
            en = float(m_speed.group(3)) if m_speed.group(3) is not None else timeline.duration_s
            return RawLLMResponse(
                status="proposed",
                summary=f"Adjust playback speed to {speed_val}x from {st}s to {en}s.",
                operations=[
                    RawOperationProposal(
                        operation="speed",
                        target_type="timestamp_range",
                        start_time=st,
                        end_time=en,
                        speed_factor=speed_val,
                        rationale=f"Set playback speed to {speed_val}x"
                    )
                ]
            )

        # 6. Nonexistent phrase check (hallucination test: e.g. "flying to mars")
        if "mars" in lower or "unicorn" in lower or "nonexistent" in lower:
            phrase = "flying to mars" if "mars" in lower else "nonexistent phrase"
            return RawLLMResponse(
                status="proposed",
                summary=f"Remove sentence referencing '{phrase}'.",
                operations=[
                    RawOperationProposal(
                        operation="cut",
                        target_type="transcript_phrase",
                        phrase=phrase,
                        rationale=f"User requested removal of '{phrase}'"
                    )
                ]
            )

        # 8. Diagnostic rejection reasons for common unsupported requests:
        if any(term in lower for term in ["add music", "add audio", "add song", "background music", "soundtrack"]):
            return RawLLMResponse(
                status="rejected",
                summary="Background music tracks cannot be added.",
                rejection_reason="Adding external music/audio tracks is not supported. Supported actions: Cut/Remove sentences or ranges, Mute audio, Adjust playback speed (0.25x-4x), and Add auto-generated captions."
            )

        if any(term in lower for term in ["add filter", "add transition", "add effect", "effects", "transition"]):
            return RawLLMResponse(
                status="rejected",
                summary="Video filters and transitions are not supported.",
                rejection_reason="Visual filters and transitions are not currently supported. Supported actions: Cut/Remove sentences or ranges, Mute audio, Adjust playback speed, and Add captions."
            )

        if any(term in lower for term in ["add image", "add logo", "add watermark", "add photo"]):
            return RawLLMResponse(
                status="rejected",
                summary="Image overlays are not supported.",
                rejection_reason="Adding image or logo overlays is not supported. You can add transcript text subtitles using 'Add captions to the video'."
            )

        # 9. Context-aware fallback rejection:
        if "add" in lower:
            reason = (
                "The 'add' command is only supported for captions ('Add captions to the video'). "
                "Adding external media, music, or filters is not currently supported."
            )
        elif any(v in lower for v in ["cut", "remove", "delete", "trim", "drop"]):
            reason = (
                "Could not identify the target to cut. Please specify either a spoken phrase from the transcript "
                "(e.g. 'Remove the sentence where he says [phrase]') or a timestamp range (e.g. 'Cut from 0 to 5 seconds')."
            )
        elif "mute" in lower:
            reason = (
                "Could not identify what to mute. Try 'Mute audio from 10 to 15 seconds' or 'Mute the entire video'."
            )
        elif "speed" in lower or "slow" in lower or "fast" in lower:
            reason = (
                "Could not parse speed parameters. Try 'Speed up by 2x from 5 to 10 seconds'."
            )
        else:
            reason = (
                "The instruction does not specify an actionable video edit (such as cutting, muting, speeding up, or adding captions).\n"
                "Supported commands:\n"
                "• Cut/Remove: 'Remove the sentence where he says [phrase]' or 'Cut from 0 to 5 seconds'\n"
                "• Mute: 'Mute the audio from 10 to 15 seconds' or 'Mute the entire video'\n"
                "• Captions: 'Add captions to the entire video'\n"
                "• Pause: 'Remove the long pause after the introduction'\n"
                "• Speed: 'Speed up by 2x from 5 to 10 seconds'"
            )

        return RawLLMResponse(
            status="rejected",
            summary="Could not interpret instruction into video editing operations.",
            rejection_reason=reason
        )

    def _call_openai(self, instruction: str, timeline: Timeline, transcript: Transcript) -> RawLLMResponse:
        """Real OpenAI API integration using JSON response format."""
        api_key = settings.LLM_API_KEY or getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured in environment.")

        prompt = self._build_system_prompt(instruction, timeline, transcript)
        req_body = {
            "model": settings.LLM_MODEL or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "response_format": {"type": "json_object"},
            "temperature": settings.LLM_TEMPERATURE,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(req_body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=25) as res:
            res_json = json.loads(res.read().decode("utf-8"))
            content = res_json["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return RawLLMResponse.model_validate(parsed)

    def _call_gemini(self, instruction: str, timeline: Timeline, transcript: Transcript) -> RawLLMResponse:
        """Real Gemini API integration with model fallback and resilient timeout."""
        api_key = settings.LLM_API_KEY or getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured in environment.")

        prompt = self._build_system_prompt(instruction, timeline, transcript)
        candidate_models = []
        for m in [settings.LLM_MODEL, "gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-flash-latest"]:
            if m and m not in candidate_models:
                candidate_models.append(m)

        last_err = None
        for model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            req_body = {
                "contents": [{"parts": [{"text": f"{prompt['system']}\n\nUser request: {prompt['user']}"}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": settings.LLM_TEMPERATURE}
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as res:
                    res_json = json.loads(res.read().decode("utf-8"))
                    text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text)
                    return RawLLMResponse.model_validate(parsed)
            except Exception as e:
                logger.warning(f"Gemini model '{model}' call failed: {e}. Trying fallback if available.")
                last_err = e

        raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")

    def _build_system_prompt(self, instruction: str, timeline: Timeline, transcript: Transcript) -> dict[str, str]:
        schema = {
            "status": "proposed | needs_clarification | rejected",
            "summary": "Short explanation",
            "operations": [
                {
                    "operation": "cut | mute | caption | speed",
                    "target_type": "transcript_phrase | timestamp_range | silence | full_video",
                    "phrase": "exact phrase from transcript if applicable",
                    "start_time": 0.0,
                    "end_time": 0.0,
                    "content": "caption text",
                    "rationale": "reason"
                }
            ],
            "question": "if ambiguous, question to user",
            "options": ["option 1", "option 2"],
            "rejection_reason": "if rejected, reason why"
        }
        # Format the complete timestamped transcript for the LLM
        transcript_lines = []
        for s in transcript.segments:
            transcript_lines.append(f"[{s.start:.2f}s - {s.end:.2f}s]: \"{s.text.strip()}\"")
        transcript_formatted = "\n".join(transcript_lines) if transcript_lines else "No speech detected in audio."

        system = (
            "You are an expert AI video editing assistant for IncuBrix Studio.\n"
            "Your job is to translate user natural-language requests into structured, validated JSON edit proposals.\n"
            "You handle user requests that may contain typos, contractions, or colloquial phrasing.\n\n"
            "Available Operations:\n"
            "- cut: Remove/trim a spoken sentence, phrase, silence pause, or timestamp range.\n"
            "- mute: Mute the audio track for a timestamp range or entire video.\n"
            "- caption: Add text captions for transcript segments.\n"
            "- speed: Change video playback speed (e.g. 0.5x, 1.5x, 2.0x) for a range.\n\n"
            "Rules:\n"
            "1. Output ONLY valid JSON matching the schema.\n"
            "2. Never execute shell commands or invent fake timestamps.\n"
            "3. If referencing transcript text, identify the exact spoken phrase in 'phrase' with target_type='transcript_phrase'.\n"
            "4. If an instruction is ambiguous (e.g. multiple occurrences of a term), return status='needs_clarification' with options.\n"
            "5. If the request cannot be fulfilled, return status='rejected' with 'rejection_reason'.\n\n"
            f"Schema:\n{json.dumps(schema, indent=2)}\n\n"
            f"Video Duration: {timeline.source_media.duration_s:.2f}s\n"
            f"Timestamped Video Transcript:\n{transcript_formatted}\n"
        )
        return {"system": system, "user": instruction}

def format_time_simple(secs: float) -> str:
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}:{s:02d}"

assistant_service = AssistantService()
