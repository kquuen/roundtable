# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this

Roundtable Meeting — an AI expert roundtable backend. Users assemble a team of AI agents (产品经理, 架构师, 项目经理, 商业分析, 主审查官) to analyze meeting transcripts from multiple professional perspectives, producing a structured review report with fact-checking.

## Commands

```bash
# Install
pip install -e .

# Run CLI (mock mode, no API key needed)
python -m roundtable.main --mock

# Run CLI (LLM mode, requires DEEPSEEK_API_KEY)
$env:DEEPSEEK_API_KEY="sk-..."   # PowerShell
python -m roundtable.main --agents 3

# Start API server
uvicorn roundtable.app:app --reload

# Run all tests (69 tests)
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_agents.py -v

# Run a single test
python -m pytest tests/test_agents.py::TestProductManager::test_analyze_returns_review -v
```

## Architecture

### Pipeline flow

```
segments (JSON) → chunk_transcript → EvidencePacket
    → orchestrator (async concurrent agent dispatch)
    → supervisor (claim-level fact checking + forbidden rules + contradiction detection)
    → memory (auto-write approved claims)
    → compose_report (structured markdown)
```

### Key modules

- **models.py** — All Pydantic v2 protocol models. 8 core types: `Session`, `TranscriptChunk`, `EvidenceClaim`, `EvidencePacket`, `AgentReview`, `SupervisorReview`, `MemoryWrite`, `PipelineResult`.
- **evidence.py** — Converts raw `{speaker, text}` segments into `EvidencePacket` with sequential chunk IDs.
- **skills.py** — Skill registry. 5 built-in `SkillManifest` definitions + YAML plugin loader (`skills/*.yaml`). Hot-reload via `POST /skills/reload`.
- **agents.py** — `Agent` base class with dual paths: `analyze()` (sync, mock) and `analyze_async()` (LLM via `ProviderAdapter`). Each agent loads its `SkillManifest` from the registry. The `EvidenceLinker` resolves `evidence_text` to `chunk_id` via LLM semantic matching.
- **orchestrator.py** — Dispatches agents concurrently via `asyncio.gather()` with per-agent timeout. Falls back to sync mock path when no provider.
- **supervisor.py** — Three-layer claim review: (1) evidence binding validation, (2) forbidden rule enforcement, (3) LLM-based cross-agent contradiction detection.
- **report.py** — Composes structured markdown with 10 sections: summary, facts, inferences, recommendations, extensions, downgraded, rejected, needs-confirmation, open questions, next actions.
- **team.py** — Session classification (LLM or keyword) → team recommendation from 4 built-in `TeamTemplate` definitions.
- **services.py** — `RoundtableService` unifies the full pipeline. Shared by CLI (`main.py`) and API (`app.py`).
- **registry.py** — `AgentRegistry` factory: maps `skill_id` → Agent class for dynamic dispatch.
- **linker.py** — `EvidenceLinker`: maps `evidence_text` paraphrases to `chunk_id` via LLM semantic matching or Chinese bigram keyword fallback.
- **providers.py** — `ProviderAdapter` wraps DeepSeek API (OpenAI-compatible). Retry with exponential backoff. Builds structured prompts from `SkillManifest`.
- **store.py** — JSON file persistence: `data/sessions/` for sessions + evidence, `reports/` for archived reports.
- **memory.py** — Auto-writes approved high-confidence claims (FACT ≥ 0.8, INFERENCE ≥ 0.85) to `data/memory/`.

### Dual execution modes

Every component supports two modes:
- **Mock** (no API key): keyword-based agent analysis, no LLM calls. Used for testing and offline development.
- **LLM** (requires `DEEPSEEK_API_KEY`): real LLM analysis via DeepSeek API with async concurrency.

### Agent forbidden rules

Each skill has `forbidden` rules enforced by the supervisor. These are keyword/heuristic checks that prevent agents from overstepping their role (e.g., PM must not assert technical architecture feasibility, Architect must not judge product strategy). Rules are loaded from `SkillManifest` and injected into `supervisor.review_claims()`.

### YAML skill plugin system

New agents can be added by placing YAML files in `skills/`. See `skills/architect.yaml` for the format. Fields: `skill_id`, `name`, `role`, `allowed_claim_types`, `allowed_domains`, `forbidden`. Skills are loaded at startup and can be hot-reloaded via `POST /skills/reload`.

## Environment

- Python ≥ 3.11
- `DEEPSEEK_API_KEY` — optional, enables LLM mode (DeepSeek API, OpenAI-compatible)
- Set `PYTHONPATH` to project root before running: `$env:PYTHONPATH = pwd` (PowerShell)
- Data directories are auto-created: `data/sessions/`, `data/memory/`, `reports/`
