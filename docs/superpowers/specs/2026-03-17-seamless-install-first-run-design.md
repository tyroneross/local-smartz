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
  "profile": "full"
}
```

- **Per-project** — stored under `<cwd>/.localsmartz/`. Different projects can use different models. Config path printed during first-run so user knows where it lives.
- `--model` flag overrides config for that run (does not save)
- `/model` REPL command updates and saves config
- Config is optional — missing config triggers first-run flow

**Single model, not two:** The picker selects one model used for both planning and execution. The config stores `planning_model` only. `execution_model` is dropped from config — it was only meaningful for the hardcoded full profile (70B planner + 32B coder). Users selecting their own model get the same model for both roles. This simplifies the UX without losing capability.

### 2. First-Run Flow

When no config exists AND no `--model` flag provided:

```
$ localsmartz "what is quantum computing"

  Local Smartz — First Run Setup
  Ollama: running (v0.15.2)
  Detected: 128GB RAM (full profile)

  Available Ollama models:
    1. qwen3:8b-q4_K_M      (5.2 GB)
    2. gpt-oss:20b           (13.8 GB)
    3. gpt-oss:120b          (65.3 GB) <- recommended

  Select model [3]: _

  Saved -> .localsmartz/config.json
  Starting research...
```

Subsequent runs skip the picker and use saved config silently.

**Recommendation logic:** Recommend the largest available model. Sorted by size ascending, recommend last. Simple heuristic — user overrides if they disagree.

**Sizes shown are on-disk** (from Ollama `/api/tags`). Labeled as such. No attempt to estimate runtime RAM — too model-dependent to be reliable.

**Non-interactive fallback:** If stdin is not a TTY (piped input, CI), auto-select the recommended model, print `Auto-selected: <model>`, save config, proceed.

**No models available:** If Ollama has zero models pulled, print error with pull suggestion and exit 1. Never fall back to hardcoded model names that don't exist.

**Ollama not running:** Preflight check before listing models. If Ollama is down, print "Start Ollama: `ollama serve`" and exit 1.

### 3. Config Validation

On load, validate:
- JSON parses successfully (corrupted → ignore, re-trigger picker)
- `planning_model` key exists and is a non-empty string (missing → re-trigger)
- Model is still available in Ollama (deleted → warn and re-trigger picker)

### 4. File Changes

| File | Change |
|------|--------|
| **New: `src/localsmartz/config.py`** | `load_config()`, `save_config()`, `first_run_picker()`, `resolve_model()` |
| `src/localsmartz/ollama.py` | Add `list_models_with_size()` returning `[(name, size_gb)]` sorted by size |
| `src/localsmartz/__main__.py` | `_run()` and `_interactive()` call `resolve_model()` before agent creation |
| `src/localsmartz/profiles.py` | No changes — `get_profile(model_override=...)` already handles overrides |
| `install.sh` | Keep `--setup` call, change final message to hint about first-run picker |
| **New: `tests/test_config.py`** | Unit tests for config load/save/picker/validation |

### 5. `config.py` Design

```python
CONFIG_FILE = ".localsmartz/config.json"

def load_config(cwd: Path) -> dict | None:
    """Load saved config. Returns None if missing, corrupted, or invalid.

    Validates JSON structure and model availability.
    If model no longer available in Ollama, returns None (triggers re-pick).
    """

def save_config(cwd: Path, config: dict) -> None:
    """Save config to .localsmartz/config.json.

    Atomic write: writes to temp file, then renames.
    """

def first_run_picker(cwd: Path, profile_name: str | None = None) -> str:
    """Interactive model picker. Returns selected model name.

    1. Check Ollama is running (exit 1 if not)
    2. List available models with sizes
    3. Recommend largest
    4. User selects (or auto-select if non-interactive)
    5. Save to config.json
    6. Return model name

    Exits with error if no models available.
    """

def resolve_model(cwd: Path, cli_model: str | None, profile_name: str | None) -> str:
    """Determine which model to use. Single entry point for model resolution.

    Priority:
    1. cli_model (--model flag) — use directly, don't save
    2. load_config(cwd) — use saved model
    3. first_run_picker(cwd, profile_name) — interactive, saves result

    No fallback to hardcoded defaults. If we get here without a model,
    the picker handles it (error if no models, pick if models exist).
    """
```

### 6. Integration Points

Where `resolve_model()` is called in `__main__.py`:

```python
# In _run():
model = resolve_model(cwd, args.model, args.profile)
profile = get_profile(args.profile, model_override=model)
# ... rest of _run() unchanged

# In _interactive():
model = resolve_model(cwd, args.model, args.profile)
profile = get_profile(args.profile, model_override=model)
# ... rest of _interactive() unchanged
# _select_model() still works for /model command — now also saves to config

# In _check() and _setup():
# No change — these use --model or profile defaults as-is
```

### 7. `/model` REPL Command

Updated behavior:
- Shows available models (same as picker)
- User selects
- Updates `args._model_override` for current session (existing behavior)
- **New:** Also saves to `.localsmartz/config.json` so next run remembers

### 8. `install.sh` Refinements

The existing `install.sh` is solid. Minor changes:

- **Keep** `localsmartz --setup || true` — ensures Ollama is running and profile models get offered for pull
- Change final message: `"Run 'localsmartz' to pick your model and start researching"`
- No structural changes

### 9. Live Testing Plan

**Immediate validation (this session):**

1. Run `localsmartz --profile lite "What is quantum computing?"` with qwen3:8b
   - Verify: streaming output, tool calls fire, response generated, thread logged
2. Run with `--model gpt-oss:20b` to verify model override
3. Test first-run picker:
   - Remove `.localsmartz/config.json`, run without `--model`
   - Verify picker shows models, saves config
   - Run again — verify picker is skipped
4. Edge cases:
   - Corrupted config.json → re-triggers picker
   - Cancel picker with Ctrl+C → exits cleanly, no partial config
   - Invalid selection → re-prompts

**DMG build (separate task):**

1. `cd app && xcodegen generate`
2. `xcodebuild -scheme LocalSmartz -configuration Release archive`
3. `bash app/build-dmg.sh`

### 10. Out of Scope

- Changing the DeepAgents/LangChain architecture
- Adding new tools or domain packs
- Remote/cloud model support
- Auto-downloading Ollama models (keep manual `ollama pull` — models are large)
- Global config (`~/.localsmartz/`) — per-project only for now
- Separate planning/execution model selection in picker (use one model for both)
