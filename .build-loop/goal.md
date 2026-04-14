# Goal: Three post-pipeline polish items

Date: 2026-04-14
Branch: main
Supersedes: prior `.build-loop/goal.md` (2026-04-13 "tool execution into pipeline specialists" — that work landed in commits 9eeab1c → 4f97a0e → c13aab7).

## Items

### 1. Graph pipeline: eliminate mid-run VRAM swap

**Problem**: `researcher` pinned to `qwen3:8b` while `analyzer` / `fact_checker` / `writer` all run on `qwen2.5-coder:32b`. The pipeline orders researcher → analyzer, so every round pays the cost of hot-swapping a 32 B model into VRAM after warming an 8 B model.

**Fix**: In `profiles.py::PROFILES["full"]["agents"]["researcher"]`, change `model` from `"qwen3:8b-q4_K_M"` → `"qwen2.5-coder:32b-instruct-q5_K_M"`. All four graph specialists now share one model; zero swaps per round. Planner stays 8 B (it's the fast-path, not part of the graph).

Lite profile untouched — all roles already 8 B.

### 2. Mid-stream model-load doesn't hide output

**Problem**: `warmupOverlay` is a full-screen `.overlay {}` in `ResearchView.swift:101-105`. When an SSE `status(stage="loading_model")` arrives mid-stream for a specialist swap, the overlay covers `OutputView` — user perceives this as the output resetting. Fixed in item #1's case for full profile, but the overlay contract is still wrong: blocking UX is only appropriate for cold start, not mid-stream swaps.

**Fix**: In `ResearchView.swift`, gate the full-screen `warmupOverlay` on `!isStreaming`. When streaming, surface model-load inline as a small tool-call entry (reuse `ToolCallEntry` infra) so the breadcrumb stays visible alongside prior output. Cold-start overlay behavior unchanged.

### 3. Replace `duckduckgo-search` with `ddgs`

**Problem**: Upstream renamed the package. Current `duckduckgo-search>=7.0.0` still works but emits a deprecation RuntimeWarning that `tools/web.py:239` already filters for.

**Fix**:
- `pyproject.toml:28`: `"duckduckgo-search>=7.0.0"` → `"ddgs>=9.0"`
- `tools/web.py:240`: `from duckduckgo_search import DDGS` → `from ddgs import DDGS`
- `tools/web.py:242`: error message already says "pip install ddgs" — fine
- `tools/web.py:239`: drop the "renamed" warning filter (no longer emitted)
- `tests/test_tools.py:153`: `patch("duckduckgo_search.DDGS")` → `patch("ddgs.DDGS")`
- `uv.lock`: regenerate via `uv lock`

T1-verified (PyPI): `from ddgs import DDGS`, `DDGS().text(query, max_results=N)` — API preserved. Python >=3.10 required (we're on 3.12/3.14 — OK).

## Scoring Criteria

| # | Criterion | Method | Pass | Evidence |
|---|-----------|--------|------|----------|
| 1 | Researcher model pinned to 32B in full profile | code: grep/diff | `profiles.py` shows `qwen2.5-coder:32b-instruct-q5_K_M` for researcher | diff |
| 2 | Full pytest suite green after changes | code: `uv run pytest -q` | 429+ passed | pytest output |
| 3 | Warmup overlay suppressed mid-stream | code: Swift diff shows `!isStreaming` guard on overlay | diff reads correctly | diff |
| 4 | Swift app compiles | code: `xcodebuild` or `swift build` per project convention | build succeeds | build output |
| 5 | `ddgs` import works | code: `uv run python -c "from ddgs import DDGS; DDGS"` | no error | cmd output |
| 6 | `duckduckgo_search` string absent from src+tests+pyproject | grep: zero hits | no matches | grep output |
| 7 | Web search still functions | code: `uv run pytest tests/test_tools.py -q` | all tests in file green | pytest output |

## Out of scope

- LangSmith / Phoenix tracing wiring (separate follow-up, optional)
- RunnableRetry fix (already landed uncommitted — will be committed with this work)
- Any other items in `.build-loop/issues/`

## Risks

- Researcher 32B may be slower for simple queries than 8B; this is measured risk — the VRAM-swap cost we're eliminating is larger in the common case (multi-round fact-check loop). If single-round queries regress noticeably, revisit.
- `ddgs` package is fairly new (9.x); watch for rate-limit or HTML-parsing behavior shifts. Existing test_tools test uses a mock so it won't catch that — accept as runtime risk.
- Overlay guard change could theoretically hide the overlay during a legitimate cold-start-then-send flow; the user send path clears outputText and sets isStreaming=true simultaneously, so the guard only flips after the stream actually begins — safe.

## Commit plan

- commit 1: RunnableRetry fix (agent.py) — already applied, pending commit
- commit 2: Researcher → 32B in full profile (profiles.py)
- commit 3: Swift overlay gate on `!isStreaming` (ResearchView.swift)
- commit 4: `duckduckgo-search` → `ddgs` (pyproject, web.py, test_tools.py, uv.lock)
