# Seamless Install + First-Run Model Picker

**Date:** 2026-03-17
**Status:** Draft
**Scope:** CLI deployment flow, persistent model config, live LLM validation

## Problem

Local Smartz hardcodes specific model+quantization strings in profiles (`llama3.1:70b-instruct-q5_K_M`, `qwen2.5-coder:32b-instruct-q5_K_M`, `qwen3:8b-q4_K_M`). If a user has different models pulled in Ollama, the default experience breaks — they get an error and must know about `--model` or `--profile` flags.

The install flow requires 5 steps (clone, cd, pip install, setup, run). Should be: clone, run one script, go.

## Solution

### 1. Persistent Config (`.localsmartz/config.json`)

Stores user's model choice after first-run picker:

```json
{
  "planning_model": "gpt-oss:120b",
  "execution_model": "gpt-oss:20b",
  "profile": "full"
}
```

- Per-project (stored under `.localsmartz/`)
- `--model` flag overrides config for that run (does not save)
- `/model` REPL command updates saved config
- Config is optional — missing config triggers first-run flow

### 2. First-Run Flow

When no config exists AND no `--model` flag provided:

```
$ localsmartz "what is quantum computing"

  Local Smartz — First Run Setup

  Detected: 128GB RAM (full profile)

  Available Ollama models:
    1. qwen3:8b-q4_K_M      (5.2 GB)
    2. gpt-oss:20b           (13.8 GB)
    3. gpt-oss:120b          (65.3 GB) <- recommended for this hardware

  Select model [3]: _

  Saved -> .localsmartz/config.json
  Starting research...
```

Subsequent runs skip the picker and use saved config silently.

**Recommendation logic:** Pick the largest model that fits comfortably in RAM. For full profile (64GB+), recommend the largest available. For lite (<64GB), recommend the smallest.

**Non-interactive fallback:** If stdin is not a TTY (piped input, CI), use the recommended model automatically and print which model was selected.

### 3. File Changes

| File | Change |
|------|--------|
| **New: `src/localsmartz/config.py`** | `load_config()`, `save_config()`, `first_run_picker()` |
| `src/localsmartz/profiles.py` | `get_profile()` checks config before hardcoded defaults |
| `src/localsmartz/__main__.py` | `_run()` and `_interactive()` trigger first-run when no config |
| `src/localsmartz/ollama.py` | Add `list_models_with_size()` returning `[(name, size_gb)]` |
| `install.sh` | Minor polish — after `--setup`, hint about first run |
| `tests/test_config.py` | Unit tests for config load/save/picker logic |

### 4. `config.py` Design

```python
CONFIG_FILE = ".localsmartz/config.json"

def load_config(cwd: Path) -> dict | None:
    """Load saved config. Returns None if no config exists."""

def save_config(cwd: Path, config: dict) -> None:
    """Save config to .localsmartz/config.json."""

def first_run_picker(cwd: Path, profile_name: str | None = None) -> dict:
    """Interactive model picker for first run.

    1. Detect profile (or use provided)
    2. List available Ollama models with sizes
    3. Recommend best match for hardware
    4. User selects
    5. Save to config.json
    6. Return config dict
    """
```

### 5. Model Resolution Order

When determining which model to use:

1. `--model` CLI flag (highest priority, not saved)
2. `.localsmartz/config.json` (saved from picker or /model command)
3. First-run picker (triggers if 1 and 2 are absent)
4. Hardcoded profile defaults (fallback if no Ollama models and non-interactive)

### 6. `install.sh` Refinements

The existing `install.sh` is solid. Minor changes:

- After successful install, print: `Run 'localsmartz' to select your model and start researching`
- Remove the `localsmartz --setup` auto-call at end (first-run picker replaces this for model selection; Ollama install check stays)
- Keep the Ollama install detection and brew fallback

### 7. Live Testing Plan

**Immediate validation (this session):**

1. Run `localsmartz --profile lite "What is quantum computing?"` with qwen3:8b
   - Verify: streaming output, tool calls fire, response generated, thread logged
2. Run with `--model gpt-oss:20b`
   - Verify: model override works, different model produces output
3. Test first-run picker with fresh `.localsmartz/` directory

**DMG build (separate task):**

1. `cd app && xcodegen generate`
2. `xcodebuild -scheme LocalSmartz -configuration Release archive`
3. `bash app/build-dmg.sh`

### 8. Out of Scope

- Changing the DeepAgents/LangChain architecture
- Adding new tools or domain packs
- Remote/cloud model support
- Auto-downloading Ollama models (keep manual pull — models are large)
