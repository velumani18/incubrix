# AI_USE.md - Disclosure of AI Coding Assistants and Tools

As required by IncuBrix Engineering Assessment 2, this document discloses all AI tools and models utilized during development, specifying their scope, purpose, and contribution.

## 1. AI Coding Assistants Used
- **Agentic IDE Assistant**: Gemini 3.8 Flash (Agentic Model) via Antigravity IDE.
  - **Scope & Contributions**:
    - Extracted requirements, constraints, and acceptance criteria from `IncuBrix_Assessment_2_Prompt_Based_Video_Editing_Engine_Brief.pdf`.
    - Synthesized the architectural blueprint for the content-anchored non-destructive timeline engine.
    - Generated boilerplate configurations, FastAPI routers, Pydantic data schemas, and the React + Vite UI.
    - Implemented and verified the automated test pack (15 scripted YAML scenarios and pytest unit/integration tests).
    - Verified runtime execution and fixed fontconfig and path handling in FFmpeg drawtext filter chains.

## 2. In-Engine AI Models
- **Speech-to-Text Transcription**:
  - `faster-whisper-base`: Used for word-level speech transcription with timestamp alignments and confidence scoring.
  - `MockTranscriptionAdapter`: Deterministic mock adapter for instant local execution and CI test suite execution with no API keys.
- **Intent Recognition & LLM Editing Assistant**:
  - Configurable LLM engine (`LLM_PROVIDER`: `mock`, `openai`, `gemini`) powering the Natural-Language Editing Assistant.
  - Generates strict, validated JSON edit proposals conforming to defined schemas (`cut`, `mute`, `caption`, `speed`).
  - Resolves transcript phrase references directly to actual source timestamps; rejects hallucinated phrases or out-of-bounds timestamps.
  - Enforces human-in-the-loop preview and user confirmation before executing any edits on the deterministic editing engine.
  - Records every accepted edit into project history for non-destructive undo and redo.
