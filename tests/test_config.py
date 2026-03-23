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


from unittest.mock import patch, MagicMock
from localsmartz.config import resolve_model, save_config


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
         patch("localsmartz.config.detect_profile", return_value="lite"), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True  # interactive mode
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


def test_save_config_merges_existing_keys(tmp_path):
    """save_config merges with existing config, doesn't overwrite."""
    save_config(tmp_path, {"planning_model": "model-a", "profile": "full"})
    save_config(tmp_path, {"folders": ["~/docs"]})
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded["planning_model"] == "model-a"
    assert loaded["folders"] == ["~/docs"]


def test_save_config_overwrites_same_key(tmp_path):
    """save_config overwrites keys that are explicitly passed."""
    save_config(tmp_path, {"planning_model": "model-a", "profile": "full"})
    save_config(tmp_path, {"planning_model": "model-b"})
    loaded = load_config(tmp_path)
    assert loaded["planning_model"] == "model-b"
    assert loaded["profile"] == "full"


def test_load_config_folders_only(tmp_path):
    """Config with only folders (no planning_model) is valid."""
    save_config(tmp_path, {"folders": ["~/docs"]})
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded["folders"] == ["~/docs"]


def test_get_folders_returns_list(tmp_path):
    """get_folders returns folder list from config."""
    from localsmartz.config import get_folders
    save_config(tmp_path, {"planning_model": "x", "folders": ["/a", "/b"]})
    assert get_folders(tmp_path) == ["/a", "/b"]


def test_get_folders_empty_when_missing(tmp_path):
    """get_folders returns [] when no folders key or no config."""
    from localsmartz.config import get_folders
    assert get_folders(tmp_path) == []
    save_config(tmp_path, {"planning_model": "x"})
    assert get_folders(tmp_path) == []


def test_get_folders_corrupted_config(tmp_path):
    """get_folders returns [] for corrupted config."""
    from localsmartz.config import get_folders
    config_dir = tmp_path / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json")
    assert get_folders(tmp_path) == []


def test_resolve_model_folders_only_config_runs_picker(tmp_path):
    """Config with only folders (no planning_model) falls through to picker."""
    save_config(tmp_path, {"folders": ["/docs"]})
    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.list_models_with_size", return_value=[("m:8b", 5.0)]), \
         patch("localsmartz.config.get_version", return_value="0.15.2"), \
         patch("localsmartz.config.detect_profile", return_value="lite"), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = resolve_model(tmp_path, cli_model=None, profile_name="lite")
        assert result == "m:8b"
