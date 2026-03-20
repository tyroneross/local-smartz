# Seamless Install + First-Run Model Picker — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent model config with first-run guided picker so Local Smartz works with whatever Ollama models the user has — no hardcoded model names required.

**Architecture:** New `config.py` module owns model resolution. `resolve_model()` is the single entry point called by `__main__.py` before any agent creation. Priority: CLI `--model` flag > saved config > first-run picker. Config stored per-project at `.localsmartz/config.json`.

**Tech Stack:** Python 3.12+, httpx (existing dep), stdlib json/tempfile

**Spec:** `docs/superpowers/specs/2026-03-17-seamless-install-first-run-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/localsmartz/config.py` | Create | `load_config`, `save_config`, `first_run_picker`, `resolve_model` |
| `src/localsmartz/ollama.py` | Modify (add function) | Add `list_models_with_size()` |
| `src/localsmartz/__main__.py` | Modify | Wire `resolve_model()` into `_run()`, `_interactive()`, `/model` save |
| `install.sh` | Modify (1 line) | Update final message |
| `tests/test_config.py` | Create | Tests for config module |
| `tests/test_ollama.py` | Modify (add tests) | Tests for `list_models_with_size()` |

---

## Task 1: Add `list_models_with_size()` to ollama.py

**Files:**
- Modify: `src/localsmartz/ollama.py` (insert after `list_models()` at line 46)
- Test: `tests/test_ollama.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ollama.py`:

```python
from localsmartz.ollama import list_models_with_size


def test_list_models_with_size():
    """Returns (name, size_gb) tuples sorted by size ascending."""
    mock_data = {
        "models": [
            {"name": "big:70b", "size": 40_000_000_000},
            {"name": "small:8b", "size": 5_000_000_000},
            {"name": "mid:20b", "size": 14_000_000_000},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data
    mock_resp.raise_for_status = MagicMock()

    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        result = list_models_with_size()
        assert len(result) == 3
        # Sorted by size ascending
        assert result[0][0] == "small:8b"
        assert result[2][0] == "big:70b"
        # Size in GB
        assert abs(result[0][1] - 5.0) < 0.1


def test_list_models_with_size_empty():
    """Returns empty list when no models."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        assert list_models_with_size() == []


def test_list_models_with_size_error():
    """Returns empty list on connection error."""
    import httpx
    with patch("localsmartz.ollama.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert list_models_with_size() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_ollama.py::test_list_models_with_size -v`
Expected: FAIL — `cannot import name 'list_models_with_size'`

- [ ] **Step 3: Implement `list_models_with_size()`**

Add to `src/localsmartz/ollama.py` after the existing `list_models()` function (after line 46):

```python
def list_models_with_size() -> list[tuple[str, float]]:
    """List models with on-disk size in GB, sorted by size ascending.

    Returns:
        List of (model_name, size_gb) tuples. Empty list on error.
    """
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        models = [
            (m["name"], m.get("size", 0) / 1e9)
            for m in data.get("models", [])
        ]
        models.sort(key=lambda x: x[1])
        return models
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_ollama.py -v`
Expected: All pass (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/git-folder/local-smartz
git add src/localsmartz/ollama.py tests/test_ollama.py
git commit -m "feat: add list_models_with_size() to ollama module"
```

---

## Task 2: Create `config.py` — load and save

**Files:**
- Create: `src/localsmartz/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for load/save**

Create `tests/test_config.py`:

```python
"""Tests for persistent config module."""

import json
from pathlib import Path

from localsmartz.config import load_config, save_config


def test_load_config_missing(tmp_path):
    """Returns None when no config file exists."""
    assert load_config(tmp_path) is None


def test_save_and_load(tmp_path):
    """Round-trip: save then load returns same data."""
    (tmp_path / ".localsmartz").mkdir()
    config = {"planning_model": "qwen3:8b", "profile": "lite"}
    save_config(tmp_path, config)

    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded["planning_model"] == "qwen3:8b"
    assert loaded["profile"] == "lite"


def test_save_creates_directory(tmp_path):
    """save_config creates .localsmartz/ if it doesn't exist."""
    config = {"planning_model": "qwen3:8b", "profile": "lite"}
    save_config(tmp_path, config)
    assert (tmp_path / ".localsmartz" / "config.json").exists()


def test_load_corrupted_json(tmp_path):
    """Returns None for corrupted JSON."""
    config_dir = tmp_path / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json{{{")
    assert load_config(tmp_path) is None


def test_load_missing_planning_model(tmp_path):
    """Returns None if planning_model key is missing."""
    config_dir = tmp_path / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"profile": "lite"}))
    assert load_config(tmp_path) is None


def test_load_empty_planning_model(tmp_path):
    """Returns None if planning_model is empty string."""
    config_dir = tmp_path / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"planning_model": "", "profile": "lite"})
    )
    assert load_config(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'localsmartz.config'`

- [ ] **Step 3: Implement load_config and save_config**

Create `src/localsmartz/config.py`:

```python
"""Persistent model configuration.

Stores user's model selection at .localsmartz/config.json so the first-run
picker only needs to run once. Config is per-project (per working directory).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

CONFIG_FILENAME = "config.json"


def _config_path(cwd: Path) -> Path:
    """Get path to config file."""
    return cwd / ".localsmartz" / CONFIG_FILENAME


def load_config(cwd: Path) -> dict | None:
    """Load saved config. Returns None if missing, corrupted, or invalid.

    Validates:
    - JSON parses successfully
    - planning_model key exists and is a non-empty string
    Does NOT validate model availability (caller's responsibility).
    """
    path = _config_path(cwd)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    model = data.get("planning_model")
    if not model or not isinstance(model, str):
        return None

    return data


def save_config(cwd: Path, config: dict) -> None:
    """Save config to .localsmartz/config.json.

    Atomic write: writes to temp file in same directory, then renames.
    Creates .localsmartz/ directory if it doesn't exist.
    """
    config_dir = cwd / ".localsmartz"
    config_dir.mkdir(parents=True, exist_ok=True)

    path = _config_path(cwd)

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_dir), suffix=".tmp", prefix="config_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_config.py -v`
Expected: All 6 pass

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/git-folder/local-smartz
git add src/localsmartz/config.py tests/test_config.py
git commit -m "feat: add config.py with load/save for persistent model config"
```

---

## Task 3: Add `first_run_picker()` and `resolve_model()` to config.py

**Files:**
- Modify: `src/localsmartz/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for resolve_model**

Add to `tests/test_config.py`:

```python
from unittest.mock import patch, MagicMock
from localsmartz.config import resolve_model


def test_resolve_model_cli_flag(tmp_path):
    """CLI --model flag takes priority, does not save."""
    result = resolve_model(tmp_path, cli_model="my-model:7b", profile_name="lite")
    assert result == "my-model:7b"
    # Should NOT have saved to config
    assert not (tmp_path / ".localsmartz" / "config.json").exists()


def test_resolve_model_from_config(tmp_path):
    """Loads from saved config when no CLI flag."""
    save_config(tmp_path, {"planning_model": "saved:model", "profile": "lite"})

    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.model_available", return_value=True):
        result = resolve_model(tmp_path, cli_model=None, profile_name="lite")
        assert result == "saved:model"


def test_resolve_model_stale_config_retriggers(tmp_path, capsys):
    """If saved model no longer available, warns and triggers picker."""
    save_config(tmp_path, {"planning_model": "deleted:model", "profile": "lite"})

    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.model_available", return_value=False), \
         patch("localsmartz.config.list_models_with_size", return_value=[("other:8b", 5.0)]), \
         patch("localsmartz.config.get_version", return_value="0.15.2"), \
         patch("localsmartz.config.detect_profile", return_value="lite"), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False  # non-interactive → auto-select
        result = resolve_model(tmp_path, cli_model=None, profile_name="lite")
        assert result == "other:8b"
        captured = capsys.readouterr()
        assert "no longer available" in captured.err


def test_first_run_picker_ctrl_c_no_partial_config(tmp_path, monkeypatch):
    """Ctrl+C during picker exits cleanly without saving partial config."""
    import pytest
    from localsmartz.config import first_run_picker

    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.list_models_with_size", return_value=[("test:8b", 5.0)]), \
         patch("localsmartz.config.get_version", return_value="0.15.2"), \
         patch("localsmartz.config.detect_profile", return_value="lite"):
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
        with pytest.raises(SystemExit) as exc_info:
            first_run_picker(tmp_path, "lite")
        assert exc_info.value.code == 130
        assert not (tmp_path / ".localsmartz" / "config.json").exists()


def test_first_run_picker_empty_input_selects_recommended(tmp_path, monkeypatch):
    """Empty input (Enter key) selects the recommended (largest) model."""
    from localsmartz.config import first_run_picker

    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.list_models_with_size",
               return_value=[("small:8b", 5.0), ("large:70b", 40.0)]), \
         patch("localsmartz.config.get_version", return_value="0.15.2"), \
         patch("localsmartz.config.detect_profile", return_value="full"):
        monkeypatch.setattr("builtins.input", lambda _: "")  # Enter key
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True})())
        result = first_run_picker(tmp_path, "full")
        assert result == "large:70b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_config.py::test_resolve_model_cli_flag -v`
Expected: FAIL — `cannot import name 'resolve_model'`

- [ ] **Step 3: Implement first_run_picker and resolve_model**

Add to `src/localsmartz/config.py`:

```python
from localsmartz.ollama import (
    check_server,
    get_version,
    list_models_with_size,
    model_available,
)
from localsmartz.profiles import detect_profile


def first_run_picker(cwd: Path, profile_name: str | None = None) -> str:
    """Interactive model picker for first run. Returns selected model name.

    1. Check Ollama is running (exit 1 if not)
    2. List available models with sizes
    3. Recommend largest
    4. User selects (or auto-select if non-interactive)
    5. Save to config.json
    6. Return model name

    Exits with error if no models available.
    """
    # Preflight
    if not check_server():
        print("\n  Ollama is not running.", file=sys.stderr)
        print("  Start it with: ollama serve", file=sys.stderr)
        sys.exit(1)

    version = get_version()
    detected = profile_name or detect_profile()

    models = list_models_with_size()
    if not models:
        print("\n  No models found in Ollama.", file=sys.stderr)
        print("  Pull a model first:", file=sys.stderr)
        print("    ollama pull qwen3:8b-q4_K_M    (5 GB, fast)", file=sys.stderr)
        print("    ollama pull llama3.1:70b        (40 GB, powerful)", file=sys.stderr)
        sys.exit(1)

    # Recommend largest model
    recommended_idx = len(models) - 1

    # Banner
    print()
    print("  \033[1mLocal Smartz\033[0m v0.1.0")
    print("  First-run setup")
    print()
    v_str = f" (v{version})" if version else ""
    print(f"  \033[32mOllama:\033[0m running{v_str}")

    import platform
    import subprocess
    try:
        if platform.system() == "Darwin":
            ram_bytes = int(subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True
            ).strip())
        else:
            import os as _os
            ram_bytes = _os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES")
        ram_gb = int(ram_bytes / (1024 ** 3))
        print(f"  Hardware: {ram_gb} GB RAM \u00b7 {detected} profile")
    except Exception:
        print(f"  Profile: {detected}")

    print()
    print("  Available models:")
    for i, (name, size_gb) in enumerate(models):
        rec = "  \033[94m<- recommended\033[0m" if i == recommended_idx else ""
        print(f"    {i + 1}. {name:<25s} ({size_gb:.1f} GB){rec}")

    default_display = recommended_idx + 1

    # Non-interactive fallback
    if not sys.stdin.isatty():
        selected = models[recommended_idx][0]
        print(f"\n  Auto-selected: {selected}")
        save_config(cwd, {"planning_model": selected, "profile": detected})
        return selected

    print()
    try:
        choice = input(f"  Select model [{default_display}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)

    if not choice:
        idx = recommended_idx
    else:
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(models)):
                print(f"  Invalid selection, using recommended: {models[recommended_idx][0]}")
                idx = recommended_idx
        except ValueError:
            # Try as model name
            matching = [i for i, (n, _) in enumerate(models) if n == choice]
            if matching:
                idx = matching[0]
            else:
                print(f"  Unknown model, using recommended: {models[recommended_idx][0]}")
                idx = recommended_idx

    selected = models[idx][0]
    save_config(cwd, {"planning_model": selected, "profile": detected})
    print(f"\n  \033[32mSaved\033[0m -> .localsmartz/config.json")

    return selected


def resolve_model(cwd: Path, cli_model: str | None, profile_name: str | None) -> str:
    """Determine which model to use. Single entry point for model resolution.

    Priority:
    1. cli_model (--model flag) — use directly, don't save
    2. load_config(cwd) — use saved model if still available
    3. first_run_picker(cwd, profile_name) — interactive, saves result

    Returns:
        Model name string to pass to get_profile(model_override=...).
    """
    # Priority 1: CLI flag
    if cli_model:
        return cli_model

    # Priority 2: Saved config
    config = load_config(cwd)
    if config:
        model = config["planning_model"]
        # Validate model still exists in Ollama
        if check_server() and model_available(model):
            return model
        else:
            print(f"\n  \033[33mSaved model '{model}' is no longer available.\033[0m",
                  file=sys.stderr)

    # Priority 3: First-run picker
    return first_run_picker(cwd, profile_name)
```

- [ ] **Step 4: Run all config tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/test_config.py -v`
Expected: All 12 pass (6 load/save + 3 resolve + 3 picker edge cases)

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/git-folder/local-smartz
git add src/localsmartz/config.py tests/test_config.py
git commit -m "feat: add first_run_picker and resolve_model to config module"
```

---

## Task 4: Integrate `resolve_model()` into `__main__.py`

**Files:**
- Modify: `src/localsmartz/__main__.py` — `_interactive()` (lines 293-379), `_run()` (lines 381-443), `/model` handler (lines 264-272)

- [ ] **Step 1: Update `_run()` to use `resolve_model()`**

In `src/localsmartz/__main__.py`, replace the model resolution in `_run()` (lines 381-396):

```python
def _run(prompt: str, args, cwd: Path, model_override: str | None = None):
    """Execute a single research query."""
    from localsmartz.agent import run_research, extract_final_response, review_output
    from localsmartz.threads import create_thread, append_entry
    from localsmartz.profiles import get_profile
    from localsmartz.config import resolve_model

    verbose = not args.quiet
    thread_id = args.thread

    # Use explicit model_override (from REPL), or resolve via config/picker
    if model_override is not None:
        effective_override = model_override
    else:
        effective_override = resolve_model(cwd, args.model, args.profile)

    # Preflight check
    profile = get_profile(args.profile, model_override=effective_override)
    if not _preflight(profile):
        sys.exit(1)
```

The rest of `_run()` (lines 398-443) stays unchanged.

- [ ] **Step 2: Update `_interactive()` to use `resolve_model()`**

In `src/localsmartz/__main__.py`, replace model selection in `_interactive()` (lines 293-312):

```python
def _interactive(args, cwd: Path):
    """Interactive REPL — Claude Code-style UX."""
    from localsmartz.profiles import get_profile
    from localsmartz.threads import get_thread, load_context
    from localsmartz.config import resolve_model

    # Resolve model via CLI flag / config / picker
    model_override = resolve_model(cwd, args.model, args.profile)
    profile = get_profile(args.profile, model_override=model_override)

    if not _preflight(profile):
        sys.exit(1)

    # Store on args for /model command to update
    args._model_override = model_override
```

Remove the old `_select_model()` call block (the one at lines 303-309 that showed picker in interactive mode). The rest of `_interactive()` from thread_id onwards (line 314+) stays unchanged.

- [ ] **Step 3: Update `/model` command to save config**

In `src/localsmartz/__main__.py`, update the `/model` handler (lines 264-272):

```python
    if command == "/model":
        new_model = _select_model(profile)
        if new_model:
            args._model_override = new_model
            # Save to config for next run
            from localsmartz.config import save_config
            save_config(cwd, {"planning_model": new_model, "profile": profile["name"]})
            print(f"  Model \u2192 {new_model}  \033[32mSaved\033[0m")
        else:
            current = args._model_override if hasattr(args, '_model_override') and args._model_override else profile["planning_model"]
            print(f"  Model: {current} (unchanged)")
        return "continue"
```

- [ ] **Step 4: Run full test suite**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/pytest tests/ -v`
Expected: All tests pass (existing + new config tests)

- [ ] **Step 5: Commit**

```bash
cd ~/Desktop/git-folder/local-smartz
git add src/localsmartz/__main__.py
git commit -m "feat: wire resolve_model into CLI entry points and /model command"
```

---

## Task 5: Update `install.sh` final message

**Files:**
- Modify: `install.sh:101-102`

- [ ] **Step 1: Update the final message**

In `install.sh`, change line 102:

Old:
```bash
echo "Done! Run: localsmartz"
```

New:
```bash
echo "Done! Run 'localsmartz' to pick your model and start researching."
```

- [ ] **Step 2: Commit**

```bash
cd ~/Desktop/git-folder/local-smartz
git add install.sh
git commit -m "chore: update install.sh final message for first-run picker"
```

---

## Task 6: Live LLM test — lite profile

**Files:** None (validation only)

**Prerequisite:** Ollama running, `qwen3:8b-q4_K_M` available.

- [ ] **Step 1: Clean config for fresh test**

```bash
cd ~/Desktop/git-folder/local-smartz
rm -f .localsmartz/config.json
```

- [ ] **Step 2: Test first-run picker**

```bash
cd ~/Desktop/git-folder/local-smartz
.venv/bin/localsmartz --profile lite "What is quantum computing? Give a brief answer."
```

Expected:
- First-run picker appears showing available models
- Select `qwen3:8b-q4_K_M`
- Config saved to `.localsmartz/config.json`
- Research runs: tool calls stream to stderr, final answer prints to stdout
- Verify: `cat .localsmartz/config.json` shows saved model

- [ ] **Step 3: Test subsequent run skips picker**

```bash
.venv/bin/localsmartz --profile lite "What is 15% of 2400?"
```

Expected: No picker shown, goes straight to research. `python_exec` tool should be called.

- [ ] **Step 4: Test --model flag override**

```bash
.venv/bin/localsmartz --model gpt-oss:20b "What is the capital of France? Brief answer."
```

Expected: Uses `gpt-oss:20b` (shown in stderr), does NOT update config.json.
Verify: `cat .localsmartz/config.json` still shows original model.

- [ ] **Step 5: Test interactive REPL**

```bash
.venv/bin/localsmartz
```

Expected:
- Banner shows saved model from config
- Type a query, verify it runs
- Type `/model`, verify picker shows with saved model marked `*`
- Select different model, verify "Saved" message
- Type `/exit`

---

## Task 7: DMG build test (separate)

**Files:** None (build validation only)

**Prerequisite:** Xcode + XcodeGen installed.

- [ ] **Step 1: Generate Xcode project**

```bash
cd ~/Desktop/git-folder/local-smartz/app && xcodegen generate
```

Expected: `LocalSmartz.xcodeproj` created.

- [ ] **Step 2: Build Debug**

```bash
xcodebuild -project LocalSmartz.xcodeproj -scheme LocalSmartz -configuration Debug build 2>&1 | tail -5
```

Expected: `BUILD SUCCEEDED`

- [ ] **Step 3: Build Release archive**

```bash
xcodebuild -project LocalSmartz.xcodeproj -scheme LocalSmartz \
  -configuration Release \
  -archivePath build/LocalSmartz.xcarchive archive 2>&1 | tail -5
```

Expected: `ARCHIVE SUCCEEDED`

- [ ] **Step 4: Build DMG**

```bash
bash build-dmg.sh
```

Expected: `LocalSmartz.dmg` created in `build/` directory. Verify it mounts:

```bash
hdiutil attach build/LocalSmartz.dmg
ls /Volumes/LocalSmartz/
hdiutil detach /Volumes/LocalSmartz
```
