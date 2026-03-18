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
