"""Per-user (global) configuration for Local Smartz.

Stores user-wide settings at ~/.localsmartz/global.json. Parallel to
src/localsmartz/config.py, which handles per-project (per-cwd) config.

This module is a pure data layer: no prints, stdlib only.
"""

import builtins
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_GLOBAL_DIR: Path = Path.home() / ".localsmartz"
DEFAULT_GLOBAL_FILE: Path = DEFAULT_GLOBAL_DIR / "global.json"

# Schema — keys mapped to their allowed Python types.
SCHEMA_KEYS: dict[str, type] = {
    "workspace": str,
    "python_path": str,
    "active_model": str,
    "plugin_paths": list,
    "active_skills": list,
}


def _defaults() -> dict[str, Any]:
    """Compute default values. Re-evaluated each call so Path.home() is honored
    even in tests that monkeypatch HOME."""
    return {
        "workspace": str(Path.home() / "Documents" / "LocalSmartz"),
        "python_path": sys.executable,
        "active_model": "",
        "plugin_paths": [
            str(Path.home() / "Desktop" / "git-folder" / "RossLabs-AI-Toolkit")
        ],
        "active_skills": [],
    }


def all_defaults() -> dict[str, Any]:
    """Return a fresh copy of the defaults dict."""
    return _defaults()


def _global_file() -> Path:
    """Resolve the global config file path at call time (honors HOME changes)."""
    return Path.home() / ".localsmartz" / "global.json"


def _global_dir() -> Path:
    return Path.home() / ".localsmartz"


def _validate(key: str, value: Any) -> None:
    """Raise ValueError if key is unknown or value type doesn't match schema."""
    if key not in SCHEMA_KEYS:
        raise ValueError(f"Unknown config key: {key!r}")
    expected = SCHEMA_KEYS[key]
    # bool is a subclass of int; we don't accept bools for str/list fields.
    if not isinstance(value, expected) or isinstance(value, bool) and expected is not bool:
        raise ValueError(
            f"Invalid type for {key!r}: expected {expected.__name__}, "
            f"got {type(value).__name__}"
        )


def _read_raw() -> dict[str, Any]:
    """Read the file as dict. Returns {} if missing or corrupted."""
    path = _global_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_global() -> dict[str, Any]:
    """Load ~/.localsmartz/global.json.

    Returns a dict with defaults filled in for any missing keys. If the file
    is missing or corrupted, returns an all-defaults dict (does not crash).

    Only known schema keys are surfaced; stray keys in the file are preserved
    under their own name but not typechecked here.
    """
    raw = _read_raw()
    merged = _defaults()
    # Only override defaults when the stored value has the expected type.
    for key, expected in SCHEMA_KEYS.items():
        if key in raw:
            value = raw[key]
            if isinstance(value, expected) and not (
                isinstance(value, bool) and expected is not bool
            ):
                merged[key] = value
    # Preserve any extra (non-schema) keys verbatim.
    for key, value in raw.items():
        if key not in SCHEMA_KEYS:
            merged[key] = value
    return merged


def save_global(data: dict[str, Any]) -> None:
    """Merge-and-save: read existing file, overlay `data`, write atomically.

    - Validates every key in `data` against SCHEMA_KEYS (raises ValueError on
      unknown key or type mismatch).
    - Creates ~/.localsmartz/ if missing.
    - Atomic write via tempfile.mkstemp in same dir + os.replace.
    """
    if not isinstance(data, dict):
        raise ValueError(f"save_global expects a dict, got {type(data).__name__}")

    for key, value in data.items():
        _validate(key, value)

    config_dir = _global_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    existing = _read_raw()
    merged = {**existing, **data}

    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_dir), suffix=".tmp", prefix="global_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(_global_file()))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get(key: str) -> Any:
    """Get one key, returning the default if the file is missing the key.

    Raises ValueError if key is not in SCHEMA_KEYS.
    """
    if key not in SCHEMA_KEYS:
        raise ValueError(f"Unknown config key: {key!r}")
    merged = load_global()
    return merged[key]


def set(key: str, value: Any) -> None:  # noqa: A001 — matches required API
    """Set one key. Validates type, persists immediately."""
    _validate(key, value)
    save_global({key: value})


# Expose builtins.set under an alias so callers that overwrote the name via
# `from localsmartz.global_config import *` can still access the builtin if
# they need it. Not part of the public API.
_builtin_set = builtins.set


def reset() -> None:
    """Delete the global config file (forces re-defaulting on next load)."""
    path = _global_file()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
