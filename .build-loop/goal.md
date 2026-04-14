# Goal: Five-item local-smartz polish (parallel)

Date: 2026-04-14 (supersedes prior goal)
Branch: main

## Items

### 1. Faster queries

**1a. Writer streaming audit + fix gaps.** The deepagents CLI path at `agent.py:673-700` already streams AIMessageChunk tokens via `stream_mode=["updates","messages"]`. The HTTP graph path at `serve.py:1648` also streams via `["updates","messages"]`. Verify both actually flush tokens at the writer step and close any gap where an intermediate node swallows the stream.

**1b. Expand `is_fast_path()` in `profiles.py`.** Current rules (≤400 chars, no research keywords, ≤2 sentence terminators) miss short factual questions like "what's the capital of Peru" when they accidentally contain a terminator or bump against the keyword list. Tighten: keep 400 char + 2 terminator caps, but positively match single-clause factual patterns ("what is", "what's", "who is", "when did", "where is", "define", "capital of", "name of") and short-circuit to true even if one research keyword appears.

### 2. Unified status/notifications

Both CLI and Swift app already consume the SSE event taxonomy (`status`, `stage`, `tool`, `text`, `heartbeat`, `done`, `error`). Two small gaps:

- **CLI**: deepagents path prints tool breadcrumbs but sits silent during writer generation. Add a 1-2 word phase label that updates in place when tokens aren't yet streaming (`⏳ Thinking` → `🔍 Searching` → `🧠 Analyzing` → `✍ Writing`). Drives off the same tool_calls + stage data the loop already sees.
- **Swift**: mid-stream `loading_model` breadcrumb landed today but surfaces via `ToolCallEntry`. Make a lightweight `StatusBanner` view that shows the most recent phase as a non-blocking top-of-output banner, leaving OutputView untouched.

### 3. New Research → project folder

`ResearchView.swift::newThread()` currently resets in-memory state only. Extend to:
- Open a sheet asking for project name + location (default `~/Desktop/<sanitized-name>/`)
- Create folder with `queries.json` (history) + `artifacts/` subdir
- Set the Swift-side `projectDir` state; pass `cwd` in the `/api/research` request body so backend uses that folder as the thread's working directory
- Backend: `serve.py::_handle_research_request` already accepts `cwd` from body. Verify and wire.

### 4. `localsmartz doctor`

New subcommand (alternative to `--check`): runs a pass/fail matrix
- Ollama daemon reachable
- Required model(s) installed
- Backend `/api/research` responds
- SSE stream well-formed (one heartbeat arrives)
- Fast-path query round-trips
- Graph-mode query round-trips (full profile only)

Each row: ✅/❌ + one-line hint on fail.

### 5. Agent roles viewer (read-only)

In the Swift Settings view, add a new `AgentsTab` that hits `GET /api/agents` (already exists, returns title/summary/model/tools) and renders each role with an expandable "System prompt" section. Read-only. Edit deferred to follow-up.

## Scoring Criteria

| # | Criterion | Method | Pass | Evidence |
|---|-----------|--------|------|----------|
| 1 | Writer streaming measurable on CLI | manual: run verbose query, watch stdout | tokens appear before full answer assembled | terminal capture |
| 2 | `is_fast_path` expanded + tested | code: new unit tests for positive matches | `"what's the capital of Peru"` / `"who is marie curie"` → True | pytest |
| 3 | CLI phase label updates in place | manual: CLI verbose query shows phase | updates as stages progress | terminal capture |
| 4 | Swift `StatusBanner` renders mid-stream phase | xcodebuild succeeds + visual scan | banner visible above OutputView | IBR screenshot |
| 5 | New Research dialog creates folder | manual: click + inspect Desktop | `~/Desktop/<name>/queries.json` exists | ls output |
| 6 | Doctor command all-green | `localsmartz doctor` exit 0 | green matrix | terminal |
| 7 | Agents Settings tab lists all roles | xcodebuild + visual | 5-6 AGENT_ROLES listed with prompts | IBR screenshot |
| 8 | Full pytest suite green | `uv run pytest -q` | 430+ passed (new tests added) | pytest output |
| 9 | Swift build | xcodebuild | BUILD SUCCEEDED | build log |

## Out of scope

- Edit mode for agent roles (explicit follow-up, #14 deferred)
- Writer-streaming overhaul (already works per code; only audit needed)
- Agent observability/tracing wiring (separate topic)
- Full mid-stream overlay runtime test (deferred, code-correct)

## Risks

- CLI phase label with ANSI in-place update can glitch on terminals that lack cursor control. Fallback: plain line per phase. Detect via `sys.stdout.isatty()`.
- `is_fast_path` over-match: a borderline multi-part question classified fast-path would miss the research loop. Mitigation: the positive pattern matching is narrow (regex anchored on first 20 chars) and still honors char/terminator caps.
- Swift folder creation needs sandbox/file-access considerations. Using `URL(fileURLWithPath:)` under `~/Desktop/` should work with standard app permissions; if Full Disk Access is required, surface a clear error.
- `/api/agents` response shape may need extending to include `system_focus` (today it returns tools list only) — small backend change.

## Parallelization

Four independent tracks:

| Track | Files | Depends on |
|-------|-------|------------|
| A — Python status + speed | `profiles.py`, `agent.py`, `tests/test_profiles.py`, `tests/test_agent.py` | — |
| B — Swift status banner | `ResearchView.swift`, new `StatusBanner.swift` | — |
| C — Project folder | `ResearchView.swift`, `ThreadListView.swift`, `BackendManager.swift`, `serve.py` (verify cwd acceptance) | — |
| D — Doctor + agents viewer | `__main__.py` (doctor), `SettingsTabs.swift` + new `AgentsTab.swift`, `serve.py` (extend /api/agents with system_focus) | — |

Tracks B and C both touch `ResearchView.swift` — that's the single coordination point. Mitigation: track B adds a top-level banner insert site, track C modifies `newThread()` and adds sheet presentation; edit sites are non-overlapping. Coordinate by landing B first, then C rebases.

## Commit plan

1. Python: fast-path expand + CLI phase label (track A, one commit)
2. Swift: StatusBanner + OutputView integration (track B)
3. Swift: New Research project folder dialog + backend wire (track C)
4. Python: `localsmartz doctor` (track D part 1)
5. Swift: AgentsTab (track D part 2)
6. Backend: extend /api/agents with system_focus (sub-commit of 5)
