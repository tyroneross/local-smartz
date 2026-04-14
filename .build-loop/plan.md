# Plan — 2026-04-14 (supersedes prior plan)

## Dependency graph

```
A. Commit RunnableRetry fix (pre-existing)
       │
       ├──────────┬─────────────┐
       ▼          ▼             ▼
B. Researcher   C. Overlay     D. ddgs rename
   → 32B          gate          (py + tests + lock)
       │          │             │
       └──────────┴─────────────┘
                  ▼
       E. Validate: pytest + swift build + grep + smoke
```

B, C, D parallel-safe — no file overlap:
- B: `src/localsmartz/profiles.py`
- C: `app/LocalSmartz/Views/ResearchView.swift`
- D: `pyproject.toml`, `src/localsmartz/tools/web.py`, `tests/test_tools.py`, `uv.lock`

## Tasks

- **A**: Commit `agent.py` RunnableRetry fix solo for clean blame.
- **B**: One-line string change in PROFILES["full"]["agents"]["researcher"]["model"] → `qwen2.5-coder:32b-instruct-q5_K_M`. Lite untouched.
- **C**: Gate `warmupOverlay` on `!isStreaming`. Mid-stream loading_model emits inline ToolCallEntry breadcrumb. Swift build if tooling present, else TAG:UNTESTED.
- **D**: Swap `duckduckgo-search` → `ddgs` in pyproject, web.py import, test_tools.py patch target. `uv lock && uv sync`. Smoke `from ddgs import DDGS`.
- **E**: Full pytest, zero-hit grep for `duckduckgo`, Phase 7 gates.

## Parallelization

All edits <20 lines. Inline edits from main agent; subagent overhead not justified.

## Research verified (T1: pypi.org/pypi/ddgs/json, 2026-04-14)

- ddgs 9.13.1 latest · `from ddgs import DDGS` · `DDGS().text(query, max_results=N)` · Python >=3.10 ✅

## Commit checkpoints

1. RunnableRetry fix (agent.py)
2. Researcher → 32B (profiles.py)
3. Overlay gate (ResearchView.swift)
4. ddgs rename (pyproject, web.py, test_tools.py, uv.lock)
