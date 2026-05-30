# Roundtable — AI Multi-Agent Collaborative Workspace

> An AI-powered roundtable where 5 expert agents analyze, debate, and verify — producing structured reports with evidence binding and supervisor review.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/tests-230%20passing-brightgreen.svg)](.)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

## What is it?

Roundtable is an AI multi-agent workspace that simulates a professional roundtable meeting. Upload a meeting transcript or audio recording, and 5 concurrent AI experts — Product Manager, Architect, Project Manager, Business Analyst, and Supervisor — analyze it from different perspectives, debate disagreements, and produce a bilingual structured report.

**Key differentiator**: Every claim is bound to source evidence, and a Supervisor agent performs claim-level fact-checking, forbidden-rule enforcement, and cross-agent contradiction detection.

## Architecture

```
Input (Text/Audio)
    │
    ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  ASR Layer  │────▶│  Evidence    │────▶│ Orchestrator │
│ Whisper/MiMo│     │  Builder     │     │  (5 agents)  │
└─────────────┘     └──────────────┘     └──────┬───────┘
                                                 │
                                          asyncio.gather()
                                                 │
                    ┌────────────────────────────┤
                    ▼            ▼           ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │Product   │ │Architect │ │Business  │
              │Manager   │ │          │ │Analyst   │
              └──────────┘ └──────────┘ └──────────┘
                    │            │           │
                    └────────────┼───────────┘
                                 ▼
                    ┌──────────────────────┐
                    │    Supervisor        │
                    │  Evidence Binding    │
                    │  Forbidden Rules     │
                    │  Contradiction Check │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Report Generator    │
                    │  Bilingual Markdown  │
                    │  + SSE Streaming     │
                    └──────────────────────┘
```

## Features

| Feature | Description |
|---------|-------------|
| **5 Concurrent Agents** | PM, Architect, PM2, Business, Supervisor — dispatched via `asyncio.gather()` with per-agent timeout |
| **Evidence Binding** | Every claim links to `chunk_id` in the source transcript via `EvidenceLinker` semantic matching |
| **Supervisor Review** | 3-layer claim validation: evidence binding → forbidden rules → LLM cross-agent contradiction |
| **Debate Mode** | Two-round structured debate with citation integrity checks |
| **SSE Streaming** | Real-time process visibility via Server-Sent Events |
| **Voice Input** | Audio upload → Whisper/MiMo transcription → auto-pipeline |
| **YAML Plugin System** | Add new agents by dropping YAML files in `skills/` |
| **Dual LLM Providers** | DeepSeek + OpenAI-compatible, with automatic fallback to mock mode |
| **Memory System** | Auto-persist high-confidence claims for cross-session knowledge |
| **230 Tests** | Full pytest suite covering models, agents, pipeline, API, and debate |

## Quick Start

```bash
# Install
pip install -e .

# Run with mock (no API key needed)
python -m roundtable.main --mock

# Run with LLM (requires DEEPSEEK_API_KEY)
export DEEPSEEK_API_KEY="sk-..."
uvicorn roundtable.app:app --reload

# Run tests
python -m pytest tests/ -v
```

## API Endpoints (16 REST + 1 WebSocket)

### Core Pipeline
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/session/create` | Create analysis session |
| POST | `/evidence/upload` | Upload transcript segments |
| POST | `/roundtable/run` | Standard 5-agent analysis |
| POST | `/roundtable/debate` | Two-round debate mode |
| POST | `/roundtable/quick` | One-shot personal roundtable |

### Voice & Streaming
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/speak` | Audio upload → transcription |
| WS | `/ws/voice` | Real-time voice session |
| POST | `/roundtable/quick/stream-start` | Start SSE stream |
| GET | `/roundtable/stream/{id}` | SSE process stream |

### Review & Memory
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/session/{id}/pending` | Get pending claims |
| POST | `/review/confirm` | Human-in-the-loop approval |
| GET | `/memory/search` | Search persisted knowledge |

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, Pydantic v2
- **Concurrency**: asyncio, concurrent agent dispatch
- **LLM**: DeepSeek API (OpenAI-compatible), provider abstraction layer
- **ASR**: Whisper API / Xiaomi MiMo audio understanding
- **Persistence**: JSON file storage (sessions, evidence, reports, memory)
- **Streaming**: Server-Sent Events (SSE)
- **Deployment**: Docker, Render/Cloudflare-ready
- **Testing**: pytest (230 tests, models + agents + pipeline + API + debate)

## Project Structure

```
roundtable/
├── app.py            # FastAPI entry — 16 REST endpoints + WebSocket
├── services.py       # Unified business orchestration (CLI/API shared)
├── orchestrator.py   # Multi-agent concurrent dispatch
├── agents.py         # Agent base class + 5 role implementations
├── providers.py      # Provider abstraction, routing, caching
├── config.py         # Config loader with env var substitution
├── debate.py         # Two-round debate + personal roundtable engine
├── supervisor.py     # Claim review: evidence binding + rules + contradictions
├── evidence.py       # Transcript → EvidencePacket chunking
├── models.py         # 8 core Pydantic v2 protocol models
├── report.py         # 10-section structured markdown generator
├── skills.py         # Skill registry + YAML plugin hot-reload
├── memory.py         # Auto-persist high-confidence claims
├── voice/            # Real-time voice protocol + ASR clients
│   ├── protocol.py
│   ├── session.py
│   └── asr_client.py
└── skills/           # YAML agent skill definitions
    ├── architect.yaml
    └── ...
```

## License

MIT
