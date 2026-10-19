# SOURCES.md - Third-Party Dependencies and Licensing

All dependencies and media used in this project are strictly compatible with commercial use. No non-commercial (e.g. CC-BY-NC) or AGPL software is included in the shipped application.

## 1. Core Libraries and Frameworks
| Dependency | Version | License | Commercial Usability | Purpose |
|------------|---------|---------|----------------------|---------|
| **FastAPI** | 0.143.0 | MIT | Approved | High-performance async REST API gateway |
| **Uvicorn** | 0.54.0 | BSD-3-Clause | Approved | ASGI production web server |
| **Pydantic** | 2.14.0 | MIT | Approved | Data parsing, validation, and JSON schema modeling |
| **FFmpeg** | 9.0.2 (Gyan full build) | LGPL / GPL v3 | Approved | High-performance media intake, filter complex rendering |
| **faster-whisper** | 1.2.1 | MIT | Approved | CTranslate2-accelerated word-level speech recognition |
| **PyYAML** | 6.0.3 | MIT | Approved | Session runner YAML script deserialization |
| **jsonschema** | 4.26.0 | MIT | Approved | Timeline validation against standard JSON Schema |
| **pytest** | 9.1.1 | MIT | Approved | Automated unit, regression, and integration testing suite |
| **React** | 18.3.1 | MIT | Approved | Frontend user interface component architecture |
| **Vite** | 5.4.21 | MIT | Approved | Frontend development server and production bundler |

## 2. Models & Data Sources
- **Faster-Whisper Base Model**: `Systran/faster-whisper-base` (Apache 2.0 license). Re-implements OpenAI Whisper under MIT/Apache commercial license.
- **Mock STT Engine**: Built-in deterministic Python generator. Zero third-party dependencies, 0 latency, 100% deterministic test reproducibility.
- **Sample Audio / Video**: Generated via FFmpeg synthetic test generators (`lavfi color` + `lavfi sine`) ensuring clean provenance with zero IP or copyright entanglements.
