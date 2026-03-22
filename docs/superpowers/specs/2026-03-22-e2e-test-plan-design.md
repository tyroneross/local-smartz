# Local Smartz — End-to-End Test Plan with IBR

**Date:** 2026-03-22
**Scope:** Full E2E testing of CLI, Web UI, macOS app, and API using real local LLM + IBR validation

## Context

Local Smartz has three user-facing surfaces (CLI, web UI, macOS SwiftUI app) and an HTTP API. Existing tests (153 unit tests) mock the LLM layer extensively. This E2E plan uses **real Ollama models** to validate the complete pipeline and **IBR** (Interface Built Right) for structured UI validation.

## Prerequisites

- Ollama running with all required models:
  - `gpt-oss:120b` (planning)
  - `qwen2.5-coder:32b-instruct-q5_K_M` (execution)
- Server restarted after model pull: `localsmartz --serve --port 11435`
- IBR installed: `npx @tyroneross/interface-built-right`
- macOS SwiftUI app built: `cd app && xcodebuild -scheme LocalSmartz build`

## Test 1: CLI Single Query

**Surface:** CLI single-query mode
**Tool:** Shell assertions

```bash
cd ~/Desktop/git-folder/local-smartz
.venv/bin/python -m localsmartz "What is the capital of Japan?" --thread test-cli-e2e --quiet
```

**Assertions:**
| Check | Expected |
|-------|----------|
| Exit code | 0 |
| stdout | Contains substantive text (non-empty, >50 chars) |
| Thread created | `.localsmartz/threads/test-cli-e2e/` exists (note: thread persistence is best-effort; missing dir is a separate failure mode from query failure) |
| Thread entry | `messages.jsonl` has ≥1 entry with valid JSON |
| No errors | stderr is empty or contains only status messages |
| Completes | Process exits (turn count not observable from shell; infer from reasonable time) |

**What we're testing:** End-to-end flow from CLI arg parsing → model resolution → agent creation → tool calling → response → thread persistence.

## Test 2: CLI REPL Session

**Surface:** Interactive REPL
**Tool:** Shell assertions via expect/script or manual observation

**Steps:**
1. Launch: `.venv/bin/python -m localsmartz --thread test-repl-e2e`
2. Verify banner: version `0.1.0`, profile name, model name, thread ID
3. `/help` → verify command list shows `/model`, `/thread`, `/exit`
4. `/model` → verify picker lists available models, select one
5. `/thread` (no argument) → verify shows current thread info (ID, entry count)
6. Query: `List 3 programming languages` → verify streaming output, tool usage indicators
7. Follow-up: `Which of those is the oldest?` → verify answer references previous context
8. `/exit` → verify clean exit, no errors

**Assertions:**
| Check | Expected |
|-------|----------|
| Banner | Shows version, profile, model, thread |
| /help | Lists all slash commands |
| /model | Shows model picker with available models |
| Multi-turn | Follow-up query references prior context |
| Thread persist | Thread has ≥2 entries after session |
| Clean exit | Exit code 0 |

**What we're testing:** REPL lifecycle, command dispatch, model switching, thread context continuity across turns.

## Test 3: Web UI — IBR Structural Scan

**Surface:** Embedded web UI at `http://localhost:11435`
**Tool:** IBR `scan`

```bash
npx ibr scan http://localhost:11435
```

**Assertions:**
| Check | Expected |
|-------|----------|
| Page loads | HTTP 200, title "Local Smartz" |
| Textarea | Has placeholder "Research prompt..." |
| Run button | Has click handler, not disabled |
| Stop button | Disabled (no active query) |
| Thread sidebar | Shows existing threads from prior tests |
| Status dot | Green (ready: true with all models) |
| Console errors | None (favicon 404 acceptable) |
| Accessibility | All interactive elements have roles/labels |
| Layout | Sidebar ~200px, main content fills remaining space |

**What we're testing:** UI structure, handler wiring, accessibility, initial state correctness.

## Test 4: Web UI — IBR Interactive Session

**Surface:** Embedded web UI
**Tool:** IBR interactive session

**Steps:**
1. `npx ibr session:start http://localhost:11435 --name "e2e-research"`
2. Type into textarea: "What are the top 3 most populated countries?"
3. Click "Run" button
4. Wait for output pane to show content (SSE streaming)
5. `npx ibr session:scan` → capture post-query state
6. `npx ibr session:close`

**Assertions:**
| Check | Expected |
|-------|----------|
| Prompt accepted | Textarea retains its value after submit |
| Run button state | Changes during query (disabled or shows "Running") |
| Stop button state | Enabled during query |
| Output pane | Contains text (research results) |
| Tool indicators | Tool call events visible (if tools used) |
| Thread sidebar | New thread appears after completion |
| Status dot | Remains green throughout |
| No JS errors | Console clean during entire flow |

**What we're testing:** Complete browser-side research flow — prompt submission, SSE streaming render, state transitions, thread persistence reflected in sidebar.

## Test 5: Web UI — IBR Snapshot Baseline

**Surface:** Embedded web UI
**Tool:** IBR snapshot + compare

**Steps:**
1. `npx ibr snapshot http://localhost:11435 --name "clean-state"` (baseline)
2. Query from Test 4 has already been run; proceed directly to compare
3. `npx ibr check` (compare current vs baseline)

**Assertions:**
| Check | Expected |
|-------|----------|
| Verdict | EXPECTED_CHANGE |
| Changed regions | Output pane (populated), thread sidebar (new entry) |
| Unchanged regions | Header, input area structure, sidebar structure |
| Diff % | Moderate (content change, not layout break) |

**What we're testing:** Visual regression detection. Establishes that a query correctly changes only the output area and thread list, not the overall layout.

## Test 6: macOS SwiftUI App — IBR Native Scan

**Surface:** Local Smartz.app (macOS)
**Tool:** IBR `scan_macos`

**Prerequisites:**
- App built and launched via `open Local\ Smartz.app`
- App must be configured (pythonPath + projectDirectory set in UserDefaults) to reach ResearchView
- If unconfigured, app will show SetupView instead — test that path separately
- Server must be running on port 11435 (app scans ports 11435-11444 dynamically)

**Assertions:**
| Check | Expected |
|-------|----------|
| Window renders | App window visible with content |
| Navigation | If configured: NavigationSplitView with sidebar + detail. If unconfigured: SetupView wizard |
| Backend status | Shows connected to backend (port may vary 11435-11444) |
| Thread list | Sidebar shows existing threads |
| Accessibility | Interactive elements have AX labels |

**What we're testing:** Native app renders correctly, connects to backend, displays expected UI structure.

## Test 7: API Endpoint Validation

**Surface:** HTTP API
**Tool:** curl + shell assertions

| Endpoint | Method | Input | Expected |
|----------|--------|-------|----------|
| `/api/health` | GET | — | `{ok: true, profile: "full"|"lite"}` (profile auto-detected) |
| `/api/status` | GET | — | `{ready: true}` (both planning + execution models available) |
| `/api/threads` | GET | — | JSON array with thread objects |
| `/api/research` | POST | `{prompt: "Hello"}` | SSE stream: `text` events → `done` event |
| `/api/research` | POST | `{}` | 400 `{error: "..."}` |
| `/api/research` | POST | `{prompt: ""}` | 400 `{error: "..."}` |
| `/` | GET | — | HTML page (200, content-type: text/html) |
| `/api/research` | OPTIONS | — | 204, CORS headers present |
| `/nonexistent` | GET | — | 404 `{error: "Not found"}` |

**SSE stream assertions for successful research:**
1. Events are valid JSON: `data: {...}\n\n` format
2. Event types in expected order: `text` and/or `tool` events → `done`
3. `done` event contains `duration_ms` (number > 0)
4. Content-Type header: `text/event-stream`
5. CORS headers: `Access-Control-Allow-Origin: *`

## Execution Order

1. ⏳ Wait for `qwen2.5-coder:32b-instruct-q5_K_M` model pull
2. Restart server: kill existing → `localsmartz --serve --port 11435`
3. **Test 7** — API endpoints (fastest, validates plumbing)
4. **Test 1** — CLI single query (validates agent pipeline)
5. **Test 3** — IBR structural scan (validates UI before interaction)
6. **Test 4** — IBR interactive session (validates full web flow)
7. **Test 5** — IBR snapshot baseline (captures regression state)
8. **Test 2** — CLI REPL session (interactive, manual observation)
9. **Test 6** — macOS native scan (validates SwiftUI app)

## Issue Assessment Framework

After each test, log:

| Category | What to capture |
|----------|----------------|
| **Pass/Fail** | Binary result with evidence (output, screenshot, IBR scan data) |
| **Bugs** | Functional failures — crashes, wrong output, broken handlers |
| **UX issues** | Confusing states, missing feedback, unclear errors |
| **A11y gaps** | Missing labels, small touch targets, no focus indicators |
| **Performance** | Response time, streaming lag, model inference speed |
| **Severity** | Critical (blocks usage) / High (major UX impact) / Medium / Low |

## Success Criteria

- All 7 tests pass with no Critical or High severity issues
- LLM produces coherent responses through all surfaces
- Thread context persists across CLI and web queries
- IBR scan shows no accessibility failures
- macOS app connects and renders correctly
