"""Tests for localsmartz.global_config (per-user config layer)."""

import json
import os
import sys
from pathlib import Path

import pytest

from localsmartz import global_config


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    """Redirect Path.home() to tmp_path so tests don't touch the real ~.

    Sets both HOME and USERPROFILE (Windows) env vars; Path.home() consults
    these via os.path.expanduser.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield tmp_path


def test_load_missing_returns_defaults(isolate_home):
    data = global_config.load_global()
    defaults = global_config.all_defaults()
    assert data == defaults
    # File should not have been created by a pure load.
    assert not (isolate_home / ".localsmartz" / "global.json").exists()


def test_defaults_use_path_home_not_hardcoded_user(isolate_home):
    defaults = global_config.all_defaults()
    expected_toolkit = str(
        isolate_home / "Desktop" / "git-folder" / "RossLabs-AI-Toolkit"
    )
    assert expected_toolkit in defaults["plugin_paths"]
    assert defaults["workspace"] == str(isolate_home / "Documents" / "LocalSmartz")
    assert defaults["python_path"] == sys.executable


def test_save_and_load_round_trip(isolate_home):
    global_config.save_global({"active_model": "qwen3:8b", "active_skills": ["a", "b"]})
    loaded = global_config.load_global()
    assert loaded["active_model"] == "qwen3:8b"
    assert loaded["active_skills"] == ["a", "b"]
    # Unset keys still carry defaults.
    assert loaded["workspace"] == str(isolate_home / "Documents" / "LocalSmartz")


def test_save_creates_directory(isolate_home):
    assert not (isolate_home / ".localsmartz").exists()
    global_config.save_global({"active_model": "m"})
    assert (isolate_home / ".localsmartz" / "global.json").exists()


def test_save_merges_existing(isolate_home):
    global_config.save_global({"active_model": "first"})
    global_config.save_global({"active_skills": ["x"]})
    raw = json.loads((isolate_home / ".localsmartz" / "global.json").read_text())
    assert raw["active_model"] == "first"
    assert raw["active_skills"] == ["x"]


def test_type_validation_rejects_wrong_type(isolate_home):
    with pytest.raises(ValueError):
        global_config.save_global({"workspace": 123})
    with pytest.raises(ValueError):
        global_config.set("plugin_paths", "not-a-list")
    with pytest.raises(ValueError):
        global_config.set("active_model", ["not-a-string"])


def test_unknown_key_rejected(isolate_home):
    with pytest.raises(ValueError):
        global_config.save_global({"bogus_key": "value"})
    with pytest.raises(ValueError):
        global_config.set("nope", "x")
    with pytest.raises(ValueError):
        global_config.get("nope")


def test_get_returns_merged_value(isolate_home):
    # Before save: default
    assert global_config.get("active_model") == ""
    global_config.set("active_model", "llama3.1")
    assert global_config.get("active_model") == "llama3.1"
    # Other keys still default
    assert global_config.get("active_skills") == []


def test_set_persists_immediately(isolate_home):
    global_config.set("active_skills", ["research", "commit"])
    raw = json.loads((isolate_home / ".localsmartz" / "global.json").read_text())
    assert raw["active_skills"] == ["research", "commit"]


def test_reset_deletes_file(isolate_home):
    global_config.save_global({"active_model": "x"})
    path = isolate_home / ".localsmartz" / "global.json"
    assert path.exists()
    global_config.reset()
    assert not path.exists()
    # load returns defaults again.
    assert global_config.load_global() == global_config.all_defaults()


def test_reset_when_missing_is_noop(isolate_home):
    # Should not raise when file does not exist.
    global_config.reset()


def test_corrupted_file_returns_defaults(isolate_home):
    config_dir = isolate_home / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "global.json").write_text("{not valid json")
    data = global_config.load_global()
    assert data == global_config.all_defaults()


def test_non_dict_json_returns_defaults(isolate_home):
    config_dir = isolate_home / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "global.json").write_text("[1, 2, 3]")
    data = global_config.load_global()
    assert data == global_config.all_defaults()


def test_atomic_write_cleans_up_on_replace_failure(isolate_home, monkeypatch):
    """Simulate os.replace failure; temp file should be cleaned up."""
    config_dir = isolate_home / ".localsmartz"
    config_dir.mkdir(parents=True, exist_ok=True)

    real_replace = os.replace

    def boom(src, dst):  # noqa: ARG001
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated"):
        global_config.save_global({"active_model": "m"})

    # Restore so our own cleanup verification can inspect the dir cleanly.
    monkeypatch.setattr(os, "replace", real_replace)

    # No leftover temp files starting with global_
    leftovers = [p for p in config_dir.iterdir() if p.name.startswith("global_")]
    assert leftovers == [], f"temp files not cleaned up: {leftovers}"
    # Final file was never created.
    assert not (config_dir / "global.json").exists()


def test_concurrent_writes_last_wins_no_partial_json(isolate_home):
    """Two rapid sequential writes: last write wins, file parses cleanly."""
    global_config.save_global({"active_model": "first"})
    global_config.save_global({"active_model": "second"})
    path = isolate_home / ".localsmartz" / "global.json"
    raw = json.loads(path.read_text())  # must parse
    assert raw["active_model"] == "second"


def test_preserves_extra_unknown_keys_already_in_file(isolate_home):
    """If the file has extra keys (e.g. from a newer version), load_global
    surfaces them and save_global preserves them on merge."""
    config_dir = isolate_home / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "global.json").write_text(
        json.dumps({"active_model": "m", "future_key": "future_value"})
    )
    loaded = global_config.load_global()
    assert loaded["active_model"] == "m"
    assert loaded["future_key"] == "future_value"

    # Save something new — future_key must be preserved.
    global_config.save_global({"active_skills": ["s1"]})
    raw = json.loads((config_dir / "global.json").read_text())
    assert raw["future_key"] == "future_value"
    assert raw["active_model"] == "m"
    assert raw["active_skills"] == ["s1"]


def test_plugin_paths_list_of_strings_accepted(isolate_home):
    global_config.set("plugin_paths", ["/a", "/b"])
    assert global_config.get("plugin_paths") == ["/a", "/b"]


def test_empty_dict_save_is_noop_but_creates_dir(isolate_home):
    global_config.save_global({})
    # File written with whatever existed before (empty).
    path = isolate_home / ".localsmartz" / "global.json"
    assert path.exists()
    raw = json.loads(path.read_text())
    assert raw == {}
