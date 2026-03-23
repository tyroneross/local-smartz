"""Persistent model configuration.

Stores user's model selection at .localsmartz/config.json so the first-run
picker only needs to run once. Config is per-project (per working directory).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from localsmartz.ollama import (
    check_server,
    get_version,
    list_models_with_size,
    model_available,
)
from localsmartz.profiles import detect_profile

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

    # Valid if it has planning_model OR folders
    has_model = isinstance(data.get("planning_model"), str) and data["planning_model"]
    has_folders = isinstance(data.get("folders"), list)
    if not has_model and not has_folders:
        return None

    return data


def save_config(cwd: Path, config: dict) -> None:
    """Save config to .localsmartz/config.json.

    Read-modify-write: merges new keys with existing config.
    Atomic write: writes to temp file in same directory, then renames.
    Creates .localsmartz/ directory if it doesn't exist.
    """
    config_dir = cwd / ".localsmartz"
    config_dir.mkdir(parents=True, exist_ok=True)

    path = _config_path(cwd)

    # Read existing config to merge
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Merge: new keys overwrite existing, existing keys preserved
    merged = {**existing, **config}

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_dir), suffix=".tmp", prefix="config_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_folders(cwd: Path) -> list[str]:
    """Get configured research folders. Returns [] if none configured.

    Reads config JSON directly — does not depend on load_config validation.
    """
    path = _config_path(cwd)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        folders = data.get("folders", [])
        return folders if isinstance(folders, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def add_folder(cwd: Path, folder_path: str) -> list[str]:
    """Add a research folder to config. Returns updated list.

    Expands ~, normalizes path. No-op if already present.
    """
    expanded = str(Path(folder_path).expanduser())
    folders = get_folders(cwd)
    if expanded not in folders:
        folders.append(expanded)
    save_config(cwd, {"folders": folders})
    return folders


def remove_folder(cwd: Path, folder_path: str) -> list[str]:
    """Remove a research folder from config. Returns updated list.

    Expands ~, normalizes path. No-op if not present.
    """
    expanded = str(Path(folder_path).expanduser())
    folders = get_folders(cwd)
    folders = [f for f in folders if f != expanded]
    save_config(cwd, {"folders": folders})
    return folders


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

    from localsmartz.utils.hardware import get_ram_gb
    ram_gb = get_ram_gb()
    if ram_gb > 0:
        print(f"  Hardware: {ram_gb} GB RAM · {detected} profile")
    else:
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
    if config and config.get("planning_model"):
        model = config["planning_model"]
        # Validate model still exists in Ollama
        if check_server() and model_available(model):
            return model
        else:
            print(f"\n  \033[33mSaved model '{model}' is no longer available.\033[0m",
                  file=sys.stderr)

    # Priority 3: First-run picker
    return first_run_picker(cwd, profile_name)
