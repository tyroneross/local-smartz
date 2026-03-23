# Local Smartz — Guided Setup Wizard

**Date:** 2026-03-22
**Scope:** Web UI setup wizard (priority) + terminal setup wizard for guided onboarding of non-technical users
**Target user:** Non-technical researcher who wants local AI research without understanding models, profiles, or Ollama internals

## Context

The current install experience dumps terminal output and assumes users know what Ollama, models, and profiles are. A non-technical researcher needs a guided, step-by-step experience that handles setup decisions for them with plain-English explanations.

## Design

### Trigger

- **Web UI:** When the user opens `localhost:11435` and the system is not fully configured (no model selected in config, or Ollama not running), the web UI renders the setup wizard instead of the research view. Once setup completes, it transitions to the research view.
- **Terminal:** `localsmartz --setup` runs the interactive CLI wizard. First run without config also triggers it automatically before entering the REPL.

### Web UI Wizard (Priority)

A multi-step wizard rendered inside the existing `_UI_HTML`. Replaces the `<main>` content area when setup is needed. The sidebar remains visible but dimmed/inactive until setup completes.

#### Step 1: Status Check (auto)

Fetches `/api/status` on load. Shows:
- Ollama status: running/not running
- RAM detected: X GB
- Suggested profile: full/lite

If Ollama is NOT running:
- Shows message: "Local Smartz needs Ollama to run AI models on your computer."
- Platform-specific install guidance:
  - macOS: "Download Ollama from ollama.com/download, open the app, then click Check Again"
  - Linux: "Run: `curl -fsSL https://ollama.ai/install.sh | sh && ollama serve`"
- "Check Again" button → re-fetches `/api/status`
- Does NOT auto-advance until Ollama is confirmed running

If Ollama IS running:
- Shows green checkmark: "Ollama is running"
- Shows hardware info: "X GB RAM detected — Y profile"
- Auto-advances to Step 2 after 1 second

#### Step 2: Choose Model

Fetches `/api/models` to get available models. Shows two sections:

**Already downloaded** (if any models exist):
- List with name + size, radio-select style
- Recommended model highlighted (largest that fits in RAM)

**Download a new model** (if few/no models):
- Curated list of recommended models with plain-English descriptions:
  - "Small (5 GB) — Fast answers, works on any Mac" → `qwen3:8b-q4_K_M`
  - "Medium (14 GB) — Better quality, needs 16GB+ RAM" → suggested medium model
  - "Large (40+ GB) — Best quality, needs 64GB+ RAM" → suggested large model
- "Download" button next to each → calls `POST /api/setup` with model name, shows progress bar
- After download completes, model appears in the "Already downloaded" list

**Select button** → calls `POST /api/models/select`, advances to Step 3

**Download-then-select timing:** The Select button must be disabled until the `done` SSE event is received from `POST /api/setup`. After `done`, the model is guaranteed to be in Ollama's index and `POST /api/models/select` will succeed.

#### Step 3: Set Workspace

- Heading: "Where are the files you want to research?"
- Explanation: "Local Smartz can read files from folders you choose — PDFs, spreadsheets, text files. This stays on your computer."
- Pre-filled input with current working directory
- "+ Add another folder" button (same as the existing folder management)
- "Continue" button → saves folders via `POST /api/folders`, advances to Step 4

#### Step 4: Test Run

- Heading: "Let's make sure everything works"
- Explanation: "Running a quick test to verify your setup..."
- Auto-submits a test query: "What is artificial intelligence? Answer in one sentence."
- Shows the SSE streaming output in real-time (reuses existing `streamSSE` + `handleEvent`)
- On `done` event: shows success message with the answer
- On `error` event: shows error with "Try Again" button
- "Start Researching" button → transitions to the research view

#### Wizard UI Design (Alt C visual language)

- Centered in the `<main>` area, max-width 480px
- Step indicator at top: `● ● ○ ○` (filled = done, hollow = remaining)
- Same dark theme, teal accent from Alt C
- Buttons: gradient teal for primary actions, subtle border for secondary
- Progress bars for model download: teal fill on dark track
- Transitions: simple fade between steps

### Terminal Wizard (`localsmartz --setup`)

Same 4 steps but interactive CLI. Uses stdin prompts, not curses.

```
Local Smartz Setup
==================

[1/4] Checking Ollama...
  ✓ Running (v0.15.2)
  ✓ 128 GB RAM — full profile

[2/4] Choose a model:

  Already downloaded:
    1. qwen3:8b-q4_K_M      (5.2 GB)
    2. gpt-oss:20b           (13.8 GB)
    3. gpt-oss:120b          (65.3 GB)  ← recommended

  Download a new model:
    4. llama3.1:70b           (~40 GB)

  Select [3]: _

[3/4] Workspace folder
  Where are the files you want to research?
  Default: ~/Desktop/git-folder/local-smartz
  > (press Enter for default)
  ✓ Workspace set

  Add another folder? (path or Enter to skip)
  > _

[4/4] Testing...
  Query: "What is artificial intelligence?"
  → Artificial intelligence is the simulation of human
    intelligence by computer systems.
  ✓ Working!

Setup complete! Run 'localsmartz' to start.
```

If Ollama is not running in Step 1:
```
[1/4] Checking Ollama...
  ✗ Ollama is not running.

  Install Ollama:
    macOS:  Download from https://ollama.com/download
    Linux:  curl -fsSL https://ollama.ai/install.sh | sh

  Start it, then press Enter to check again...
  > _
```

### API Changes

#### `POST /api/setup` — extend to accept model name

Currently pulls all profile models. Extend to accept an optional `model` parameter:

```json
// Request (new)
{"model": "qwen3:8b-q4_K_M"}

// SSE events (same as current)
{"type": "text", "content": "Pulling qwen3:8b..."}
{"type": "text", "content": "Progress: 45%"}
{"type": "done", "duration_ms": 30000}
```

If no `model` specified, falls back to current behavior (pull all profile models).

**Body handling:** The existing `_read_json_body()` raises `ValueError` on empty body. The extended `_handle_setup()` must check `Content-Length` before calling it — treat empty/missing body as `model=None` (profile-pull mode), not as an error.

#### `GET /api/status` — add `version` field

Add `version` to the response so the web UI can display it without hardcoding:

```json
{
  "profile": "full",
  "planning_model": "gpt-oss:120b",
  "ready": true,
  "version": "0.1.0",
  "ollama": {"running": true, "version": "0.15.2", "models": [...]}
}
```

#### `GET /api/status` — add `ram_gb` field

Add detected RAM so the wizard can show hardware info:

```json
{
  "ram_gb": 128,
  ...
}
```

### Config Changes

No new config fields. The wizard uses existing:
- `planning_model` — set by Step 2
- `folders` — set by Step 3
- `profile` — set by Step 2

The wizard completion state is derived from `/api/status` response: if `status.ready === true` → setup is complete → show research view. This covers all cases:
- No config → `ready: false` (no model selected)
- Config exists but model deleted from Ollama → `ready: false` (model missing)
- Ollama not running → `ready: false`
- Everything configured and available → `ready: true`

The JS wizard trigger must check `status.ready`, NOT individual fields like `planning_model` existence.

## Files to Modify

| File | Changes |
|------|---------|
| `src/localsmartz/serve.py` | Add wizard HTML/JS to `_UI_HTML`. Update `fetchStatus()` JS to use `d.version` instead of hardcoded string. Extend `_handle_status()` to include `version` and `ram_gb`. Extend `_handle_setup()` to accept optional `model` param (handle empty body gracefully). |
| `src/localsmartz/__main__.py` | Rewrite `--setup` flow as interactive 4-step wizard. Guard all `input()` calls with `sys.stdin.isatty()` — non-interactive mode accepts defaults, skips Steps 3-4, exits with guidance. |
| `src/localsmartz/utils/hardware.py` | Extract RAM detection from `config.py:first_run_picker()` into shared `get_ram_gb()` helper. Called by both `_handle_status()` and `first_run_picker()`. |
| `tests/test_serve.py` | Test wizard detection logic, extended status response, setup with model param |

## Success Criteria

- Non-technical user can go from `curl install | bash` → open browser → working research in under 5 minutes (excluding model download time)
- Web wizard shows/hides based on setup completeness — no manual URL navigation needed
- Test query proves the system works before the user tries their own research
- Terminal wizard provides the same guided experience for CLI-only users
- Existing users who already have config are unaffected — wizard doesn't appear
