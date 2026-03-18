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
